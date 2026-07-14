"""
检测处理管线模块
整合 YOLOv11m 车辆检测 + GPU 中文车牌识别 + 白名单匹配 + 标注绘制
"""
import cv2
import numpy as np
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from vehicle_detector import VehicleDetector, VehicleDetection, HAS_YOLO
from lpr_recognizer import LPRRecognizer, PlateRecognition, HAS_PLATE_RECOGNIZER
from trained_plate_recognizer import BoxPlateRecognizer
from whitelist_manager import WhitelistManager, MatchResult
from draw_utils import (
    draw_vehicle_box, draw_plate_info, draw_info_panel,
    COLOR_GREEN, COLOR_BLUE, COLOR_ORANGE, COLOR_WHITE,
)


@dataclass
class DetectionResult:
    """完整的单次检测结果（车辆 + 车牌 + 白名单）"""
    # 车辆信息
    vehicle_bbox: Tuple[int, int, int, int]  # (x1, y1, x2, y2)
    vehicle_class: str                        # car / motorcycle / bus / truck
    vehicle_class_cn: str                     # 中文类别名
    yolo_confidence: float                    # YOLO 检测置信度
    track_id: int = -1                        # 当前摄像头内的 ByteTrack ID
    camera_id: str = ""                       # 视频源/摄像头名称
    timestamp: float = 0.0                    # 检测时间戳

    # 车牌信息
    plate_text: str = ""                      # 车牌号码
    plate_confidence: float = 0.0             # 车牌识别置信度
    plate_bbox: Tuple[int, int, int, int] = (0, 0, 0, 0)

    # 白名单信息
    whitelisted: bool = False                 # 是否匹配白名单
    match_rule: str = ""                      # 匹配规则描述

    @property
    def has_plate(self) -> bool:
        return len(self.plate_text) > 0

    @property
    def summary(self) -> str:
        """单行摘要"""
        if self.has_plate:
            status = "✓白名单" if self.whitelisted else "✗非白名单"
            return f"{self.plate_text} [{self.vehicle_class_cn}] Y:{self.yolo_confidence:.0%} P:{self.plate_confidence:.0%} {status}"
        else:
            return f"[{self.vehicle_class_cn}] Y:{self.yolo_confidence:.0%} (无车牌)"


