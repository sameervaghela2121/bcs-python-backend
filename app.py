#!/usr/bin/env python3
"""
FastAPI service that combines person detection (capture_on_detect.py) and
best-frame selection (preprocess.py) into a single API call.
"""

import os
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles

from capture_on_detect import capture_person_frames
from preprocess import analyze_detection_folder

app = FastAPI(title="Capture on Detect API")

# No credentials in source: the RTSP URL (with camera username/password)
# must be supplied via the RTSP_URL env var or the ?rtsp_url= query param.
DEFAULT_RTSP_URL = os.environ.get("RTSP_URL")
MODEL_PATH = "yolov8n.pt"

DETECTED_FRAMES_DIR = Path("detected_frames")
BEST_FRAMES_DIR = Path("best_frames")
DETECTED_FRAMES_DIR.mkdir(exist_ok=True)
BEST_FRAMES_DIR.mkdir(exist_ok=True)

# Serve saved frames so clients can fetch images by URL instead of raw paths.
app.mount("/frames/detected", StaticFiles(directory=DETECTED_FRAMES_DIR), name="detected_frames")
app.mount("/frames/best", StaticFiles(directory=BEST_FRAMES_DIR), name="best_frames")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/capture-and-select")
def capture_and_select(
    duration: int = Query(30, ge=5, le=300, description="Max seconds to capture"),
    rtsp_url: str = Query(None, description="Override the RTSP source for this run"),
    confidence: float = Query(0.5, ge=0.1, le=1.0, description="Detection confidence threshold"),
):
    """
    Runs the full pipeline in one call:
      1. Capture from RTSP and save a frame every time a person is detected
         (stops after `duration` seconds, or after 10s with no detection).
      2. Score every saved frame and copy the top 5 into best_frames/.

    Each call gets its own timestamped subfolder so results from different
    calls never mix together.
    """
    resolved_rtsp_url = rtsp_url or DEFAULT_RTSP_URL
    if not resolved_rtsp_url:
        raise HTTPException(
            status_code=400,
            detail="No RTSP URL configured. Set the RTSP_URL environment variable "
                   "or pass ?rtsp_url=... on the request.",
        )

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    detected_dir = DETECTED_FRAMES_DIR / f"run_{run_id}"
    best_dir = BEST_FRAMES_DIR / f"run_{run_id}"

    try:
        capture_stats = capture_person_frames(
            rtsp_url=resolved_rtsp_url,
            model_path=MODEL_PATH,
            output_dir=str(detected_dir),
            confidence=confidence,
            duration=duration,
            no_detection_timeout=10,
            show_preview=False,
        )
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))

    if capture_stats["detection_count"] == 0:
        return {
            "run_id": run_id,
            "capture": capture_stats,
            "best_frames": None,
            "message": "No person detected during the capture window.",
        }

    report = analyze_detection_folder(str(detected_dir), str(best_dir))

    best_frames = None
    if report:
        best_frames = [
            {
                "rank": i + 1,
                "filename": f"best_frame_{i + 1}_{frame['filename']}",
                "overall_score": frame["overall_score"],
                "url": f"/frames/best/run_{run_id}/best_frame_{i + 1}_{frame['filename']}",
            }
            for i, frame in enumerate(report["top_frames"])
        ]

    return {
        "run_id": run_id,
        "capture": capture_stats,
        "best_frames": best_frames,
        "report_url": f"/frames/best/run_{run_id}/analysis_report.json" if report else None,
    }
