# Stream Detection Script
# Supports: RTSP, RTMP, HTTP, local video, camera
import cv2, sys, time, argparse, numpy as np
from pathlib import Path
from collections import Counter, deque
from ultralytics import YOLO
from mog_anomaly_engine import MOGAnomalyEngine
from parking_zone import ParkingZoneDetector, draw_zone_interactive

class Config:
    SOURCE = 0
    VEHICLE_MODEL = "yolo26x.pt"
    PLATE_MODEL   = "C:/Users/21495/runs/detect/license_plate_26m/weights/best.pt"
    VEHICLE_CONF  = 0.4        # 车辆检测阈值（用户确认 C 车能检出）
    PLATE_CONF    = 0.20       # 全帧车牌阈值
    PLATE_CONF_CROP = 0.12     # 裁剪补检阈值，平衡误检和漏检
    ANOMALY_CONF  = 0.5        # 异常检测阈值（高于车辆阈值，减少误报）
    FRAME_SKIP    = 1          # 逐帧检测
    DEVICE        = "cuda:0"   # 推理设备: cuda:0=GPU, cpu=CPU
    # 道路正常物体白名单（COCO 类别 ID），白名单之外 = 异常
    NORMAL_CLASSES = {0, 1, 2, 3, 5, 6, 7, 9, 10, 11}  # +train(6): yolo26s 可能把车误判为 train
    ANOMALY_MIN_FRAMES = 5   # 异常判定需要的最少跟踪帧数
    ANOMALY_VOTE_RATIO = 0.6 # 异常票数占比超过此值才报警
    PLATE_HOLD_FRAMES = 5    # 车牌消失后保留 N 帧，减少闪烁漏检
    MOG_ANOMALY      = False # MOG2 7层过滤异常检测引擎
    MOG_MIN_AREA     = 150   # 最小异常区域面积(px²)
    MOG_MIN_DURATION = 2.0   # 持续存在秒数下限
    MOG_MAX_DURATION = 5.0   # 持续存在秒数上限
    MOG_WARMUP       = 100   # 预热帧数（5秒）
    MOG_DEBUG        = False # 调试模式：打印过滤日志
    PARKING_ZONE     = False # 禁停区域违章停车检测
    PARKING_TIME     = 3.0   # 静止判定秒数
    PARKING_DIST     = 30    # 移动距离阈值(px)
    DEBUG_PLATE   = False
    WINDOW_NAME   = "Stream Detection - Vehicle and Plate"
    RESIZE_WIDTH  = 1280
    SCREENSHOT_DIR = "screenshots"
    RTSP_REOPEN_TIMEOUT = 5
    RTSP_BUFFER_SIZE    = 2        # 最低缓冲，降低延迟（原30帧=1.2s延迟）
    COLORS = {
        "car": (0,255,0),
        "truck": (255,165,0),
        "bus": (0,255,255),
        "motorcycle": (255,100,100),
        "license_plate": (255,0,255),
        "default": (0,255,0),
    }
try:
    import hyperlpr3 as lpr3
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

