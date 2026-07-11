"""
沙盘摄像头视频推流查看器 — 增强版
基于 PyQt6 + OpenCV，支持 RTSP/RTMP 多路视频流切换查看

新增功能:
- YOLOv11m 车辆检测（可配置置信度阈值）
- YOLO Pose + CRNN 中文车牌识别（GPU 批量推理）
- 白名单匹配（精确/前缀/归一化匹配规则）
- 实时标注画面中的车辆和车牌
- 支持处理本地视频文件
"""
import sys
import os
import cv2
import numpy as np
from datetime import datetime
from typing import List, Optional

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QComboBox, QPushButton, QLabel, QGroupBox, QStatusBar, QFrame,
    QCheckBox, QDoubleSpinBox, QSpinBox, QFileDialog, QLineEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter,
    QScrollArea, QMessageBox, QTabWidget, QTextEdit,
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QThread, QMutex
from PyQt6.QtGui import QImage, QPixmap, QFont, QColor, QBrush

from detection_processor import DetectionProcessor, DetectionResult, HAS_YOLO
from whitelist_manager import WhitelistManager, WhitelistEntry


# ============================================================
# 视频源配置（支持 RTSP / RTMP）
# ============================================================
STREAM_SOURCES = {
    "桥面":           "rtsp://10.126.59.120:8554/live/live1",
    "停车场出口":     "rtsp://10.126.59.120:8554/live/live2",
    "行人":           "rtsp://10.126.59.120:8554/live/live3",
    "消防车识别":     "rtsp://10.126.59.120:8554/live/live4",
    "桥出口":         "rtsp://10.126.59.120:8554/live/live5",
    "桥入口":         "rtsp://10.126.59.120:8554/live/live6",
    "道路2":          "rtsp://10.126.59.120:8554/live/live7",
    "隧道(事故识别)": "rtsp://10.126.59.120:8554/live/live8",
    "隧道(车辆数量)": "rtsp://10.126.59.120:8554/live/live9",
    "道路3":          "rtsp://10.126.59.120:8554/live/live10",
    "停车场入口":     "rtsp://10.126.59.120:8554/live/live11",
    "道路1":          "rtsp://10.126.59.120:8554/live/live12",
}

# 默认配置文件路径
DEFAULT_WHITELIST_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "whitelist.json"
)


