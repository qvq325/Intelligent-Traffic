import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

import main
from main import MainWindow, VideoCaptureThread


def test_video_thread_pause_state_and_source_switch():
    thread = VideoCaptureThread()

    assert not thread.is_paused()

    thread.set_paused(True)
    assert thread.is_paused()

    thread.set_paused(False)
    assert not thread.is_paused()

    thread.set_paused(True)
    thread.set_url("example.mp4", "本地视频")
    assert not thread.is_paused()


def test_monitor_pause_button_toggles_video_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DEFAULT_TRAFFIC_MAP_FILE", str(tmp_path / "traffic_map.json"))
    monkeypatch.setattr(main, "DEFAULT_WHITELIST_FILE", str(tmp_path / "whitelist.json"))
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    window._current_source_url = "example.mp4"
    window._current_display_name = "example.mp4"
    window._btn_pause.setEnabled(True)

    window._on_pause_clicked()
    assert window._video_paused
    assert window._thread.is_paused()
    assert window._btn_pause.toolTip() == "继续播放"

    window._on_pause_clicked()
    assert not window._video_paused
    assert not window._thread.is_paused()
    assert window._btn_pause.toolTip() == "暂停视频"

    window.close()
    app.processEvents()
