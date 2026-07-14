from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
APP_SCRIPT = (ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
API_SCRIPT = (ROOT / "frontend" / "js" / "api.js").read_text(encoding="utf-8")
SCRIPT = (ROOT / "frontend" / "js" / "system-management.js").read_text(
    encoding="utf-8"
)


def test_model_pipeline_and_device_tabs_have_semantic_panels():
    for key, label in (
        ("model-pipelines", "模型配置"),
        ("devices", "设备管理"),
    ):
        assert f'data-system-tab="{key}"' in INDEX
        assert f'>{label}</button>' in INDEX
        assert f'id="system-panel-{key}"' in INDEX
        assert f'data-system-panel="{key}"' in INDEX
        assert f'data-system-state="{key}"' in INDEX
        assert f'data-system-content="{key}"' in INDEX

    assert 'id="system-model-pipeline-form"' in INDEX
    assert 'id="system-model-pipeline-scenes"' in INDEX
    assert 'id="system-model-pipeline-save"' in INDEX
    assert 'id="system-device-streams-body"' in INDEX


def test_tab_loader_has_explicit_model_and_device_branches():
    assert 'else if (key === "model-pipelines") await loadModelPipelines();' in SCRIPT
    assert 'else if (key === "devices") await loadDevices();' in SCRIPT
    assert '"model-pipelines"' in SCRIPT
    assert '"devices"' in SCRIPT


def test_model_pipeline_save_builds_a_complete_fixed_scene_payload():
    assert "const MODEL_PIPELINE_SCENES = [" in SCRIPT
    for scene_key in ("realtime", "traffic_map", "no_parking", "road_abnormal"):
        assert f'key: "{scene_key}"' in SCRIPT
    assert "settings: MODEL_PIPELINE_SCENES.map" in SCRIPT
    assert "readModelPipelineSetting(scene.key)" in SCRIPT
    for field in (
        "scene_key",
        "preset",
        "enabled",
        "device_preference",
        "yolo_threshold",
        "lpr_threshold",
        "frame_interval",
        "inference_size",
        "parking_move_threshold",
        "mog_history",
        "mog_variance_threshold",
        "mog_min_area",
        "mog_min_duration",
        "mog_max_duration",
        "mog_warmup_frames",
    ):
        assert f"{field}:" in SCRIPT


def test_device_polling_follows_tab_and_document_visibility_lifecycle():
    assert "const DEVICE_STREAM_KEYS = [" in SCRIPT
    for scene_key in ("realtime", "traffic_map", "no_parking", "road_abnormal"):
        assert f'key: "{scene_key}"' in SCRIPT
    assert "function startDevicePolling()" in SCRIPT
    assert "function stopDevicePolling()" in SCRIPT
    assert 'if (key === "devices") startDevicePolling();' in SCRIPT
    assert "else stopDevicePolling();" in SCRIPT
    assert 'document.addEventListener("visibilitychange"' in SCRIPT
    assert "window.setInterval" in SCRIPT
    assert "window.clearInterval" in SCRIPT


def test_model_management_never_exposes_weight_paths_or_password_controls():
    combined = f"{INDEX}\n{SCRIPT}".lower()
    assert "vehicle_model_path" not in combined
    assert "plate_model_path" not in combined
    assert "license_plate_best.pt" not in combined
    assert "yolo26x.pt" not in combined
    assert 'name="model_path"' not in combined
    assert 'name="password"' not in combined


def test_realtime_monitor_has_no_duplicate_model_configuration_editor():
    assert 'data-monitor-tab="settings"' not in INDEX
    assert 'id="monitor-settings-pane"' not in INDEX
    assert 'id="device-select"' not in INDEX
    assert 'id="yolo-threshold"' not in INDEX
    assert 'id="lpr-threshold"' not in INDEX
    assert 'id="detect-interval"' not in INDEX
    assert 'id="detection-toggle"' not in INDEX

    for stale_binding in (
        "detectionToggle:",
        "settingsForm:",
        "deviceSelect:",
        "yoloThreshold:",
        "lprThreshold:",
        "detectInterval:",
        "settingsDirty:",
        "function updateRangeOutputs()",
        "function saveDetectionSettings(",
    ):
        assert stale_binding not in APP_SCRIPT


def test_model_pipeline_form_accepts_defaults_and_reveals_invalid_advanced_fields():
    assert (
        'modelPipelineNumberInput("parking_move_threshold", '
        'setting.parking_move_threshold, { min: 0.001, max: 1, step: 0.001 })'
        in SCRIPT
    )
    assert 'elements.modelPipelineForm.addEventListener("invalid"' in SCRIPT
    assert "details.open = true;" in SCRIPT


def test_no_parking_scene_management_uses_global_camera_semantics():
    assert 'const topologyBound = sceneType === "road_abnormal";' in SCRIPT
    assert 'const relationshipText = topologyBound' in SCRIPT
    assert ': "全局摄像头";' in SCRIPT
    assert 'statusCell("ready", "无需拓扑复核")' in SCRIPT
    assert (
        'sceneType !== "no_parking" && reviewStatus === "needs_review"'
        in SCRIPT
    )
    assert 'if (scene.scene_type === "road_abnormal") {' in SCRIPT
    assert 'details.push(`拓扑：' in SCRIPT
    assert "<th>关联范围</th>" in INDEX
    assert "<th>拓扑与修订</th>" not in INDEX


def test_api_client_displays_structured_configuration_errors_first():
    structured = 'typeof payload.error?.message === "string"'
    detail = 'typeof payload.detail === "string"'
    assert structured in API_SCRIPT
    assert detail in API_SCRIPT
    assert API_SCRIPT.index(structured) < API_SCRIPT.index(detail)
