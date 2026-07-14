"""
MOG2 异常检测引擎 - 7层过滤管线
==================================
Layer 1: ROI过滤       - 仅保留道路/车道区域
Layer 2: 面积过滤      - 过滤小面积噪声和光斑
Layer 3: 持续时间过滤  - 连续存在2~5秒才候选
Layer 4: 阴影过滤      - MOG2阴影检测 + HSV颜色过滤
Layer 5: 形态学去噪    - 开运算去噪 + 闭运算连接
Layer 6: 类别融合过滤  - YOLO正常车辆->不报警
Layer 7: 背景模型重置  - 视角变化/模式切换后重置并预热

输出: 异常类型 - 异常位置 - 受影响车道 - 告警时间 - 快照 - 置信度
"""
import cv2, numpy as np, time
from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict

# ============================================================
@dataclass
class AnomalyAlert:
    """单条异常告警"""
    anomaly_type: str          # small_debris / medium_object / large_object / elongated_object
    position: Tuple[int,int,int,int]  # (x, y, w, h)
    lane: str                  # left / middle / right
    alert_time: float          # timestamp
    snapshot: Optional[np.ndarray] = None
    confidence: float = 0.5    # 0~1
    frame_id: int = 0

@dataclass
class TrackedObject:
    """跟踪中的候选物体"""
    bbox: Tuple[int,int,int,int] = (0,0,0,0)
    centroid: Tuple[int,int] = (0,0)
    area: int = 0
    first_seen: float = 0.0
    last_seen: float = 0.0
    duration: float = 0.0
    confirmed: bool = False
    snapshot: Optional[np.ndarray] = None
    stability: float = 0.0       # 位置稳定性 (0~1)
    miss_count: int = 0          # 连续丢帧计数（暂留用）
    active_count: int = 0        # 实际被检测到的帧数
    total_count: int = 0         # 从首次出现到现在的总帧数

