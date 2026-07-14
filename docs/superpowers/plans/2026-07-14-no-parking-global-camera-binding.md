# No-Parking Global Camera Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make no-parking scenes bind only to one camera from the fixed 12-camera catalog, while keeping road-abnormal scenes topology-bound and migrating existing data automatically.

**Architecture:** Keep the shared `scene_archive` table, but make topology fields conditionally nullable: `no_parking` rows store no topology, while `road_abnormal` rows retain topology and revision requirements. Normalize legacy no-parking rows at database and package-import boundaries, then make activation and topology lifecycle behavior branch explicitly on `scene_type`.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite, pytest, vanilla JavaScript.

---

## File Map

- Modify `backend/configuration/schema.py`: schema v3 table definition and transactional v2-to-v3 migration SQL.
- Modify `backend/configuration/service.py`: scene normalization, camera validation, topology update/delete rules.
- Modify `backend/configuration/activation.py`: type-specific scene activation validation.
- Modify `backend/state.py`: persistence, topology runtime behavior, and package preflight behavior.
- Modify `backend/configuration/package.py`: legacy package normalization and type-specific validation.
- Modify `frontend/js/api.js`: display structured configuration error messages.
- Modify `frontend/js/system-management.js`: global-camera semantics for no-parking rows and activation dialogs.
- Modify `frontend/index.html`: rename the shared scene relationship column.
- Modify `tests/test_configuration_repository.py`: schema constraints and migration coverage.
- Modify `tests/test_activation_coordinator.py`: activation behavior coverage.
- Modify `tests/test_configuration_api.py`: package compatibility coverage.
- Modify `tests/test_system_management_frontend.py`: frontend contract coverage.
- Create `tests/test_scene_topology_independence.py`: focused runtime and service lifecycle tests.

### Task 1: Schema v3 and Automatic Data Migration

**Files:**
- Modify: `backend/configuration/schema.py:6-390`
- Modify: `tests/test_configuration_repository.py:1-410`

- [ ] **Step 1: Run symbol impact analysis before editing**

Run:

```powershell
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 SCHEMA_VERSION
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 SCHEMA_MIGRATIONS
```

Expected: no HIGH or CRITICAL result. If either result is HIGH or CRITICAL, report it before editing.

- [ ] **Step 2: Write failing schema and migration tests**

Update the core relation fixture so the no-parking row uses `NULL` topology fields, then add assertions equivalent to:

```python
repository.execute(
    connection,
    """
    INSERT INTO scene_archive (
        scene_id, scene_type, name, topology_id, topology_revision,
        camera_id, reference_asset_id, validated_config_json
    ) VALUES (
        'scene-1', 'no_parking', '禁停场景', NULL, NULL,
        'camera-b', 'scene-asset', '{"zones":[]}'
    )
    """,
)

with pytest.raises(sqlite3.IntegrityError):
    with repository.transaction() as connection:
        repository.execute(
            connection,
            """
            INSERT INTO scene_archive (
                scene_id, scene_type, name, topology_id, topology_revision,
                camera_id, validated_config_json
            ) VALUES (
                'bad-no-parking', 'no_parking', '错误绑定',
                'topology-1', 1, 'camera-b', '{"zones":[]}'
            )
            """,
        )
```

Add a real v2 migration test that creates a v2-shaped `scene_archive` containing one no-parking and one road-abnormal row, sets `schema_metadata.schema_version` and `PRAGMA user_version` to `2`, reopens the repository, and asserts:

```python
assert repository.fetch_one("PRAGMA user_version")[0] == 3

no_parking = repository.fetch_one(
    "SELECT topology_id, topology_revision, review_status FROM scene_archive WHERE scene_id = 'legacy-no-parking'"
)
assert tuple(no_parking) == (None, None, "ready")

road_abnormal = repository.fetch_one(
    "SELECT topology_id, topology_revision, review_status FROM scene_archive WHERE scene_id = 'legacy-road-abnormal'"
)
assert tuple(road_abnormal) == ("topology-1", 1, "needs_review")
assert repository.fetch_all("PRAGMA foreign_key_check") == []
```

- [ ] **Step 3: Run tests and verify the old schema fails**

Run:

