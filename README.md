# Person Capture Processor - Multithreaded

**One file. Simple. Background processing.**

## What It Does

1. **API Call** → Start capture (10 seconds)
2. **API Returns** → Immediately (don't wait)
3. **Background** → Capture frames
4. **Background** → Postprocess (analyze, select best, delete raw)
5. **Each call** → New timestamped folder

## Quick Start

### Option 1: Command Line

```bash
python person_capture_processor_lite.py person_001 --source "rtsp://sam_trt:Admin@123@10.134.80.84:554/stream1"
```

Returns immediately. Processing continues in background.

### Option 2: Python

```python
from person_capture_processor_lite import capture_person_api

result = capture_person_api(
    person_id="person_001",
    camera_source="rtsp://sam_trt:Admin@123@10.134.80.84:554/stream1"
)

print(result)
# Returns immediately with status: "started"
```

### Option 3: Multiple People (Parallel)

```python
from person_capture_processor_lite import capture_person_api
import time

# Call 1
result1 = capture_person_api("person_001", "0")
print(f"person_001: {result1['status']}")  # "started"

time.sleep(2)

# Call 2 (while person_001 still capturing)
result2 = capture_person_api("person_002", "0")
print(f"person_002: {result2['status']}")  # "started"

# Call 3 (same person, new folder)
result3 = capture_person_api("person_001", "0")
print(f"person_001 (call 2): {result3['status']}")  # "started"

# All running in parallel!
print("\nAll started - processing in background")
```

## Folder Structure

```
person_data/
├── person_001/
│   ├── 20240803_153045/    ← Call 1
│   │   └── best_frames/
│   │       ├── best_01_*.jpg
│   │       ├── best_02_*.jpg
│   │       └── processing_report.json
│   │
│   └── 20240803_153105/    ← Call 2 (NEW folder)
│       └── best_frames/
│           └── ...
│
└── person_002/
    └── 20240803_153115/
        └── best_frames/
```

**Key:** Raw frames automatically deleted after postprocessing!

## How It Works

### Timeline

```
T=0:00   API Call 1 (person_001)
         └─ Start Capture Thread → Return ✅

T=0:05   API Call 2 (person_002, while person_001 capturing!)
         └─ Start Capture Thread → Return ✅

T=0:10   person_001 Capture Complete
         └─ Queue Postprocessing → Start Postprocess Thread

T=0:10   person_002 Capture Complete
         └─ Queue Postprocessing → Start Postprocess Thread

T=0:10   Both postprocessing in parallel!

T=0:35   All Complete
         └─ Best frames saved
         └─ Raw frames deleted
         └─ Ready for more calls!
```

## Threads

- **CaptureWorker** - Captures frames (10 sec, then returns)
- **PostprocessWorker** - Analyzes, selects best, deletes raw (background)
- **Each person** - Gets own threads

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

Best frames selected by overall score.

## Response

### Immediate Response (API returns this right away)

```json
{
  "status": "started",
  "person_id": "person_001",
  "message": "Capturing - processing in background",
  "timestamp": "2024-08-03T21:30:45.123456",
  "directories": {
    "best_frames": "/path/to/person_001/20240803_213045/best_frames"
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
    print(f"Complete! Best frames: {report['best_frames_selected']}")
else:
    print("Still processing...")
```

## Logs

Thread names show what's running:

```
[Capture-person_001]      ← Capturing
[Postprocess-person_001]  ← Postprocessing (background)
[Capture-person_002]      ← Capturing for different person (parallel)
```

Example output:

```
2026-08-03 21:30:46 - [MainThread] - INFO - 🔴 API CALL: person_id=person_001
2026-08-03 21:30:46 - [MainThread] - INFO - 📁 Folder: person_001/20240803_213046
2026-08-03 21:30:46 - [MainThread] - INFO - ✅ API RESPONSE: Capture started in background
2026-08-03 21:30:46 - [Capture-person_001] - INFO - 🎬 CAPTURE START: person_001
2026-08-03 21:30:56 - [Capture-person_001] - INFO - ✓ CAPTURE END: person_001 - 300 frames
2026-08-03 21:30:56 - [Postprocess-person_001] - INFO - 📊 POSTPROCESS START: person_001
2026-08-03 21:31:15 - [Postprocess-person_001] - INFO - ✓ POSTPROCESS END: person_001
2026-08-03 21:31:15 - [Postprocess-person_001] - INFO - 🗑️  Deleted raw frames
```

## Features

✅ **API Returns Immediately** - No waiting  
✅ **Multithreaded** - Capture + Postprocess separate  
✅ **Multiple People** - Parallel processing  
✅ **Auto Cleanup** - Raw frames deleted  
✅ **Quality Scoring** - 6 metrics  
✅ **Unique Folders** - Each call gets timestamped folder  
✅ **Background Processing** - No blocking  
✅ **One File** - Simple and clean  

## Usage Examples

### Example 1: Single Person

```bash
python person_capture_processor_lite.py person_001 --source "0"
# Returns immediately, processing in background
```

### Example 2: RTSP Stream

```bash
python person_capture_processor_lite.py person_001 \
  --source "rtsp://sam_trt:Admin@123@10.134.80.84:554/stream1"
# Returns immediately
```

### Example 3: Python Loop

```python
from person_capture_processor_lite import capture_person_api
import time

for i in range(5):
    result = capture_person_api(
        person_id=f"person_{i:03d}",
        camera_source="0"
    )
    print(f"Call {i+1}: {result['status']}")
    time.sleep(1)

print("\nAll started! Processing in background...")
# Keep script running to see logs
time.sleep(120)
```

### Example 4: Check Folder

```python
from pathlib import Path
import json

base_dir = Path("person_data")

# List all people
for person_dir in base_dir.glob("person_*"):
    person_id = person_dir.name
    
    # List all calls for this person
    for call_dir in sorted(person_dir.glob("*")):
        if call_dir.is_dir():
            report = call_dir / "best_frames" / "processing_report.json"
            if report.exists():
                with open(report) as f:
                    data = json.load(f)
                print(f"{person_id}/{call_dir.name}: {data['best_frames_selected']} best frames")
```

## Performance

| Metric | Value |
|--------|-------|
| **API Response** | <100ms |
| **Capture Time** | 10 seconds |
| **Postprocessing** | 20-30 seconds |
| **Concurrent People** | Unlimited |
| **Frames/Person** | ~300 (30 fps × 10s) |

## Installation

```bash
pip install opencv-python numpy ultralytics
```

## That's It!

One file. Three options (CLI, Python function, or API). Background processing. Done.

```bash
python person_capture_processor_lite.py person_001 --source "rtsp://..."
```

Processing continues in background while API returns immediately!
