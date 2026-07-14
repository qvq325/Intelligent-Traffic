# Model Pipelines And Device Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add centralized per-scene trusted pipeline switching and device/stream health monitoring without breaking existing scene or configuration workflows.

**Architecture:** Persist one strict row for each fixed scene, resolve it through a trusted model registry, and inject immutable runtime options into the existing capture/monitor components. Add a read-only metrics service and two focused system-management tabs; keep legacy APIs and package imports compatible.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, SQLite, Ultralytics, OpenCV, psutil, NVML, vanilla JavaScript/CSS, pytest.

---

### Task 1: Schema V2 And Strict Configuration Contract

**Files:**
- Modify: `backend/configuration/schema.py`
- Modify: `backend/configuration/repository.py`
- Modify: `backend/configuration/models.py`
- Test: `tests/test_model_pipeline_configuration.py`

- [x] Write tests that initialize a new repository with exactly four model-pipeline rows, migrate a schema-version-1 database without losing existing rows, reject future versions, and validate complete four-scene Pydantic payloads while rejecting unknown fields, duplicate/missing scenes, invalid presets, and out-of-range values.
- [x] Run `uv run pytest tests/test_model_pipeline_configuration.py -q` and confirm failures identify the missing table, migration, and request model.
- [x] Add schema version 2, the constrained `model_pipeline_setting` table, explicit `1 -> 2` migration, default seeding from `detection_settings`, and `ModelPipelineConfiguration`/`ModelPipelineBatchUpdate` models.
- [x] Re-run the focused test and `tests/test_configuration_repository.py` until both pass.

### Task 2: Trusted Registry And Atomic Service

**Files:**
- Create: `backend/model_pipelines.py`
- Modify: `backend/configuration/service.py`
- Modify: `backend/configuration/router.py`
- Modify: `backend/configuration/__init__.py`
- Test: `tests/test_model_pipeline_service.py`

- [x] Write tests for fixed preset metadata, trusted absolute path resolution, missing/wrong-task weight rejection through an injected inspector, ordered reads, atomic four-row updates, audit redaction, and `GET/PUT /api/config/model-pipelines` contracts.
- [x] Run the focused tests and confirm they fail because the registry/service/routes do not exist.
- [x] Implement `ModelPipelineRegistry`, `ConfigurationService.model_pipeline_settings`, `ConfigurationService.update_model_pipeline_settings`, and static router endpoints before dynamic routes. Validate every row before opening the transaction and never include model binary details in audit summaries.
- [x] Re-run service/API tests plus existing configuration API tests.

### Task 3: Configurable Detection Processor And Plate Adapter

**Files:**
- Modify: `vehicle_detector.py`
- Modify: `lpr_recognizer.py`
- Modify: `detection_processor.py`
- Create: `trained_plate_recognizer.py`
- Test: `tests/test_model_pipeline_runtime.py`

- [x] Write tests proving custom vehicle weight and inference size reach Ultralytics, legacy LPR remains the default, and trained box detections are cropped and converted into the existing `PlateRecognition` contract through injected detector/OCR doubles.
- [x] Run the focused tests and observe expected constructor/adapter failures.
- [x] Extend detector/processor constructors with explicit model options, add inference size to prediction, and implement `BoxPlateRecognizer` using the trusted detection weight and existing OCR decoding boundary.
- [x] Re-run focused and existing detection-pipeline tests.

### Task 4: Runtime Processor Replacement

**Files:**
- Modify: `backend/video_stream.py`
- Modify: `backend/state.py`
- Test: `tests/test_video_service.py`
- Test: `tests/test_model_pipeline_runtime.py`

- [x] Write tests showing each stream has a scene key, a pipeline revision rebuilds only its processor, four scene updates are applied to the matching services, and a failed replacement preserves the previous initialized processor.
- [x] Run the focused tests and verify the missing runtime API failures.
- [x] Add immutable processor options and an injectable factory to `VideoStreamService`; build replacements outside the condition lock and swap on success. Add `ApplicationState.apply_model_pipeline_settings` and load it during bootstrap/reload.
- [x] Re-run video/state/API regression tests.

