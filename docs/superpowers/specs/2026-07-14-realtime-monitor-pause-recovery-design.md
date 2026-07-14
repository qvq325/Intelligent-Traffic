# Realtime Monitor Pause Recovery Design

## Problem

The no-parking and road-abnormal workspaces freeze the shared realtime stream
while a reference frame is edited. That automatic pause remains active after
navigation because the frontend does not record who owns it or release it.
The active single-view button and the play button also return early for the
current mode/source, so neither action can recover the paused stream.

The `422` response for a bare `/api/video/preview` request is unrelated: the
endpoint requires `source_id`, and the server continues serving requests after
that validation response.

## State Model

Add an optional `workspacePause` record to frontend state:

- `owner`: the workspace that initiated the automatic pause.
- `sourceId`: the stream source that was paused.

A workspace takes ownership only when it changes a previously playing stream
to paused. A stream that was already paused remains user-owned.

## Recovery Rules

1. Leaving the owning workspace releases its pause only when the active source
   still matches the recorded source.
2. Returning to an already active single-view mode resumes a paused stream as
   an explicit user command.
3. Pressing play for the already connected source resumes it instead of taking
   the existing early-return path.
4. Successful explicit resume clears stale workspace ownership.
5. Failed automatic resume restores ownership so a later navigation or user
   command can retry. Responses for a source that is no longer active do not
   overwrite the current UI state.

## Compatibility

Manual pauses are preserved across ordinary navigation. Existing multi-camera
snapshot behavior remains responsible for pauses created by multi-camera mode.
No backend API contract changes are required.

## Verification

- Add frontend contract coverage for ownership capture and release.
- Cover active single-view and same-source play recovery paths.
- Update the app asset version to prevent stale browser code.
- Run focused tests, the full test suite, JavaScript syntax validation, and
  live API checks after restarting the server.
