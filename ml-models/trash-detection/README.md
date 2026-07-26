# Trash Detection

YOLOv8n floating-debris detector for the UCF Senior Design project "Trash Crab." This is the
*outward-facing* perception model: it finds trash on the water surface so the boat can drive at it.
It is separate from the basket fullness estimator in [`../src/`](../src/), which looks *inside* the
caddy and does not use a neural network. See [`../algoREADME.md`](../algoREADME.md) for that one.

---

## Model Card

| | |
|---|---|
| Architecture | YOLOv8n (nano), fine-tuned from `yolov8n.pt` |
| Framework | Ultralytics 8.4.19 |
| Task | Object detection |
| Classes | 1 — `waste` (class id `0`) |
| Input size | 640 x 640 |
| Training | 100 epochs, batch 16, run name `flow_new_yolov8n` |
| Dataset | FloW-Img (ICCV 2021), floating waste on inland waters |
| Accuracy | mAP@0.5 = 87.3% |
| Checkpoint date | 2026-03-05 |

Every detection this model produces is class `waste`. There is no plastic/metal/organic split — if you
need material classification, that is a different model and a different dataset.

### Weights

| File | Size | Use it for |
|---|---|---|
| [`models/best.pt`](models/best.pt) | ~6.2 MB | PyTorch inference on a laptop or the Pi CPU |
| [`models/converted/best.rvc2.tar.xz`](models/converted/best.rvc2.tar.xz) | ~5.6 MB | On-device inference on the OAK-D Lite's VPU |

Both are committed to the repo. You do not need to train or convert anything to run this.

---

## Which path do I use?

There are two ways to run this model, and they are **not** interchangeable. Pick based on where the
inference math should happen.

### Path A — host inference with `detector.py`

The frame is decoded on the CPU, PyTorch runs the network, boxes come back in pixel coordinates.

Use it for: development on your laptop, scoring recorded video, sanity-checking the model, unit tests.

Do **not** use it as the flight path on the Pi 5. YOLOv8n on a Pi 5 CPU lands in the low single-digit
FPS, and it burns CPU the navigation stack needs.

### Path B — on-device inference with `oak_detector_test.py`

The OAK-D Lite's Myriad X VPU runs the network itself. The Pi only receives finished detections.
Pi CPU cost is close to zero.

Use it for: the actual robot.

