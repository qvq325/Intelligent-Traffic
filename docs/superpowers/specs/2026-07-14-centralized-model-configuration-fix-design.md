# Centralized Model Configuration Fix

## Goal

Make system management the only model-configuration UI while retaining the
legacy detection-settings API for compatibility. Ensure a valid model-pipeline
form submission reaches persistence and the matching runtime processor.

## Frontend

- Remove the detection-parameter tab and form from realtime monitoring.
- Remove the realtime detection enable switch because it mutates the legacy
  configuration independently from the centralized scene setting.
- Remove the corresponding DOM bindings, form state, and event handlers from
  the main application script.
- Change the no-parking movement threshold input step to `0.001`, which accepts
  the persisted default `0.03`.
- When native form validation finds an invalid control inside collapsed model
  advanced settings, open that section so the browser can focus the control.
- Advance the cache-busting URL for both changed JavaScript modules so a new
  HTML shell can never initialize against the previously cached DOM bindings.

## Compatibility

The `PUT /api/detection/settings` endpoint and its API client method remain
available. No database schema, request contract, or compatibility behavior is
changed in this fix.

## Verification

- Source-contract tests ensure realtime monitoring has no duplicate model
  editor or stale JavaScript bindings.
- Frontend tests cover the corrected movement-threshold step and invalid-field
  expansion behavior.
- An API/runtime integration test saves a new realtime model revision, verifies
  persistence, verifies the runtime desired options, and initializes a
  processor double with the saved options.
- Focused frontend, configuration, runtime, and full regression suites must
  pass, followed by JavaScript syntax checks and GitNexus change detection.