```powershell
pytest tests/test_configuration_repository.py -q
```

Expected: failures because schema version is still 2, no-parking topology fields are non-null, and no v2-to-v3 migration exists.

- [ ] **Step 4: Implement the conditional scene table and migration**

Set `SCHEMA_VERSION = 3`. Define the scene table with nullable topology fields and a type-specific check:

```python
topology_id TEXT,
topology_revision INTEGER CHECK (
    topology_revision IS NULL OR topology_revision >= 1
),
camera_id TEXT NOT NULL,
review_status TEXT NOT NULL DEFAULT 'ready'
    CHECK (review_status IN ('ready', 'needs_review')),
CHECK (
    (
        scene_type = 'no_parking'
        AND topology_id IS NULL
        AND topology_revision IS NULL
        AND review_status = 'ready'
    )
    OR
    (
        scene_type = 'road_abnormal'
        AND topology_id IS NOT NULL
        AND topology_revision IS NOT NULL
    )
)
```

Add migration `2 -> 3` with this transaction-safe sequence:

```sql
CREATE TEMP TABLE _activation_state_v2_backup AS
SELECT * FROM activation_state;

DROP TABLE activation_state;

CREATE TABLE scene_archive_v3 (
    scene_id TEXT PRIMARY KEY,
    scene_type TEXT NOT NULL
        CHECK (scene_type IN ('no_parking', 'road_abnormal')),
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    topology_id TEXT,
    topology_revision INTEGER
        CHECK (topology_revision IS NULL OR topology_revision >= 1),
    camera_id TEXT NOT NULL,
    reference_asset_id TEXT,
    validated_config_json TEXT NOT NULL
        CHECK (json_valid(validated_config_json)),
    review_status TEXT NOT NULL DEFAULT 'ready'
        CHECK (review_status IN ('ready', 'needs_review')),
    created_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (
        (
            scene_type = 'no_parking'
            AND topology_id IS NULL
            AND topology_revision IS NULL
            AND review_status = 'ready'
        )
        OR
        (
            scene_type = 'road_abnormal'
            AND topology_id IS NOT NULL
            AND topology_revision IS NOT NULL
        )
    ),
    FOREIGN KEY (topology_id) REFERENCES topology_profile(topology_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (camera_id) REFERENCES camera(camera_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (reference_asset_id) REFERENCES asset(asset_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

INSERT INTO scene_archive_v3 (
    scene_id, scene_type, name, topology_id, topology_revision,
    camera_id, reference_asset_id, validated_config_json,
    review_status, created_at, updated_at
)
SELECT
    scene_id,
    scene_type,
    name,
    CASE WHEN scene_type = 'no_parking' THEN NULL ELSE topology_id END,
    CASE WHEN scene_type = 'no_parking' THEN NULL ELSE topology_revision END,
    camera_id,
    reference_asset_id,
    validated_config_json,
    CASE WHEN scene_type = 'no_parking' THEN 'ready' ELSE review_status END,
    created_at,
    updated_at
FROM scene_archive;

DROP TABLE scene_archive;
ALTER TABLE scene_archive_v3 RENAME TO scene_archive;

CREATE TABLE activation_state (
    singleton_id INTEGER PRIMARY KEY DEFAULT 1 CHECK (singleton_id = 1),
    stream_profile_id TEXT NOT NULL,
    topology_id TEXT NOT NULL,
    topology_revision INTEGER NOT NULL CHECK (topology_revision >= 1),
    no_parking_scene_id TEXT,
    road_abnormal_scene_id TEXT,
    updated_at TEXT NOT NULL DEFAULT
        (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY (stream_profile_id) REFERENCES stream_binding_profile(profile_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (topology_id) REFERENCES topology_profile(topology_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (no_parking_scene_id) REFERENCES scene_archive(scene_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    FOREIGN KEY (road_abnormal_scene_id) REFERENCES scene_archive(scene_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
);

INSERT INTO activation_state (
    singleton_id, stream_profile_id, topology_id, topology_revision,
    no_parking_scene_id, road_abnormal_scene_id, updated_at
)
SELECT
    singleton_id, stream_profile_id, topology_id, topology_revision,
    no_parking_scene_id, road_abnormal_scene_id, updated_at
FROM _activation_state_v2_backup;

DROP TABLE _activation_state_v2_backup;
```

