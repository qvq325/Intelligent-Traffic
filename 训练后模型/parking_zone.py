"""禁停区域违章停车检测模块"""
import cv2, numpy as np, time
from collections import deque
from typing import List, Tuple, Optional

class ParkingZoneDetector:
    """禁停区域检测器：跟踪车辆，判断在禁停区内静止时长"""

    def __init__(self, parking_time: float = 3.0, move_threshold: int = 30):
        self.zones: List[np.ndarray] = []     # 多个禁停区多边形
        self.parking_time = parking_time
        self.move_threshold = move_threshold
        self.tracker: dict = {}
        self.violations: list = []
        self.zone_set = False

    def add_zone(self, points: List[Tuple[int,int]]):
        """添加一个禁停区多边形"""
        if len(points) >= 3:
            self.zones.append(np.array(points, dtype=np.int32))
            self.zone_set = True
            print(f"[PARKING] Zone #{len(self.zones)} added: {len(points)} points")

    def clear_zones(self):
        """清除所有禁停区"""
        self.zones.clear()
        self.zone_set = False
        print("[PARKING] All zones cleared")

    def in_zone(self, cx: int, cy: int) -> bool:
        """判断点是否在任一禁停区内"""
        for poly in self.zones:
            if cv2.pointPolygonTest(poly, (cx, cy), False) >= 0:
                return True
        return False

    def check(self, vehicles: List[Tuple[int,int,int,int,int]],
              fps: float = 20.0) -> List[dict]:
        """
        检测违章停车（位置继承防 ByteTrack ID 跳变）
        """
        self.violations = []
        active_ids = set()
        now = time.time()
        maxlen = max(1, int(fps * self.parking_time))

        for (x1, y1, x2, y2, tid) in vehicles:
            if tid is None:
                continue
            active_ids.add(tid)
            cx, cy = (x1 + x2) // 2, (y1 + y2) // 2

            if not self.in_zone(cx, cy):
                continue

            # 位置继承：新 ID 在旧 ID 50px 内 → 继承历史
            if tid not in self.tracker:
                inherited = False
                for old_tid, old_dq in list(self.tracker.items()):
                    if old_tid not in active_ids and len(old_dq) > 0:
                        ox, oy, _ = old_dq[-1]
                        if np.sqrt((cx-ox)**2 + (cy-oy)**2) < 50:
                            self.tracker[tid] = old_dq
                            del self.tracker[old_tid]
                            inherited = True
                            break
                if not inherited:
                    self.tracker[tid] = deque(maxlen=maxlen)

            self.tracker[tid].append((cx, cy, now))

            pt = self.tracker[tid]
            if len(pt) == pt.maxlen:
                oldest = pt[0]
                dist = np.sqrt((cx - oldest[0])**2 + (cy - oldest[1])**2)
                if dist < self.move_threshold:
                    self.violations.append({
                        'track_id': tid,
                        'bbox': (x1, y1, x2, y2),
                        'center': (cx, cy),
                        'duration': now - oldest[2]
                    })

        # 清理失联车辆（保留2秒防ID跳变）
        for tid in list(self.tracker.keys()):
            if tid not in active_ids:
                if self.tracker[tid]:
                    last_time = self.tracker[tid][-1][2]
                    if now - last_time > 2.0:
                        del self.tracker[tid]
                else:
                    del self.tracker[tid]

        return self.violations

    def draw(self, frame: np.ndarray) -> np.ndarray:
        """绘制所有禁停区和违章车辆"""
        for i, poly in enumerate(self.zones):
            cv2.polylines(frame, [poly], True, (0, 0, 255), 2)
            if len(poly) > 0:
                cv2.putText(frame, f"NO PARKING #{i+1}", tuple(poly[0]),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)

        for v in self.violations:
            x1, y1, x2, y2 = v['bbox']
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 215, 255), 2)
            label = f"PARKING #{v['track_id']} {v['duration']:.1f}s"
            cv2.putText(frame, label, (x1, y1-8),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 215, 255), 2)

        return frame

    def clear(self):
        """重置跟踪数据"""
        self.tracker.clear()
        self.violations.clear()
        self.zones.clear()
        self.zone_set = False


def draw_zone_interactive(frame: np.ndarray,
                          window_name: str = "Set No-Parking Zone"
                          ) -> Optional[List[Tuple[int,int]]]:
    """交互式绘制禁停区多边形，返回顶点列表或 None"""
    points = []
    confirmed = False
    h, w = frame.shape[:2]

    # 显示缩放比例
    disp_w = min(w, 1280)
    disp_h = int(disp_w / w * h)
    scale_x = w / disp_w
    scale_y = h / disp_h

    def mouse_cb(event, x, y, flags, param):
        nonlocal points, confirmed
        if confirmed:
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((int(x * scale_x), int(y * scale_y)))
        elif event == cv2.EVENT_RBUTTONDOWN:
            if points:
                points.pop()  # 右键撤销上一个点

    cv2.namedWindow(window_name)
    cv2.setMouseCallback(window_name, mouse_cb)

    print("\n=== Draw No-Parking Zone ===")
    print("LEFT CLICK: add vertex | ENTER: confirm | R: reset | ESC: cancel")

    while True:
        disp = frame.copy()
        for i, pt in enumerate(points):
            cv2.circle(disp, pt, 5, (0, 255, 0), -1)
            if i > 0:
                cv2.line(disp, points[i-1], pt, (0, 255, 0), 2)
        if len(points) >= 3:
            cv2.polylines(disp, [np.array(points)], True, (0, 255, 255), 2)
            cv2.putText(disp, f"{len(points)} pts - ENTER to confirm",
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
        else:
            cv2.putText(disp, f"Click to add ({len(points)}/3+)",
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        cv2.imshow(window_name, cv2.resize(disp, (disp_w, disp_h)))
        key = cv2.waitKey(1) & 0xFF
        if key == 13 and len(points) >= 3:  # Enter
            confirmed = True
            break
        elif key == ord('r'):
            points = []
        elif key == 27:  # ESC
            cv2.destroyWindow(window_name)
            return None

    cv2.destroyWindow(window_name)
    return points
