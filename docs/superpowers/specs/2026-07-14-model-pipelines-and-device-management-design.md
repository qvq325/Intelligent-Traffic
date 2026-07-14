# Model Pipelines And Device Management Design

## Goal

Centralize the model and algorithm settings used by the four runtime scenes, allow each scene to switch between the existing and trained pipelines, and expose device and active-stream health in system management.

## Scope

The four fixed scene keys are `realtime`, `traffic_map`, `no_parking`, and `road_abnormal`. Each scene owns a complete persisted configuration instead of inheriting mutable global settings. Existing APIs and the legacy detection settings row remain available for compatibility.

The system exposes two trusted presets:

- `legacy` uses `yolo11m.pt`, the existing pose-based plate recognizer, the current dwell-only no-parking rule, and the current road-abnormal MOG2 implementation.
- `trained` uses `训练后模型/yolo26x.pt`, `训练后模型/license_plate_best.pt`, a box-detection-to-existing-OCR adapter, movement-qualified no-parking events, and the documented seven-layer MOG anomaly engine.

Users cannot upload weights or enter arbitrary filesystem paths. The registry resolves only project-owned trusted files and verifies their existence and model task before a preset is made selectable.

## Persistence And API

SQLite schema version 2 adds `model_pipeline_setting`, seeded with four rows during new database creation and the version 1 to 2 migration. Defaults preserve current behavior and inherit compatible threshold/device values from `detection_settings`.

`GET /api/config/model-pipelines` returns available presets/devices and all four complete scene rows. `PUT /api/config/model-pipelines` accepts exactly four complete rows, rejects unknown fields, duplicate/missing scenes, invalid parameter ranges, unavailable devices, and unavailable trusted weights. The update is one transaction with one audit record per changed scene.

`GET /api/config/devices` returns current CPU and memory utilization, process utilization, available GPU utilization/memory/temperature when NVML is usable, and sanitized status for the four `VideoStreamService` instances. RTSP URLs and credentials are never included.

New configuration packages export an optional `model-pipelines.json` document. Existing version 1 packages without that document remain importable and retain the current model-pipeline rows.

## Runtime Switching

`ModelPipelineRegistry` maps a persisted row to immutable runtime options and validates preset assets. `VideoStreamService` receives a processor factory plus a scene key. A configuration revision invalidates only the affected scene processor. The worker builds a replacement processor outside its state lock and swaps it only after successful initialization; failure leaves the previous processor active and reports a stable load error.

`DetectionProcessor` accepts model paths, inference size, and an LPR mode. The trained plate adapter detects boxes with the trusted plate YOLO model, crops them, and reuses the existing OCR network and result contract.

`NoParkingMonitor` receives its scene pipeline options. The trained rule records normalized anchor history and requires displacement below `parking_move_threshold` before opening an event. The legacy rule continues to use dwell time only.

`RoadAbnormalMonitor` receives its scene pipeline options. The trained preset switches its object detector weight and delegates foreground anomaly generation to an adapter around the documented `MOGAnomalyEngine`; both variants continue to feed the existing structured candidate/event persistence path.

## Frontend

System management gains two tabs: `模型配置` and `设备管理`. Model configuration uses one compact editable section per scene with a preset selector, enable toggle, device selector, thresholds, inference interval/size, and collapsible scene-specific MOG or movement settings. Password-like model paths are not displayed because paths are not user-editable.

Device management shows CPU, memory, process, GPU, and four stable stream rows. It refreshes while the tab is visible and stops polling when the user leaves the tab. Desktop controls wrap; mobile layouts stack and tables scroll horizontally without changing action-column dimensions.

## Errors And Safety

Validation continues to use `CONFIG_VALIDATION_ERROR`. Preset or device availability errors use `MODEL_PIPELINE_UNAVAILABLE`; runtime replacement failures are shown in stream status without destroying the working processor. Database writes are atomic. Monitoring failures degrade individual metric sections rather than failing the entire endpoint.

## Verification

Tests cover schema migration, strict request validation, atomic persistence, registry resolution, processor replacement, trained plate adaptation, trained stationary parking behavior, trained road MOG delegation, device metric degradation, API contracts, and frontend contracts. Completion requires the full pytest suite, JavaScript syntax checks, `git diff --check`, GitNexus change detection, and desktop/mobile browser inspection.
