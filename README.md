# Person Capture Processor - Multi-Camera, Multithreaded

**Camera-agnostic. Background processing. One report per person, per call.**

## What It Does

1. **Camera sources** → Configured once via `.env` (`CAM1_SOURCE`, `CAM2_SOURCE`, ...)
2. **API Call** → Start capture across all configured cameras
3. **API Returns** → Immediately (don't wait)
4. **Background** → Each camera captures frames into its own folder, in parallel
5. **Background** → Each camera independently postprocesses (analyze, select best, delete its raw frames)
6. **Each call** → New timestamped folder shared by all cameras, with one merged report
7. **One camera down?** → It's marked as an error in the report; every other camera still runs to completion, unaffected

## Camera Configuration (`.env`)

Camera sources are never passed in the API request — they're read from environment variables so the whole fleet of cameras is configured in one place.

```bash
cp .env.example .env
```

```dotenv
# One line per camera: CAM<N>_SOURCE = webcam index ("0", "1", ...) or an RTSP URL
CAM1_SOURCE=0
CAM2_SOURCE=rtsp://user:password@10.134.80.84:554/stream1
CAM3_SOURCE=rtsp://user:password@10.134.80.85:554/stream1
```

- Add or remove `CAM<N>_SOURCE` lines to change how many cameras are used - no code changes needed.
- If no `CAM<N>_SOURCE` vars are set, it falls back to a single camera from `CAMERA_SOURCE` (default `"0"`).

## Quick Start

### Option 1: Command Line

```bash
python person_capture.py person_001
```

Captures from every camera in `.env`, returns immediately, processing continues in background.

### Option 2: Python

```python
from person_capture import capture_person_api

result = capture_person_api(person_id="person_001")

print(result)
# Returns immediately with status: "started" and the list of cameras used
```

### Option 3: Multiple People (Parallel)

```python
from person_capture import capture_person_api
import time

# Call 1
result1 = capture_person_api("person_001")
print(f"person_001: {result1['status']}")  # "started"

time.sleep(2)

# Call 2 (while person_001 still capturing)
result2 = capture_person_api("person_002")
print(f"person_002: {result2['status']}")  # "started"

# Call 3 (same person, new folder)
result3 = capture_person_api("person_001")
print(f"person_001 (call 2): {result3['status']}")  # "started"

# All running in parallel, across all cameras!
print("\nAll started - processing in background")
```

## Folder Structure

Every camera writes to its own raw folder, and every camera's best frames land in one shared `best_frames/` folder, namespaced by camera:

```
person_data/
├── person_001/
│   ├── 20240803_153045/            ← Call 1
│   │   ├── cam1_raw_frames/        ← deleted after cam1 postprocessing
│   │   ├── cam2_raw_frames/        ← deleted after cam2 postprocessing
│   │   ├── cam3_raw_frames/        ← deleted after cam3 postprocessing
│   │   └── best_frames/
│   │       ├── cam1_best_01_*.jpg
│   │       ├── cam1_best_02_*.jpg
│   │       ├── cam2_best_01_*.jpg
│   │       ├── cam3_best_01_*.jpg
│   │       └── processing_report.json   ← merged results, all cameras
│   │
│   └── 20240803_153105/            ← Call 2 (NEW folder)
│       └── ...
│
└── person_002/
    └── 20240803_153115/
        └── ...
```

**Key:** Each camera's raw frames are deleted independently, right after that camera's own postprocessing finishes - cameras don't wait on each other.

## How It Works

### Real-Time Capture Phase

Every configured camera starts capturing at the same time, each in its own thread, each writing to its own `cam<N>_raw_frames/` folder.

```
Camera 1 (cam1)      Camera 2 (cam2)      Camera 3 (cam3)
     │                     │                     │
     ▼                     ▼                     ▼
cam1_raw_frames/     cam2_raw_frames/     cam3_raw_frames/
frame_0001.jpg        frame_0001.jpg       frame_0001.jpg
...                    ...                  ...
```

### Post-Processing Phase (Independent Threads)

As soon as a camera finishes capturing, **its own** postprocess thread starts - it does not wait for other cameras. Each thread scores only its own camera's frames, copies its best frames into the shared `best_frames/`, merges its results into `processing_report.json`, then deletes its own raw folder.

```
Post-process (cam1)    Post-process (cam2)    Post-process (cam3)
Scan cam1_raw/          Scan cam2_raw/          Scan cam3_raw/
Quality scoring         Quality scoring         Quality scoring
      │                       │                       │
      └───────────────────────┼───────────────────────┘
                               ▼
                    best_frames/ (unified)
        cam1_best_01.jpg, cam1_best_02.jpg, ...
        cam2_best_01.jpg, cam2_best_02.jpg, ...
        cam3_best_01.jpg, cam3_best_02.jpg, ...
        processing_report.json (all camera scores)
```

### Timeline

```
T=0:00   API Call (person_001)
         └─ Start CaptureWorker thread per camera → Return ✅

T=0:00   cam1, cam2, cam3 all capturing in parallel

T=0:15   cam2 finishes early (or fails to open)
         └─ cam2 PostprocessWorker starts immediately - doesn't wait for cam1/cam3

T=0:15   cam1, cam3 finish
         └─ Their own PostprocessWorkers start independently

T=0:35   All cameras done
         └─ Best frames saved per camera
         └─ Each camera's raw frames deleted as soon as it's done
         └─ Ready for more calls!
```

## Camera Failure Isolation

If a camera can't be opened (bad RTSP URL, camera offline, wrong index) or drops out mid-capture:

- That camera's failure is logged and recorded in `processing_report.json` under `cameras.<camera_id>` with `"status": "error"` and a reason.
- Its (empty or partial) raw folder is still cleaned up.
- **Every other camera keeps capturing and postprocessing normally** - one bad camera never blocks or delays the rest, since each camera runs on its own capture and postprocess threads.

## Threads

- **CaptureWorker** (one per camera) - Captures frames for that camera, then hands off to postprocessing
- **PostprocessWorker** (one per camera) - Analyzes, selects best, merges into the shared report, deletes that camera's raw frames
- **Each person + camera combination** - Gets its own thread pair

## Quality Scoring

Each frame scored on 6 metrics:

| Metric | Weight | Purpose |
|--------|--------|---------|
| Person Confidence | 30% | Detection confidence |
| Person Size | 20% | Fills 15-30% of frame |
| Sharpness | 20% | Focus quality |
| Person Centering | 15% | How centered |
| Brightness | 10% | Optimal lighting |
| Contrast | 5% | Tonal variation |

Best frames selected per camera by overall score.

## Response

### Immediate Response (API returns this right away)

```json
{
  "status": "started",
  "person_id": "person_001",
  "message": "Capturing - processing in background",
  "timestamp": "2024-08-03T21:30:45.123456",
  "cameras": ["cam1", "cam2", "cam3"],
  "directories": {
    "best_frames": "/path/to/person_001/20240803_213045/best_frames",
    "raw_frames": {
      "cam1": "/path/to/person_001/20240803_213045/cam1_raw_frames",
      "cam2": "/path/to/person_001/20240803_213045/cam2_raw_frames",
      "cam3": "/path/to/person_001/20240803_213045/cam3_raw_frames"
    }
  }
}
```

### Check Status Later

```python
from pathlib import Path
import json

# Check if complete
best_frames_dir = Path(response['directories']['best_frames'])
report_file = best_frames_dir / "processing_report.json"

if report_file.exists():
    with open(report_file) as f:
        report = json.load(f)
    for camera_id, camera_result in report['cameras'].items():
        print(f"{camera_id}: {camera_result['status']} - {camera_result.get('best_frames_selected', 0)} best frames")
else:
    print("Still processing...")
```

## Logs

Thread names show what's running, per camera:

```
[Capture-person_001-cam1]      ← Capturing on cam1
[Capture-person_001-cam2]      ← Capturing on cam2 (parallel)
[Postprocess-person_001-cam1]  ← Postprocessing cam1 (background)
```

Example output:

```
2026-08-06 12:30:46 - [MainThread] - INFO - 🔴 API CALL: person_id=person_001
2026-08-06 12:30:46 - [MainThread] - INFO - 📁 Folder: person_001/20260806_123046
2026-08-06 12:30:46 - [MainThread] - INFO - ✅ API RESPONSE: Capture started in background for 3/3 camera(s)
2026-08-06 12:30:46 - [Capture-person_001-cam1] - INFO - 🎬 CAPTURE START: person_001 [cam1 -> 0]
2026-08-06 12:30:46 - [Capture-person_001-cam2] - ERROR - ❌ [cam2] Cannot open camera source: rtsp://...
2026-08-06 12:31:01 - [Capture-person_001-cam1] - INFO - ✓ CAPTURE END: person_001 [cam1] - 300 frames
2026-08-06 12:31:01 - [Postprocess-person_001-cam1] - INFO - 📊 POSTPROCESS START: person_001 [cam1]
2026-08-06 12:31:05 - [Postprocess-person_001-cam1] - INFO - ✓ POSTPROCESS END: person_001 [cam1]
2026-08-06 12:31:05 - [Postprocess-person_001-cam1] - INFO -   🗑️  [cam1] Deleted raw frames (cam1_raw_frames)
```

## Features

✅ **Multi-Camera** - Any number of cameras, configured via `.env`
✅ **Camera Failure Isolation** - One camera failing never blocks the others
✅ **API Returns Immediately** - No waiting
✅ **Multithreaded** - One capture + postprocess thread pair per camera
✅ **Multiple People** - Parallel processing
✅ **Auto Cleanup** - Each camera's raw frames deleted independently
✅ **Quality Scoring** - 6 metrics
✅ **Unique Folders** - Each call gets a timestamped folder
✅ **Merged Report** - One `processing_report.json` per call, per-camera breakdown
✅ **Background Processing** - No blocking

## Usage Examples

### Example 1: Single Person

```bash
python person_capture.py person_001
# Captures from every camera in .env, returns immediately, processing in background
```

### Example 2: Python Loop

```python
from person_capture import capture_person_api
import time

for i in range(5):
    result = capture_person_api(person_id=f"person_{i:03d}")
    print(f"Call {i+1}: {result['status']} - cameras: {result['cameras']}")
    time.sleep(1)

print("\nAll started! Processing in background...")
# Keep script running to see logs
time.sleep(120)
```

### Example 3: Check Folder

```python
from pathlib import Path
import json

base_dir = Path("person_data")

# List all people
for person_dir in base_dir.glob("*"):
    person_id = person_dir.name

    # List all calls for this person
    for call_dir in sorted(person_dir.glob("*")):
        if call_dir.is_dir():
            report = call_dir / "best_frames" / "processing_report.json"
            if report.exists():
                with open(report) as f:
                    data = json.load(f)
                for camera_id, camera_result in data['cameras'].items():
                    print(f"{person_id}/{call_dir.name}/{camera_id}: "
                          f"{camera_result['status']}, "
                          f"{camera_result.get('best_frames_selected', 0)} best frames")
```

## Performance

| Metric | Value |
|--------|-------|
| **API Response** | <100ms |
| **Capture Time** | ~15 seconds per camera (parallel, not additive) |
| **Postprocessing** | 20-30 seconds per camera (parallel, not additive) |
| **Concurrent People** | Unlimited |
| **Concurrent Cameras** | Unlimited (one thread pair each) |
| **Frames/Camera** | ~300-450 (30 fps × capture duration) |

## Installation

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your camera sources
```

## That's It!

Configure cameras once in `.env`. Call the API with just a `person_id`. Every camera captures and postprocesses in parallel, independently, and one bad camera never stalls the rest.

```bash
python person_capture.py person_001
```

Processing continues in background while API returns immediately!
