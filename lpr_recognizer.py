"""GPU Chinese license-plate detection and recognition.

The pipeline follows we0091234/Chinese_license_plate_detection_recognition
and its current yolo26-plate successor: YOLO Pose locates a plate and its four
corners, then a CRNN recognizes the rectified plate and its color.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import cv2
import numpy as np

try:
    import torch
    from ultralytics import YOLO

    from plate_recognition_model import PlateOCRNet

    HAS_PLATE_RECOGNIZER = True
except ImportError:
    HAS_PLATE_RECOGNIZER = False


PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_DETECTOR_MODEL = PROJECT_DIR / "weights" / "yolo26s-plate-detect.pt"
DEFAULT_RECOGNIZER_MODEL = PROJECT_DIR / "weights" / "plate_rec_color.pth"

PLATE_CHARACTERS = (
    "#京沪津渝冀晋蒙辽吉黑苏浙皖闽赣鲁豫鄂湘粤桂琼川贵云藏陕甘青宁新"
    "学警港澳挂使领民航危0123456789ABCDEFGHJKLMNPQRSTUVWXYZ险品"
)
PLATE_COLORS = ("黑色", "蓝色", "绿色", "白色", "黄色")
NORMALIZE_MEAN = 0.588
NORMALIZE_STD = 0.193


@dataclass
class PlateRecognition:
    plate_text: str
    confidence: float
    bbox: Tuple[int, int, int, int]
    plate_color: str = ""


class LPRRecognizer:
    """YOLO Pose plate detector plus batched CUDA CRNN recognizer."""

    def __init__(
        self,
        conf_threshold: float = 0.7,
        device: str = "cpu",
        detector_model: Path = DEFAULT_DETECTOR_MODEL,
        recognizer_model: Path = DEFAULT_RECOGNIZER_MODEL,
        detector_conf: float = 0.3,
        detector_iou: float = 0.5,
        image_size: int = 640,
    ) -> None:
        if not HAS_PLATE_RECOGNIZER:
            raise ImportError("车牌识别依赖未安装，请运行 uv sync")

        self.conf_threshold = conf_threshold
        self.device = torch.device(device)
        self.detector_conf = detector_conf
        self.detector_iou = detector_iou
        self.image_size = image_size
        self.use_half = self.device.type == "cuda"

        detector_path = Path(detector_model)
        recognizer_path = Path(recognizer_model)
        for model_path in (detector_path, recognizer_path):
            if not model_path.is_file():
                raise FileNotFoundError(f"车牌模型不存在: {model_path}")

        self.detector = YOLO(str(detector_path))
        self.detector.to(self.device)
        self.recognizer = self._load_recognizer(recognizer_path)

    def _load_recognizer(self, model_path: Path) -> PlateOCRNet:
        checkpoint = torch.load(model_path, map_location="cpu", weights_only=True)
        model = PlateOCRNet(
            cfg=checkpoint["cfg"],
            num_classes=len(PLATE_CHARACTERS),
            color_classes=len(PLATE_COLORS),
        )

        # Upstream names differ from the local descriptive module names.
        replacements = {
            "conv1.": "color_conv.",
            "bn1.": "color_bn.",
            "color_classifier.": "color_classifier.",
            "color_bn.": "color_classifier_bn.",
            "newCnn.": "character_classifier.",
        }
        state_dict = {}
        for name, value in checkpoint["state_dict"].items():
            mapped = name
            for old, new in replacements.items():
                if mapped.startswith(old):
                    mapped = new + mapped[len(old):]
                    break
            state_dict[mapped] = value

        missing, unexpected = model.load_state_dict(state_dict, strict=False)
        trainable_missing = [name for name in missing if not name.endswith("num_batches_tracked")]
        if trainable_missing or unexpected:
            raise RuntimeError(
                f"车牌识别权重不兼容: missing={trainable_missing}, unexpected={unexpected}"
            )

        model.to(self.device).eval()
        if self.use_half:
            model.half()
        return model

    @staticmethod
    def _rectify_plate(image: np.ndarray, points: np.ndarray) -> np.ndarray:
        points = points.astype(np.float32)
        top_left, top_right, bottom_right, bottom_left = points
        width = max(
            int(np.linalg.norm(bottom_right - bottom_left)),
            int(np.linalg.norm(top_right - top_left)),
        )
        height = max(
            int(np.linalg.norm(top_right - bottom_right)),
            int(np.linalg.norm(top_left - bottom_left)),
        )
        if width < 2 or height < 2:
            return np.empty((0, 0, 3), dtype=np.uint8)

        destination = np.array(
            [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
            dtype=np.float32,
        )
        transform = cv2.getPerspectiveTransform(points, destination)
        return cv2.warpPerspective(image, transform, (width, height))

    @staticmethod
    def _merge_double_plate(image: np.ndarray) -> np.ndarray:
        height = image.shape[0]
        upper = image[: int(5 * height / 12), :]
        lower = image[int(height / 3):, :]
        if upper.size == 0 or lower.size == 0:
            return image
        upper = cv2.resize(upper, (lower.shape[1], lower.shape[0]))
        return np.hstack((upper, lower))

    def _prepare_batch(self, images: List[np.ndarray]) -> torch.Tensor:
        tensors = []
        for image in images:
            resized = cv2.resize(image, (168, 48)).astype(np.float32)
            resized = (resized / 255.0 - NORMALIZE_MEAN) / NORMALIZE_STD
            tensors.append(resized.transpose(2, 0, 1))
        batch = torch.from_numpy(np.stack(tensors)).to(self.device)
        return batch.half() if self.use_half else batch

    @staticmethod
    def _decode(sequence: torch.Tensor) -> Tuple[str, float]:
        probabilities = torch.softmax(sequence.float(), dim=-1)
        confidence, indices = probabilities.max(dim=-1)
        indices = indices.detach().cpu().tolist()
        confidence = confidence.detach().cpu().tolist()

        characters = []
        scores = []
        previous = 0
        for index, score in zip(indices, confidence):
            if index != 0 and index != previous:
                characters.append(PLATE_CHARACTERS[index])
                scores.append(float(score))
            previous = index
        return "".join(characters), float(np.mean(scores)) if scores else 0.0

    def recognize(self, image: np.ndarray) -> List[PlateRecognition]:
        if image is None or image.size == 0:
            return []

        with torch.inference_mode():
            detections = self.detector.predict(
                image,
                conf=self.detector_conf,
                iou=self.detector_iou,
                imgsz=self.image_size,
                device=str(self.device),
                verbose=False,
            )

        rectified = []
        metadata = []
        for result in detections:
            if result.boxes is None or result.keypoints is None:
                continue
            count = min(len(result.boxes), len(result.keypoints.xy))
            for index in range(count):
                box = result.boxes.xyxy[index].detach().cpu().numpy()
                points = result.keypoints.xy[index].detach().cpu().numpy()
                plate = self._rectify_plate(image, points)
                if plate.size == 0:
                    continue
                if int(result.boxes.cls[index]) == 1:
                    plate = self._merge_double_plate(plate)
                rectified.append(plate)
                metadata.append(box)

        if not rectified:
            return []

        batch = self._prepare_batch(rectified)
        with torch.inference_mode():
            sequences, color_logits = self.recognizer(batch)
            color_indices = color_logits.float().argmax(dim=-1).detach().cpu().tolist()

        recognized = []
        height, width = image.shape[:2]
        for sequence, color_index, box in zip(sequences, color_indices, metadata):
            text, confidence = self._decode(sequence)
            if not text or confidence < self.conf_threshold:
                continue
            x1, y1, x2, y2 = box.astype(int)
            bbox = (
                int(max(0, min(x1, width - 1))),
                int(max(0, min(y1, height - 1))),
                int(max(1, min(x2, width))),
                int(max(1, min(y2, height))),
            )
            recognized.append(
                PlateRecognition(
                    plate_text=text,
                    confidence=confidence,
                    bbox=bbox,
                    plate_color=PLATE_COLORS[color_index],
                )
            )

        recognized.sort(key=lambda item: item.confidence, reverse=True)
        return recognized

    def recognize_roi(
        self,
        frame: np.ndarray,
        vehicle_bbox: Tuple[int, int, int, int],
        padding_ratio: float = 0.15,
    ) -> List[PlateRecognition]:
        height, width = frame.shape[:2]
        x1, y1, x2, y2 = vehicle_bbox
        pad_x = int((x2 - x1) * padding_ratio)
        pad_y = int((y2 - y1) * padding_ratio)
        left = max(0, int(x1) - pad_x)
        top = max(0, int(y1) - pad_y)
        right = min(width, int(x2) + pad_x)
        bottom = min(height, int(y2) + pad_y)
        roi = frame[top:bottom, left:right]

        results = self.recognize(roi)
        for result in results:
            bx1, by1, bx2, by2 = result.bbox
            result.bbox = (bx1 + left, by1 + top, bx2 + left, by2 + top)
        return results

    @property
    def threshold(self) -> float:
        return self.conf_threshold

    @threshold.setter
    def threshold(self, value: float) -> None:
        self.conf_threshold = max(0.0, min(1.0, value))
