# RTSP Probe Result Refresh Fix

## Problem

The single-stream RTSP probe endpoint records every completed probe result in
the configuration database. When a stream is unavailable, it then returns a
`502 STREAM_PROBE_FAILED` response so the caller can distinguish a failed
connectivity check from a successful one.

The frontend sends the request through `runMutation`. That helper only runs its
configured refresh after `execute` resolves. A failed probe rejects during
`execute`, so the stored failure result is not reloaded and the stream table
continues to show its previous probe status until the user manually refreshes
the page. Successful single probes and batch probes already refresh the table.

## Decision

Keep the existing backend response contract and the shared mutation helper.
Update only `probeStream` so the stream list is reloaded after the probe request
settles, regardless of whether the probe succeeds or fails.

While the request is pending, mark the target stream as probing and render that
temporary state. When the request settles, clear the temporary state and load
the authoritative stream list from `/api/config/streams`. Preserve the original
probe error so the existing event handler still displays the structured error
notification after the refreshed failure result appears in the table.

## Scope

- Modify `probeStream` in `frontend/js/system-management.js`.
- Add a focused frontend regression test for the failed-probe refresh path.
- Preserve the successful probe report and notifications.
- Preserve batch probing behavior, selection state, credential masking, and
  the backend API contract.
- Do not modify `runMutation`; GitNexus reports that helper as CRITICAL risk
  with 15 direct callers and eight affected process groups.
- Do not change failed probes to HTTP 200 responses.

## Data Flow

1. The user starts a single-stream probe from the RTSP stream table.
2. The row displays a temporary probing state.
3. The backend probes the stream and records the result.
4. A successful probe resolves normally; a failed probe returns the existing
   structured `STREAM_PROBE_FAILED` error.
5. In both cases, the frontend clears the temporary state and reloads the
   stream collection.
6. The table renders the new probe status and timestamp. Any original probe
   error continues to the existing notification handler.

## Error Handling

The refresh must not convert a failed probe into a successful operation. If the
probe request fails, its original structured error remains the primary error.
If reloading the stream collection also fails, the frontend reports the refresh
failure without discarding the original probe outcome.

## Verification

- Add a regression assertion that the single-stream probe cleanup reloads the
  stream collection on both success and failure.
- Run `tests/test_system_management_frontend.py`.
- Run the configuration API probe tests to confirm the backend contract and
  persisted result behavior remain unchanged.
- Run the full test suite when focused coverage passes.
- Run GitNexus `detect_changes` and confirm the new change is limited to the
  single-stream probe interaction and its test.
