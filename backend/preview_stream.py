"""Lightweight source-specific streams for the multi-camera monitor."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
from typing import Iterator

import cv2


class PreviewChannel:
    """Decode one source and publish a bandwidth-limited MJPEG preview."""

    def __init__(
        self,
        source_id: str,
        url: str,
        *,
        max_fps: float = 10.0,
        max_width: int = 960,
        idle_timeout: float = 5.0,
    ) -> None:
        self.source_id = source_id
        self.url = url
        self.max_fps = max_fps
        self.max_width = max_width
        self.idle_timeout = idle_timeout

        self._condition = threading.Condition(threading.RLock())
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._sequence = 0
        self._latest_jpeg: bytes | None = None
        self._subscribers = 0
        self._idle_since: float | None = None
        self._warmup_thread: threading.Thread | None = None

    def acquire(self) -> None:
        with self._condition:
            self._subscribers += 1
            self._idle_since = None
        self.start()

    def release(self) -> None:
        with self._condition:
            self._subscribers = max(0, self._subscribers - 1)
            if self._subscribers == 0:
                self._idle_since = time.monotonic()
            self._condition.notify_all()

    def prewarm(self, timeout: float = 8.0) -> bool:
        """Populate the first-frame cache without blocking application startup."""
        with self._condition:
            if self._latest_jpeg is not None:
                return False
            if self._warmup_thread and self._warmup_thread.is_alive():
                return False
            self._warmup_thread = threading.Thread(
                target=self._run_warmup,
                args=(timeout,),
                name=f"preview-warmup-{self.source_id}",
                daemon=True,
            )
            self._warmup_thread.start()
            return True

    def has_cached_frame(self) -> bool:
        with self._condition:
            return self._latest_jpeg is not None

    def latest_frame(self) -> bytes | None:
        with self._condition:
            return self._latest_jpeg

    def _run_warmup(self, timeout: float) -> None:
        with self._condition:
            sequence = self._sequence
        self.acquire()
        try:
            self.wait_for_frame(sequence, timeout=timeout)
        finally:
            self.release()

    def wait_for_frame(
        self,
        sequence: int,
        timeout: float = 2.0,
    ) -> tuple[int, bytes | None]:
        self.start()
        with self._condition:
            self._condition.wait_for(
                lambda: self._sequence != sequence or self._stop_event.is_set(),
                timeout=timeout,
            )
            if self._sequence == sequence:
                return sequence, None
            return self._sequence, self._latest_jpeg

    def start(self) -> None:
        with self._condition:
            if self._thread and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name=f"preview-{self.source_id}",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self.request_stop()
        self.join(timeout)

    def request_stop(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()

    def join(self, timeout: float = 5.0) -> None:
        with self._condition:
            thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        frame_interval = 1.0 / max(self.max_fps, 1.0)
        while not self._stop_event.is_set() and not self._idle_expired():
            capture = self._open_capture()
            if capture is None:
                self._stop_event.wait(2.0)
                continue
            try:
                while not self._stop_event.is_set() and not self._idle_expired():
                    started = time.monotonic()
                    ok, frame = capture.read()
                    if not ok or frame is None:
                        break
                    height, width = frame.shape[:2]
                    if width > self.max_width:
                        target_height = max(1, round(height * self.max_width / width))
                        frame = cv2.resize(
                            frame,
                            (self.max_width, target_height),
                            interpolation=cv2.INTER_AREA,
                        )
                    encoded_ok, encoded = cv2.imencode(
                        ".jpg",
                        frame,
                        [cv2.IMWRITE_JPEG_QUALITY, 74],
                    )
                    if encoded_ok:
                        self._publish(encoded.tobytes())
                    remaining = frame_interval - (time.monotonic() - started)
                    if remaining > 0 and self._stop_event.wait(remaining):
                        break
            finally:
                capture.release()
            if self._idle_expired():
                break
            self._stop_event.wait(1.0)

    def _idle_expired(self) -> bool:
        with self._condition:
            return (
                self._subscribers == 0
                and self._idle_since is not None
                and time.monotonic() - self._idle_since >= self.idle_timeout
            )

    def _open_capture(self) -> cv2.VideoCapture | None:
        params = [
            cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
            5000,
            cv2.CAP_PROP_READ_TIMEOUT_MSEC,
            3000,
        ]
        try:
            capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG, params)
        except (cv2.error, TypeError):
            capture = cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)
        if not capture.isOpened():
            capture.release()
            return None
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return capture

    def _publish(self, jpeg: bytes) -> None:
        with self._condition:
            self._latest_jpeg = jpeg
            self._sequence += 1
            self._condition.notify_all()


class MultiCameraPreviewService:
    """Own lazily started preview channels for configured camera sources."""

    def __init__(self, sources: Mapping[str, str]) -> None:
        self._channels = {
            source_id: PreviewChannel(source_id, url)
            for source_id, url in sources.items()
        }
        self._stop_event = threading.Event()
        self._prewarm_cancel_event = threading.Event()
        self._prewarm_lock = threading.Lock()
        self._prewarm_thread: threading.Thread | None = None

    def has_source(self, source_id: str) -> bool:
        return source_id in self._channels

    def reconfigure(self, sources: Mapping[str, str]) -> dict[str, list[str]]:
        """Atomically replace configured URLs while preserving unchanged caches."""
        self._prewarm_cancel_event.set()
        with self._prewarm_lock:
            prewarm_thread = self._prewarm_thread
        if prewarm_thread and prewarm_thread.is_alive():
            prewarm_thread.join(timeout=9.0)

        previous = self._channels
        replacement: dict[str, PreviewChannel] = {}
        changed: list[str] = []
        removed = sorted(set(previous) - set(sources))
        for source_id, url in sources.items():
            channel = previous.get(source_id)
            if channel is not None and channel.url == url:
                replacement[source_id] = channel
                continue
            changed.append(source_id)
            replacement[source_id] = PreviewChannel(source_id, url)

        retired = [
            channel
            for source_id, channel in previous.items()
            if source_id not in replacement or replacement[source_id] is not channel
        ]
        self._channels = replacement
        for channel in retired:
            channel.request_stop()
        for channel in retired:
            channel.join()
        self._prewarm_cancel_event.clear()
        return {"changed": sorted(changed), "removed": removed}

    def prewarm(self, source_ids: Iterable[str] | None = None) -> list[str]:
        requested = list(self._channels if source_ids is None else source_ids)
        with self._prewarm_lock:
            if self._prewarm_thread and self._prewarm_thread.is_alive():
                return []
            self._prewarm_thread = threading.Thread(
                target=self._run_prewarm,
                args=(requested,),
                name="preview-prewarm-coordinator",
                daemon=True,
            )
            self._prewarm_thread.start()
        return requested

    def _run_prewarm(self, source_ids: list[str]) -> None:
        pending = [source_id for source_id in source_ids if source_id in self._channels]
        for _ in range(3):
            if self._prewarm_cancelled() or not pending:
                return
            for offset in range(0, len(pending), 6):
                batch = pending[offset:offset + 6]
                for source_id in batch:
                    if self._prewarm_cancelled():
                        return
                    self._channels[source_id].prewarm(timeout=7.0)
                    self._stop_event.wait(0.08)

                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline and not self._prewarm_cancelled():
                    if all(
                        self._channels[source_id].has_cached_frame()
                        for source_id in batch
                    ):
                        break
                    self._stop_event.wait(0.25)

            pending = [
                source_id
                for source_id in pending
                if not self._channels[source_id].has_cached_frame()
            ]
            self._stop_event.wait(0.75)

    def _prewarm_cancelled(self) -> bool:
        return self._stop_event.is_set() or self._prewarm_cancel_event.is_set()

    def cancel_prewarm(self, keep_source_id: str | None = None) -> None:
        """Release background preview decoders before single-stream processing."""
        self._prewarm_cancel_event.set()
        for source_id, channel in self._channels.items():
            if source_id != keep_source_id:
                channel.request_stop()

    def wait_for_frame(
        self,
        source_id: str,
        sequence: int,
        timeout: float = 2.0,
    ) -> tuple[int, bytes | None]:
        return self._channels[source_id].wait_for_frame(sequence, timeout)

    def snapshot(
        self,
        source_id: str,
        timeout: float = 8.0,
        *,
        cached_only: bool = False,
    ) -> bytes | None:
        channel = self._channels[source_id]
        cached = channel.latest_frame()
        if cached is not None or cached_only:
            return cached
        with self.subscribe(source_id):
            _, frame = channel.wait_for_frame(-1, timeout=timeout)
            return frame

    @contextmanager
    def subscribe(self, source_id: str) -> Iterator[None]:
        channel = self._channels[source_id]
        channel.acquire()
        try:
            yield
        finally:
            channel.release()

    def stop(self) -> None:
        self._stop_event.set()
        self._prewarm_cancel_event.set()
        for channel in self._channels.values():
            channel.request_stop()
        for channel in self._channels.values():
            channel.join()
