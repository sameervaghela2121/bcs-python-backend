# Frame Quality Scoring

Each captured frame with a detected person is scored 0-100 on six metrics, then combined into a weighted `overall_score`. Only frames where a person was detected are ranked (see `person_detected` filter in `PostprocessWorker`).

## Metrics

| Metric | What it measures | How it's calculated |
|---|---|---|
| `sharpness` | Focus / blur | Laplacian variance of the grayscale frame, scaled: `min(100, variance / 1500 * 100)`. Higher variance = more edge detail = sharper. |
| `brightness` | Exposure | Mean grayscale pixel value, scored by closeness to 128 (mid-gray): `100 - abs(mean - 128) / 128 * 100`. Peaks at 100 when mean brightness is exactly 128; falls off toward pure black or pure white. |
| `contrast` | Tonal spread | Standard deviation of grayscale pixel values: `min(100, std / 100 * 100)`. Higher std = more distinction between light and dark areas. |
| `person_confidence` | YOLO detection certainty | YOLOv8 detection confidence × 100, for the first detected person. Bounded 50-100 (frames below 0.5 confidence are filtered out before scoring). |
| `person_size` | How much of the frame the person fills | Person bounding-box area as % of frame area. Peaks at 100 around a 30% area ratio, then decreases if the person is too close/fills too much of the frame (avoids extreme close-ups). |
| `person_centering` | How centered the person is | Distance of the person's bounding-box center from the frame's center (as % of frame width/height), inverted: `100 - avg(dist_x%, dist_y%)`. Closer to center = higher score. |

When multiple people are detected in one frame, `person_size` and `person_centering` use whichever detection scores best; `person_confidence` uses the first detection returned by YOLO.

## Overall score

```python
overall_score = (
    sharpness         * 0.30 +
    person_size       * 0.25 +
    person_confidence * 0.20 +
    person_centering  * 0.10 +
    brightness        * 0.10 +
    contrast          * 0.05
)
```

Weights sum to 1.0. Sharpness and person size dominate (55% combined) so a blurry or poorly-framed shot can't win purely on a strong detection — quality and clear visibility of the person are prioritized over raw detection confidence.

Within each camera's captured frames, the top 5 by `overall_score` are copied to `best_frames/` as `cam{N}_best_{rank}_{filename}.jpg`.