Reuse shared column-definition constants for new-database and migration table creation so the constraints cannot drift.

- [ ] **Step 5: Run schema tests**

Run:

```powershell
pytest tests/test_configuration_repository.py -q
```

Expected: all tests pass, including v2-to-v3 migration and `PRAGMA foreign_key_check`.

- [ ] **Step 6: Commit the isolated schema task**

```powershell
git add backend/configuration/schema.py tests/test_configuration_repository.py
git commit -m "feat: decouple no-parking scenes from topology schema"
```

### Task 2: Persist Only the Global Camera Binding

**Files:**
- Modify: `backend/configuration/service.py:850-1065`
- Modify: `backend/state.py:700-745`
- Create: `tests/test_scene_topology_independence.py`

- [ ] **Step 1: Run impact analysis for every existing symbol to edit**

Run:

```powershell
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 upsert_scene_archive
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 persist_scene
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 delete_topology
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 update_topology
```

Expected: `persist_scene` reports the no-parking and road-abnormal save endpoints as direct callers; no result is HIGH or CRITICAL.

- [ ] **Step 2: Write failing persistence and topology lifecycle tests**

Create focused service tests with a temporary repository and assert:

```python
saved = service.upsert_scene_archive(
    {
        "scene_id": "no-parking-1",
        "scene_type": "no_parking",
        "name": "固定摄像头禁停",
        "topology_id": "legacy-topology",
        "topology_revision": 99,
        "camera_id": "camera-b",
        "reference_asset_id": None,
        "config": {"zones": []},
    }
)
assert saved["topology_id"] is None
assert saved["topology_revision"] is None
assert saved["review_status"] == "ready"
```

Add these error and lifecycle assertions:

```python
with pytest.raises(ConfigurationError) as caught:
    service.upsert_scene_archive(
        {
            "scene_type": "no_parking",
            "name": "未知摄像头",
            "camera_id": "camera-missing",
            "config": {"zones": []},
        }
    )
assert caught.value.code == "SCENE_CAMERA_INVALID"

current_topology = service.get_topology("topology-1")
updated_topology = {
    key: current_topology[key]
    for key in (
        "name",
        "map_asset_id",
        "map_width",
        "map_height",
        "nodes",
        "segments",
        "cameras",
    )
}
service.update_topology("topology-1", updated_topology)
assert service.get_scene("no-parking-1")["review_status"] == "ready"
assert service.get_activation_state()["no_parking_scene_id"] == "no-parking-1"
assert service.get_scene("road-abnormal-1")["review_status"] == "needs_review"
assert service.get_activation_state()["road_abnormal_scene_id"] is None
```

Verify deletion ignores no-parking rows but still rejects a road-abnormal reference with `TOPOLOGY_IN_USE`.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```powershell
pytest tests/test_scene_topology_independence.py -q
```

Expected: failures because persistence still copies the active topology and topology lifecycle SQL still targets both scene types.

- [ ] **Step 4: Implement scene normalization and explicit camera validation**

At the start of `upsert_scene_archive`, copy and normalize values:

```python
values = dict(values)
scene_type = values.get("scene_type")
camera_id = values.get("camera_id")
camera = self.repository.fetch_one(
    "SELECT camera_id FROM camera WHERE camera_id = ?",
    (camera_id,),
)
if camera is None:
    raise ConfigurationError(
        "SCENE_CAMERA_INVALID",
        "场景摄像头不在固定全局摄像头目录中",
        details=[{"camera_id": camera_id}],
    )
if scene_type == "no_parking":
    values["topology_id"] = None
    values["topology_revision"] = None
elif not values.get("topology_id") or values.get("topology_revision") is None:
    raise ConfigurationError(
        "SCENE_TOPOLOGY_REQUIRED",
        "道路异常场景必须绑定道路拓扑",
    )
```

Update insert and update SQL to accept the normalized nullable values.

In `ApplicationState.persist_scene`, write topology values conditionally:

```python
topology_id = state["topology_id"] if scene_type == "road_abnormal" else None
topology_revision = (
    state["topology_revision"] if scene_type == "road_abnormal" else None
)
```

