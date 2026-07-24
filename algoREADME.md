<<<<<<< HEAD
# Caddy Fullness Estimator

This is the Trash Crab basket-fullness estimator. It runs on the Raspberry Pi 5, uses the inside-facing Raspberry Pi Camera Module 3 Wide, and estimates how full the basket is without using a neural network. The whole idea is to keep it lightweight, understandable, and easy to tune on the boat.

Right now the estimator works by comparing the current basket view against an empty-basket reference, measuring how much of the basket area looks occupied, and turning that into a simple label the rest of the robot can use.

## What It Gives You

- Works with live `Picamera2` frames on the Pi or with saved images while developing.
- Lets you calibrate the basket region once and reuse that ROI later.
- Captures a fresh empty-basket reference before a run so changing water color or lighting does not immediately throw things off.
- Estimates fullness with both area coverage and height-based occupancy bands.
- Returns:
  - `label_id`: `1`, `2`, or `3`
  - `label`: `empty`, `partially_full`, or `full`
  - `fullness_percent`: a continuous `0-100` estimate
  - extra signals like `area_score`, `height_score`, and `confidence`
- Can save debug images when you need to see what the estimator was looking at.

## Project Layout

```text
.
├── README.md
├── requirements.txt
├── config/
│   └── default_config.yaml
├── data/
│   ├── calibration/
│   └── debug/
├── src/
│   ├── __init__.py
│   ├── calibration.py
│   ├── camera.py
│   ├── configuration.py
│   ├── estimator.py
│   ├── main.py
│   ├── preprocessing.py
│   └── visualization.py
└── tests/
    └── test_estimator.py
```

## Assumptions

- Raspberry Pi 5 with 8 GB RAM
- Raspberry Pi Camera Module 3 Wide
- The inside-facing camera is mounted in a fixed position
- The basket shape and camera view stay consistent after installation
- The basket can have water visible in frame, so some tuning for glare and shimmer is expected

## Setup

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Make sure the camera stack works

On Raspberry Pi OS, check that `libcamera` sees the camera before you try running the estimator:

```bash
libcamera-hello
```

If that works, `Picamera2` should also be able to grab frames from Python.

## Quick Start

### Calibrate from the Pi camera

```bash
python -m src.main calibrate
```

Do this once after the camera mount and basket geometry are finalized. The goal here is to lock in the basket ROI so later runs do not need you to redraw it.

By default, calibration:

- captures a frame
- lets you draw a polygon ROI
- falls back to rectangle selection if needed
- saves:
  - `data/calibration/empty_reference.jpg`
  - `data/calibration/roi_mask.png`
  - `data/calibration/calibration.json`

Polygon controls:

- Left click: add ROI points
- `Enter` or `Space`: finish polygon
- `r`: reset points
- `Esc`: cancel

### Calibrate from a saved image

```bash
python -m src.main calibrate --source image --input-image /path/to/empty_basket.jpg
```

### Run continuously

```bash
python -m src.main run
```

When `camera.source` is `picamera2`, `run` automatically captures a fresh empty-basket reference before analysis starts. That is a much better fit for this project than reusing an old empty image forever, especially if water color, reflections, or general lighting have changed since the last time the robot was on the water.

If you want to refresh the empty reference by itself:

```bash
python -m src.main refresh-reference
```

### Analyze once

```bash
python -m src.main analyze-once
```

### Run with saved images instead of the Pi camera

```bash
python -m src.main analyze-once --source image --input-image /path/to/test_frame.jpg
python -m src.main run --source directory --input-dir /path/to/test_frames
```

### Save debug artifacts

```bash
python -m src.main run --debug
```

Debug output goes under `data/debug/` and includes:

- original processed frame
- ROI overlay
- foreground/debris mask
- band occupancy visualization
- heatmap-style difference map

## Example Output

```json
{
  "timestamp": "2026-06-27T14:30:22",
  "label_id": 2,
  "label": "partially_full",
  "fullness_percent": 58.4,
  "raw_score": 0.61,
  "smoothed_score": 0.58,
  "area_score": 0.47,
  "height_score": 0.66,
  "confidence": 0.72,
  "changed_area_ratio": 0.31,
  "band_occupancy": [0.04, 0.18, 0.41, 0.63, 0.72],
  "highest_occupied_band": 1
}
```

Band indices go from the top of the ROI to the bottom.

## Configuration

The default settings live in `config/default_config.yaml`. The main sections worth tuning are:

- `processing`: image normalization, blur, thresholding, morphology, contour filtering
- `estimation`: band count, band weights, fusion weights, fullness thresholds
- `smoothing`: rolling window size, median or mean smoothing, hysteresis margin
- `camera`: capture resolution, warmup, processing interval
- `paths`: where calibration and debug artifacts are stored

If you want to override the defaults with your own config file:

```bash
python -m src.main run --config /path/to/custom_config.yaml
```

The newer mission/runtime settings are:

- `mission.refresh_reference_on_run`: automatically capture a fresh empty reference at mission start for live PiCam runs
- `mission.reference_frame_count`: number of frames fused with a median operation to build the empty reference
- `mission.reference_settle_frames`: frames to discard before capturing the mission-start reference
- `runtime.recovery_delay_seconds`: wait time before retrying after a recoverable runtime failure
- `runtime.max_consecutive_failures`: maximum recoverable failures before exiting (`0` means retry indefinitely)

## How It Decides Fullness

1. Capture or load a frame.
2. Resize it to the configured processing resolution.
3. Normalize brightness using CLAHE on the luminance channel.
4. Mask everything outside the calibrated basket ROI.
5. Compare the current frame to the saved empty reference.
6. Threshold and clean the difference image to isolate debris.
7. Measure occupancy in horizontal bands.
8. Combine:
   - `height_score`: how high the detected debris rises
   - `area_score`: how much of the ROI changed compared with the empty basket
9. Smooth results over multiple frames and apply hysteresis to reduce label flicker.

## Testing

Run the tests with:

```bash
pytest
```

The current tests cover:

- label threshold mapping
- score fusion
- band occupancy calculation
- config loading and validation

## Using It in Python

The core estimator is implemented as an importable class:

```python
from src.estimator import CaddyFullnessEstimator

estimator = CaddyFullnessEstimator()
result = estimator.analyze_once()
```

The returned result is JSON-serializable, so it is easy to hand off to a local dashboard, another Pi process, or a small service later on.

## Real-World Caveats

- Strong shadows, glare, and shimmering water can still confuse the empty-reference comparison.
- The estimator assumes the basket and camera do not shift after calibration.
- Automatic retries help with small runtime hiccups, but they cannot fix hard hardware failures like power loss or a dead camera.
- Transparent bags, reflective trash, or debris that visually blends into the basket can make the estimate less reliable.
- If you move the camera, change the mount height, or significantly change processing resolution, recalibrate.
- Polygon ROI selection needs OpenCV GUI support. On a headless setup, use a manual ROI in the config or calibrate from a machine with display support.
=======
# trash-crab
Computer Science Senior Design Project - Low Cost Marine "Roomba"-Style Cleanup ASV
>>>>>>> 8eb6f451286cb9789cc407af37bf9d5b3083e19f