### Task 5: Trained No-Parking And Road-Abnormal Algorithms

**Files:**
- Modify: `backend/no_parking.py`
- Modify: `backend/road_abnormal.py`
- Create: `backend/trained_mog.py`
- Test: `tests/test_no_parking.py`
- Test: `tests/test_road_abnormal.py`

- [x] Write tests proving legacy parking still triggers after dwell, trained parking rejects a moving track and accepts a stationary one, legacy road foreground remains available, and trained mode delegates to a per-stream seven-layer engine while preserving existing event payloads.
- [x] Run both focused suites and confirm trained-mode expectations fail.
- [x] Add bounded anchor history and pipeline options to `NoParkingMonitor`; port the headless `MOGAnomalyEngine` behavior behind a small adapter and make `RoadAbnormalMonitor` swap detector/foreground strategy on pipeline revision.
- [x] Re-run both focused suites and runtime integration tests.

### Task 6: Device Monitoring API

**Files:**
- Create: `backend/device_monitor.py`
- Modify: `backend/state.py`
- Modify: `backend/configuration/router.py`
- Test: `tests/test_device_monitor.py`
- Test: `tests/test_configuration_api.py`

- [x] Write tests using injected psutil/NVML providers for CPU, memory, process, multiple GPUs, NVML absence, and sanitized four-stream statuses; add API response assertions.
- [x] Run focused tests and confirm the service/route are missing.
- [x] Implement `DeviceMonitor.snapshot()` with per-provider degradation and register `GET /api/config/devices` using the four runtime service status providers.
- [x] Re-run focused and API regression suites.

### Task 7: Optional Package Document

**Files:**
- Modify: `backend/configuration/package.py`
- Test: `tests/test_configuration_contracts.py`

- [x] Write tests that new exports include `model-pipelines.json`, imports apply it atomically, and old packages lacking it retain current rows.
- [x] Run the focused tests and confirm export/import expectations fail.
- [x] Add the optional document without adding it to legacy required-document keys; validate it with the same strict model and include its rows in restore transactions.
- [x] Re-run package, repository, and configuration API suites.

### Task 8: Model And Device Management UI

**Files:**
- Modify: `frontend/index.html`
- Modify: `frontend/js/system-management.js`
- Modify: `frontend/styles.css`
- Create: `tests/test_system_management_frontend.py`

- [x] Write source-contract tests for the two tabs, explicit `ensureTabLoaded` branches, complete four-scene save payload, device polling lifecycle, stable stream rows, and password/path absence.
- [x] Run the focused test and confirm missing markup/functions.
- [x] Add semantic tab panels, compact field controls, scene-specific advanced fields, device meters/tables, save/error states, and polling only while the device tab is active. Preserve all existing action dispatch and tab keyboard behavior.
- [x] Run the frontend contract test and `node --check frontend/js/system-management.js`.

### Task 9: Full Verification And Visual Acceptance

**Files:**
- Verify all modified files; no Git commit.

- [x] Run `uv run pytest -q` and require all tests to pass without warnings introduced by this change.
- [x] Run `node --check frontend/js/system-management.js` and any other changed JavaScript modules.
- [x] Run `git diff --check`.
- [ ] Run GitNexus change detection and confirm affected symbols/processes match configuration, runtime detection, road/no-parking monitoring, and system-management UI only.
- [x] Restart the local service on an unused port, open a connectable Chrome instance if needed, and inspect model/device tabs at desktop and mobile widths without activating or changing production streams.

> GitNexus verification note: `detect_changes(scope=all)` was run on 2026-07-14 and reported CRITICAL (331 indexed symbols, 48 processes, 26 tracked files). The aggregate includes pre-existing uncommitted `app.py`, preview-stream, topology, and scene workflow changes that were already present before this continuation, so the narrower "only" assertion cannot be made without first isolating that earlier work.