class DetectionProcessor:
    """
    检测处理管线

    流程:
    1. YOLOv11m 检测车辆 → 获取车辆边界框
    2. YOLO Pose + CRNN 在整帧中检测并识别车牌
    3. 识别到的车牌与白名单进行匹配
    4. 在帧上绘制标注信息

    使用方式:
        processor = DetectionProcessor(yolo_conf=0.5, lpr_conf=0.7)
        processor.initialize()  # 加载模型
        annotated_frame, results = processor.process(frame)
    """

    def __init__(
        self,
        yolo_conf: float = 0.5,
        lpr_conf: float = 0.7,
        device: str = "cpu",
        vehicle_model_path: str | Path | None = None,
        plate_model_path: str | Path | None = None,
        inference_size: int = 640,
        lpr_mode: str = "pose",
    ):
        normalized_lpr_mode = lpr_mode.lower().strip()
        if normalized_lpr_mode not in {"pose", "box"}:
            raise ValueError("lpr_mode must be 'pose' or 'box'")
        if normalized_lpr_mode == "box" and plate_model_path is None:
            raise ValueError("box lpr_mode requires plate_model_path")

        self._yolo_conf = yolo_conf
        self._lpr_conf = lpr_conf
        self._device = self._detect_device(device)
        self._vehicle_model_path = vehicle_model_path
        self._plate_model_path = plate_model_path
        self._inference_size = inference_size
        self._lpr_mode = normalized_lpr_mode

        self.vehicle_detector: Optional[VehicleDetector] = None
        self.lpr_recognizer: Optional[LPRRecognizer | BoxPlateRecognizer] = None
        self.whitelist_manager = WhitelistManager()

        self._initialized = False
        self._init_error: Optional[str] = None

        # 统计信息
        self.total_frames_processed = 0
        self.total_vehicles_detected = 0
        self.total_plates_recognized = 0

    @staticmethod
    def _detect_device(preferred: str) -> str:
        """检测可用的推理设备

        Args:
            preferred: 偏好设备 — "auto" 自动选择最佳, "cpu"/"cuda"/"cuda:0"/"mps" 等

        Returns:
            实际使用的设备字符串，始终会验证设备是否可用
        """
        preferred_lower = preferred.lower().strip() if preferred else "cpu"

        try:
            import torch

            # "auto" 模式：按优先级自动选择
            if preferred_lower == "auto":
                if torch.cuda.is_available():
                    return "cuda:0"
                if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                    return "mps"
                return "cpu"

            # 显式指定 CUDA 设备
            if preferred_lower.startswith("cuda"):
                if torch.cuda.is_available():
                    # 验证指定的设备索引是否有效
                    device_count = torch.cuda.device_count()
                    if ":" in preferred_lower:
                        idx = int(preferred_lower.split(":")[1])
                        if idx < device_count:
                            return preferred_lower
                        else:
                            print(f"[DetectionProcessor] 警告: {preferred_lower} 不可用, 回退到 cuda:0")
                            return "cuda:0"
                    else:
                        return "cuda:0"
                else:
                    print("[DetectionProcessor] 警告: CUDA 不可用, 回退到 CPU")
                    return "cpu"

            # MPS (Apple Silicon)
            if preferred_lower == "mps":
                if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                    return "mps"
                else:
                    print("[DetectionProcessor] 警告: MPS 不可用, 回退到 CPU")
                    return "cpu"

        except ImportError:
            if preferred_lower.startswith("cuda") or preferred_lower == "mps":
                print("[DetectionProcessor] 警告: PyTorch 未安装, 回退到 CPU")
            pass

        return "cpu"

    @staticmethod
    def get_available_devices() -> list:
        """获取所有可用的推理设备列表

        Returns:
            [(device_id, display_name), ...] 列表，如 [("cpu", "CPU"), ("cuda:0", "GPU (CUDA)")]
        """
        devices = [("cpu", "CPU")]
        try:
            import torch
            if torch.cuda.is_available():
                for i in range(torch.cuda.device_count()):
                    name = torch.cuda.get_device_name(i)
                    devices.append((f"cuda:{i}", f"GPU: {name}" if name else f"GPU {i} (CUDA)"))
            if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                devices.append(("mps", "GPU (Apple MPS)"))
        except ImportError:
            pass
        return devices

    def initialize(self) -> bool:
        """
        初始化检测模型（首次调用时会自动下载模型文件）

        Returns:
            是否初始化成功
        """
        if self._initialized:
            return True

        print(f"[DetectionProcessor] 正在初始化... (device={self._device})")

        # 初始化 YOLO 车辆检测器
        if HAS_YOLO:
            try:
                vehicle_options = {
                    "conf_threshold": self._yolo_conf,
                    "device": self._device,
                    "inference_size": self._inference_size,
                }
                if self._vehicle_model_path is not None:
                    vehicle_options["model_path"] = self._vehicle_model_path
                self.vehicle_detector = VehicleDetector(
                    **vehicle_options,
                )
                print(f"[DetectionProcessor] YOLOv11m 加载成功 (device={self._device})")
            except Exception as e:
                self._init_error = f"YOLO 加载失败: {e}"
                print(f"[DetectionProcessor] {self._init_error}")
                return False
        else:
            self._init_error = "未安装 ultralytics 库"
            print(f"[DetectionProcessor] {self._init_error}")
            return False

        # 初始化 GPU 车牌检测与识别器
        if HAS_PLATE_RECOGNIZER:
            try:
                if self._lpr_mode == "box":
                    if self._plate_model_path is None:
                        raise ValueError("box 车牌识别模式需要 plate_model_path")
                    self.lpr_recognizer = BoxPlateRecognizer(
                        model_path=self._plate_model_path,
                        conf_threshold=self._lpr_conf,
                        device=self._device,
                        inference_size=self._inference_size,
                    )
                elif self._lpr_mode == "pose":
                    lpr_options = {
                        "conf_threshold": self._lpr_conf,
                        "device": self._device,
                        "image_size": self._inference_size,
                    }
                    if self._plate_model_path is not None:
                        lpr_options["detector_model"] = self._plate_model_path
                    self.lpr_recognizer = LPRRecognizer(**lpr_options)
                else:
                    raise ValueError(f"不支持的车牌识别模式: {self._lpr_mode}")
                print(f"[DetectionProcessor] 中文车牌识别模型加载成功 (device={self._device})")
            except Exception as e:
                self._init_error = f"车牌识别模型加载失败: {e}"
                print(f"[DetectionProcessor] {self._init_error}")
                # 不 return False — 没有 LPR 仍可做车辆检测
        else:
            print("[DetectionProcessor] 车牌识别依赖未安装，车牌识别不可用")

        self._initialized = True
        print("[DetectionProcessor] 初始化完成")
        return True

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def init_error(self) -> Optional[str]:
        return self._init_error

    @property
    def has_lpr(self) -> bool:
        return self.lpr_recognizer is not None

    # ---- 阈值属性 ----

    @property
    def yolo_threshold(self) -> float:
        return self._yolo_conf

    @yolo_threshold.setter
    def yolo_threshold(self, value: float):
        self._yolo_conf = max(0.01, min(1.0, value))
        if self.vehicle_detector:
            self.vehicle_detector.threshold = self._yolo_conf

    @property
    def lpr_threshold(self) -> float:
        return self._lpr_conf

    @lpr_threshold.setter
    def lpr_threshold(self, value: float):
        self._lpr_conf = max(0.01, min(1.0, value))
        if self.lpr_recognizer:
            self.lpr_recognizer.threshold = self._lpr_conf

    # ---- 核心处理 ----

    def process(
        self,
        frame: np.ndarray,
        camera_id: str = "",
    ) -> Tuple[np.ndarray, List[DetectionResult]]:
        """
        对一帧图像执行完整的检测管线

        Args:
            frame: BGR 格式的 numpy 图像数组

        Returns:
            (annotated_frame, detection_results) 元组
            - annotated_frame: 绘制了标注的图像
            - detection_results: 检测结果列表
        """
        if not self._initialized:
            return frame, []

        results: List[DetectionResult] = []
        annotated = frame.copy()
        h, w = annotated.shape[:2]

        # ---- Step 1: YOLO 车辆检测 ----
        vehicles = self.vehicle_detector.detect(frame, tracker_key=camera_id or "default")
        self.total_vehicles_detected += len(vehicles)

        # ---- Step 2: 整帧只运行一次车牌模型，再按空间位置关联车辆 ----
        plates = self.lpr_recognizer.recognize(frame) if self.lpr_recognizer else []

        # ---- Step 3: 车辆与车牌关联 + 白名单匹配 ----
        for vehicle in vehicles:
            result = DetectionResult(
                vehicle_bbox=vehicle.bbox,
                vehicle_class=vehicle.class_name,
                vehicle_class_cn=vehicle.class_name_cn,
                yolo_confidence=vehicle.confidence,
                track_id=vehicle.track_id,
                camera_id=camera_id,
                timestamp=time.time(),
            )

            candidates = [
                plate for plate in plates
                if self._plate_belongs_to_vehicle(plate, vehicle.bbox)
            ]
            if candidates:
                best_plate = max(candidates, key=lambda item: item.confidence)
                result.plate_text = best_plate.plate_text
                result.plate_confidence = best_plate.confidence
                result.plate_bbox = best_plate.bbox
                self.total_plates_recognized += 1

                match = self.whitelist_manager.check(best_plate.plate_text)
                result.whitelisted = match.matched
                result.match_rule = match.match_rule

            results.append(result)

            # ---- 绘制标注 ----
            # 确定边框颜色
            if result.whitelisted:
                color = COLOR_GREEN
            elif result.has_plate:
                color = COLOR_ORANGE
            else:
                color = COLOR_BLUE

            # 绘制车辆检测框
            track_label = f"#{result.track_id} " if result.track_id >= 0 else ""
            label = f"{track_label}{result.vehicle_class_cn} {result.yolo_confidence:.0%}"
            draw_vehicle_box(annotated, result.vehicle_bbox, label, color)

            # 绘制车牌信息
            if result.has_plate:
                draw_plate_info(
                    annotated,
                    result.vehicle_bbox,
                    result.plate_text,
                    result.plate_confidence,
                    result.whitelisted,
                )

        # ---- 绘制统计信息面板 ----
        panel_lines = [
            f"车辆检测: {len(vehicles)} 辆  |  车牌识别: {sum(1 for r in results if r.has_plate)} 个",
        ]
        if self.whitelist_manager.enabled and self.whitelist_manager.count > 0:
            whitelist_count = sum(1 for r in results if r.whitelisted)
            panel_lines.append(
                f"白名单匹配: {whitelist_count}/{len(vehicles)}  |  白名单总数: {self.whitelist_manager.count}"
            )
        draw_info_panel(annotated, panel_lines, position=(10, 10), font_size=14)

        self.total_frames_processed += 1
        return annotated, results

    def reset_tracking(self):
        """Reset camera-local tracking without reloading inference weights."""
        if self.vehicle_detector:
            self.vehicle_detector.reset_tracking()

    @staticmethod
    def _plate_belongs_to_vehicle(
        plate: PlateRecognition,
        vehicle_bbox: Tuple[int, int, int, int],
    ) -> bool:
        """Associate a plate when its center lies inside a vehicle box."""
        px1, py1, px2, py2 = plate.bbox
        center_x = (px1 + px2) / 2
        center_y = (py1 + py2) / 2
        vx1, vy1, vx2, vy2 = vehicle_bbox
        return vx1 <= center_x <= vx2 and vy1 <= center_y <= vy2
