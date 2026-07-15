from __future__ import annotations

from collections import OrderedDict, defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lpr_recognizer import PlateRecognition


@dataclass(frozen=True, slots=True)
class TrackedVehicle:
    bbox: tuple[int, int, int, int]
    track_id: int
    confidence: float


@dataclass(frozen=True, slots=True)
class PlateTrackObservation:
    recognition: PlateRecognition
    detector_confidence: float
    relative_bbox: tuple[float, float, float, float]

    @classmethod
    def from_absolute(
        cls,
        recognition: PlateRecognition,
        *,
        detector_confidence: float,
        vehicle_bbox: tuple[int, int, int, int],
    ) -> PlateTrackObservation:
        vx1, vy1, vx2, vy2 = vehicle_bbox
        width = max(1, vx2 - vx1)
        height = max(1, vy2 - vy1)
        x1, y1, x2, y2 = recognition.bbox
        return cls(
            recognition=recognition,
            detector_confidence=float(detector_confidence),
            relative_bbox=(
                (x1 - vx1) / width,
                (y1 - vy1) / height,
                (x2 - vx1) / width,
                (y2 - vy1) / height,
            ),
        )


@dataclass(slots=True)
class _TrackState:
    history: deque[tuple[int, PlateTrackObservation]]
    last_active_tick: int
    last_observed_tick: int


class PlateTemporalFusion:
    def __init__(
        self,
        window_size: int = 5,
        hold_frames: int = 5,
        max_tracks: int = 256,
        decay: float = 0.9,
    ) -> None:
        self.window_size = max(1, int(window_size))
        self.hold_frames = max(0, int(hold_frames))
        self.max_tracks = max(1, int(max_tracks))
        self.decay = max(0.0, min(1.0, float(decay)))
        self._ticks: dict[str, int] = {}
        self._tracks: OrderedDict[tuple[str, int], _TrackState] = OrderedDict()

    def resolve(
        self,
        camera_id: str,
        vehicles: Sequence[TrackedVehicle],
        observations: Mapping[int, PlateTrackObservation],
    ) -> list[PlateRecognition]:
        tick = self._ticks.get(camera_id, 0) + 1
        self._ticks[camera_id] = tick
        resolved: list[PlateRecognition] = []
        for vehicle in vehicles:
            if vehicle.track_id < 0:
                continue
            key = (camera_id, vehicle.track_id)
            observation = observations.get(vehicle.track_id)
            state = self._tracks.get(key)
            if state is None and observation is None:
                continue
            if state is None:
                state = _TrackState(
                    history=deque(maxlen=self.window_size),
                    last_active_tick=tick,
                    last_observed_tick=tick,
                )
                self._tracks[key] = state
            state.last_active_tick = tick
            if observation is not None:
                state.history.append((tick, observation))
                state.last_observed_tick = tick
            self._tracks.move_to_end(key)
            missed = tick - state.last_observed_tick
            if state.history and missed <= self.hold_frames:
                resolved.append(self._render(state, vehicle, missed))

        for key, state in list(self._tracks.items()):
            if key[0] == camera_id and tick - state.last_active_tick > self.hold_frames:
                del self._tracks[key]
        while len(self._tracks) > self.max_tracks:
            self._tracks.popitem(last=False)
        return resolved

    def _render(
        self,
        state: _TrackState,
        vehicle: TrackedVehicle,
        missed: int,
    ) -> PlateRecognition:
        scores: dict[str, float] = defaultdict(float)
        newest: dict[str, int] = {}
        for observed_tick, item in state.history:
            text = item.recognition.plate_text
            scores[text] += item.detector_confidence * item.recognition.confidence
            newest[text] = max(newest.get(text, 0), observed_tick)
        winner = max(scores, key=lambda text: (scores[text], newest[text]))
        _observed_tick, representative = max(
            (
                (observed_tick, item)
                for observed_tick, item in state.history
                if item.recognition.plate_text == winner
            ),
            key=lambda pair: (
                pair[1].detector_confidence * pair[1].recognition.confidence,
                pair[0],
            ),
        )
        vx1, vy1, vx2, vy2 = vehicle.bbox
        width = max(1, vx2 - vx1)
        height = max(1, vy2 - vy1)
        rx1, ry1, rx2, ry2 = representative.relative_bbox
        bbox = (
            max(vx1, min(vx2, round(vx1 + rx1 * width))),
            max(vy1, min(vy2, round(vy1 + ry1 * height))),
            max(vx1, min(vx2, round(vx1 + rx2 * width))),
            max(vy1, min(vy2, round(vy1 + ry2 * height))),
        )
        return PlateRecognition(
            plate_text=winner,
            confidence=representative.recognition.confidence * self.decay**missed,
            bbox=bbox,
            plate_color=representative.recognition.plate_color,
        )

    def reset(self, camera_id: str | None = None) -> None:
        if camera_id is None:
            self._ticks.clear()
            self._tracks.clear()
            return
        self._ticks.pop(camera_id, None)
        for key in [key for key in self._tracks if key[0] == camera_id]:
            del self._tracks[key]

    @property
    def cache_size(self) -> int:
        return len(self._tracks)
