"""PyQt widgets for sandbox road topology, heat, tracks, and camera placement."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

from PyQt6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from traffic_map import CameraPlacement, RoadSegment, SegmentState, TrafficMapModel


class TrafficMapCanvas(QWidget):
    map_clicked = pyqtSignal(float, float)
    camera_clicked = pyqtSignal(str)
    segment_clicked = pyqtSignal(str)
    road_drawn = pyqtSignal(object)
    drawing_mode_changed = pyqtSignal(str)

    def __init__(self, model: TrafficMapModel, image_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.model = model
        self.image_path = Path(image_path)
        self.pixmap = QPixmap(str(self.image_path))
        self.segment_states: Dict[str, SegmentState] = {}
        self.selected_camera = ""
        self.selected_segment = ""
        self.show_background = True
        self.placement_mode = False
        self.drawing_mode = ""
        self.drawing_points: List[Tuple[float, float]] = []
        self.cursor_point: Tuple[float, float] | None = None
        self.setMinimumSize(560, 560)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("background: #181a1d;")
        self._camera_hint = QLabel(self)
        self._camera_hint.setFont(QFont("Microsoft YaHei", 9))
        self._camera_hint.setStyleSheet("""
            QLabel {
                color: #f2f5f6;
                background-color: #30383e;
                border: 1px solid #68757d;
                padding: 5px 7px;
            }
        """)
        self._camera_hint.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self._camera_hint.hide()

    def sizeHint(self) -> QSize:
        return QSize(800, 760)

    def set_selected_camera(self, camera_id: str) -> None:
        self.selected_camera = camera_id
        self.update()

    def set_selected_segment(self, segment_id: str) -> None:
        self.selected_segment = segment_id
        self.update()

    def set_background_visible(self, visible: bool) -> None:
        self.show_background = bool(visible)
        self.update()

    def load_background(self, image_path: Path) -> bool:
        image_path = Path(image_path)
        pixmap = QPixmap(str(image_path))
        if pixmap.isNull():
            return False
        self.image_path = image_path
        self.pixmap = pixmap
        self.updateGeometry()
        self.update()
        return True

    def reload_background(self) -> bool:
        return self.load_background(self.image_path)

    def set_placement_mode(self, enabled: bool) -> None:
        if enabled:
            self.cancel_road_drawing()
        self.placement_mode = enabled
        self.setCursor(
            Qt.CursorShape.CrossCursor if enabled else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def start_road_drawing(self, mode: str) -> None:
        if mode not in {"line", "curve", "polyline"}:
            raise ValueError(f"Unsupported road drawing mode: {mode}")
        self.placement_mode = False
        self.drawing_mode = mode
        self.drawing_points = []
        self.cursor_point = None
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocus()
        self.drawing_mode_changed.emit(mode)
        self.update()

    def cancel_road_drawing(self) -> None:
        was_drawing = bool(self.drawing_mode)
        self.drawing_mode = ""
        self.drawing_points = []
        self.cursor_point = None
        if not self.placement_mode:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        if was_drawing:
            self.drawing_mode_changed.emit("")
        self.update()

    def finish_road_drawing(self) -> bool:
        required = 3 if self.drawing_mode == "curve" else 2
        if not self.drawing_mode or len(self.drawing_points) < required:
            return False

        if self.drawing_mode == "curve":
            start, control, end = self.drawing_points[:3]
            completed = []
            for index in range(25):
                ratio = index / 24
                inverse = 1.0 - ratio
                completed.append((
                    inverse * inverse * start[0]
                    + 2 * inverse * ratio * control[0]
                    + ratio * ratio * end[0],
                    inverse * inverse * start[1]
                    + 2 * inverse * ratio * control[1]
                    + ratio * ratio * end[1],
                ))
        else:
            completed = list(self.drawing_points)

        self.cancel_road_drawing()
        self.road_drawn.emit(completed)
        return True

    def refresh_state(self) -> None:
        self.segment_states = self.model.segment_states()
        self.update()

    def _content_rect(self) -> QRectF:
        if self.pixmap.isNull():
            return QRectF(self.rect())
        available = QRectF(self.rect()).adjusted(10, 10, -10, -10)
        ratio = min(
            available.width() / self.pixmap.width(),
            available.height() / self.pixmap.height(),
        )
        width = self.pixmap.width() * ratio
        height = self.pixmap.height() * ratio
        return QRectF(
            available.center().x() - width / 2,
            available.center().y() - height / 2,
            width,
            height,
        )

    def _to_canvas(self, point: Tuple[float, float]) -> QPointF:
        rect = self._content_rect()
        return QPointF(rect.left() + point[0] * rect.width(), rect.top() + point[1] * rect.height())

    def _from_canvas(self, point: QPointF) -> Tuple[float, float]:
        rect = self._content_rect()
        return (
            min(1.0, max(0.0, (point.x() - rect.left()) / max(1.0, rect.width()))),
            min(1.0, max(0.0, (point.y() - rect.top()) / max(1.0, rect.height()))),
        )

    @staticmethod
    def _heat_color(heat: float, covered: bool) -> QColor:
        if not covered:
            return QColor("#7b8088")
        stops = [
            (0.0, QColor("#2fb171")),
            (0.35, QColor("#d5bd32")),
            (0.65, QColor("#ed812b")),
            (1.0, QColor("#d83b4b")),
        ]
        heat = min(1.0, max(0.0, heat))
        for (left_value, left), (right_value, right) in zip(stops, stops[1:]):
            if heat <= right_value:
                ratio = (heat - left_value) / (right_value - left_value)
                return QColor(
                    int(left.red() + (right.red() - left.red()) * ratio),
                    int(left.green() + (right.green() - left.green()) * ratio),
                    int(left.blue() + (right.blue() - left.blue()) * ratio),
                )
        return stops[-1][1]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#181a1d"))
        content = self._content_rect()
        if self.show_background and not self.pixmap.isNull():
            painter.drawPixmap(content.toRect(), self.pixmap)
            painter.fillRect(content, QColor(8, 12, 14, 38))
        else:
            painter.fillRect(content, QColor("#101417"))

        covered_segments = {camera.segment_id for camera in self.model.cameras.values()}
        for segment in self.model.segments.values():
            if len(segment.points) < 2:
                continue
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(self._to_canvas(segment.points[0]))
            for point in segment.points[1:]:
                path.lineTo(self._to_canvas(point))
            if segment.segment_id == self.selected_segment:
                painter.setPen(QPen(QColor("#f4cc58"), 12, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                painter.drawPath(path)
            painter.setPen(QPen(QColor(15, 18, 20, 180), 8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            state = self.segment_states.get(segment.segment_id)
            heat = state.heat if state else 0.0
            color = self._heat_color(heat, segment.segment_id in covered_segments)
            line_style = Qt.PenStyle.DashLine if segment.level == "bridge" else Qt.PenStyle.SolidLine
            painter.setPen(QPen(color, 4, line_style, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPath(path)
            if segment.direction != "双向":
                self._draw_direction_arrows(painter, segment, color)
            if segment.segment_id == self.selected_segment:
                label_position = self._to_canvas(segment.points[len(segment.points) // 2])
                label = f"{segment.name} · {segment.direction}"
                painter.setFont(QFont("Microsoft YaHei", 9, QFont.Weight.DemiBold))
                painter.setPen(QColor("#15191c"))
                painter.drawText(label_position + QPointF(7, -7), label)
                painter.setPen(QColor("#ffe28a"))
                painter.drawText(label_position + QPointF(6, -8), label)

        self._draw_tracks(painter)
        self._draw_cameras(painter)
        self._draw_road_preview(painter)
        self._draw_legend(painter)

        if self.placement_mode:
            painter.setPen(QPen(QColor("#f3c74f"), 2, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(content.adjusted(2, 2, -2, -2))

    def _draw_direction_arrows(
        self, painter: QPainter, segment: RoadSegment, color: QColor
    ) -> None:
        points = [self._to_canvas(point) for point in segment.points]
        edges = []
        total_length = 0.0
        for start, end in zip(points, points[1:]):
            length = math.dist((start.x(), start.y()), (end.x(), end.y()))
            if length > 0.5:
                edges.append((start, end, length))
                total_length += length
        if not edges:
            return

        arrow_count = max(1, min(4, int(total_length / 180.0) + 1))
        painter.setPen(QPen(QColor("#11161a"), 1))
        painter.setBrush(color.lighter(150))
        for arrow_index in range(arrow_count):
            target = total_length * (arrow_index + 1) / (arrow_count + 1)
            traversed = 0.0
            for start, end, length in edges:
                if traversed + length < target:
                    traversed += length
                    continue
                ratio = (target - traversed) / length
                center = QPointF(
                    start.x() + (end.x() - start.x()) * ratio,
                    start.y() + (end.y() - start.y()) * ratio,
                )
                ux = (end.x() - start.x()) / length
                uy = (end.y() - start.y()) / length
                tip = center + QPointF(ux * 7, uy * 7)
                base = center - QPointF(ux * 5, uy * 5)
                left = base + QPointF(-uy * 4, ux * 4)
                right = base + QPointF(uy * 4, -ux * 4)
                painter.drawPolygon(QPolygonF([tip, left, right]))
                break
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _draw_road_preview(self, painter: QPainter) -> None:
        if not self.drawing_mode:
            return
        points = list(self.drawing_points)
        if self.cursor_point is not None:
            points.append(self.cursor_point)
        painter.setPen(QPen(QColor("#f4cc58"), 3, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(QColor("#f4cc58"))
        for point in self.drawing_points:
            painter.drawEllipse(self._to_canvas(point), 5, 5)
        if len(points) < 2:
            return
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(self._to_canvas(points[0]))
        if self.drawing_mode == "curve" and len(points) >= 3:
            path.quadTo(self._to_canvas(points[1]), self._to_canvas(points[2]))
        else:
            for point in points[1:]:
                path.lineTo(self._to_canvas(point))
        painter.drawPath(path)

    def _draw_tracks(self, painter: QPainter) -> None:
        class_colors = {
            "car": QColor("#29b6f6"),
            "motorcycle": QColor("#f1c44c"),
            "bus": QColor("#ef6c61"),
            "truck": QColor("#ab78d1"),
        }
        painter.setFont(QFont("Microsoft YaHei", 8))
        for track in self.model.tracks.values():
            color = class_colors.get(track.vehicle_class, QColor("#f5f5f5"))
            if len(track.history) > 1:
                path = QPainterPath(self._to_canvas(track.history[0]))
                for point in track.history[1:]:
                    path.lineTo(self._to_canvas(point))
                trail = QColor(color)
                trail.setAlpha(145)
                painter.setPen(QPen(trail, 2))
                painter.drawPath(path)
            position = self._to_canvas((track.x, track.y))
            painter.setPen(QPen(QColor("#111418"), 2))
            painter.setBrush(color)
            vector = None
            if len(track.history) > 1:
                for previous in reversed(track.history[:-1]):
                    previous_canvas = self._to_canvas(previous)
                    dx = position.x() - previous_canvas.x()
                    dy = position.y() - previous_canvas.y()
                    length = math.hypot(dx, dy)
                    if length > 0.5:
                        vector = (dx / length, dy / length)
                        break
            if vector is None:
                segment = self.model.segments.get(track.segment_id)
                if segment and len(segment.points) > 1:
                    _, _, _, edge_index, _ = self.model._project_to_polyline(
                        (track.x, track.y), segment.points
                    )
                    start = self._to_canvas(segment.points[edge_index])
                    end = self._to_canvas(segment.points[edge_index + 1])
                    dx, dy = end.x() - start.x(), end.y() - start.y()
                    length = math.hypot(dx, dy)
                    if length > 0.5:
                        vector = (dx / length, dy / length)
            if vector is None:
                painter.drawEllipse(position, 6, 6)
            else:
                ux, uy = vector
                tip = position + QPointF(ux * 8, uy * 8)
                rear = position - QPointF(ux * 6, uy * 6)
                painter.drawPolygon(QPolygonF([
                    tip,
                    rear + QPointF(-uy * 5, ux * 5),
                    rear + QPointF(uy * 5, -ux * 5),
                ]))
            label = track.plate_text or track.global_id
            painter.setPen(QColor("#111418"))
            painter.drawText(position + QPointF(8, 10), label)
            painter.setPen(QColor("#f4f6f8"))
            painter.drawText(position + QPointF(7, 9), label)

    def _draw_cameras(self, painter: QPainter) -> None:
        painter.setFont(QFont("Microsoft YaHei", 8))
        for camera in self.model.cameras.values():
            position = self._to_canvas((camera.x, camera.y))
            selected = camera.camera_id == self.selected_camera
            if selected:
                angle = math.radians(camera.heading)
                center_angle = -math.pi / 2 + angle
                length = camera.view_range * self._content_rect().height()
                left = position + QPointF(
                    math.cos(center_angle - 0.42) * length,
                    math.sin(center_angle - 0.42) * length,
                )
                right = position + QPointF(
                    math.cos(center_angle + 0.42) * length,
                    math.sin(center_angle + 0.42) * length,
                )
                field = QPolygonF([position, left, right])
                painter.setPen(QPen(QColor(0, 145, 170, 150), 1))
                painter.setBrush(QColor(0, 170, 195, 42))
                painter.drawPolygon(field)

            painter.setPen(QPen(QColor("#1c2227"), 2))
            painter.setBrush(QColor("#f3c74f") if selected else QColor("#18a9c2"))
            painter.drawEllipse(position, 7 if selected else 6, 7 if selected else 6)
            if selected:
                painter.setPen(QColor("#202428"))
                painter.drawText(position + QPointF(9, -5), camera.camera_id)
                painter.setPen(QColor("#f4f6f8"))
                painter.drawText(position + QPointF(8, -6), camera.camera_id)

    def _draw_legend(self, painter: QPainter) -> None:
        content = self._content_rect()
        x = content.left() + 14
        y = content.top() + 14
        labels = [
            ("畅通", QColor("#2fb171")),
            ("缓行", QColor("#d5bd32")),
            ("拥挤", QColor("#ed812b")),
            ("拥堵", QColor("#d83b4b")),
            ("未覆盖", QColor("#7b8088")),
        ]
        painter.setFont(QFont("Microsoft YaHei", 8))
        for label, color in labels:
            painter.fillRect(QRectF(x, y, 18, 5), color)
            painter.setPen(QColor("#202428"))
            painter.drawText(QPointF(x + 24, y + 7), label)
            y += 17

    def mousePressEvent(self, event) -> None:
        point = event.position()
        if event.button() == Qt.MouseButton.RightButton and self.drawing_mode:
            self.cancel_road_drawing()
            return
        if not self._content_rect().contains(point):
            return
        if self.drawing_mode:
            if event.button() != Qt.MouseButton.LeftButton:
                return
            normalized = self._from_canvas(point)
            self.drawing_points.append(normalized)
            required = 3 if self.drawing_mode == "curve" else 2
            if self.drawing_mode != "polyline" and len(self.drawing_points) == required:
                self.finish_road_drawing()
            else:
                self.update()
            return
        if self.placement_mode:
            x, y = self._from_canvas(point)
            self.map_clicked.emit(x, y)
            return
        nearest = self._nearest_camera(point)
        if nearest:
            self.camera_clicked.emit(nearest)
            return
        segment_id = self._nearest_segment(point)
        if segment_id:
            self.segment_clicked.emit(segment_id)

    def mouseMoveEvent(self, event) -> None:
        if self.drawing_mode and self._content_rect().contains(event.position()):
            self._camera_hint.hide()
            self.cursor_point = self._from_canvas(event.position())
            self.update()
            return
        camera_id = self._nearest_camera(event.position(), max_distance=12)
        if camera_id:
            self._show_camera_hint(event.position(), camera_id)
        else:
            self._camera_hint.hide()

    def _show_camera_hint(self, cursor: QPointF, camera_id: str) -> None:
        camera = self.model.cameras[camera_id]
        segment = self.model.segments.get(camera.segment_id)
        road = segment.name if segment else "未关联"
        direction = segment.direction if segment else "--"
        self._camera_hint.setText(
            f"{camera_id}\n道路: {road} · {direction}\n朝向: {camera.heading:.0f}°"
        )
        self._camera_hint.adjustSize()
        margin = 8
        x = min(
            int(cursor.x()) + 14,
            max(margin, self.width() - self._camera_hint.width() - margin),
        )
        y = min(
            int(cursor.y()) + 14,
            max(margin, self.height() - self._camera_hint.height() - margin),
        )
        self._camera_hint.move(max(margin, x), max(margin, y))
        self._camera_hint.show()
        self._camera_hint.raise_()

    def leaveEvent(self, event: QEvent) -> None:
        self._camera_hint.hide()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if (
            self.drawing_mode == "polyline"
            and event.button() == Qt.MouseButton.LeftButton
        ):
            self.finish_road_drawing()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.drawing_mode:
            self.cancel_road_drawing()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.drawing_mode:
            self.finish_road_drawing()
            return
        if event.key() == Qt.Key.Key_Backspace and self.drawing_mode:
            if self.drawing_points:
                self.drawing_points.pop()
                self.update()
            return
        super().keyPressEvent(event)

    def _nearest_segment(self, point: QPointF, max_distance: float = 12) -> str:
        normalized = self._from_canvas(point)
        segment_id, projected, _ = self.model.nearest_segment(normalized)
        projected_canvas = self._to_canvas(projected)
        distance = math.dist((point.x(), point.y()), (projected_canvas.x(), projected_canvas.y()))
        return segment_id if distance <= max_distance else ""

    def _nearest_camera(self, point: QPointF, max_distance: float = 18) -> str:
        matches = [
            (math.dist((point.x(), point.y()), (self._to_canvas((camera.x, camera.y)).x(), self._to_canvas((camera.x, camera.y)).y())), camera.camera_id)
            for camera in self.model.cameras.values()
        ]
        if not matches:
            return ""
        distance, camera_id = min(matches)
        return camera_id if distance <= max_distance else ""


class TrafficMapPanel(QWidget):
    status_message = pyqtSignal(str)

    def __init__(
        self,
        model: TrafficMapModel,
        map_image: Path,
        camera_ids: Iterable[str],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("trafficMapPanel")
        self.setStyleSheet("""
            #trafficMapPanel { background-color: #22262a; color: #e5e8ea; }
            #trafficMapPanel QComboBox,
            #trafficMapPanel QLineEdit,
            #trafficMapPanel QSpinBox,
            #trafficMapPanel QDoubleSpinBox {
                background-color: #30363b;
                color: #eef2f4;
                border: 1px solid #4d565d;
                padding: 4px 6px;
            }
            #trafficMapPanel QPushButton {
                background-color: #343b40;
                color: #eef2f4;
                border: 1px solid #505a61;
                padding: 5px 9px;
            }
            #trafficMapPanel QPushButton:hover { background-color: #3d474d; }
            #trafficMapPanel QPushButton:checked {
                background-color: #176f7d;
                border-color: #29a9be;
            }
            #trafficMapPanel QTableWidget {
                background-color: #282d31;
                alternate-background-color: #2e3438;
                color: #dfe4e7;
                border: 1px solid #41494f;
                gridline-color: #3a4247;
            }
            #trafficMapPanel QHeaderView::section {
                background-color: #343b40;
                color: #cfd6da;
                border: none;
                border-bottom: 1px solid #4b555c;
                padding: 5px;
            }
        """)
        self.model = model
        self._default_map_image = Path(map_image)
        self._loading_camera = False
        self._loading_road = False
        self._editing_segment_id = ""
        initial_map_path = self._resolve_map_path(
            self.model.map_image_path or str(self._default_map_image)
        )
        self.canvas = TrafficMapCanvas(model, initial_map_path, self)
        default_map_path = self._default_map_image.resolve()
        if self.canvas.pixmap.isNull() and initial_map_path != default_map_path:
            self.canvas.load_background(default_map_path)
        self._build_ui(list(camera_ids))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(500)

    def _build_ui(self, camera_ids) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("道路态势")
        title.setFont(QFont("Microsoft YaHei", 13, QFont.Weight.DemiBold))
        title.setStyleSheet("color: #eef2f4;")
        header.addWidget(title)
        header.addStretch()
        self.background_checkbox = QCheckBox("显示底图")
        self.background_checkbox.setChecked(True)
        self.background_checkbox.setToolTip("关闭后仅显示道路拓扑和实时态势")
        self.background_checkbox.toggled.connect(self.canvas.set_background_visible)
        header.addWidget(self.background_checkbox)
        self.summary_label = QLabel("车辆 0  |  活跃道路 0")
        self.summary_label.setStyleSheet("color: #aeb8be;")
        header.addWidget(self.summary_label)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.addWidget(self.canvas)

        side = QWidget()
        side.setMinimumWidth(340)
        side.setMaximumWidth(440)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 0, 0, 0)
        side_layout.setSpacing(8)

        map_row = QHBoxLayout()
        map_label = QLabel("底图")
        map_label.setStyleSheet("color: #cfd6da;")
        map_row.addWidget(map_label)
        self.map_path_input = QLineEdit(
            self.model.map_image_path or str(self._default_map_image)
        )
        self.map_path_input.setPlaceholderText("选择底图文件")
        self.map_path_input.returnPressed.connect(self._reload_map_image)
        map_row.addWidget(self.map_path_input, 1)

        browse_map_button = QPushButton()
        browse_map_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        browse_map_button.setFixedSize(32, 32)
        browse_map_button.setToolTip("选择底图")
        browse_map_button.setAccessibleName("选择底图")
        browse_map_button.clicked.connect(self._browse_map_image)
        map_row.addWidget(browse_map_button)

        refresh_map_button = QPushButton()
        refresh_map_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        refresh_map_button.setFixedSize(32, 32)
        refresh_map_button.setToolTip("重新加载底图")
        refresh_map_button.setAccessibleName("重新加载底图")
        refresh_map_button.clicked.connect(self._reload_map_image)
        map_row.addWidget(refresh_map_button)
        side_layout.addLayout(map_row)

        self.editor_tabs = QTabWidget()
        self.editor_tabs.addTab(self._build_camera_editor(camera_ids), "摄像头")
        self.editor_tabs.addTab(self._build_road_editor(), "道路编辑")
        side_layout.addWidget(self.editor_tabs)

        stats_title = QLabel("路段统计")
        stats_title.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.DemiBold))
        stats_title.setStyleSheet("color: #e5e8ea; margin-top: 8px;")
        side_layout.addWidget(stats_title)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["路段", "方向", "车辆", "流量/分", "占用率"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        side_layout.addWidget(self.table, 1)

        reset_button = QPushButton("清除实时轨迹")
        reset_button.clicked.connect(self._reset_runtime)
        side_layout.addWidget(reset_button)

        splitter.addWidget(side)
        splitter.setSizes([860, 400])
        root.addWidget(splitter, 1)

        self.canvas.map_clicked.connect(self._place_camera)
        self.canvas.camera_clicked.connect(self.camera_combo.setCurrentText)
        self.canvas.segment_clicked.connect(self._select_road_from_canvas)
        self.canvas.road_drawn.connect(self._finish_road_drawing)
        if camera_ids:
            self._load_camera(camera_ids[0])
        if self.road_combo.count() > 0:
            self._load_road(self.road_combo.itemData(0))

    def _resolve_map_path(self, path_text: str) -> Path:
        if not path_text:
            return self._default_map_image.resolve()
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = self.model.config_path.parent / path
        return path.resolve()

    def _portable_map_path(self, path: Path) -> str:
        config_dir = self.model.config_path.parent.resolve()
        try:
            return str(path.resolve().relative_to(config_dir))
        except ValueError:
            return str(path.resolve())

    def _browse_map_image(self) -> None:
        current_path = self._resolve_map_path(self.map_path_input.text().strip())
        start_dir = current_path.parent if current_path.parent.is_dir() else self.model.config_path.parent
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择道路底图",
            str(start_dir),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;所有文件 (*)",
        )
        if path:
            self.map_path_input.setText(path)
            self._reload_map_image()

    def _reload_map_image(self) -> bool:
        path_text = self.map_path_input.text().strip()
        if not path_text:
            self.status_message.emit("请先设置底图路径")
            return False
        image_path = self._resolve_map_path(path_text)
        if not self.canvas.load_background(image_path):
            self.status_message.emit(f"底图加载失败: {image_path}")
            return False

        portable_path = self._portable_map_path(image_path)
        self.map_path_input.setText(portable_path)
        self.model.map_image_path = portable_path
        try:
            self.model.save()
        except OSError as error:
            self.status_message.emit(f"底图已加载，但路径保存失败: {error}")
            return True
        self.status_message.emit(f"底图已重新加载: {image_path.name}")
        return True

    def _build_camera_editor(self, camera_ids) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)
        form = QFormLayout()
        form.setSpacing(7)
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(camera_ids)
        self.camera_combo.currentTextChanged.connect(self._load_camera)
        form.addRow("视频源", self.camera_combo)

        self.segment_combo = QComboBox()
        for segment in self.model.segments.values():
            self.segment_combo.addItem(segment.name, segment.segment_id)
        self.segment_combo.currentIndexChanged.connect(self._apply_editor_values)
        form.addRow("关联道路", self.segment_combo)

        self.heading_spin = QSpinBox()
        self.heading_spin.setRange(0, 359)
        self.heading_spin.setSuffix("°")
        self.heading_spin.valueChanged.connect(self._apply_editor_values)
        form.addRow("朝向", self.heading_spin)

        self.range_spin = QDoubleSpinBox()
        self.range_spin.setRange(1.0, 50.0)
        self.range_spin.setDecimals(1)
        self.range_spin.setSuffix("%")
        self.range_spin.valueChanged.connect(self._apply_editor_values)
        form.addRow("覆盖距离", self.range_spin)

        self.coordinate_label = QLabel("--")
        self.coordinate_label.setStyleSheet("color: #9da8ae;")
        form.addRow("地图坐标", self.coordinate_label)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        self.place_button = QPushButton("在图上定位")
        self.place_button.setCheckable(True)
        self.place_button.setToolTip("启用后，在沙盘地图中单击摄像头的实际位置")
        self.place_button.toggled.connect(self.canvas.set_placement_mode)
        button_row.addWidget(self.place_button)

        save_button = QPushButton("保存")
        save_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        save_button.setToolTip("保存摄像头位置、朝向、覆盖距离和关联道路")
        save_button.clicked.connect(self._save)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)
        return page

    def _build_road_editor(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 6, 6, 6)

        form = QFormLayout()
        form.setSpacing(7)
        self.road_combo = QComboBox()
        for segment in self.model.segments.values():
            self.road_combo.addItem(segment.name, segment.segment_id)
        self.road_combo.currentIndexChanged.connect(self._load_road_from_combo)
        form.addRow("道路", self.road_combo)

        self.road_id_input = QLineEdit()
        self.road_id_input.setReadOnly(True)
        form.addRow("道路 ID", self.road_id_input)

        self.road_name_input = QLineEdit()
        form.addRow("名称", self.road_name_input)

        self.road_level_combo = QComboBox()
        for label, value in (
            ("地面", "ground"),
            ("桥梁", "bridge"),
            ("隧道", "tunnel"),
            ("停车场", "parking"),
            ("功能区", "service"),
        ):
            self.road_level_combo.addItem(label, value)
        form.addRow("层级", self.road_level_combo)

        self.road_direction_combo = QComboBox()
        for direction in (
            "双向", "顺时针", "逆时针", "东行", "西行", "南行", "北行", "入口", "出口"
        ):
            self.road_direction_combo.addItem(direction, direction)
        form.addRow("方向", self.road_direction_combo)

        self.road_capacity_spin = QSpinBox()
        self.road_capacity_spin.setRange(1, 100)
        self.road_capacity_spin.setSuffix(" 辆")
        form.addRow("容量", self.road_capacity_spin)
        layout.addLayout(form)

        command_row = QHBoxLayout()
        new_button = QPushButton("新建")
        new_button.clicked.connect(self._new_road)
        command_row.addWidget(new_button)
        line_button = QPushButton("绘制直线")
        line_button.setToolTip("在地图上依次单击起点和终点；右键或 Esc 取消")
        line_button.clicked.connect(lambda: self._start_road_drawing("line"))
        command_row.addWidget(line_button)
        curve_button = QPushButton("绘制曲线")
        curve_button.setToolTip("在地图上依次单击起点、控制点和终点；右键或 Esc 取消")
        curve_button.clicked.connect(lambda: self._start_road_drawing("curve"))
        command_row.addWidget(curve_button)
        layout.addLayout(command_row)

        connection_row = QHBoxLayout()
        polyline_button = QPushButton("连续道路")
        polyline_button.setToolTip("依次添加连接节点，双击或按 Enter 完成绘制")
        polyline_button.clicked.connect(lambda: self._start_road_drawing("polyline"))
        connection_row.addWidget(polyline_button)
        self.finish_road_button = QPushButton("完成绘制")
        self.finish_road_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.finish_road_button.setEnabled(False)
        self.finish_road_button.clicked.connect(self._complete_road_drawing)
        connection_row.addWidget(self.finish_road_button)
        self.canvas.drawing_mode_changed.connect(
            lambda mode: self.finish_road_button.setEnabled(mode == "polyline")
        )
        layout.addLayout(connection_row)

        action_row = QHBoxLayout()
        apply_button = QPushButton("保存属性")
        apply_button.clicked.connect(self._save_road_properties)
        action_row.addWidget(apply_button)
        delete_button = QPushButton("删除")
        delete_button.clicked.connect(self._delete_road)
        action_row.addWidget(delete_button)
        layout.addLayout(action_row)
        return page

    def _refresh_road_controls(self, selected_id: str = "") -> None:
        camera_id = self.camera_combo.currentText()
        camera_segment = self.model.ensure_camera(camera_id).segment_id if camera_id else ""

        self.segment_combo.blockSignals(True)
        self.segment_combo.clear()
        for segment in self.model.segments.values():
            self.segment_combo.addItem(segment.name, segment.segment_id)
        camera_index = self.segment_combo.findData(camera_segment)
        if camera_index >= 0:
            self.segment_combo.setCurrentIndex(camera_index)
        self.segment_combo.blockSignals(False)

        self.road_combo.blockSignals(True)
        self.road_combo.clear()
        for segment in self.model.segments.values():
            self.road_combo.addItem(segment.name, segment.segment_id)
        selected_index = self.road_combo.findData(selected_id)
        self.road_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.road_combo.blockSignals(False)
        if self.road_combo.count() > 0:
            self._load_road(self.road_combo.currentData())

    def _load_road_from_combo(self, index: int) -> None:
        if index >= 0:
            self._load_road(self.road_combo.itemData(index))

    def _load_road(self, segment_id: str) -> None:
        segment = self.model.segments.get(segment_id)
        if segment is None:
            return
        self._loading_road = True
        self._editing_segment_id = segment_id
        self.road_id_input.setText(segment.segment_id)
        self.road_name_input.setText(segment.name)
        self.road_capacity_spin.setValue(segment.capacity)
        level_index = self.road_level_combo.findData(segment.level)
        if level_index < 0:
            self.road_level_combo.addItem(segment.level, segment.level)
            level_index = self.road_level_combo.findData(segment.level)
        self.road_level_combo.setCurrentIndex(level_index)
        direction_index = self.road_direction_combo.findData(segment.direction)
        if direction_index < 0:
            self.road_direction_combo.addItem(segment.direction, segment.direction)
            direction_index = self.road_direction_combo.findData(segment.direction)
        self.road_direction_combo.setCurrentIndex(direction_index)
        self.canvas.set_selected_segment(segment_id)
        self._loading_road = False

    def _select_road_from_canvas(self, segment_id: str) -> None:
        index = self.road_combo.findData(segment_id)
        if index >= 0:
            self.road_combo.setCurrentIndex(index)
            self.editor_tabs.setCurrentIndex(1)
        self._select_segment_in_table(segment_id)

    def _select_segment_in_table(self, segment_id: str) -> None:
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == segment_id:
                self.table.selectRow(row)
                self.table.setCurrentItem(item)
                self.table.scrollToItem(
                    item, QAbstractItemView.ScrollHint.PositionAtCenter
                )
                return

    def _new_road(self) -> None:
        self._editing_segment_id = ""
        self.road_combo.blockSignals(True)
        self.road_combo.setCurrentIndex(-1)
        self.road_combo.blockSignals(False)
        segment_id = self.model.next_segment_id()
        self.road_id_input.setText(segment_id)
        self.road_name_input.setText(f"新建道路 {segment_id.split('_')[-1]}")
        self.road_capacity_spin.setValue(4)
        self.road_level_combo.setCurrentIndex(0)
        self.road_direction_combo.setCurrentIndex(0)
        self.canvas.set_selected_segment("")
        self.status_message.emit("新道路属性已准备，可选择直线、曲线或连续道路开始绘制")

    def _start_road_drawing(self, mode: str) -> None:
        if not self.road_id_input.text():
            self._new_road()
        self.place_button.setChecked(False)
        self.canvas.start_road_drawing(mode)
        if mode == "polyline":
            self.status_message.emit(
                "请依次添加道路节点，双击、按 Enter 或点击完成绘制；Backspace 撤销节点"
            )
        else:
            needed = "起点和终点" if mode == "line" else "起点、控制点和终点"
            self.status_message.emit(f"请在地图上依次选择{needed}；右键或 Esc 取消")

    def _complete_road_drawing(self) -> None:
        if not self.canvas.finish_road_drawing():
            self.status_message.emit("连续道路至少需要两个节点")

    def _finish_road_drawing(self, points) -> None:
        segment_id = self.road_id_input.text().strip() or self.model.next_segment_id()
        try:
            segment = self.model.upsert_segment(
                segment_id=segment_id,
                name=self.road_name_input.text(),
                points=points,
                capacity=self.road_capacity_spin.value(),
                level=self.road_level_combo.currentData(),
                direction=self.road_direction_combo.currentData(),
            )
            self.model.save()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "道路保存失败", str(error))
            return
        self._editing_segment_id = segment.segment_id
        self._refresh_road_controls(segment.segment_id)
        self.refresh()
        self.status_message.emit(f"道路“{segment.name}”已绘制并保存")

    def _save_road_properties(self) -> None:
        segment = self.model.segments.get(self._editing_segment_id)
        if segment is None:
            self.status_message.emit("请先绘制新道路，或选择已有道路")
            return
        try:
            updated = self.model.upsert_segment(
                segment_id=segment.segment_id,
                name=self.road_name_input.text(),
                points=segment.points,
                capacity=self.road_capacity_spin.value(),
                level=self.road_level_combo.currentData(),
                direction=self.road_direction_combo.currentData(),
            )
            self.model.save()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "道路保存失败", str(error))
            return
        self._refresh_road_controls(updated.segment_id)
        self.refresh()
        self.status_message.emit(f"道路“{updated.name}”属性已保存")

    def _delete_road(self) -> None:
        segment = self.model.segments.get(self._editing_segment_id)
        if segment is None:
            return
        reply = QMessageBox.question(
            self,
            "删除道路",
            f"确定删除道路“{segment.name}”吗？关联摄像头将自动切换到最近道路。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self.model.delete_segment(segment.segment_id):
            QMessageBox.warning(self, "无法删除", "至少需要保留一条道路。")
            return
        self.model.save()
        self._editing_segment_id = ""
        self._refresh_road_controls()
        self._load_camera(self.camera_combo.currentText())
        self.refresh()
        self.status_message.emit(f"道路“{segment.name}”已删除")

    def _load_camera(self, camera_id: str) -> None:
        if not camera_id:
            return
        self._loading_camera = True
        camera = self.model.ensure_camera(camera_id)
        self.canvas.set_selected_camera(camera_id)
        self.heading_spin.setValue(round(camera.heading))
        self.range_spin.setValue(camera.view_range * 100.0)
        index = self.segment_combo.findData(camera.segment_id)
        if index >= 0:
            self.segment_combo.setCurrentIndex(index)
        self.coordinate_label.setText(f"x={camera.x:.3f}, y={camera.y:.3f}")
        self._loading_camera = False

    def _apply_editor_values(self) -> None:
        if self._loading_camera:
            return
        camera_id = self.camera_combo.currentText()
        if not camera_id:
            return
        camera = self.model.ensure_camera(camera_id)
        self.model.set_camera(
            camera_id,
            camera.x,
            camera.y,
            heading=self.heading_spin.value(),
            view_range=self.range_spin.value() / 100.0,
            segment_id=self.segment_combo.currentData(),
        )
        self.canvas.update()

    def _place_camera(self, x: float, y: float) -> None:
        camera_id = self.camera_combo.currentText()
        if not camera_id:
            return
        segment_id, _, _ = self.model.nearest_segment((x, y))
        self.model.set_camera(
            camera_id,
            x,
            y,
            heading=self.heading_spin.value(),
            view_range=self.range_spin.value() / 100.0,
            segment_id=segment_id,
        )
        index = self.segment_combo.findData(segment_id)
        if index >= 0:
            self.segment_combo.setCurrentIndex(index)
        self.coordinate_label.setText(f"x={x:.3f}, y={y:.3f}")
        self.place_button.setChecked(False)
        self.canvas.update()
        self.status_message.emit(f"已更新 {camera_id} 的地图位置，点击保存写入配置")

    def _save(self) -> None:
        try:
            self.model.save()
            self.status_message.emit(f"摄像头与道路配置已保存: {self.model.config_path.name}")
        except OSError as error:
            self.status_message.emit(f"配置保存失败: {error}")

    def _reset_runtime(self) -> None:
        self.model.reset_runtime()
        self.refresh()
        self.status_message.emit("实时轨迹与热度已清除")

    def set_active_camera(self, camera_id: str) -> None:
        if self.camera_combo.findText(camera_id) >= 0:
            self.camera_combo.setCurrentText(camera_id)
        self.canvas.set_selected_camera(camera_id)

    def update_detections(self, camera_id: str, detections, frame_size: Tuple[int, int]) -> None:
        self.model.update_detections(camera_id, detections, frame_size)
        self.refresh()

    def refresh(self) -> None:
        states = self.model.segment_states()
        self.canvas.segment_states = states
        self.canvas.update()
        active = sum(1 for state in states.values() if state.vehicle_count > 0)
        self.summary_label.setText(f"车辆 {len(self.model.tracks)}  |  活跃道路 {active}")
        ordered = sorted(
            self.model.segments.values(),
            key=lambda segment: (-states[segment.segment_id].vehicle_count, segment.name),
        )
        self.table.setRowCount(len(ordered))
        for row, segment in enumerate(ordered):
            state = states[segment.segment_id]
            values = [
                segment.name,
                segment.direction,
                str(state.vehicle_count),
                str(state.flow_per_minute),
                f"{state.occupancy:.0%}",
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, segment.segment_id)
                if column > 0:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, column, item)
        self._select_segment_in_table(self.canvas.selected_segment)
