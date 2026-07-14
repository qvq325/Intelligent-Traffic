"""Bounded, parallel RTSP first-frame probing."""

from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.parse import urlsplit

import cv2


@dataclass(frozen=True, slots=True)
class ProbeResult:
    stream_id: str
    ok: bool
    code: str
    message: str
    elapsed_ms: int
    width: int = 0
    height: int = 0

    def as_dict(self) -> dict:
        return asdict(self)


class StreamProbeService:
    def __init__(self, *, timeout_seconds: float = 8.0, max_workers: int = 6) -> None:
        self.timeout_seconds = max(0.5, float(timeout_seconds))
        self.max_workers = max(1, min(12, int(max_workers)))

    def probe(self, stream_id: str, url: str) -> ProbeResult:
        started = time.monotonic()
        try:
            parsed = urlsplit(url)
        except ValueError:
            return self._failed(stream_id, started, "STREAM_URL_INVALID", "RTSP 地址无效")
        if parsed.scheme.lower() not in {"rtsp", "rtsps"} or not parsed.hostname:
            return self._failed(stream_id, started, "STREAM_URL_INVALID", "RTSP 地址无效")

        capture = cv2.VideoCapture()
        capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, int(self.timeout_seconds * 1000))
        capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(self.timeout_seconds * 1000))
        try:
            if not capture.open(url, cv2.CAP_FFMPEG):
                return self._failed(stream_id, started, "STREAM_CONNECT_FAILED", "无法建立 RTSP 连接")
            ok, frame = capture.read()
            if not ok or frame is None or getattr(frame, "size", 0) == 0:
                return self._failed(stream_id, started, "STREAM_NOT_DECODABLE", "未取得可解码首帧")
            height, width = frame.shape[:2]
            return ProbeResult(
                stream_id=stream_id,
                ok=True,
                code="OK",
                message="首帧解码成功",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                width=int(width),
                height=int(height),
            )
        except (cv2.error, OSError, socket.error) as exc:
            return self._failed(stream_id, started, "STREAM_PROBE_FAILED", str(exc))
        finally:
            capture.release()

    def probe_many(self, streams: Iterable[dict]) -> list[dict]:
        requested = [dict(stream) for stream in streams]
        if not requested:
            return []
        results: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(requested))) as pool:
            futures = {
                pool.submit(self.probe, item["stream_id"], item["rtsp_url"]): item["stream_id"]
                for item in requested
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result().as_dict()
        return [results[item["stream_id"]] for item in requested]

    @staticmethod
    def _failed(stream_id: str, started: float, code: str, message: str) -> ProbeResult:
        return ProbeResult(
            stream_id=stream_id,
            ok=False,
            code=code,
            message=message[:500],
            elapsed_ms=int((time.monotonic() - started) * 1000),
        )
