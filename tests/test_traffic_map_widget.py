import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPointF, Qt
from PyQt6.QtGui import QColor, QImage
from PyQt6.QtWidgets import QApplication

from traffic_map import TrafficMapModel
from traffic_map_widget import TrafficMapCanvas, TrafficMapPanel


def test_canvas_road_click_locates_matching_stats_row(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    model = TrafficMapModel(tmp_path / "traffic_map.json", ["道路1"])
    panel = TrafficMapPanel(model, tmp_path / "missing-map.png", ["道路1"])
    panel._timer.stop()
    panel.refresh()

    segment_id = "service_clockwise"
    panel.canvas.segment_clicked.emit(segment_id)

    selected_item = panel.table.item(panel.table.currentRow(), 0)
    assert selected_item.data(Qt.ItemDataRole.UserRole) == segment_id
    assert selected_item.text() == model.segments[segment_id].name
    assert panel.editor_tabs.currentIndex() == 1

    panel.refresh()
    refreshed_item = panel.table.item(panel.table.currentRow(), 0)
    assert refreshed_item.data(Qt.ItemDataRole.UserRole) == segment_id

    panel.close()
    app.processEvents()


def test_background_can_be_hidden_without_hiding_topology(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    model = TrafficMapModel(tmp_path / "traffic_map.json")
    canvas = TrafficMapCanvas(model, tmp_path / "missing-map.png")

    assert canvas.show_background
    assert model.segments

    canvas.set_background_visible(False)

    assert not canvas.show_background
    assert model.segments["north_eastbound"].direction == "东行"

    canvas.close()
    app.processEvents()


def test_map_path_can_be_saved_and_refreshed(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    config_path = tmp_path / "traffic_map.json"
    image_path = tmp_path / "custom-map.png"
    image = QImage(24, 16, QImage.Format.Format_RGB32)
    image.fill(QColor("#c62828"))
    assert image.save(str(image_path))

    model = TrafficMapModel(config_path)
    panel = TrafficMapPanel(model, tmp_path / "missing-map.png", [])
    panel._timer.stop()
    panel.map_path_input.setText(str(image_path))

    assert panel._reload_map_image()
    assert panel.canvas.pixmap.size().width() == 24
    assert panel.canvas.pixmap.size().height() == 16
    assert model.map_image_path == "custom-map.png"

    replacement = QImage(40, 20, QImage.Format.Format_RGB32)
    replacement.fill(QColor("#1565c0"))
    assert replacement.save(str(image_path))

    assert panel._reload_map_image()
    assert panel.canvas.pixmap.size().width() == 40
    assert panel.canvas.pixmap.size().height() == 20
    assert TrafficMapModel(config_path).map_image_path == "custom-map.png"

    panel.close()
    app.processEvents()


def test_camera_placement_mode_only_draws_an_outline(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    model = TrafficMapModel(
        tmp_path / "traffic_map.json", ["道路1", "道路2"]
    )
    canvas = TrafficMapCanvas(model, tmp_path / "missing-map.png")
    canvas.resize(600, 600)
    canvas.set_selected_camera("道路1")

    before = QImage(canvas.size(), QImage.Format.Format_RGB32)
    canvas.render(before)

    canvas.set_placement_mode(True)
    after = QImage(canvas.size(), QImage.Format.Format_RGB32)
    canvas.render(after)

    center = canvas._content_rect().center().toPoint()
    assert after.pixelColor(center) == before.pixelColor(center)

    canvas.close()
    app.processEvents()


def test_camera_hover_uses_canvas_hint_instead_of_system_tooltip(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    model = TrafficMapModel(tmp_path / "traffic_map.json", ["道路1"])
    canvas = TrafficMapCanvas(model, tmp_path / "missing-map.png")
    canvas.resize(600, 600)

    canvas._show_camera_hint(QPointF(100, 100), "道路1")

    assert not canvas._camera_hint.isHidden()
    assert "道路1" in canvas._camera_hint.text()
    assert "background-color: #30383e" in canvas._camera_hint.styleSheet()

    canvas.leaveEvent(QEvent(QEvent.Type.Leave))
    assert canvas._camera_hint.isHidden()

    canvas.close()
    app.processEvents()


def test_continuous_road_drawing_emits_all_connected_points(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    model = TrafficMapModel(tmp_path / "traffic_map.json")
    canvas = TrafficMapCanvas(model, tmp_path / "missing-map.png")
    emitted = []
    canvas.road_drawn.connect(emitted.append)
    points = [
        (0.1, 0.1),
        (0.2, 0.3),
        (0.45, 0.25),
        (0.7, 0.6),
        (0.9, 0.8),
    ]

    canvas.start_road_drawing("polyline")
    canvas.drawing_points.extend(points)

    assert canvas.finish_road_drawing()
    assert emitted == [points]
    assert canvas.drawing_mode == ""
    assert canvas.drawing_points == []

    canvas.close()
    app.processEvents()


def test_existing_curve_drawing_still_generates_smooth_path(tmp_path):
    app = QApplication.instance() or QApplication(sys.argv)
    model = TrafficMapModel(tmp_path / "traffic_map.json")
    canvas = TrafficMapCanvas(model, tmp_path / "missing-map.png")
    emitted = []
    canvas.road_drawn.connect(emitted.append)

    canvas.start_road_drawing("curve")
    canvas.drawing_points.extend([(0.1, 0.2), (0.5, 0.8), (0.9, 0.2)])

    assert canvas.finish_road_drawing()
    assert len(emitted[0]) == 25
    assert emitted[0][0] == (0.1, 0.2)
    assert emitted[0][-1] == (0.9, 0.2)

    canvas.close()
    app.processEvents()