# ============================================================
# 视频采集线程（增强版：支持本地文件 + 检测管线）
# ============================================================
class VideoCaptureThread(QThread):
    """
    在独立线程中持续读取视频帧，可选执行检测管线。

    线程安全设计：
    - 所有 cv2.VideoCapture 的创建/读取/释放 仅在工作线程 run() 中执行
    - 主线程通过 set_xxx() 方法设置参数，工作线程在下一轮循环中检测并应用
    - QMutex 保护共享标志的读写
    """
    frame_ready = pyqtSignal(object)                  # 发送解码/标注后的帧 (numpy array)
    detection_results_ready = pyqtSignal(list)        # 发送检测结果列表
    connection_status = pyqtSignal(bool, str)         # 连接状态 + 消息
    detection_status = pyqtSignal(str)                 # 检测模块状态消息

    def __init__(self, parent=None):
        super().__init__(parent)
        self._mutex = QMutex()
        self._running = False
        self._pending_url = ""           # 非空表示需要切换源

        # 检测相关
        self._detection_enabled = False
        self._pending_detection_enabled: Optional[bool] = None
        self._detect_interval = 5        # 每隔 N 帧执行一次检测
        self._pending_detect_interval: Optional[int] = None
        self._yolo_conf = 0.5
        self._pending_yolo_conf: Optional[float] = None
        self._lpr_conf = 0.7
        self._pending_lpr_conf: Optional[float] = None
        self._device = "auto"            # 推理设备：auto/cpu/cuda:0 等
        self._pending_device: Optional[str] = None
        self._frame_count = 0
        self._processor: Optional[DetectionProcessor] = None
        self._last_results: List[DetectionResult] = []
        self._pending_init_detection = False

        # 白名单管理器引用（从主线程传入）
        self._whitelist_manager: Optional[WhitelistManager] = None

    # ---- 主线程调用（线程安全） ----

    def set_url(self, url: str):
        """请求切换到新的视频源"""
        self._mutex.lock()
        self._pending_url = url
        self._mutex.unlock()

    def set_detection_enabled(self, enabled: bool):
        """启用/禁用检测"""
        self._mutex.lock()
        self._pending_detection_enabled = enabled
        self._mutex.unlock()

    def set_detect_interval(self, interval: int):
        """设置检测间隔（帧数）"""
        self._mutex.lock()
        self._pending_detect_interval = max(1, interval)
        self._mutex.unlock()

    def set_yolo_threshold(self, conf: float):
        """设置 YOLO 置信度阈值"""
        self._mutex.lock()
        self._pending_yolo_conf = max(0.01, min(1.0, conf))
        self._mutex.unlock()

    def set_lpr_threshold(self, conf: float):
        """设置 LPR 置信度阈值"""
        self._mutex.lock()
        self._pending_lpr_conf = max(0.01, min(1.0, conf))
        self._mutex.unlock()

    def set_device(self, device: str):
        """设置推理设备 (cpu / auto / cuda / cuda:0 / mps 等)

        注意: 切换设备会触发模型重新加载，请在未启用检测时调用
        """
        self._mutex.lock()
        self._pending_device = device
        self._mutex.unlock()

    def set_whitelist_manager(self, wm: WhitelistManager):
        """设置白名单管理器引用"""
        self._whitelist_manager = wm

    def stop(self):
        """请求停止线程"""
        self._mutex.lock()
        self._running = False
        self._pending_url = ""
        self._mutex.unlock()
        self.wait(8000)

    # ---- 工作线程内部 ----

    def _apply_pending(self):
        """应用所有待处理的参数更新（在工作线程中调用）"""
        self._mutex.lock()

        if self._pending_detection_enabled is not None:
            self._detection_enabled = self._pending_detection_enabled
            if self._detection_enabled:
                self._pending_init_detection = True
            self._pending_detection_enabled = None

        if self._pending_detect_interval is not None:
            self._detect_interval = self._pending_detect_interval
            self._pending_detect_interval = None

        if self._pending_yolo_conf is not None:
            self._yolo_conf = self._pending_yolo_conf
            if self._processor:
                self._processor.yolo_threshold = self._yolo_conf
            self._pending_yolo_conf = None

        if self._pending_lpr_conf is not None:
            self._lpr_conf = self._pending_lpr_conf
            if self._processor:
                self._processor.lpr_threshold = self._lpr_conf
            self._pending_lpr_conf = None

        if self._pending_device is not None:
            new_device = self._pending_device
            self._pending_device = None
            if new_device != self._device:
                self._device = new_device
                # 设备切换 → 销毁旧处理器，触发重新加载
                if self._processor is not None:
                    self._processor = None
                self._last_results.clear()
                # 如果当前启用了检测，立即触发重新初始化
                if self._detection_enabled:
                    self._pending_init_detection = True

        self._mutex.unlock()

    def _get_pending_url(self) -> str:
        """取出待切换的 URL 并清空"""
        self._mutex.lock()
        url = self._pending_url
        self._pending_url = ""
        self._mutex.unlock()
        return url

    def _is_running(self) -> bool:
        self._mutex.lock()
        val = self._running
        self._mutex.unlock()
        return val

    def _is_local_file(self, url: str) -> bool:
        """判断 URL 是否为本地文件路径"""
        if not url:
            return False
        # 排除 RTSP/RTMP/HTTP 等网络协议
        if url.startswith(("rtsp://", "rtmp://", "http://", "https://", "udp://")):
            return False
        return os.path.exists(url)

    def run(self):
        """工作线程主循环"""
        self._mutex.lock()
        self._running = True
        self._mutex.unlock()

        cap = None
        current_url = ""
        is_local = False
        video_fps = 0.0

        while self._is_running():
            # ---- 应用待处理的参数 ----
            self._apply_pending()

            # ---- 初始化检测处理器（独立于视频源，即使无视频流也能加载模型） ----
            if self._pending_init_detection:
                self._pending_init_detection = False
                self._init_detection_processor()

            # ---- 检查是否有待切换的 URL ----
            new_url = self._get_pending_url()
            if new_url:
                if cap is not None:
                    cap.release()
                    cap = None
                current_url = new_url
                is_local = self._is_local_file(current_url)
                cap = self._open_capture(current_url)
                if cap is not None and is_local:
                    video_fps = cap.get(cv2.CAP_PROP_FPS)
                    if video_fps <= 0:
                        video_fps = 30.0
                else:
                    video_fps = 0.0
                # 切换源时清除上次检测结果
                self._last_results.clear()
                self._frame_count = 0

            # ---- 无采集器时短暂休眠 ----
            if cap is None:
                self.msleep(100)
                continue

            # ---- 读取一帧 ----
            try:
                ret, frame = cap.read()
            except Exception:
                self.connection_status.emit(False, "cv2.read() 异常，尝试重连...")
                self.msleep(1000)
                cap.release()
                cap = self._open_capture(current_url)
                continue

            if not ret:
                if is_local:
                    # 本地视频播放完毕
                    self.connection_status.emit(False, "本地视频播放完毕")
                    cap.release()
                    cap = None
                    self._last_results.clear()
                    continue
                else:
                    self.connection_status.emit(False, "读取帧失败，尝试重连...")
                    self.msleep(1000)
                    cap.release()
                    cap = self._open_capture(current_url)
                    continue

            self._frame_count += 1

            # ---- 执行检测 ----
            annotated = frame
            if (self._detection_enabled
                    and self._processor is not None
                    and self._processor.is_initialized
                    and self._frame_count % self._detect_interval == 0):

                try:
                    annotated, self._last_results = self._processor.process(frame)
                    # 发射检测结果到 UI 线程
                    self.detection_results_ready.emit(self._last_results)
                except Exception as e:
                    self.detection_status.emit(f"检测异常: {e}")
                    annotated = frame
            elif self._last_results:
                # 使用缓存的检测结果在帧上绘制标注
                annotated = self._draw_cached_results(frame)

            self.frame_ready.emit(annotated)

            # ---- 控制帧率 ----
            if is_local and video_fps > 0:
                # 本地视频：按视频帧率控制
                delay_ms = int(1000.0 / video_fps)
                self.msleep(max(1, delay_ms))
            else:
                # RTSP 流：小延迟防止 CPU 跑满
                self.msleep(5)

        # ---- 退出清理 ----
        if cap is not None:
            cap.release()

    def _init_detection_processor(self):
        """在工作线程中初始化检测处理器"""
        self.detection_status.emit(f"正在加载车辆与车牌识别模型... (device={self._device})")
        self._processor = DetectionProcessor(
            yolo_conf=self._yolo_conf,
            lpr_conf=self._lpr_conf,
            device=self._device,
        )

        # 传入白名单管理器引用
        if self._whitelist_manager:
            self._processor.whitelist_manager = self._whitelist_manager

        success = self._processor.initialize()
        if success:
            self.detection_status.emit(
                f"模型加载完成 (device={self._processor._device}, "
                f"LPR={'可用' if self._processor.has_lpr else '不可用'})"
            )
        else:
            self.detection_status.emit(f"模型加载失败: {self._processor.init_error}")
            self._detection_enabled = False
            self._mutex.lock()
            self._pending_detection_enabled = False
            self._mutex.unlock()

    def _draw_cached_results(self, frame: np.ndarray) -> np.ndarray:
        """使用缓存的检测结果在帧上绘制标注"""
        from draw_utils import draw_vehicle_box, draw_plate_info, draw_info_panel
        from draw_utils import COLOR_GREEN, COLOR_BLUE, COLOR_ORANGE

        annotated = frame.copy()

        for r in self._last_results:
            if r.whitelisted:
                color = COLOR_GREEN
            elif r.has_plate:
                color = COLOR_ORANGE
            else:
                color = COLOR_BLUE

            label = f"{r.vehicle_class_cn} {r.yolo_confidence:.0%}"
            draw_vehicle_box(annotated, r.vehicle_bbox, label, color)

            if r.has_plate:
                draw_plate_info(
                    annotated, r.vehicle_bbox,
                    r.plate_text, r.plate_confidence, r.whitelisted,
                )

        # 统计面板
        n_vehicles = len(self._last_results)
        n_plates = sum(1 for r in self._last_results if r.has_plate)
        panel_lines = [f"车辆: {n_vehicles} 辆  |  车牌: {n_plates} 个 (缓存)"]

        wm = self._whitelist_manager
        if wm and wm.enabled and wm.count > 0:
            n_wl = sum(1 for r in self._last_results if r.whitelisted)
            panel_lines.append(f"白名单: {n_wl}/{n_vehicles}  |  总数: {wm.count}")

        draw_info_panel(annotated, panel_lines, position=(10, 10), font_size=14)
        return annotated

    def _open_capture(self, url: str):
        """工作线程内部：打开视频流/文件并返回 VideoCapture 对象"""
        if not url:
            return None

        # Windows 上优先使用 D3D11VA 硬件解码；输出仍为兼容现有处理链的 BGR Mat。
        hw_params = [
            cv2.CAP_PROP_HW_ACCELERATION,
            cv2.VIDEO_ACCELERATION_D3D11,
            cv2.CAP_PROP_HW_DEVICE,
            0,
        ]
        cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG, hw_params)
        actual_hw = int(cap.get(cv2.CAP_PROP_HW_ACCELERATION)) if cap.isOpened() else 0
        decoder = (
            "GPU D3D11VA"
            if actual_hw == cv2.VIDEO_ACCELERATION_D3D11
            else "CPU FFmpeg (后端未启用 D3D11VA)"
        )
        if not cap.isOpened():
            cap.release()
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            decoder = "CPU FFmpeg (GPU 解码不可用，已回退)"
        if not cap.isOpened():
            self.connection_status.emit(False, f"无法打开视频源: {url}")
            cap.release()
            return None

        # 减小缓冲区以降低延迟
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        source_type = "本地文件" if self._is_local_file(url) else "视频流"
        self.connection_status.emit(True, f"已连接 ({source_type}, {decoder}): {url}")
        return cap