- [ ] **Step 5: Restrict topology lifecycle SQL to road-abnormal scenes**

Change topology revision invalidation to:

```sql
UPDATE scene_archive
SET review_status = 'needs_review', updated_at = ?
WHERE scene_type = 'road_abnormal'
  AND topology_id = ?
  AND topology_revision < ?
```

When the active topology is updated, set only `road_abnormal_scene_id = NULL`. In `delete_topology`, query only:

```sql
SELECT scene_id, name
FROM scene_archive
WHERE scene_type = 'road_abnormal' AND topology_id = ?
```

- [ ] **Step 6: Run focused and repository tests**

Run:

```powershell
pytest tests/test_scene_topology_independence.py tests/test_configuration_repository.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit the persistence task**

```powershell
git add backend/configuration/service.py backend/state.py tests/test_scene_topology_independence.py
git commit -m "feat: bind no-parking scenes to global cameras"
```

### Task 3: Make Activation and Runtime Topology-Independent

**Files:**
- Modify: `backend/configuration/activation.py:610-700`
- Modify: `backend/state.py:604-680`
- Modify: `tests/test_activation_coordinator.py:275-330`
- Modify: `tests/test_scene_topology_independence.py`

- [ ] **Step 1: Run impact analysis before editing runtime symbols**

Run:

```powershell
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 activate_scene
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 apply_topology
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 persist_active_topology
```

Disambiguate CLI results against `backend/configuration/activation.py` and `backend/state.py` with `npx gitnexus context --repo VideoTest --file <path> <symbol>`. Do not edit if a HIGH or CRITICAL result appears before reporting it.

- [ ] **Step 2: Add failing activation tests**

Add a no-parking test whose legacy topology deliberately differs from activation state:

```python
class SuccessfulProbe:
    class Result:
        def as_dict(self):
            return {
                "stream_id": "stream-1",
                "ok": True,
                "code": "OK",
                "message": "ok",
                "elapsed_ms": 1,
            }

    def probe(self, *_args):
        return self.Result()


class SceneService(FakeService):
    def __init__(
        self,
        *,
        scene_type,
        topology_id,
        topology_revision,
        review_status,
    ):
        super().__init__()
        self.scene = {
            "scene_id": "scene-1",
            "scene_type": scene_type,
            "topology_id": topology_id,
            "topology_revision": topology_revision,
            "review_status": review_status,
            "camera_id": "camera-1",
        }

    def get_scene(self, _scene_id):
        return dict(self.scene)

    def resolve_camera_stream(self, _camera_id):
        return {
            "stream_id": "stream-1",
            "rtsp_url": "rtsp://example.test/1",
        }


class SceneRuntime(FakeRuntime):
    def __init__(self):
        super().__init__()
        self.activated = []

    def activate_scene_runtime(self, scene, _stream_url):
        self.activated.append(scene["scene_id"])
        return {"running": True}

    def deactivate_scene_runtime(self, _scene_type):
        return {"running": False}


def test_no_parking_activation_ignores_topology_and_review_status():
    service = SceneService(
        scene_type="no_parking",
        topology_id="legacy-topology",
        topology_revision=1,
        review_status="needs_review",
    )
    service.state.update(topology_id="current-topology", topology_revision=15)
    runtime = SceneRuntime()

    result = ActivationCoordinator(service, runtime, SuccessfulProbe()).activate_scene(
        "scene-1"
    )

    assert result["status"] == "succeeded"
    assert runtime.activated == ["scene-1"]
```

Add the paired regression:

```python
def test_road_abnormal_activation_still_requires_current_topology():
    service = SceneService(
        scene_type="road_abnormal",
        topology_id="legacy-topology",
        topology_revision=1,
        review_status="ready",
    )
    service.state.update(topology_id="current-topology", topology_revision=15)

    with pytest.raises(ConfigurationError) as caught:
        ActivationCoordinator(service, SceneRuntime(), SuccessfulProbe()).activate_scene(
            "scene-1"
        )

    assert caught.value.code == "SCENE_TOPOLOGY_MISMATCH"
