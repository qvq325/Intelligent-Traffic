from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import torch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from detection_processor import DetectionProcessor


def percentile(values: list[float], value: float) -> float:
    return (
        float(np.percentile(np.asarray(values, dtype=np.float64), value))
        if values
        else 0.0
    )


def run(source: Path, frames: int, interval: int, device: str) -> dict:
    processor = DetectionProcessor(
        yolo_conf=0.65,
        lpr_conf=0.30,
        device=device,
        vehicle_model_path=PROJECT_ROOT / "训练后模型" / "yolo26x.pt",
        plate_model_path=PROJECT_ROOT / "训练后模型" / "license_plate_best.pt",
        inference_size=640,
        lpr_mode="box",
    )
    if not processor.initialize():
        raise RuntimeError(processor.init_error or "processor initialization failed")

    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open source: {source}")

    if device.startswith("cuda"):
        torch.cuda.reset_peak_memory_stats(device)
    latencies: list[float] = []
    vehicle_observations = 0
    plate_observations = 0
    text_switches = 0
    last_text: dict[int, str] = {}
    decoded = 0
    sampled = 0
    started = time.perf_counter()
    try:
        while decoded < frames:
            ok, frame = capture.read()
            if not ok:
                break
            decoded += 1
            if decoded % interval:
                continue
            before = time.perf_counter()
            _, results = processor.process(frame, camera_id=source.name)
            latencies.append((time.perf_counter() - before) * 1000.0)
            sampled += 1
            vehicle_observations += len(results)
            for result in results:
                if not result.has_plate:
                    continue
                plate_observations += 1
                previous = last_text.get(result.track_id)
                if previous and previous != result.plate_text:
                    text_switches += 1
                if result.track_id >= 0:
                    last_text[result.track_id] = result.plate_text
    finally:
        capture.release()

    elapsed = time.perf_counter() - started
    peak_memory = (
        int(torch.cuda.max_memory_allocated(device))
        if device.startswith("cuda")
        else 0
    )
    return {
        "source": str(source),
        "decoded_frames": decoded,
        "sampled_frames": sampled,
        "vehicle_observations": vehicle_observations,
        "plate_observations": plate_observations,
        "plate_continuity": round(
            plate_observations / max(1, vehicle_observations), 4
        ),
        "unique_plate_tracks": len(last_text),
        "plate_text_switches": text_switches,
        "latency_ms_p50": round(percentile(latencies, 50), 2),
        "latency_ms_p95": round(percentile(latencies, 95), 2),
        "sampled_fps": round(sampled / max(elapsed, 1e-9), 2),
        "peak_cuda_memory_bytes": peak_memory,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("--frames", type=int, default=300)
    parser.add_argument("--interval", type=int, default=5)
    parser.add_argument("--device", default="cuda:0")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = run(
        args.source,
        max(1, args.frames),
        max(1, args.interval),
        args.device,
    )
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