def draw_box(frame, x1, y1, x2, y2, label, conf, color):
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    text = f"{label} {conf:.2f}"
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 2)
    cv2.rectangle(frame, (x1, y1 - th - 8), (x1 + tw + 6, y1), color, -1)
    cv2.putText(frame, text, (x1 + 3, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 2)

def is_stream(source):
    s = str(source).lower()
    return any(s.startswith(p) for p in ["rtsp://", "rtmp://", "http://", "https://"])

def build_capture(source):
    cap = cv2.VideoCapture(str(source))
    if is_stream(source):
        cap.set(cv2.CAP_PROP_BUFFERSIZE, Config.RTSP_BUFFER_SIZE)
        print(f"  [STREAM] mode | buffer={Config.RTSP_BUFFER_SIZE} | reconnect={Config.RTSP_REOPEN_TIMEOUT}s")
    return cap

# ====== 多线程抓帧：后台疯狂读帧只留最新，消除 RTSP 缓冲延迟 ======
import threading
class ThreadedCapture:
    def __init__(self, source):
        self.source = source
        self.cap = build_capture(source)
        self.lock = threading.Lock()
        self.latest = None
        self.alive = True
        self.connected = True
        self.thread = threading.Thread(target=self._grab, daemon=True)
        self.thread.start()

    def _grab(self):
        while self.alive:
            ret, frame = self.cap.read()
            if not ret:
                self.connected = False
                time.sleep(0.1)
                continue
            with self.lock:
                self.latest = frame
            self.connected = True

    def read(self):
        """返回最新帧，线程安全"""
        with self.lock:
            if self.latest is not None:
                return True, self.latest.copy()
        return False, None

    def is_connected(self):
        return self.connected

    def release(self):
        self.alive = False
        self.thread.join(timeout=2)
        self.cap.release()

    def get_props(self):
        w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fps = self.cap.get(cv2.CAP_PROP_FPS) or 30
        return w, h, fps

# Check if OpenCV GUI is available
try:
    cv2.namedWindow("_gui_test_", cv2.WINDOW_NORMAL)
    cv2.destroyWindow("_gui_test_")
    HAS_GUI = True
except Exception:
    HAS_GUI = False

def run(source, mode="both", use_track=False, use_ocr=False, out_path=None):
    print("=" * 55)
    print("  Stream Detection - Vehicle & License Plate")
    print("=" * 55)

    print()
    print("[LOAD] Loading models...")
    vehicle_model = None
    plate_model = None
    ocr = None

    if mode in ("vehicle", "both"):
        vehicle_model = YOLO(Config.VEHICLE_MODEL)
        print(f"  Vehicle: {Config.VEHICLE_MODEL}  |  device: {Config.DEVICE}")
    if mode in ("plate", "both"):
        plate_model = YOLO(Config.PLATE_MODEL)
        print(f"  Plate: {Config.PLATE_MODEL}  |  device: {Config.DEVICE}")

    if use_ocr and mode in ("plate", "both"):
        if not HAS_OCR:
            print("  [WARN] HyperLPR3 not installed, skipping OCR. pip install hyperlpr3")
            use_ocr = False
        else:
            ocr = lpr3.LicensePlateCatcher()

    # LPR 多帧投票缓存
    lpr_cache = {}  # pid -> {"votes":[], "best":"", "last":int}
    LPR_CONF = 0.5   # HyperLPR3 置信度过滤

    def lpr_recognize(crop_img, pid):
        """HyperLPR3 识别 + 多帧投票，返回最优车牌号"""
        if not use_ocr or ocr is None or crop_img.size == 0:
            return None
        if pid in lpr_cache and frame_count - lpr_cache[pid]["last"] <= 30:
            return lpr_cache[pid]["best"]
        try:
            h = crop_img.shape[0]
            if h < 100:
                s = 100.0 / h
                crop_img = cv2.resize(crop_img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)
            result = ocr(crop_img)
            if result and len(result) > 0:
                text, conf = result[0][0], result[0][1]
                if conf >= LPR_CONF and text and len(text) >= 5:
                    if pid not in lpr_cache:
                        lpr_cache[pid] = {"votes": [], "best": "", "last": 0}
                    lpr_cache[pid]["votes"].append(text)
                    lpr_cache[pid]["last"] = frame_count
                    lpr_cache[pid]["best"] = Counter(lpr_cache[pid]["votes"]).most_common(1)[0][0]
                    return lpr_cache[pid]["best"]
        except Exception:
            pass
        return None

    tcap = ThreadedCapture(source) if is_stream(source) else None
    cap = build_capture(source) if not is_stream(source) else None

    if is_stream(source):
        if tcap.latest is None:
            print("[WAIT] 等待流首帧...", end="", flush=True)
            for _ in range(50):  # 最多等5秒
                time.sleep(0.1)
                if tcap.latest is not None:
                    break
            if tcap.latest is None:
                print(" 失败")
                print(f"[ERROR] Cannot open stream: {source}")
                return
            print(" OK")
        width, height, fps = tcap.get_props()
    else:
        if not cap.isOpened():
            print(f"[ERROR] Cannot open source: {source}")
            return
        width, height = int(cap.get(3)), int(cap.get(4))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30

    stype = "STREAM" if is_stream(source) else ("CAMERA" if isinstance(source, int) else "FILE")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) if cap else 0

    print(f"[INFO] Type: {stype}")
    print(f"       Source: {source}")
    print(f"       Resolution: {width}x{height} | FPS: {fps:.1f}")
    print(f"       Mode: {mode} | Track: {use_track} | OCR: {use_ocr} | MOG: {Config.MOG_ANOMALY} | Parking: {Config.PARKING_ZONE}")
    print(f"       Frame skip: {Config.FRAME_SKIP}")
    print(f"       Keys: Q/ESC=Quit S=Shot P=Pause T=Track O=OCR Z=AddZone C=ClearZones")
    if is_stream(source):
        print(f"  [LATENCY] Multi-threaded capture: only latest frame used")
    print("-" * 55)
    anomaly_tracker = {}  # tid -> {"votes": Counter(), "frames": int}
    plate_smoothing = {}  # pid -> {"x1","y1","x2","y2","conf","remaining":int}
    mog_engine = MOGAnomalyEngine(
        min_area=Config.MOG_MIN_AREA,
        min_duration=Config.MOG_MIN_DURATION,
        max_duration=Config.MOG_MAX_DURATION,
        warmup_frames=Config.MOG_WARMUP
    ) if Config.MOG_ANOMALY else None
    if mog_engine and Config.MOG_DEBUG:
        mog_engine.debug = True
    parking_detector = ParkingZoneDetector(
        parking_time=Config.PARKING_TIME,
        move_threshold=Config.PARKING_DIST
    ) if Config.PARKING_ZONE else None

    frame_count = 0
    fps_start = time.time()
    fps_counter = 0
    current_fps = 0.0
    paused = False
    vehicle_results = None
    last_reconnect = time.time()

    # === GUI or headless mode ===
    if HAS_GUI:
        cv2.namedWindow(Config.WINDOW_NAME, cv2.WINDOW_NORMAL)
        if Config.RESIZE_WIDTH:
            ratio = Config.RESIZE_WIDTH / width if width > 0 else 1.0
            cv2.resizeWindow(Config.WINDOW_NAME, Config.RESIZE_WIDTH, int(height * ratio))
        Path(Config.SCREENSHOT_DIR).mkdir(parents=True, exist_ok=True)
    else:
        # Headless mode: save to video file
        out_dir = Path("stream_output")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_path or str(out_dir / f"stream_{time.strftime('%Y%m%d_%H%M%S')}.avi")
        fourcc = cv2.VideoWriter_fourcc(*"XVID")
        writer = cv2.VideoWriter(out_path, fourcc, fps, (width, height))
        print(f"\n  [HEADLESS] GUI not available, saving to: {out_path}")
        print(f"  Run: pip install opencv-python  (to enable preview window)")
        print(f"  Ctrl+C to stop\n")

    while True:
        if not paused:
            if is_stream(source):
                ret, frame = tcap.read()
                if not ret:
                    frame = np.ones((max(height, 480), max(width, 640), 3), dtype=np.uint8) * 50
                    cv2.putText(frame, "STREAM DISCONNECTED - RECONNECTING...",
                                (50, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
                else:
                    frame_count += 1
            else:
                ret, frame = cap.read()
                if not ret:
                    print("[INFO] Video ended.")
                    break
                frame_count += 1

        do_detect = (frame_count % Config.FRAME_SKIP == 0) or paused

        n_vehicles = 0
        n_anomalies = 0
        n_mog = 0
        active_ids = set()
        vehicle_boxes = []  # 用于过滤全帧车身误判
        crop_plate_pids = []  # 裁剪补检车牌 PID，用于时序平滑
        detected_plates = set()  # 去重用

        # ====== 第一轮：全帧扫车牌（先存结果不画，避免污染后续裁剪）======
        full_plates = []
        if plate_model and mode == "both":
            try:
                fp = plate_model(frame, conf=Config.PLATE_CONF, device=Config.DEVICE, verbose=False)
                if fp[0].boxes is not None:
                    for pb in fp[0].boxes:
                        x1, y1, x2, y2 = map(int, pb.xyxy[0])
                        pc = float(pb.conf[0])
                        key = (x1//20, y1//20, x2//20, y2//20)
                        if key in detected_plates:
                            continue
                        detected_plates.add(key)
                        pid = hash((x1//30, y1//30, x2//30, y2//30)) % 100000
                        lpr_text = lpr_recognize(frame[y1:y2, x1:x2], pid) if use_ocr else None
                        full_plates.append((x1, y1, x2, y2, pc, lpr_text))
                        plate_smoothing[pid] = {"x1":x1,"y1":y1,"x2":x2,"y2":y2,"conf":pc,"remaining":Config.PLATE_HOLD_FRAMES}
            except Exception as e:
                if frame_count <= 3:
                    print(f"  [ERR] Full-frame plate failed: {e}")

        # ====== 第二轮：车辆检测 + 裁剪补检车牌 ======
        if vehicle_model and do_detect:
            try:
                if use_track:
                    results = vehicle_model.track(frame, conf=Config.VEHICLE_CONF,
                        persist=True, tracker="bytetrack.yaml", device=Config.DEVICE, verbose=False)
                else:
                    results = vehicle_model(frame, conf=Config.VEHICLE_CONF, device=Config.DEVICE, verbose=False)
                vehicle_results = results[0]
            except Exception as e:
                vehicle_results = None

        if vehicle_results is not None and vehicle_results.boxes is not None:
            for box in vehicle_results.boxes:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                label = vehicle_model.names.get(cls_id, "vehicle")
                is_normal = cls_id in Config.NORMAL_CLASSES

                if is_normal:
                    if use_track and box.id is not None:
                        active_ids.add(int(box.id[0]))
                    vehicle_boxes.append((x1, y1, x2, y2))
                    color = Config.COLORS.get(label, Config.COLORS["default"])
                    tid = int(box.id[0]) if (use_track and box.id is not None) else None
                    disp = f"{label}#{tid}" if tid is not None else label
                    n_vehicles += 1

                    # ====== 裁剪补检车牌（仅对车辆）======
                    if plate_model and mode == "both":
                        h, w = frame.shape[:2]
                        bw, bh = x2 - x1, y2 - y1
                        margin_x = int(bw * 0.2)
                        margin_y = int(bh * 0.2)
                        x1c, y1c = max(0, x1 - margin_x), max(0, y1 - margin_y)
                        x2c, y2c = min(w, x2 + margin_x), min(h, y2 + margin_y)
                        if x2c > x1c and y2c > y1c:
                            crop = frame[y1c:y2c, x1c:x2c]
                            ch, cw = crop.shape[:2]
                            pr = plate_model(crop, conf=Config.PLATE_CONF_CROP, device=Config.DEVICE, verbose=False)
                            if pr[0].boxes is not None:
                                for pb in pr[0].boxes:
                                    px1, py1, px2, py2 = map(int, pb.xyxy[0])
                                    pw, ph = px2 - px1, py2 - py1
                                    if pw > cw * 0.6 or ph > ch * 0.6:
                                        continue  # 车身误判：尺寸过大
                                    if ph > 0 and pw / ph < 2.0:
                                        continue  # 车身误判：宽高比太方（真车牌 ≥2.5:1）
                                    pc = float(pb.conf[0])
                                    gx1 = x1c + px1
                                    gy1 = y1c + py1
                                    gx2 = x1c + px2
                                    gy2 = y1c + py2
                                    key = (gx1//20, gy1//20, gx2//20, gy2//20)
                                    if key in detected_plates:
                                        continue
                                    detected_plates.add(key)
                                    draw_box(frame, gx1, gy1, gx2, gy2, "plate", pc,
                                             Config.COLORS["license_plate"])
                                    pid = hash((gx1//30, gy1//30, gx2//30, gy2//30)) % 100000
                                    plate_smoothing[pid] = {"x1":gx1,"y1":gy1,"x2":gx2,"y2":gy2,"conf":pc,"remaining":Config.PLATE_HOLD_FRAMES}
                                    crop_plate_pids.append(pid)
                                    if use_ocr and (gy2 - gy1) >= 15:
                                        lpr_text = lpr_recognize(frame[gy1:gy2, gx1:gx2], pid)
                                        if lpr_text:
                                            cv2.putText(frame, lpr_text, (gx1, gy2 + 22),
                                                        cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)
                else:
                    # 异常物体：多帧投票判定
                    tid = int(box.id[0]) if (use_track and box.id is not None) else None
                    if tid is not None:
                        active_ids.add(tid)
                        if tid not in anomaly_tracker:
                            anomaly_tracker[tid] = {"votes": Counter(), "frames": 0, "confirmed": False}
                        anomaly_tracker[tid]["votes"][label] += 1
                        anomaly_tracker[tid]["frames"] += 1
                        t = anomaly_tracker[tid]
                        if not t["confirmed"]:
                            total_v = sum(t["votes"].values())
                            anomaly_v = sum(c for n, c in t["votes"].items()
                                          if next((k for k, v in vehicle_model.names.items() if v == n), -1) not in Config.NORMAL_CLASSES)
                            ratio = anomaly_v / total_v if total_v > 0 else 0
                            if (t["frames"] >= Config.ANOMALY_MIN_FRAMES and
                                ratio >= Config.ANOMALY_VOTE_RATIO):
                                t["confirmed"] = True  # 一旦确认就锁死
                        is_anomaly = t["confirmed"] and conf >= Config.ANOMALY_CONF
                    else:
                        # 无跟踪：单帧判定
                        is_anomaly = conf >= Config.ANOMALY_CONF

                    if not is_anomaly:
                        continue
                    color = (0, 0, 255)
                    best = anomaly_tracker[tid]["votes"].most_common(1)[0][0] if tid else label
                    disp = f"ANOMALY:{best}"
                    n_anomalies += 1

                # 画框
                draw_box(frame, x1, y1, x2, y2, disp, conf, color)

        # ====== 禁停区域违章停车检测（仅计算，不画框）======
        n_parking = 0
        if parking_detector is not None and parking_detector.zone_set:
            p_vehicles = []
            if vehicle_results is not None and vehicle_results.boxes is not None:
                for box in vehicle_results.boxes:
                    if box.id is None:
                        continue
                    cls_id = int(box.cls[0])
                    if cls_id in Config.NORMAL_CLASSES:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        p_vehicles.append((x1, y1, x2, y2, int(box.id[0])))
            parking_detector.check(p_vehicles, fps)
            n_parking = len(parking_detector.violations)
            if n_parking > 0 and frame_count % 30 == 0:
                for v in parking_detector.violations:
                    print(f"  [PARKING] id={v['track_id']} dur={v['duration']:.1f}s pos={v['center']}")

        # ====== MOG2 7层过滤异常检测引擎 ======
        if mog_engine is not None:
            alerts = mog_engine.process(frame, vehicle_boxes, frame_count)
            mog_engine.draw_alerts(frame)
            n_mog = len(alerts)
            # 输出告警信息到控制台
            for alert in alerts:
                print(f"  [MOG-ALERT] #{alert.frame_id} type={alert.anomaly_type} "
                      f"lane={alert.lane} conf={alert.confidence} pos={alert.position}")
            if Config.MOG_DEBUG:
                fg = mog_engine.get_foreground_mask()
                if fg is not None:
                    cv2.imshow("MOG Foreground Mask", fg)

        # 清理失联跟踪
        for tid in list(anomaly_tracker.keys()):
            if tid not in active_ids:
                del anomaly_tracker[tid]

        # ====== 时序平滑：保留消失中的车牌 ======
        active_pids = set()
        for x1, y1, x2, y2, pc, _ in full_plates:
            active_pids.add(hash((x1//30, y1//30, x2//30, y2//30)) % 100000)
        active_pids.update(crop_plate_pids)
        for pid, s in list(plate_smoothing.items()):
            if s["remaining"] > 0 and pid not in active_pids:
                alpha = s["remaining"] / Config.PLATE_HOLD_FRAMES
                cv2.rectangle(frame, (s["x1"], s["y1"]), (s["x2"], s["y2"]),
                              (255, int(255*alpha), int(255*alpha)), 1)
            s["remaining"] -= 1
            if s["remaining"] <= 0:
                del plate_smoothing[pid]

        # ====== 画全帧检测的车牌框（过滤车身误判）======
        for x1, y1, x2, y2, pc, lpr_text in full_plates:
            # 真车牌面积远小于车身，误判的车身"车牌"面积 > 车身30%
            pa = (x2 - x1) * (y2 - y1)
            skip = False
            for vx1, vy1, vx2, vy2 in vehicle_boxes:
                va = (vx2 - vx1) * (vy2 - vy1)
                if va > 0 and pa / va > 0.25:  # 车牌面积 > 车身25% = 车身误判
                    skip = True
                    break
            if skip:
                continue
            draw_box(frame, x1, y1, x2, y2, "plate", pc,
                     Config.COLORS["license_plate"])
            if lpr_text:
                cv2.putText(frame, lpr_text, (x1, y2 + 22),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2)

        # Plate-only mode (full frame scan, lower conf)
        if plate_model and mode == "plate" and do_detect:
            try:
                results = plate_model(frame, conf=Config.PLATE_CONF, device=Config.DEVICE, verbose=False)
                if results[0].boxes is not None:
                    for box in results[0].boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        pc = float(box.conf[0])
                        draw_box(frame, x1, y1, x2, y2, "plate", pc,
                                 Config.COLORS["license_plate"])
            except Exception as e:
                print(f"  [WARN] Plate detect error: {e}")

        # ====== 禁停区绘制（最后画，不影响MOG2）======
        if parking_detector is not None and parking_detector.zone_set:
            parking_detector.draw(frame)

        # FPS calculation
        fps_counter += 1
        if fps_counter >= 30:
            elapsed = time.time() - fps_start
            current_fps = fps_counter / elapsed if elapsed > 0 else 0
            fps_start = time.time()
            fps_counter = 0

        # HUD overlay
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (380, 200), (0, 0, 0), -1)
        frame = cv2.addWeighted(overlay, 0.55, frame, 0.45, 0)
        cv2.putText(frame, f"FPS: {current_fps:.1f}", (10, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
        cv2.putText(frame, f"Frame: {frame_count}", (10, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1)
        track_str = "ON" if use_track else "OFF"
        ocr_str = "ON" if use_ocr else "OFF"
        cv2.putText(frame, f"Track: {track_str}  OCR: {ocr_str}",
                    (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 180), 1)
        if mode == "both":
            cv2.putText(frame, f"Vehicles: {n_vehicles}", (10, 105),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 1)
        if n_anomalies > 0:
            cv2.putText(frame, f"Anomalies: {n_anomalies}", (10, 128),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 255), 1)
        if n_mog > 0:
            cv2.putText(frame, f"MOG Alerts: {n_mog}", (10, 150),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 0, 0), 1)
        if n_parking > 0:
            cv2.putText(frame, f"Parking: {n_parking}", (10, 170),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 215, 255), 1)
        if mog_engine is not None:
            st = mog_engine.get_stats()
            if st["warmed_up"]:
                cv2.putText(frame, f"MOG tracking: {st['tracked_objects']} ({st['confirmed_candidates']} confirmed)",
                            (10, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)
            else:
                cv2.putText(frame, f"MOG warming: {st['frame_count']}/{Config.MOG_WARMUP}",
                            (10, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)

        # Source tag (bottom-right)
        src_tag = "RTSP/RTMP" if is_stream(source) else ("CAM" if isinstance(source, int) else "FILE")
        cv2.putText(frame, src_tag, (width - 130, height - 15),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)

        # Status (top-right)
        status = "PAUSED" if paused else ("LIVE" if is_stream(source) else "PLAYING")
        sc = (0, 165, 255) if paused else (0, 255, 0)
        cv2.putText(frame, status, (width - 110, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, sc, 2)
        if HAS_GUI:
            cv2.imshow(Config.WINDOW_NAME, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:
                break
            elif key == ord("s"):
                fn = Config.SCREENSHOT_DIR + "/stream_" + time.strftime("%Y%m%d_%H%M%S") + ".jpg"
                cv2.imwrite(fn, frame)
                print("  [SCREENSHOT] " + fn)
            elif key == ord("p"):
                paused = not paused
                print("  [PAUSED]" if paused else "  [RESUMED]")
            elif key == ord("t"):
                use_track = not use_track
                vehicle_results = None
                print("  [TRACK] ON" if use_track else "  [TRACK] OFF")
            elif key == ord("o"):
                use_ocr = not use_ocr
                if use_ocr and not HAS_OCR:
                    print("  [WARN] HyperLPR3 not installed")
                    use_ocr = False
                elif use_ocr and ocr is None:
                    ocr = lpr3.LicensePlateCatcher()
                print("  [OCR] ON" if use_ocr else "  [OCR] OFF")
            elif key == ord("z") and parking_detector is not None:
                print("  [ZONE] Drawing mode - LEFT=add RIGHT=undo ENTER=confirm ESC=cancel")
                zone_pts = draw_zone_interactive(frame)
                if zone_pts is not None:
                    parking_detector.add_zone(zone_pts)
                else:
                    print("  [ZONE] Cancelled")
            elif key == ord("c") and parking_detector is not None:
                parking_detector.clear_zones()
                print("  [ZONE] All cleared")
        else:
            # Headless mode: write frame to video
            writer.write(frame)
            if frame_count % 100 == 0:
                print(f"  [{time.strftime('%H:%M:%S')}] Frame {frame_count} | Vehicles: {n_vehicles}")

    if is_stream(source):
        tcap.release()
    else:
        cap.release()
    if HAS_GUI:
        cv2.destroyAllWindows()
        print(f"\n[DONE] {frame_count} frames | screenshots: {Config.SCREENSHOT_DIR}/")
    else:
        writer.release()
        print(f"\n[DONE] {frame_count} frames | saved: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Stream Detection - RTSP/RTMP/Local/Camera")
    parser.add_argument("--source", "-s", default=Config.SOURCE,
                        help=f"Video source (default: {Config.SOURCE})")
    parser.add_argument("--model", "-m", choices=["vehicle", "plate", "both"],
                        default="both", help="Detection mode")
    parser.add_argument("--conf", type=float, default=Config.VEHICLE_CONF,
                        help=f"Vehicle confidence (default: {Config.VEHICLE_CONF})")
    parser.add_argument("--plate-conf", type=float, default=Config.PLATE_CONF,
                        help=f"Plate confidence (default: {Config.PLATE_CONF})")
    parser.add_argument("--skip", type=int, default=Config.FRAME_SKIP,
                        help=f"Frame skip interval (default: {Config.FRAME_SKIP})")
    parser.add_argument("--track", action="store_true",
                        help="Enable ByteTrack object tracking")
    parser.add_argument("--ocr", action="store_true",
                        help="Enable HyperLPR3 plate text recognition")
    parser.add_argument("--resize", type=int, default=Config.RESIZE_WIDTH,
                        help=f"Window resize width (default: {Config.RESIZE_WIDTH})")
    parser.add_argument("--output", "-o", default=None,
                        help="Output video path (headless/no-GUI mode)")
    parser.add_argument("--device", "-d", default=Config.DEVICE,
                        help=f"Inference device (default: {Config.DEVICE}). cpu | cuda:0 | cuda:1 ...")
    parser.add_argument("--mog", action="store_true",
                        help="Enable MOG2 background subtraction for anomaly detection")
    parser.add_argument("--mog-debug", action="store_true",
                        help="Show MOG2 foreground mask and debug logs")
    parser.add_argument("--parking", action="store_true",
                        help="Enable no-parking zone violation detection")
    parser.add_argument("--parking-time", type=float, default=Config.PARKING_TIME,
                        help=f"Parking violation threshold in seconds (default: {Config.PARKING_TIME})")

    args = parser.parse_args()

    Config.VEHICLE_CONF = args.conf
    Config.PLATE_CONF = args.plate_conf
    Config.FRAME_SKIP = args.skip
    Config.RESIZE_WIDTH = args.resize
    Config.DEVICE = args.device
    Config.MOG_ANOMALY = args.mog
    Config.MOG_DEBUG = args.mog_debug
    Config.PARKING_ZONE = args.parking
    Config.PARKING_TIME = args.parking_time

    source = args.source
    if isinstance(source, str) and source.isdigit():
        source = int(source)

    run(source=source, mode=args.model, use_track=args.track, use_ocr=args.ocr,
        out_path=args.output)