```

In `tests/test_scene_topology_independence.py`, assert that `ApplicationState.apply_topology` deactivates only the active road-abnormal scene and that `persist_active_topology` does not call `no_parking.stop()` or `no_parking_video.stop_stream()`.

- [ ] **Step 3: Run focused tests and verify failure**

Run:

```powershell
pytest tests/test_activation_coordinator.py tests/test_scene_topology_independence.py -q
```

Expected: no-parking activation fails with `SCENE_NEEDS_REVIEW` or `SCENE_TOPOLOGY_MISMATCH`, and topology runtime tests show no-parking being stopped.

- [ ] **Step 4: Branch activation validation by scene type**

Wrap existing review and topology checks in:

```python
if scene["scene_type"] == "road_abnormal":
    if scene["review_status"] != "ready":
        raise ConfigurationError(
            code="SCENE_NEEDS_REVIEW",
            message="场景绑定的拓扑修订已变化，请重新确认后再启用",
            details=[{"scene_id": scene_id}],
        )
    if (
        scene["topology_id"] != state["topology_id"]
        or scene["topology_revision"] != state["topology_revision"]
    ):
        raise ConfigurationError(
            code="SCENE_TOPOLOGY_MISMATCH",
            message="场景与当前激活拓扑不兼容",
            details=[{"scene_id": scene_id}],
        )
```

Leave stream resolution, probing, rollback, and activation-state persistence shared by both types.

- [ ] **Step 5: Preserve no-parking runtime during topology changes**

In `ApplicationState.apply_topology`, inspect only `road_abnormal_scene_id`. In `persist_active_topology`, stop only:

```python
self.road_abnormal.stop()
self.road_abnormal_video.stop_stream()
```

Do not stop `self.no_parking` or `self.no_parking_video`.

- [ ] **Step 6: Run activation and runtime tests**

Run:

```powershell
pytest tests/test_activation_coordinator.py tests/test_scene_topology_independence.py -q
```

Expected: all tests pass; road-abnormal mismatch coverage remains green.

- [ ] **Step 7: Commit the activation task**

```powershell
git add backend/configuration/activation.py backend/state.py tests/test_activation_coordinator.py tests/test_scene_topology_independence.py
git commit -m "fix: keep no-parking active across topology changes"
```

### Task 4: Normalize Legacy Configuration Packages

**Files:**
- Modify: `backend/configuration/package.py:343-500,648-810,890-1000`
- Modify: `backend/state.py:313-410`
- Modify: `tests/test_configuration_api.py:500-720`

- [ ] **Step 1: Run impact analysis before editing import/export symbols**

Run:

```powershell
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 _validate_documents
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 _replace_user_configuration
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 preflight_configuration_package
```

Expected: configuration import/export and startup preflight are affected; no HIGH or CRITICAL result.

- [ ] **Step 2: Add failing export and legacy-import tests**

Export a package containing a no-parking scene and assert:

```python
with zipfile.ZipFile(io.BytesIO(package)) as archive:
    scenes = json.loads(archive.read("config/scene-archives.json"))["scenes"]
no_parking = next(item for item in scenes if item["scene_type"] == "no_parking")
assert no_parking["topology_id"] is None
assert no_parking["topology_revision"] is None
assert no_parking["review_status"] == "ready"
```

Rewrite the package with legacy topology values on the no-parking scene, import it, and assert the installed row is normalized to `(None, None, "ready")`. Add a road-abnormal package with an unknown topology and assert `CONFIG_REFERENCE_INVALID`.

- [ ] **Step 3: Run package tests and verify failure**

Run:

```powershell
pytest tests/test_configuration_api.py -q
```

Expected: legacy no-parking import is rejected or preserves old topology values.

- [ ] **Step 4: Normalize no-parking scene documents during validation**

After validating `scene_type` and `camera_id`, apply:

```python
if scene["scene_type"] == "no_parking":
    scene["topology_id"] = None
    scene["topology_revision"] = None
    scene["review_status"] = "ready"
elif scene["topology_id"] not in target_topologies:
    raise ConfigurationError(
        "CONFIG_REFERENCE_INVALID",
        "道路异常场景引用未知拓扑",
        details=[{"scene_id": scene["scene_id"]}],
    )
