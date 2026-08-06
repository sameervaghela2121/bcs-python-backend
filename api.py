#!/usr/bin/env python3
"""
FastAPI Server with UUID-based folder naming
Run with: uvicorn api:app --host 0.0.0.0 --port 8000 --reload

- Endpoint: POST /api/capture
- Parameters: person_id, trigger_at
- Folder naming: {person_id}_{uuid4}/raw_frames + best_frames
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import logging
from typing import Optional

from person_capture import capture_person_api

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)-12s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title="Person Capture Service",
    version="1.0",
    description="Multithreaded person capture with UUID-based folder naming"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CaptureRequest(BaseModel):
    person_id: str
    trigger_at: Optional[str] = None  # ISO format timestamp (for reference)
    camera_source: Optional[str] = "0"  # Camera index or RTSP URL




# ═══════════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

@app.post("/api/capture")
async def capture(request: CaptureRequest):
    """
    Start capture for a person

    Request:
    {
        "person_id": "person_001",
        "trigger_at": "2024-08-03T21:30:45.123456",  (optional)
        "camera_source": "rtsp://..."  (optional, default: "0")
    }

    Response:
    {
        "status": "started",
        "person_id": "person_001",
        "message": "Capturing - processing in background",
        "timestamp": "2024-08-03T21:30:45.123456",
        "directories": {
            "best_frames": "/path/to/person_001/best_frames"
        }
    }
    """
    if not request.person_id:
        raise HTTPException(status_code=400, detail="person_id is required")

    result = capture_person_api(
        person_id=request.person_id,
        camera_source=request.camera_source or "0",
        trigger_at=request.trigger_at
    )

    if result['status'] == 'error':
        raise HTTPException(status_code=500, detail=result['error'])

    return result


@app.get("/api/health")
async def health():
    """Health check"""
    return {"status": "healthy", "service": "Person Capture Service"}


@app.get("/api/folders")
async def list_folders():
    """List all captured folders"""
    from pathlib import Path

    base_dir = Path("person_data")
    if not base_dir.exists():
        return {"total": 0, "folders": []}

    folders = []
    for person_folder in base_dir.glob("person_*"):
        if person_folder.is_dir():
            for timestamp_folder in person_folder.glob("*"):
                if timestamp_folder.is_dir():
                    best_frames = len(list((timestamp_folder / "best_frames").glob("best_*.jpg")))
                    folders.append({
                        "person_id": person_folder.name,
                        "timestamp": timestamp_folder.name,
                        "best_frames": best_frames,
                        "path": str(timestamp_folder)
                    })

    return {"total": len(folders), "folders": folders}


def _get_local_ip() -> str:
    """Best-effort LAN IP detection (no actual traffic sent)."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


if __name__ == "__main__":
    import uvicorn

    HOST = "0.0.0.0"
    PORT = 8000
    network_ip = _get_local_ip()

    print(f"""
╔═══════════════════════════════════════════════════════════════════╗
║            PERSON CAPTURE SERVICE - FastAPI                       ║
╚═══════════════════════════════════════════════════════════════════╝

Local:    http://localhost:{PORT}
Network:  http://{network_ip}:{PORT}

Then access:
  📚 Swagger Docs:  http://localhost:{PORT}/docs
  📋 ReDoc:         http://localhost:{PORT}/redoc

API Endpoints:
  📸 POST /api/capture   - Start capture
  💊 GET /api/health     - Health check
  📋 GET /api/folders    - List folders

Example:
  curl -X POST http://{network_ip}:{PORT}/api/capture \\
    -H "Content-Type: application/json" \\
    -d '{{"person_id": "person_001", "camera_source": "0"}}'

═══════════════════════════════════════════════════════════════════
    """)

    uvicorn.run("api:app", host=HOST, port=PORT, reload=True)