# ============================================================
# 视频显示控件
# ============================================================
class VideoDisplayWidget(QFrame):
    """用 QLabel 承接 QPixmap 显示视频帧"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Sunken)
        self.setStyleSheet("background-color: #1e1e1e; border: 2px solid #3a3a3a;")

        self._label = QLabel("等待连接视频流...", self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setFont(QFont("Microsoft YaHei", 12))
        self._label.setStyleSheet("color: #888; border: none;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)

    def show_frame(self, frame):
        """将 OpenCV BGR numpy 帧转为 QPixmap 显示"""
        if frame is None:
            return

        h, w = frame.shape[:2]
        bytes_per_line = 3 * w
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qimg)

        scaled = pixmap.scaled(
            self._label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._label.setPixmap(scaled)
        self._label.setText("")


# ============================================================
# 白名单管理面板
# ============================================================
class WhitelistPanel(QWidget):
    """白名单管理 UI 面板"""

    whitelist_changed = pyqtSignal()

    def __init__(self, whitelist_manager: WhitelistManager, parent=None):
        super().__init__(parent)
        self._wm = whitelist_manager
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ---- 操作栏 ----
        op_layout = QHBoxLayout()
        op_layout.setSpacing(6)

        self._plate_input = QLineEdit()
        self._plate_input.setPlaceholderText("输入车牌号，如 京A12345")
        self._plate_input.setFont(QFont("Microsoft YaHei", 10))
        self._plate_input.setStyleSheet(self._input_style())
        self._plate_input.returnPressed.connect(self._on_add)
        op_layout.addWidget(self._plate_input)

        btn_add = QPushButton("+ 添加")
        btn_add.setFont(QFont("Microsoft YaHei", 10))
        btn_add.clicked.connect(self._on_add)
        btn_add.setFixedWidth(70)
        op_layout.addWidget(btn_add)

        btn_load = QPushButton("📂 加载")
        btn_load.setFont(QFont("Microsoft YaHei", 10))
        btn_load.clicked.connect(self._on_load_file)
        btn_load.setFixedWidth(70)
        op_layout.addWidget(btn_load)

        btn_save = QPushButton("💾 保存")
        btn_save.setFont(QFont("Microsoft YaHei", 10))
        btn_save.clicked.connect(self._on_save_file)
        btn_save.setFixedWidth(70)
        op_layout.addWidget(btn_save)

        layout.addLayout(op_layout)

        # ---- 白名单表格 ----
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["车牌号", "备注"])
        self._table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self._table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setFont(QFont("Microsoft YaHei", 10))
        self._table.setStyleSheet(self._table_style())
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        layout.addWidget(self._table)

        # ---- 底部按钮 ----
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(6)

        self._lbl_count = QLabel("共 0 条")
        self._lbl_count.setFont(QFont("Microsoft YaHei", 9))
        self._lbl_count.setStyleSheet("color: #aaa;")
        bottom_layout.addWidget(self._lbl_count)

        bottom_layout.addStretch()

        btn_remove = QPushButton("🗑 移除选中")
        btn_remove.setFont(QFont("Microsoft YaHei", 10))
        btn_remove.clicked.connect(self._on_remove)
        btn_remove.setFixedWidth(100)
        bottom_layout.addWidget(btn_remove)

        btn_clear = QPushButton("清空全部")
        btn_clear.setFont(QFont("Microsoft YaHei", 10))
        btn_clear.clicked.connect(self._on_clear)
        btn_clear.setFixedWidth(80)
        bottom_layout.addWidget(btn_clear)

        layout.addLayout(bottom_layout)

    # ---- 事件 ----

    def _on_add(self):
        plate = self._plate_input.text().strip().upper()
        if not plate:
            return
        self._wm.add(plate, note="手动添加")
        self._plate_input.clear()
        self.refresh()
        self.whitelist_changed.emit()

    def _on_remove(self):
        rows = set()
        for item in self._table.selectedItems():
            rows.add(item.row())
        for row in sorted(rows, reverse=True):
            plate_item = self._table.item(row, 0)
            if plate_item:
                self._wm.remove(plate_item.text())
        self.refresh()
        self.whitelist_changed.emit()

    def _on_clear(self):
        reply = QMessageBox.question(
            self, "确认清空", "确定要清空所有白名单记录吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._wm.clear()
            self.refresh()
            self.whitelist_changed.emit()

    def _on_load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载白名单文件", "",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if path:
            count = self._wm.load(path)
            self.refresh()
            self.whitelist_changed.emit()
            QMessageBox.information(self, "加载完成", f"已加载 {count} 条白名单记录")

    def _on_save_file(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "保存白名单文件", "whitelist.json",
            "JSON 文件 (*.json);;所有文件 (*)"
        )
        if path:
            self._wm.save(path)

    # ---- 公共方法 ----

    def refresh(self):
        """刷新白名单表格显示"""
        entries = self._wm.get_all()
        self._table.setRowCount(len(entries))
        for i, entry in enumerate(entries):
            plate_item = QTableWidgetItem(entry.plate)
            plate_item.setForeground(QBrush(QColor("#4fc3f7")))
            self._table.setItem(i, 0, plate_item)

            note_item = QTableWidgetItem(entry.note)
            note_item.setForeground(QBrush(QColor("#aaa")))
            self._table.setItem(i, 1, note_item)

        self._lbl_count.setText(f"共 {len(entries)} 条")

    # ---- 样式 ----

    @staticmethod
    def _input_style():
        return """
            QLineEdit {
                background-color: #3c3c3c;
                color: #eee;
                border: 1px solid #555;
                padding: 4px 8px;
                border-radius: 4px;
            }
            QLineEdit:focus {
                border: 1px solid #0078d4;
            }
        """

    @staticmethod
    def _table_style():
        return """
            QTableWidget {
                background-color: #2b2b2b;
                color: #ddd;
                border: 1px solid #444;
                gridline-color: #3a3a3a;
            }
            QTableWidget::item:selected {
                background-color: #0078d4;
            }
            QHeaderView::section {
                background-color: #333;
                color: #ccc;
                border: none;
                padding: 4px;
            }
        """


# ============================================================
# 检测结果面板
# ============================================================
class DetectionResultsPanel(QWidget):
    """实时显示检测到的车牌信息"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # 标题
        lbl = QLabel("📋 实时检测结果")
        lbl.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
        lbl.setStyleSheet("color: #ddd; padding: 2px 0;")
        layout.addWidget(lbl)

        # 结果表格
        # 列: 车牌号 | 车辆类型 | YOLO置信度 | LPR置信度 | 白名单
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            "车牌号", "车辆类型", "YOLO", "LPR", "白名单"
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setFont(QFont("Microsoft YaHei", 9))
        self._table.setStyleSheet("""
            QTableWidget {
                background-color: #2b2b2b;
                color: #ddd;
                border: 1px solid #444;
                gridline-color: #3a3a3a;
            }
            QTableWidget::item:selected {
                background-color: #0078d4;
            }
            QHeaderView::section {
                background-color: #333;
                color: #ccc;
                border: none;
                padding: 3px;
                font-size: 9pt;
            }
        """)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        layout.addWidget(self._table)

        # 状态标签
        self._lbl_status = QLabel("等待检测...")
        self._lbl_status.setFont(QFont("Microsoft YaHei", 9))
        self._lbl_status.setStyleSheet("color: #888;")
        layout.addWidget(self._lbl_status)

    def update_results(self, results: List[DetectionResult]):
        """更新检测结果表格"""
        # 只显示有车牌的结果
        plate_results = [r for r in results if r.has_plate]
        self._table.setRowCount(len(plate_results))

        for i, r in enumerate(plate_results):
            # 车牌号
            plate_item = QTableWidgetItem(r.plate_text)
            plate_item.setForeground(QBrush(QColor("#4fc3f7")))
            plate_item.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Bold))
            self._table.setItem(i, 0, plate_item)

            # 车辆类型
            type_item = QTableWidgetItem(r.vehicle_class_cn)
            type_item.setForeground(QBrush(QColor("#ccc")))
            self._table.setItem(i, 1, type_item)

            # YOLO 置信度
            yolo_item = QTableWidgetItem(f"{r.yolo_confidence:.0%}")
            yolo_color = QColor("#4caf50") if r.yolo_confidence >= 0.7 else QColor("#ff9800")
            yolo_item.setForeground(QBrush(yolo_color))
            self._table.setItem(i, 2, yolo_item)

            # LPR 置信度
            lpr_item = QTableWidgetItem(f"{r.plate_confidence:.0%}")
            lpr_color = QColor("#4caf50") if r.plate_confidence >= 0.8 else QColor("#ff9800")
            lpr_item.setForeground(QBrush(lpr_color))
            self._table.setItem(i, 3, lpr_item)

            # 白名单状态
            if r.whitelisted:
                wl_item = QTableWidgetItem("✓ 白名单")
                wl_item.setForeground(QBrush(QColor("#4caf50")))
            else:
                wl_item = QTableWidgetItem("✗ 非白名单")
                wl_item.setForeground(QBrush(QColor("#ff5252")))
            self._table.setItem(i, 4, wl_item)

        # 状态
        total = len(results)
        n_plates = len(plate_results)
        n_wl = sum(1 for r in results if r.whitelisted)
        self._lbl_status.setText(
            f"共检测 {total} 辆车 | 识别 {n_plates} 个车牌 | 白名单匹配 {n_wl} 个"
        )

    def clear_results(self):
        """清空表格"""
        self._table.setRowCount(0)
        self._lbl_status.setText("等待检测...")