```

Keep all keys in the document so package schema version 1 remains backward compatible. `_export_scene` naturally emits `null` from normalized database rows, and `_replace_user_configuration` writes those nullable values unchanged.

- [ ] **Step 5: Make activation preflight type-specific**

In `ApplicationState.preflight_configuration_package`, replace the shared scene compatibility condition with:

```python
if scene["scene_type"] == "road_abnormal" and (
    scene["topology_id"] != activation["topology_id"]
    or int(scene["topology_revision"]) != int(activation["topology_revision"])
    or scene["review_status"] != "ready"
):
    raise ConfigurationError(
        "CONFIG_ACTIVATION_INVALID",
        "目标激活道路异常场景与拓扑修订不兼容",
        details=[{"scene_id": scene_id}],
    )
```

No-parking remains protected by fixed-camera validation and complete active stream-profile validation earlier in preflight.

- [ ] **Step 6: Run package and API tests**

Run:

```powershell
pytest tests/test_configuration_api.py tests/test_api.py -q
```

Expected: all tests pass, including old-package normalization and road-abnormal rejection.

- [ ] **Step 7: Commit the package task**

```powershell
git add backend/configuration/package.py backend/state.py tests/test_configuration_api.py
git commit -m "feat: normalize no-parking bindings in config packages"
```

### Task 5: Update System Management Semantics and Error Messages

**Files:**
- Modify: `frontend/index.html:805-815`
- Modify: `frontend/js/system-management.js:753-785,2205-2218`
- Modify: `frontend/js/api.js:9-20`
- Modify: `tests/test_system_management_frontend.py`

- [ ] **Step 1: Run impact analysis before editing frontend symbols**

Run:

```powershell
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 renderScenes
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 activateScene
npx gitnexus impact --repo VideoTest --direction upstream --depth 3 request
```

Expected: only system-management rendering/actions and API consumers are affected; report any HIGH or CRITICAL result before editing.

- [ ] **Step 2: Add failing static frontend contract tests**

Extend the frontend test module with:

```python
API_SCRIPT = (ROOT / "frontend" / "js" / "api.js").read_text(encoding="utf-8")


def test_no_parking_scene_management_uses_global_camera_semantics():
    assert 'sceneType === "no_parking"' in SCRIPT
    assert '"全局摄像头"' in SCRIPT
    assert '"无需拓扑复核"' in SCRIPT
    assert 'sceneType !== "no_parking" && reviewStatus === "needs_review"' in SCRIPT


def test_api_client_displays_structured_configuration_errors():
    assert "payload.error?.message" in API_SCRIPT
```

Also assert the scene table header contains `关联范围` instead of `拓扑与修订`.

- [ ] **Step 3: Run frontend tests and verify failure**

Run:

```powershell
pytest tests/test_system_management_frontend.py -q
```

Expected: failures because no-parking rows still show topology data and `api.js` ignores `error.message`.

- [ ] **Step 4: Render type-specific relationship and review cells**

In `renderScenes`, derive:

```javascript
const topologyBound = sceneType === "road_abnormal";
const relationshipText = topologyBound
  ? `${scene.topology_name || scene.topology_id || "--"} · r${scene.topology_revision || "--"}`
  : "全局摄像头";
const reviewCell = topologyBound
  ? statusCell(reviewStatus)
  : statusCell("ready", "无需拓扑复核");
```

Use `relationshipText` and `reviewCell` in the row. Disable activation with:

```javascript
disabled: active || (sceneType !== "no_parking" && reviewStatus === "needs_review")
```

In `activateScene`, build details as:

```javascript
const details = [`摄像头：${scene.camera_name || scene.camera_id || "--"}`];
if (scene.scene_type === "road_abnormal") {
  details.push(`拓扑：${scene.topology_name || scene.topology_id || "--"} · r${scene.topology_revision || "--"}`);
}
```

Rename the shared column header to `关联范围`.

- [ ] **Step 5: Surface structured configuration errors**

Update the error parser in `frontend/js/api.js`:

```javascript
if (typeof payload.error?.message === "string") {
  message = payload.error.message;
} else if (typeof payload.detail === "string") {
  message = payload.detail;
} else if (Array.isArray(payload.detail) && payload.detail[0]?.msg) {
  message = payload.detail[0].msg;
}
```

- [ ] **Step 6: Run frontend tests**

Run:

```powershell
pytest tests/test_system_management_frontend.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Commit the frontend task**