# ============================================================
class MOGAnomalyEngine:
    """7层过滤管线异常检测引擎"""

    def __init__(self,
                 history: int = 500,
                 var_threshold: int = 25,
                 min_area: int = 150,
                 min_duration: float = 2.0,
                 max_duration: float = 5.0,
                 roi_polygon: Optional[List[Tuple[int,int]]] = None,
                 warmup_frames: int = 50):
        # MOG2 背景建模
        self.mog = cv2.createBackgroundSubtractorMOG2(
            history=history, varThreshold=var_threshold, detectShadows=True)
        self.history = history
        self.var_threshold = var_threshold

        # 参数
        self.min_area = min_area
        self.min_duration = min_duration
        self.max_duration = max_duration

        # ROI
        self.roi_polygon = roi_polygon
        self.roi_mask = None

        # 跟踪
        self.tracked: Dict[tuple, TrackedObject] = {}
        self.alerts: List[AnomalyAlert] = []

        # 形态学核
        self.k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self.k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))

        # 状态
        self.frame_count = 0
        self.warmup_frames = warmup_frames
        self.is_warmed_up = False
        self.fg_debug = None  # 调试用前景掩码
        self.debug = False     # 调试模式：打印每层过滤日志

        # 阴影 HSV 范围 (低饱和度+低亮度 = 阴影/暗区)
        self.shadow_hsv_low = np.array([0, 0, 0])
        self.shadow_hsv_high = np.array([180, 60, 80])

        # 近期车辆掩码：记录YOLO刚检测过的车辆位置，抑制"车走留影"
        self.recent_vehicle_mask = None
        self.vehicle_mask_decay = 0.92  # 每帧衰减系数

    # ====== Layer 7: 背景重置 ======
    def set_roi(self, polygon: List[Tuple[int,int]]):
        """设置道路ROI多边形"""
        self.roi_polygon = polygon
        self.roi_mask = None

    def reset(self):
        """重置背景模型并重新预热"""
        self.mog = cv2.createBackgroundSubtractorMOG2(
            history=self.history, varThreshold=self.var_threshold, detectShadows=True)
        self.tracked.clear()
        self.alerts.clear()
        self.recent_vehicle_mask = None
        self.frame_count = 0
        self.is_warmed_up = False
        self.fg_debug = None
        print(f"[MOG] Layer7: background reset, warming up {self.warmup_frames} frames...")

    # ====== 主处理管线 ======
    def process(self, frame: np.ndarray,
                yolo_boxes: List[Tuple[int,int,int,int,float]],
                frame_id: int = 0,
                timestamp: float = None) -> List[AnomalyAlert]:
        """
        完整 7 层管线处理一帧

        Args:
            frame: BGR 图像
            yolo_boxes: [(x1,y1,x2,y2,cls_id,conf), ...]
            frame_id: 帧序号
            timestamp: 时间戳

        Returns:
            本帧产生的异常告警列表
        """
        if timestamp is None:
            timestamp = time.time()

        self.frame_count += 1
        self.alerts = []

        # Layer 7: 预热检查
        if not self.is_warmed_up:
            self.mog.apply(frame, learningRate=-1)  # 预热阶段正常学习
            if self.frame_count >= self.warmup_frames:
                self.is_warmed_up = True
                print(f"[MOG] Layer7: warmup complete ({self.warmup_frames} frames), detection active")
            return []

        h, w = frame.shape[:2]

        # 更新 ROI mask
        if self.roi_polygon is not None and self.roi_mask is None:
            self.roi_mask = np.zeros((h, w), dtype=np.uint8)
            cv2.fillPoly(self.roi_mask, [np.array(self.roi_polygon, dtype=np.int32)], 255)

        # ====== 核心: MOG2 前景提取 ======
        fg_raw = self.mog.apply(frame, learningRate=0.001)  # 极慢学习

        # ====== Layer 4: 阴影过滤 (阶段1 - MOG2内置阴影标记) ======
        # MOG2 detectShadows=True 时，阴影被标记为灰度值 ~127
        _, fg_mask = cv2.threshold(fg_raw, 200, 255, cv2.THRESH_BINARY)

        # ====== Layer 4: 阴影过滤 (阶段2 - HSV颜色过滤) ======
        fg_mask = self._hsv_shadow_filter(frame, fg_mask)

        # ====== Layer 1: ROI 过滤 ======
        if self.roi_mask is not None:
            fg_mask = cv2.bitwise_and(fg_mask, self.roi_mask)

        # ====== Layer 5: 形态学去噪 ======
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN, self.k_open)   # 去噪点
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, self.k_close) # 连接碎片

        self.fg_debug = fg_mask


        # ====== 近期车辆掩码：记录YOLO车辆位置，抑制"车走留影" ======
        if self.recent_vehicle_mask is None:
            self.recent_vehicle_mask = np.zeros((h, w), dtype=np.float32)
        self.recent_vehicle_mask *= self.vehicle_mask_decay  # 衰减旧标记
        for vbox in yolo_boxes:
            vx1, vy1, vx2, vy2 = int(vbox[0]), int(vbox[1]), int(vbox[2]), int(vbox[3])
            self.recent_vehicle_mask[vy1:vy2, vx1:vx2] = 1.0

        # ====== Layer 2: 面积过滤 + 提取轮廓 ======
        contours, _ = cv2.findContours(fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        active_hashes = set()
        edge_margin = 5  # 像素：轮廓离画面边缘太近的忽略

        if self.debug and self.frame_count % 30 == 0:
            fg_pixels = np.sum(fg_mask > 0)
            print(f"  [MOG-DEBUG] frame={self.frame_count} fg_pixels={fg_pixels} "
                  f"contours_raw={len(contours)} tracked={len(self.tracked)} "
                  f"confirmed={sum(1 for o in self.tracked.values() if o.confirmed)}")

        for cnt in contours:
            area = cv2.contourArea(cnt)
            if area < self.min_area:          # Layer 2: 面积过滤
                continue
            if area > w * h * 0.6:            # 排除巨型误检（整个画面变化）
                continue

            x, y, bw, bh = cv2.boundingRect(cnt)
            # 碰到画面边缘 → 可能是OSD/支架/固定物，忽略
            if (x <= edge_margin or y <= edge_margin or
                x + bw >= w - edge_margin or y + bh >= h - edge_margin):
                continue

            cx, cy = x + bw // 2, y + bh // 2
            aspect = bw / max(bh, 1)

            # 宽高比过滤（合理物体范围）
            if aspect > 8 or aspect < 0.1:
                continue

            obj_hash = (x // 60, y // 60, bw // 60, bh // 60)
            active_hashes.add(obj_hash)

            if obj_hash not in self.tracked:
                self.tracked[obj_hash] = TrackedObject(
                    bbox=(x, y, bw, bh),
                    centroid=(cx, cy),
                    area=area,
                    first_seen=timestamp,
                    last_seen=timestamp,
                    duration=0.0,
                    snapshot=frame[max(0,y):min(int(frame.shape[0]),y+bh),
                                   max(0,x):min(int(frame.shape[1]),x+bw)].copy()
                )
            else:
                obj = self.tracked[obj_hash]
                # 平滑 bbox（指数移动平均，防抖动导致跟踪断裂）
                alpha_smooth = 0.3  # 越低越平滑
                ox, oy, obw, obh = obj.bbox
                obj.bbox = (
                    int(ox * (1-alpha_smooth) + x * alpha_smooth),
                    int(oy * (1-alpha_smooth) + y * alpha_smooth),
                    int(obw * (1-alpha_smooth) + bw * alpha_smooth),
                    int(obh * (1-alpha_smooth) + bh * alpha_smooth)
                )
                x, y, bw, bh = obj.bbox
                # 计算位置稳定性
                old_cx, old_cy = obj.centroid
                obj.centroid = (x + bw // 2, y + bh // 2)
                displacement = np.sqrt((obj.centroid[0] - old_cx)**2 + (obj.centroid[1] - old_cy)**2)
                obj.stability = max(0, 1.0 - displacement / 50.0)
                obj.centroid = (cx, cy)
                obj.area = area
                obj.last_seen = timestamp
                obj.duration = timestamp - obj.first_seen
                obj.miss_count = 0
                obj.active_count += 1  # 本帧被检测到

        # ====== Layer 3: 统一确认检查（含暂留中的对象）======
        for obj_hash, obj in self.tracked.items():
            if obj.confirmed:
                continue
            obj.duration = timestamp - obj.first_seen
            obj.total_count += 1  # 从首次出现以来的总帧数
            x, y, bw, bh = obj.bbox
            in_vehicle_zone = False
            if self.recent_vehicle_mask is not None:
                rv = self.recent_vehicle_mask[y:y+bh, x:x+bw]
                if rv.size > 0 and np.mean(rv) > 0.3:
                    in_vehicle_zone = True
            req_duration = self.max_duration if in_vehicle_zone else self.min_duration
            detect_rate = obj.active_count / max(obj.total_count, 1)
            # 时长达标 且 检测率>80%（排除间歇性噪点）
            if obj.duration >= req_duration and detect_rate >= 0.6:
                obj.confirmed = True

        # 清理消失的跟踪物体（暂留5帧防MOG2间歇性丢帧）
        for h in list(self.tracked.keys()):
            if h not in active_hashes:
                self.tracked[h].miss_count += 1
                if self.tracked[h].miss_count > 5:  # 连续丢5帧(~0.25s)才真删除
                    del self.tracked[h]
            else:
                self.tracked[h].miss_count = 0

        # ====== Layer 6: 类别融合过滤 -> 直接生成告警（纯MOG2，无持久化）======
        for obj_hash, obj in self.tracked.items():
            if not obj.confirmed:
                continue

            x, y, bw, bh = obj.bbox

            # 检查与 YOLO 正常车辆的重叠
            vehicle_overlap = False
            for vbox in yolo_boxes:
                vx1, vy1, vx2, vy2 = int(vbox[0]), int(vbox[1]), int(vbox[2]), int(vbox[3])
                if self._boxes_overlap(
                    (x, y, x+bw, y+bh),
                    (int(vx1), int(vy1), int(vx2), int(vy2))):
                    vehicle_overlap = True
                    break

            if vehicle_overlap:
                continue

            anomaly_type = self._classify(obj)
            confidence = self._confidence(obj)
            lane = self._get_lane(obj.centroid, w)

            alert = AnomalyAlert(
                anomaly_type=anomaly_type,
                position=(x, y, bw, bh),
                lane=lane,
                alert_time=timestamp,
                snapshot=obj.snapshot,
                confidence=confidence,
                frame_id=frame_id
            )
            self.alerts.append(alert)

        return self.alerts

    # ====== 内部方法 ======
    def _hsv_shadow_filter(self, frame, fg_mask):
        """HSV 颜色空间排除路面阴影暗区"""
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        shadow = cv2.inRange(hsv, self.shadow_hsv_low, self.shadow_hsv_high)
        fg_mask[shadow > 0] = 0
        return fg_mask

    def _boxes_overlap(self, a, b):
        """判断 MOG轮廓(a) 是否被 YOLO车辆框(b) 覆盖：交集/MOG面积 > 0.5 或 中心点在车内"""
        ax1, ay1, ax2, ay2 = a  # MOG contour bbox
        bx1, by1, bx2, by2 = b  # YOLO vehicle bbox
        ix1, iy1 = max(ax1, bx1), max(ay1, by1)
        ix2, iy2 = min(ax2, bx2), min(ay2, by2)
        if ix1 >= ix2 or iy1 >= iy2:
            return False
        inter = (ix2-ix1) * (iy2-iy1)
        area_a = (ax2-ax1) * (ay2-ay1)  # MOG面积
        # MOG轮廓50%以上被车辆框覆盖 → 属于车辆部件
        if inter / (area_a + 1e-6) > 0.5:
            return True
        # 或 MOG中心点落在车辆框内
        cx_a = (ax1 + ax2) // 2
        cy_a = (ay1 + ay2) // 2
        if bx1 <= cx_a <= bx2 and by1 <= cy_a <= by2:
            return True
        return False

    def _classify(self, obj: TrackedObject) -> str:
        """根据物体特征分类异常类型"""
        area, bw, bh = obj.area, obj.bbox[2], obj.bbox[3]
        aspect = bw / max(bh, 1)
        if area < 500:
            return "small_debris"
        elif area < 2000:
            return "medium_object"
        elif aspect > 3 or aspect < 0.33:
            return "elongated_object"
        else:
            return "large_object"

    def _confidence(self, obj: TrackedObject) -> float:
        """计算异常置信度 0~1，基于持续时间和稳定性"""
        dur_score = min(obj.duration / self.max_duration, 1.0)
        return round(0.4 + 0.4 * dur_score + 0.2 * obj.stability, 2)

    def _get_lane(self, centroid: Tuple[int,int], frame_width: int) -> str:
        """根据物体X坐标判断车道"""
        x = centroid[0]
        if x < frame_width * 0.33:
            return "left"
        elif x < frame_width * 0.66:
            return "middle"
        else:
            return "right"

    # ====== 可视化 ======
    def draw_alerts(self, frame: np.ndarray) -> np.ndarray:
        """在帧上绘制异常告警框"""
        COLORS = {
            "small_debris": (0, 200, 255),     # 金黄
            "medium_object": (0, 0, 255),       # 红
            "elongated_object": (255, 0, 255),  # 紫
            "large_object": (0, 0, 200),        # 深红
        }
        for alert in self.alerts:
            x, y, bw, bh = alert.position
            c = COLORS.get(alert.anomaly_type, (0, 0, 255))
            cv2.rectangle(frame, (x, y), (x+bw, y+bh), c, 2)
            label = f"MOG:{alert.anomaly_type} {alert.confidence:.1f}"
            cv2.putText(frame, label, (x, y-8),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, c, 2)
            cv2.putText(frame, f"lane:{alert.lane}", (x, y+bh+15),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.4, c, 1)
        return frame

    def get_stats(self) -> dict:
        """获取当前统计信息"""
        return {
            "tracked_objects": len(self.tracked),
            "confirmed_candidates": sum(1 for o in self.tracked.values() if o.confirmed),
            "alerts_this_frame": len(self.alerts),
            "warmed_up": self.is_warmed_up,
            "frame_count": self.frame_count,
        }

    def get_foreground_mask(self) -> Optional[np.ndarray]:
        """获取当前帧处理后的前景掩码（调试用）"""
        return self.fg_debug