# ============================================================
# 主窗口
# ============================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("沙盘摄像头推流查看器 — 车辆检测 + 车牌识别")
        self.setMinimumSize(1280, 720)
        self.resize(1600, 900)

        self._current_frame = None

        # ---- 白名单管理器 ----
        self._whitelist_manager = WhitelistManager()
        # 尝试加载默认白名单文件
        if os.path.exists(DEFAULT_WHITELIST_FILE):
            self._whitelist_manager.load(DEFAULT_WHITELIST_FILE)

        # ---- 视频采集线程 ----
        self._thread = VideoCaptureThread(self)
        self._thread.frame_ready.connect(self._on_frame)
        self._thread.connection_status.connect(self._on_status)
        self._thread.detection_results_ready.connect(self._on_detection_results)
        self._thread.detection_status.connect(self._on_detection_status)
        self._thread.set_whitelist_manager(self._whitelist_manager)
        self._thread.start()

        # ---- UI 构建 ----
        self._build_ui()

        # ---- 启动后自动连第一个源 ----
        QTimer.singleShot(500, self._auto_start_first)

    # ---------- UI 构建 ----------

    def _build_ui(self):
        central = QWidget(self)
        self.setCentralWidget(central)

        # 使用 QSplitter 实现左右分栏
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)

        # ---- 左侧：视频显示 ----
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(8, 8, 4, 8)
        left_layout.setSpacing(8)

        # 控制栏
        left_layout.addLayout(self._build_control_bar())

        # 视频显示
        self._video_display = VideoDisplayWidget(self)
        left_layout.addWidget(self._video_display, stretch=1)

        # 检测状态标签
        self._lbl_detection_status = QLabel("检测: 未启用")
        self._lbl_detection_status.setFont(QFont("Microsoft YaHei", 9))
        self._lbl_detection_status.setStyleSheet("color: #888; padding: 2px;")
        left_layout.addWidget(self._lbl_detection_status)

        splitter.addWidget(left_widget)

        # ---- 右侧：设置面板 ----
        right_widget = QWidget()
        right_widget.setFixedWidth(320)
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(4, 8, 8, 8)
        right_layout.setSpacing(8)

        # 选项卡：白名单 / 检测结果
        tab = QTabWidget()
        tab.setFont(QFont("Microsoft YaHei", 10))

        self._whitelist_panel = WhitelistPanel(self._whitelist_manager)
        self._whitelist_panel.whitelist_changed.connect(self._on_whitelist_changed)
        self._whitelist_panel.refresh()
        tab.addTab(self._whitelist_panel, "🛡 白名单")

        self._results_panel = DetectionResultsPanel()
        tab.addTab(self._results_panel, "📋 检测结果")

        right_layout.addWidget(tab, stretch=1)

        # 状态栏
        self._statusbar = QStatusBar(self)
        self._statusbar.setFont(QFont("Microsoft YaHei", 9))
        self._statusbar.showMessage("就绪 — 请选择视频源并点击播放")
        self.setStatusBar(self._statusbar)

        splitter.addWidget(right_widget)
        splitter.setSizes([1200, 320])

        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.addWidget(splitter)

    def _build_control_bar(self):
        """构建顶部控制栏"""
        layout = QHBoxLayout()
        layout.setSpacing(8)

        # 视频源选择
        lbl_src = QLabel("视频源:")
        lbl_src.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(lbl_src)

        self._combo = QComboBox()
        self._combo.setMinimumWidth(180)
        self._combo.setFont(QFont("Microsoft YaHei", 10))
        self._combo.addItems(STREAM_SOURCES.keys())
        self._combo.currentTextChanged.connect(self._on_source_changed)
        layout.addWidget(self._combo)

        self._btn_play = QPushButton("▶ 播放")
        self._btn_play.setFont(QFont("Microsoft YaHei", 10))
        self._btn_play.clicked.connect(self._on_play_clicked)
        layout.addWidget(self._btn_play)

        # 本地视频
        self._btn_local = QPushButton("📁 本地视频")
        self._btn_local.setFont(QFont("Microsoft YaHei", 10))
        self._btn_local.clicked.connect(self._on_open_local)
        layout.addWidget(self._btn_local)

        # 截图
        self._btn_snapshot = QPushButton("📷 截图")
        self._btn_snapshot.setFont(QFont("Microsoft YaHei", 10))
        self._btn_snapshot.clicked.connect(self._on_snapshot_clicked)
        layout.addWidget(self._btn_snapshot)

        layout.addSpacing(16)

        # 分隔
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setStyleSheet("color: #555;")
        sep.setFixedWidth(1)
        layout.addWidget(sep)

        # 检测开关
        self._chk_detection = QCheckBox("启用检测")
        self._chk_detection.setFont(QFont("Microsoft YaHei", 10))
        self._chk_detection.setStyleSheet("color: #ddd;")
        self._chk_detection.toggled.connect(self._on_detection_toggled)
        layout.addWidget(self._chk_detection)

        # 推理设备选择
        lbl_device = QLabel("设备:")
        lbl_device.setFont(QFont("Microsoft YaHei", 9))
        lbl_device.setStyleSheet("color: #aaa;")
        layout.addWidget(lbl_device)

        self._combo_device = QComboBox()
        self._combo_device.setMinimumWidth(100)
        self._combo_device.setFont(QFont("Microsoft YaHei", 9))
        self._combo_device.setStyleSheet("""
            QComboBox {
                background-color: #3c3c3c;
                color: #eee;
                border: 1px solid #555;
                padding: 3px 6px;
                border-radius: 4px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background-color: #3c3c3c;
                color: #eee;
                selection-background-color: #0078d4;
            }
        """)
        # 自动检测可用设备并填充
        from detection_processor import DetectionProcessor
        available_devices = DetectionProcessor.get_available_devices()
        for dev_id, dev_name in available_devices:
            self._combo_device.addItem(dev_name, dev_id)
        # 默认选中 auto（即第一个非 CPU 设备，或 CPU）
        if len(available_devices) > 1:
            self._combo_device.setCurrentIndex(1)  # 优先 GPU
        self._combo_device.currentIndexChanged.connect(self._on_device_changed)
        layout.addWidget(self._combo_device)

        # YOLO 阈值
        lbl_yolo = QLabel("YOLO阈值:")
        lbl_yolo.setFont(QFont("Microsoft YaHei", 9))
        lbl_yolo.setStyleSheet("color: #aaa;")
        layout.addWidget(lbl_yolo)

        self._spin_yolo = QDoubleSpinBox()
        self._spin_yolo.setRange(0.05, 1.0)
        self._spin_yolo.setSingleStep(0.05)
        self._spin_yolo.setValue(0.5)
        self._spin_yolo.setDecimals(2)
        self._spin_yolo.setFixedWidth(70)
        self._spin_yolo.setFont(QFont("Microsoft YaHei", 10))
        self._spin_yolo.setStyleSheet(self._spin_style())
        self._spin_yolo.valueChanged.connect(self._on_yolo_threshold_changed)
        layout.addWidget(self._spin_yolo)

        # LPR 阈值
        lbl_lpr = QLabel("LPR阈值:")
        lbl_lpr.setFont(QFont("Microsoft YaHei", 9))
        lbl_lpr.setStyleSheet("color: #aaa;")
        layout.addWidget(lbl_lpr)

        self._spin_lpr = QDoubleSpinBox()
        self._spin_lpr.setRange(0.05, 1.0)
        self._spin_lpr.setSingleStep(0.05)
        self._spin_lpr.setValue(0.7)
        self._spin_lpr.setDecimals(2)
        self._spin_lpr.setFixedWidth(70)
        self._spin_lpr.setFont(QFont("Microsoft YaHei", 10))
        self._spin_lpr.setStyleSheet(self._spin_style())
        self._spin_lpr.valueChanged.connect(self._on_lpr_threshold_changed)
        layout.addWidget(self._spin_lpr)

        # 检测间隔
        lbl_int = QLabel("间隔:")
        lbl_int.setFont(QFont("Microsoft YaHei", 9))
        lbl_int.setStyleSheet("color: #aaa;")
        layout.addWidget(lbl_int)

        self._spin_interval = QSpinBox()
        self._spin_interval.setRange(1, 60)
        self._spin_interval.setValue(5)
        self._spin_interval.setSuffix(" 帧")
        self._spin_interval.setFixedWidth(70)
        self._spin_interval.setFont(QFont("Microsoft YaHei", 10))
        self._spin_interval.setStyleSheet(self._spin_style())
        self._spin_interval.valueChanged.connect(self._on_interval_changed)
        layout.addWidget(self._spin_interval)

        layout.addStretch()

        self._lbl_resolution = QLabel("分辨率: --")
        self._lbl_resolution.setFont(QFont("Microsoft YaHei", 9))
        self._lbl_resolution.setStyleSheet("color: #aaa;")
        layout.addWidget(self._lbl_resolution)

        return layout

    # ---------- 事件处理 ----------

    def _auto_start_first(self):
        """启动后自动连接第一个视频源"""
        if self._combo.count() > 0:
            name = self._combo.currentText()
            self._start_stream(STREAM_SOURCES[name])

    def _on_source_changed(self, name: str):
        """下拉框切换视频源"""
        url = STREAM_SOURCES.get(name, "")
        if url:
            self._start_stream(url)

    def _on_play_clicked(self):
        """手动点击播放按钮"""
        name = self._combo.currentText()
        url = STREAM_SOURCES.get(name, "")
        if url:
            self._start_stream(url)

    def _on_open_local(self):
        """打开本地视频文件"""
        path, _ = QFileDialog.getOpenFileName(
            self, "打开本地视频文件", "videos/",
            "视频文件 (*.mp4 *.avi *.mov *.mkv *.flv *.wmv);;所有文件 (*)"
        )
        if path:
            self._start_stream(path)

    def _start_stream(self, url: str):
        """通知采集线程切换到新的视频源"""
        if os.path.exists(url):
            name = os.path.basename(url)
        else:
            name = self._combo.currentText()
        self._statusbar.showMessage(f"正在连接 {name} ...")
        self._results_panel.clear_results()
        self._thread.set_url(url)

    def _on_frame(self, frame):
        """收到帧，刷新显示"""
        self._current_frame = frame
        self._video_display.show_frame(frame)

        h, w = frame.shape[:2]
        self._lbl_resolution.setText(f"分辨率: {w}x{h}")

    def _on_status(self, ok: bool, msg: str):
        self._statusbar.showMessage(msg)

    def _on_detection_toggled(self, enabled: bool):
        """检测开关切换"""
        self._thread.set_detection_enabled(enabled)
        if enabled:
            self._lbl_detection_status.setText("检测: 正在加载模型...")
            self._lbl_detection_status.setStyleSheet("color: #ff9800; padding: 2px;")
        else:
            self._lbl_detection_status.setText("检测: 未启用")
            self._lbl_detection_status.setStyleSheet("color: #888; padding: 2px;")
            self._results_panel.clear_results()

    def _on_yolo_threshold_changed(self, value: float):
        """YOLO 置信度阈值变更"""
        self._thread.set_yolo_threshold(value)

    def _on_lpr_threshold_changed(self, value: float):
        """LPR 置信度阈值变更"""
        self._thread.set_lpr_threshold(value)

    def _on_device_changed(self, index: int):
        """推理设备切换"""
        device_id = self._combo_device.currentData()
        if device_id:
            self._thread.set_device(device_id)
            self._statusbar.showMessage(f"推理设备已切换为: {device_id}（重新启用检测后生效）")

    def _on_interval_changed(self, value: int):
        """检测间隔变更"""
        self._thread.set_detect_interval(value)

    def _on_detection_results(self, results: List[DetectionResult]):
        """收到检测结果"""
        self._results_panel.update_results(results)

    def _on_detection_status(self, msg: str):
        """检测模块状态消息"""
        if "加载" in msg:
            self._lbl_detection_status.setText(f"检测: {msg}")
            self._lbl_detection_status.setStyleSheet("color: #ff9800; padding: 2px;")
        elif "失败" in msg:
            self._lbl_detection_status.setText(f"检测: {msg}")
            self._lbl_detection_status.setStyleSheet("color: #ff5252; padding: 2px;")
            self._chk_detection.blockSignals(True)
            self._chk_detection.setChecked(False)
            self._chk_detection.blockSignals(False)
        else:
            self._lbl_detection_status.setText(f"检测: {msg}")
            self._lbl_detection_status.setStyleSheet("color: #4caf50; padding: 2px;")

    def _on_whitelist_changed(self):
        """白名单变更时通知采集线程"""
        # 白名单管理器在检测处理器中共享引用，自动生效
        # 如果有本地默认文件，自动保存
        if self._whitelist_manager.count > 0:
            self._whitelist_manager.save(DEFAULT_WHITELIST_FILE)

    def _on_snapshot_clicked(self):
        """截图保存（保存带标注的帧）"""
        if self._current_frame is None:
            self._statusbar.showMessage("暂无视频帧，请先播放")
            return

        filename = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(filename, self._current_frame)
        self._statusbar.showMessage(f"截图已保存: {filename}")

    def closeEvent(self, event):
        self._thread.stop()
        # 退出前保存白名单
        if self._whitelist_manager.count > 0:
            self._whitelist_manager.save(DEFAULT_WHITELIST_FILE)
        super().closeEvent(event)

    # ---------- 样式 ----------

    @staticmethod
    def _spin_style():
        return """
            QDoubleSpinBox, QSpinBox {
                background-color: #3c3c3c;
                color: #eee;
                border: 1px solid #555;
                padding: 3px 4px;
                border-radius: 4px;
            }
            QDoubleSpinBox:focus, QSpinBox:focus {
                border: 1px solid #0078d4;
            }
        """