```powershell
git add frontend/index.html frontend/js/system-management.js frontend/js/api.js tests/test_system_management_frontend.py
git commit -m "fix: show global camera binding for no-parking scenes"
```

### Task 6: Full Verification and Live Database Migration

**Files:**
- Verify: all files changed above
- Runtime data: `runtime/config/config.sqlite3`

- [ ] **Step 1: Run the complete focused backend suite**

Run:

```powershell
pytest tests/test_configuration_repository.py tests/test_activation_coordinator.py tests/test_scene_topology_independence.py tests/test_configuration_api.py tests/test_api.py -q
```

Expected: zero failures.

- [ ] **Step 2: Run the complete test suite**

Run:

```powershell
pytest -q
```

Expected: zero failures.

- [ ] **Step 3: Restart the server so schema v3 migrates the live database**

Stop only the VideoTest server process, then run the existing startup script without opening a browser:

```powershell
.\start.ps1
```

Expected: the API listens on `http://127.0.0.1:8011`, startup completes without migration errors, and `GET /api/health` returns HTTP 200.

- [ ] **Step 4: Verify live data and behavior through read-only endpoints**

Run:

```powershell
$summary = Invoke-RestMethod 'http://127.0.0.1:8011/api/config/summary'
$scenes = Invoke-RestMethod 'http://127.0.0.1:8011/api/config/scenes?scene_type=no_parking'
$summary.schema_version
$scenes | Select-Object scene_id,name,camera_id,topology_id,topology_revision,review_status
```

Expected: schema version is 3; every no-parking scene has a fixed camera ID, null topology fields, and `review_status = ready`.

- [ ] **Step 5: Exercise the original failing start request**

Select one migrated no-parking scene and call the same endpoint used by the browser:

```powershell
$scene = $scenes | Select-Object -First 1
Invoke-RestMethod \
  -Method Post \
  -Uri 'http://127.0.0.1:8011/api/no-parking/start' \
  -ContentType 'application/json' \
  -Body (@{ scene_id = $scene.scene_id } | ConvertTo-Json)
```

Expected: HTTP 200 and a running no-parking status. Stop it afterward with `POST /api/no-parking/stop`.

- [ ] **Step 6: Verify no-parking survives a topology-only operation**

With a no-parking scene running, use the existing configuration API test or browser flow to activate another valid topology. Confirm `/api/no-parking/status` remains `running: true` with the same `active_scene_id`. Do not mutate the user's live topology unless a disposable topology already exists; otherwise rely on the automated test.

- [ ] **Step 7: Run GitNexus change detection before any implementation commit or handoff**

Run `gitnexus_detect_changes` through the configured MCP with scope `all` (or the local backend equivalent when MCP is unavailable):

```powershell
node --input-type=module -e "import { LocalBackend } from 'file:///C:/Users/1631/AppData/Roaming/npm/node_modules/gitnexus/dist/mcp/local/local-backend.js'; const backend = new LocalBackend(); await backend.init(); const result = await backend.callTool('detect_changes', { repo: 'VideoTest', scope: 'all' }); console.log(JSON.stringify(result, null, 2)); await backend.dispose();"
```

Expected: changed symbols and affected flows are limited to scene persistence, scene activation, topology lifecycle, configuration import/export, and system-management display. Investigate any unrelated process before completion.

- [ ] **Step 8: Review final diff without disturbing pre-existing user changes**

Run:

```powershell
git diff --check
git status --short
git diff -- backend/configuration/schema.py backend/configuration/service.py backend/configuration/activation.py backend/state.py backend/configuration/package.py frontend/js/api.js frontend/js/system-management.js frontend/index.html tests/test_configuration_repository.py tests/test_activation_coordinator.py tests/test_scene_topology_independence.py tests/test_configuration_api.py tests/test_system_management_frontend.py
```

Expected: no whitespace errors; no unrelated files added to the implementation scope; pre-existing worktree changes remain preserved.
