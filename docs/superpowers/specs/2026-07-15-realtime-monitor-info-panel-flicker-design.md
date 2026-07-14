# Realtime Monitor Information Panel Flicker Fix

## Problem

The video frame's upper-left detection summary alternates between two text
formats. Full inference frames use `车辆检测` and `车牌识别`, while cached
frames use `车辆`, `车牌`, and a `(缓存)` suffix. With the default detection
interval of five frames, the same panel repeatedly switches between different
text pixels and appears to flicker even when the counts have not changed.

## Decision

Keep the existing frame-processing and cached-result paths, but make the cached
summary use the same labels and structure as the full inference summary. Remove
the cached-only suffix from the rendered video panel.

The panel will continue to show the most recently published detection counts.
Those counts may change after a new inference result, but the presentation will
remain stable between inference frames.

## Scope

- Update `VideoStreamService._draw_cached_results` so its information-panel
  lines match `DetectionProcessor.process`.
- Preserve the existing detection interval, result cache, bounding-box drawing,
  MJPEG publication, REST status payload, and frontend metrics.
- Do not refactor the shared annotation pipeline or move the overlay into the
  browser as part of this fix.

## Data Flow

1. A full inference frame produces current detection results and renders the
   canonical summary text.
2. The service stores those results as the current cache.
3. Frames before the next inference redraw the cached boxes and the same
   canonical summary text on the latest source frame.
4. A later inference may update counts and boxes, without a cached/live wording
   transition.

## Verification

- Add focused coverage that captures the cached information-panel lines and
  verifies they use the canonical `车辆检测` and `车牌识别` labels.
- Verify the cached panel has no `(缓存)` suffix.
- Run the focused video-service tests and the full regression suite.
- Run GitNexus change detection and confirm only the expected video annotation
  path and tests are affected.