# ============================================================
# 入口
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    app.setStyleSheet("""
        QMainWindow {
            background-color: #2b2b2b;
        }
        QGroupBox {
            color: #ddd;
            font-weight: bold;
            border: 1px solid #444;
            border-radius: 6px;
            margin-top: 8px;
            padding-top: 16px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 6px;
        }
        QComboBox {
            background-color: #3c3c3c;
            color: #eee;
            border: 1px solid #555;
            padding: 4px 8px;
            border-radius: 4px;
        }
        QComboBox::drop-down {
            border: none;
        }
        QComboBox QAbstractItemView {
            background-color: #3c3c3c;
            color: #eee;
            selection-background-color: #0078d4;
        }
        QPushButton {
            background-color: #0078d4;
            color: #fff;
            border: none;
            padding: 6px 16px;
            border-radius: 4px;
        }
        QPushButton:hover {
            background-color: #1a8ae8;
        }
        QPushButton:pressed {
            background-color: #005fa3;
        }
        QLabel {
            color: #ddd;
        }
        QCheckBox {
            color: #ddd;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
        }
        QTabWidget::pane {
            border: 1px solid #444;
            background-color: #2b2b2b;
        }
        QTabBar::tab {
            background-color: #333;
            color: #ccc;
            padding: 6px 12px;
            border: 1px solid #444;
            border-bottom: none;
            border-top-left-radius: 4px;
            border-top-right-radius: 4px;
        }
        QTabBar::tab:selected {
            background-color: #3c3c3c;
            color: #fff;
        }
        QTabBar::tab:hover {
            background-color: #444;
        }
        QStatusBar {
            background-color: #323232;
            color: #aaa;
            border-top: 1px solid #444;
        }
        QScrollArea {
            border: none;
            background-color: transparent;
        }
        QSplitter::handle {
            background-color: #444;
        }
    """)

    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
