# Realtime Monitor Information Panel Flicker Fix

## Problem

The video frame's upper-left detection summary appears on a full inference
frame but disappears from the following cached frames whenever the valid
detection result is empty. A 30-frame MJPEG sample with the default inference
interval of five showed the panel only on frames 1, 6, 11, 16, 21, and 26.

`VideoStreamService._run` stores an empty detection result as `[]`, then uses
the truthiness of that list to decide whether a cache exists. This conflates
"inference completed with zero vehicles" with "no inference result is
available". The inference frame contains the zero-count panel, while the next
four frames publish unannotated video, producing the regular flicker.

The live and cached rendering paths also use different summary labels. That
wording difference is secondary, but it must be removed so non-empty cached
results do not produce a smaller text-only flicker.

## Decision

Use the existing non-null `last_results_snapshot` as the cache-validity signal
in `VideoStreamService._run`. The snapshot is already created after every
successfully published inference result, including an empty result, and is
cleared on source changes, rejected publication, or invalid processing tokens.

Keep the existing frame-processing and cached-result paths. Allow the cached
renderer to receive an empty result list so it draws a stable zero-count panel,
and make its summary labels match the full inference summary without a
cached-only suffix.

The panel will continue to show the most recently published detection counts.
Those counts may change after a new inference result, but the presentation will
remain stable between inference frames.

## Scope

- Update the cached-result branch in `VideoStreamService._run` to test snapshot
  validity instead of result-list truthiness.
- Update `VideoStreamService._draw_cached_results` so its information-panel
  lines match `DetectionProcessor.process`.
- Preserve the existing detection interval, result cache, bounding-box drawing,
  MJPEG publication, REST status payload, and frontend metrics.
- Preserve the pre-inference state: no cached panel is drawn until one valid
  inference result has been published.
- Do not refactor the shared annotation pipeline or move the overlay into the
  browser as part of this fix.

## Data Flow

1. A full inference frame produces current detection results and renders the
   canonical summary text.
2. The service stores those results, including an empty list, and records the
   processing snapshot that makes the cache valid.
3. Frames before the next inference redraw the cached boxes and the same
   canonical summary text on the latest source frame. An empty cache redraws a
   zero-count panel without boxes.
4. A later inference may update counts and boxes, without a cached/live wording
   transition.
5. A source change, detection disable, processor replacement, or stale token
   clears or rejects the snapshot so old results are not redrawn.

## Verification

- Add focused coverage that captures the cached information-panel lines and
  verifies they use the canonical `车辆检测` and `车牌识别` labels.
- Verify the cached panel has no `(缓存)` suffix.
- Add a service-loop regression test where inference returns zero vehicles and
  verify the following non-inference frame invokes cached rendering with an
  empty list.
- Restart the application and sample 30 consecutive MJPEG frames. Verify the
  panel is present on every post-inference frame rather than only every fifth
  frame.
- Run the focused video-service tests and the full regression suite.
- Run GitNexus change detection and confirm only the expected video annotation
  path and tests are affected.
