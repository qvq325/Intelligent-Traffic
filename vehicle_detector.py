"""
YOLOv11m 车辆检测模块
使用 Ultralytics YOLO11 模型检测画面中的车辆（car, motorcycle, bus, truck）
"""
import cv2
import numpy as np
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

try:
    from ultralytics import YOLO
    from ultralytics.trackers.byte_tracker import BYTETracker
    from ultralytics.utils import IterableSimpleNamespace, YAML
    from ultralytics.utils.checks import check_yaml
    HAS_YOLO = True
except ImportError:
    HAS_YOLO = False


# COCO 数据集中的车辆类别
VEHICLE_CLASSES = {
    2: "car",         # 小汽车
    3: "motorcycle",  # 摩托车
    5: "bus",         # 公交车
    7: "truck",       # 卡车
}

# 类别名称到中文的映射
CLASS_NAME_CN = {
    "car": "小汽车",
    "motorcycle": "摩托车",
    "bus": "公交车",
    "truck": "卡车",
}


@dataclass
class VehicleDetection:
    """单次车辆检测结果"""
    bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2) 归一化或像素坐标
    confidence: float                 # YOLO 检测置信度 (0-1)
    class_id: int                     # COCO 类别 ID
    class_name: str                   # 类别名称 (car/motorcycle/bus/truck)
    class_name_cn: str                # 中文类别名称
    track_id: int = -1                # ByteTrack local track ID


class VehicleDetector:
    """
    YOLOv11m 车辆检测器

    使用方式:
        detector = VehicleDetector(conf_threshold=0.5, device="cpu")
        detections = detector.detect(frame)
    """

    def __init__(
        self,
        conf_threshold: float = 0.5,
        device: str = "cpu",
        model_name: str = "yolo11m.pt",
        imgsz: int = 640,
    ):
        """
        初始化车辆检测器

        Args:
            conf_threshold: 检测置信度阈值 (0-1)，低于此值的检测结果将被过滤
            device: 推理设备 ("cpu", "cuda", "cuda:0", "mps" 等)
            model_name: YOLO 模型名称或路径
            imgsz: 推理时的图像尺寸
        """
        if not HAS_YOLO:
            raise ImportError(
                "未安装 ultralytics 库，请运行: pip install ultralytics"
            )

        self.conf_threshold = conf_threshold
        self.device = device
        self.imgsz = imgsz

        # 加载模型（首次运行会自动下载 yolo11m.pt）
        self.model = YOLO(model_name)
        tracker_config = IterableSimpleNamespace(**YAML.load(check_yaml("bytetrack.yaml")))
        tracker_config.device = device
        self._tracker_config = tracker_config
        self._trackers = {}

    def detect(self, frame: np.ndarray, tracker_key: str = "default") -> List[VehicleDetection]:
        """
        对一帧图像执行车辆检测

        Args:
            frame: BGR 格式的 numpy 图像数组

        Returns:
            VehicleDetection 对象列表，按置信度降序排列
        """
        detections: List[VehicleDetection] = []

        results = self.model.predict(
            frame,
            imgsz=self.imgsz,
            device=self.device,
            conf=self.conf_threshold,
            classes=list(VEHICLE_CLASSES.keys()),
            verbose=False,
        )

        if not results or len(results) == 0:
            return detections

        result = results[0]
        boxes = result.boxes

        if boxes is None or len(boxes) == 0:
            return detections

        h, w = frame.shape[:2]
        tracker = self._trackers.get(tracker_key)
        if tracker is None:
            tracker = BYTETracker(self._tracker_config)
            self._trackers[tracker_key] = tracker
        tracks = tracker.update(boxes.cpu().numpy(), frame)

        for track in tracks:
            x1, y1, x2, y2 = track[:4].astype(int)
            track_id = int(track[4])
            conf = float(track[5])
            cls_id = int(track[6])
            cls_name = VEHICLE_CLASSES.get(cls_id, "unknown")

            # 边界检查
            x1 = max(0, x1)
            y1 = max(0, y1)
            x2 = min(w, x2)
            y2 = min(h, y2)

            if x2 <= x1 or y2 <= y1:
                continue

            detections.append(VehicleDetection(
                bbox=(int(x1), int(y1), int(x2), int(y2)),
                confidence=conf,
                class_id=cls_id,
                class_name=cls_name,
                class_name_cn=CLASS_NAME_CN.get(cls_name, cls_name),
                track_id=track_id,
            ))

        # 按置信度降序排列
        detections.sort(key=lambda d: d.confidence, reverse=True)
        return detections

    def reset_tracking(self, tracker_key: Optional[str] = None):
        """Discard one camera tracker, or every tracker when no key is given."""
        if tracker_key is None:
            self._trackers.clear()
        else:
            self._trackers.pop(tracker_key, None)

    @property
    def threshold(self) -> float:
        return self.conf_threshold

    @threshold.setter
    def threshold(self, value: float):
        """动态更新置信度阈值"""
        self.conf_threshold = max(0.0, min(1.0, value))