The two paths return **different coordinate systems** — Path A gives pixels, Path B gives normalized
0-1 floats. This is the single most common thing to get wrong when moving code between them. See
[Coordinate systems](#coordinate-systems).

---

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

[`requirements.txt`](requirements.txt) is unpinned and covers both paths plus conversion. If you only
need one path, install the subset:

| Path | Packages |
|---|---|
| A — host inference | `ultralytics`, `torch`, `opencv-python`, `numpy` |
| B — OAK-D Lite | `depthai`, `opencv-python`, `numpy` |
| Conversion (rare) | `hubai-sdk` |

`torch` is a ~2 GB install. Skip it on the Pi if you are only running Path B.

This venv is separate from the fullness estimator's. That one wants `picamera2` and pins its versions;
this one wants `torch` and does not. Do not merge them.

---

## Path A — `TrashDetector`

[`detector.py`](detector.py) wraps Ultralytics in a small class that returns plain dicts instead of
Ultralytics `Results` objects, so nothing downstream has to import `ultralytics`.

### API

```python
TrashDetector(model_path=None, conf=0.4)
```

| Argument | Default | Meaning |
|---|---|---|
| `model_path` | `models/best.pt` next to `detector.py` | Path to any Ultralytics `.pt`. Resolved relative to the module, not the working directory, so it works no matter where you launch from. |
| `conf` | `0.4` | Confidence floor. Detections below it are discarded inside Ultralytics and never reach you. |

```python
detector.detect(frame) -> list[dict]
```

`frame` is a BGR `numpy` array (i.e. straight out of `cv2.imread` or `cv2.VideoCapture.read`).
Returns one dict per surviving detection, or `[]` when the frame is clean.

The model is loaded once in `__init__`. Build the detector once and reuse it — constructing one per
frame will dominate your runtime.

### Detection schema

```python
{
    "class_id":   0,                              # always 0 (waste)
    "class_name": "waste",                        # always "waste"
    "confidence": 0.87,                           # float, >= conf
    "bbox":       [x1, y1, x2, y2],               # pixels, top-left / bottom-right
    "center":     [cx, cy],                       # pixels, box midpoint
}
```

Coordinates are **pixels in the frame you passed in** — Ultralytics rescales from its 640x640 letterbox
back to your original resolution. All values are Python floats, so the list is JSON-serializable as-is.

### Single image

```python
import cv2
from detector import TrashDetector

detector = TrashDetector()
frame = cv2.imread("test.jpg")

for det in detector.detect(frame):
    print(f"{det['class_name']} {det['confidence']:.2f} at {det['center']}")
```

### Video or webcam

```python
import cv2
from detector import TrashDetector

detector = TrashDetector(conf=0.5)
capture = cv2.VideoCapture(0)          # or "clip.mp4"

while True:
    ok, frame = capture.read()
    if not ok:
        break

    for det in detector.detect(frame):
        x1, y1, x2, y2 = (int(v) for v in det["bbox"])
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(
            frame,
            f"{det['class_name']} {det['confidence']:.2f}",
            (x1, max(y1 - 10, 20)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2,
        )

    cv2.imshow("Trash Detection", frame)
    if cv2.waitKey(1) & 0xFF == ord("q"):
        break

capture.release()
cv2.destroyAllWindows()
```

### Steering toward a target

The nearest-target heuristic most navigation code wants — pick the largest box, since on open water
apparent area tracks proximity:

```python
def pick_target(detections, frame_width):
    """Return (horizontal_error, area) for the most promising target, or None."""
    if not detections:
        return None

    def area(det):
        x1, y1, x2, y2 = det["bbox"]
        return (x2 - x1) * (y2 - y1)

    target = max(detections, key=area)
    # Negative = target is left of center, positive = right.
    horizontal_error = (target["center"][0] - frame_width / 2) / (frame_width / 2)
    return horizontal_error, area(target)
```

`horizontal_error` comes out in `-1.0 .. 1.0`, which drops straight into a steering controller.

### Loading a different checkpoint

```python
detector = TrashDetector(model_path="/path/to/other.pt", conf=0.3)
```

`class_name` is read from whatever checkpoint you load (`self.model.names`), so a multi-class model
would populate it correctly without any change to `detector.py`.

---

## Path B — OAK-D Lite

[`oak_detector_test.py`](oak_detector_test.py) is a runnable end-to-end demo: it builds a DepthAI
pipeline, streams the color camera into the on-device detection network, and draws boxes plus an FPS
counter. Run it with the camera plugged in:

```bash
python oak_detector_test.py
```

Press `q` to quit.

### What the pipeline does

```
CAM_A (color) ──► DetectionNetwork (NN Archive, runs on the VPU)
                       ├── .passthrough ──► frame_queue       (the exact frame inferred on)
                       └── .out ─────────► detection_queue    (boxes)
```

Reading frames from `.passthrough` rather than from the camera directly is deliberate: it guarantees
the frame you draw on is the one the boxes were computed from. Tapping the camera node instead
introduces a drift of a frame or two, and boxes visibly lag the image.

Both queues are created with `maxSize=4, blocking=False` and read with `tryGet()`, which returns `None`
when nothing is ready. This is what keeps the loop from stalling when the NN is slower than the camera
— the loop keeps drawing the last known detections over the newest frame instead of blocking.

### Requirements

- Luxonis OAK-D Lite over USB3 (a USB2 cable works but throttles throughput)
- DepthAI v3 API — this script uses `dai.Pipeline()` as a context manager, `Camera.build()`, and
  `dai.NNArchive`. The older v2 style (`dai.Device(pipeline)`, `.blob` files, `MobileNetDetectionNetwork`)
  will not run this code.
- On Linux/Pi, the udev rule for Luxonis devices:
  ```bash
  echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="03e7", MODE="0666"' | \
    sudo tee /etc/udev/rules.d/80-movidius.rules
  sudo udevadm control --reload-rules && sudo udevadm trigger
  ```
  Replug the camera afterward.

### Coordinate systems

DepthAI returns **normalized** box coordinates in `0.0 .. 1.0`. Pixels come from `frame_norm()`:

```python
def frame_norm(frame, bbox):
    norm_values = np.full(len(bbox), frame.shape[0])   # height for y
    norm_values[::2] = frame.shape[1]                  # width for x (even indices)
    return (np.clip(np.array(bbox), 0, 1) * norm_values).astype(int)
```

The `[::2]` stride is doing the work: it overwrites the x slots (indices 0 and 2 of
`[xmin, ymin, xmax, ymax]`) with the frame width, leaving the y slots as height. `np.clip` guards
against the network emitting a box that runs slightly off-frame.

**So:** Path A's `bbox` is already pixels; Path B's `detection.xmin` etc. are normalized. Any shared
downstream code should take one convention — normalized is the safer choice, since it survives a
resolution change.

### Confidence threshold

Set on the network node, not per-call:

```python
detection_network.setConfidenceThreshold(0.4)
```

Filtering happens on-device, so raising it also cuts USB traffic.

### Class labels

```python
labels = detection_network.getClasses()
```

These come from the NN Archive's embedded metadata, which is why the label shows up as `waste`
without the script hardcoding it. The loop bounds-checks `class_id` against `len(labels)` and falls
back to the raw integer — harmless here with one class, but it keeps the demo from crashing if a
multi-class archive is swapped in with mismatched metadata.

### Adapting it for the robot

The demo script is a top-level script, not a library — it runs the pipeline at import. For the boat,
lift the loop body into your own module, drop the `cv2.imshow`/`waitKey` block (there is no display on
a headless Pi, and `imshow` will throw), and publish detections instead:

```python
detections_out = []
for detection in detections:
    detections_out.append({
        "class_id":   int(detection.label),
        "class_name": labels[int(detection.label)],
        "confidence": float(detection.confidence),
        "bbox":       [detection.xmin, detection.ymin, detection.xmax, detection.ymax],  # normalized!
    })
```

That mirrors `TrashDetector.detect()`'s schema except for the coordinate convention.

---

## Conversion (`convert_model.py`)

**You almost certainly do not need to run this.** The converted archive is already committed at
[`models/converted/best.rvc2.tar.xz`](models/converted/best.rvc2.tar.xz). Re-run it only after
retraining.

[`convert_model.py`](convert_model.py) submits `best.pt` to Luxonis HubAI and downloads an RVC2 NN
Archive. Settings that matter:

| Setting | Value | Why |
|---|---|---|
| `yolo_version` | `"yolov8"` | Must match the training architecture or the decode head is wired wrong |
| `yolo_input_shape` | `[640, 640]` | Must match training `imgsz` |
| `yolo_class_names` | `["waste"]` | Becomes `getClasses()` on-device. Must match `data.yaml` order exactly |
| `number_of_shaves` | `6` | VPU compute slices allocated to this network |
| `compress_to_fp16` | `True` | Halves model size; negligible accuracy cost at this scale |
| `superblob` | `True` | Emits a blob valid across shave counts, so the archive is not locked to `number_of_shaves=6` |

Requires HubAI credentials in the environment. If `hubai_sdk` fails to import or the client API has
moved, the fastest fallback is the Luxonis Hub web converter — upload `best.pt`, pick RVC2, and use
the same settings from the table above.

If you retrain with a different `imgsz` or class list, update this script **before** converting.
A mismatch produces an archive that loads fine and then emits garbage boxes, which is a miserable
thing to debug on the water.

---

## Tuning confidence

`conf=0.4` is the default on both paths.

| Symptom | Change | Trade-off |
|---|---|---|
| Missing real trash | Lower to `0.25 – 0.3` | More false positives — wave crests, foam, glare, sun glint |
| Chasing phantoms | Raise to `0.5 – 0.6` | Misses small or partly submerged debris |

Water is a hostile background: specular highlights and whitecaps read as objects to a small detector.
If you are seeing steady false positives at a fixed spot in frame, suspect a reflection off the boat's
own hull before you blame the model.

Because the model has a single class, temporal filtering is cheap and effective — requiring a
detection to appear in N consecutive frames near the same location kills most glare artifacts without
touching the threshold. Worth adding in the navigation layer rather than here.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FileNotFoundError: Model archive not found` | The `.tar.xz` was not checked out | Confirm the file exists; check Git LFS if the repo adopts it later |
| `RuntimeError: No available devices` | OAK-D Lite not enumerated | Replug, use a USB3 port, install the udev rule above |
| `X_LINK_DEVICE_NOT_FOUND` mid-run | USB brownout or cable dropout | Powered hub; suspect the cable first — it is usually the cable |
| `AttributeError` on `dai.node.Camera.build` | DepthAI v2 installed | `pip install -U depthai` for v3 |
| `cv2.error` on `imshow` | Headless Pi, no display | Strip the display block; see [Adapting it for the robot](#adapting-it-for-the-robot) |
| Boxes lag the video | Reading frames from the camera node instead of `.passthrough` | Use `.passthrough` |
| Path A runs at ~2 FPS on the Pi | Expected — CPU PyTorch | Use Path B |
| Detections always `[]` | `conf` too high, or frame is RGB not BGR | Lower `conf`; confirm channel order |
| Boxes land in the wrong place | Mixed pixel and normalized coordinates | See [Coordinate systems](#coordinate-systems) |

---

## Repo layout

```text
trash-detection/
├── README.md               # this file
├── requirements.txt
├── detector.py             # Path A — TrashDetector wrapper around Ultralytics
├── oak_detector_test.py    # Path B — runnable OAK-D Lite demo
├── convert_model.py        # .pt -> RVC2 NN Archive via HubAI (rarely needed)
└── models/
    ├── best.pt
    └── converted/
        └── best.rvc2.tar.xz
```

## Known gaps

Things worth knowing before you build on this:

- **No test coverage.** `oak_detector_test.py` is a manual demo despite the `_test` name — `pytest`
  will not collect it, and it needs hardware. `detector.py` has no tests at all.
- **No batch/video utility.** Scoring a folder of clips means writing the loop yourself.
- **No distance estimate.** The OAK-D Lite has stereo depth, but the pipeline only builds the color
  camera and a plain `DetectionNetwork`. Switching to `StereoDepth` + `SpatialDetectionNetwork` would
  give real XYZ per detection instead of the box-area proxy in
  [Steering toward a target](#steering-toward-a-target). This is the highest-value next step for
  navigation.
- **Single class.** No material sorting is possible with these weights.
- **`convert_model.py` is unverified against the current `hubai-sdk`.** The committed archive was
  produced successfully, but the script may need updating against the SDK's current surface.

## Dataset

Trained on the FloW-Img dataset (ICCV 2021) — floating waste imagery captured from an unmanned
surface vessel on inland waters. Domain-relevant, which is why 87.3% mAP@0.5 from a nano model is
believable here. Expect degradation on open ocean, in heavy chop, or at dusk, none of which the
training distribution covers.
