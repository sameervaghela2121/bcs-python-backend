#!/usr/bin/env python3
"""
Person Detection & Frame Processing Service
Multi-camera, multithreaded - Capture & Postprocessing in background
API returns immediately after starting capture

Workflow:
1. Camera sources are loaded from env (CAM1_SOURCE, CAM2_SOURCE, ...)
2. API call starts one CaptureWorker thread per camera (parallel)
3. API returns immediately
4. Each camera's capture finishes independently → queues its own PostprocessWorker
5. Each PostprocessWorker analyzes only its own camera's frames, selects best,
   deletes that camera's raw frames, and merges its results into the shared report
6. Each call creates a new timestamped folder shared by all cameras
7. Multiple people processed in parallel
"""

import cv2
import os
import re
import json
import time
import threading
import shutil
from pathlib import Path
from datetime import datetime
import numpy as np 
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, Optional
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)-12s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DEFAULT_CAMERA_SOURCE = os.getenv("CAMERA_SOURCE", "0")
CAM_SOURCE_PATTERN = re.compile(r"^CAM(\d+)_SOURCE$")


def load_camera_sources() -> Dict[str, str]:
    """
    Load camera sources from env vars shaped CAM1_SOURCE, CAM2_SOURCE, ...
    Each value is either a webcam index ("0") or an RTSP URL.

    Falls back to a single camera ("cam1") using CAMERA_SOURCE if no
    CAM<N>_SOURCE vars are set.
    """
    numbered = {}
    for key, value in os.environ.items():
        match = CAM_SOURCE_PATTERN.match(key)
        if match and value:
            numbered[int(match.group(1))] = value

    if numbered:
        return {f"cam{n}": src for n, src in sorted(numbered.items())}

    return {"cam1": DEFAULT_CAMERA_SOURCE}


class FrameProcessor:
    """Analyzes frames with lazy YOLOv8 loading"""

    def __init__(self):
        self.model = None

    def _load_model(self):
        if self.model is None:
            try:
                from ultralytics import YOLO
                logger.info("Loading YOLOv8...")
                self.model = YOLO("yolov8n.pt")
                logger.info("✓ YOLOv8 loaded")
            except Exception as e:
                logger.warning(f"YOLOv8 failed: {e}")
                self.model = False

    def calculate_brightness(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = np.mean(gray)
        score = 100 - abs(brightness - 128) / 128 * 100
        return max(0, min(100, score))

    def calculate_contrast(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        contrast = np.std(gray)
        score = (contrast / 100) * 100
        return max(0, min(100, score))

    def calculate_sharpness(self, frame):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        sharpness = np.var(laplacian)
        score = min(100, (sharpness / 1500) * 100)
        return score

    def detect_persons(self, frame):
        self._load_model()
        if self.model is False:
            return []

        try:
            results = self.model(frame, conf=0.5, classes=0, verbose=False)
            detections = []
            for r in results:
                for box in r.boxes:
                    if box.conf > 0.5:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        detections.append({
                            'bbox': (x1, y1, x2, y2),
                            'confidence': float(box.conf)
                        })
            return detections
        except Exception as e:
            logger.warning(f"Detection error: {e}")
            return []

    def calculate_person_size(self, frame, detections):
        if not detections:
            return 0
        frame_area = frame.shape[0] * frame.shape[1]
        best_score = 0
        for det in detections:
            x1, y1, x2, y2 = det['bbox']
            person_area = (x2 - x1) * (y2 - y1)
            area_ratio = (person_area / frame_area) * 100
            if area_ratio > 30:
                score = 100 - (area_ratio - 30) / 50 * 100
            else:
                score = (area_ratio / 30) * 100
            best_score = max(best_score, score)
        return max(0, min(100, best_score))

    def calculate_person_centering(self, frame, detections):
        if not detections:
            return 0
        frame_height, frame_width = frame.shape[:2]
        best_score = 0
        for det in detections:
            x1, y1, x2, y2 = det['bbox']
            person_center_x = (x1 + x2) / 2
            person_center_y = (y1 + y2) / 2
            frame_center_x = frame_width / 2
            frame_center_y = frame_height / 2
            dist_x = abs(person_center_x - frame_center_x) / frame_width * 100
            dist_y = abs(person_center_y - frame_center_y) / frame_height * 100
            score = 100 - (dist_x + dist_y) / 2
            best_score = max(best_score, score)
        return max(0, min(100, best_score))

    def analyze_frame(self, frame_path):
        frame = cv2.imread(str(frame_path))
        if frame is None:
            return None

        detections = self.detect_persons(frame)
        brightness = self.calculate_brightness(frame)
        contrast = self.calculate_contrast(frame)
        sharpness = self.calculate_sharpness(frame)
        person_size = self.calculate_person_size(frame, detections)
        person_center = self.calculate_person_centering(frame, detections)
        person_confidence = detections[0]['confidence'] * 100 if detections else 0

        overall_score = (
            sharpness * 0.30 +
            person_size * 0.25 +
            person_confidence * 0.20 +
            person_center * 0.10 +
            brightness * 0.10 +
            contrast * 0.05
        )

        return {
            'path': str(frame_path),
            'filename': frame_path.name,
            'scores': {
                'brightness': round(brightness, 2),
                'contrast': round(contrast, 2),
                'sharpness': round(sharpness, 2),
                'person_confidence': round(person_confidence, 2),
                'person_size': round(person_size, 2),
                'person_centering': round(person_center, 2),
            },
            'overall_score': round(overall_score, 2),
            'person_detected': len(detections) > 0
        }


class SharedReport:
    """
    Thread-safe, incrementally-written report shared by all cameras of a
    single capture_person() call. Each camera's PostprocessWorker merges its
    own results in independently, as soon as it finishes - it does not wait
    for the other cameras.
    """

    def __init__(self, path: Path, person_id: str, cameras: Dict[str, str]):
        self.path = path
        self.lock = threading.Lock()
        self.data = {
            'person_id': person_id,
            'started_at': datetime.now().isoformat(),
            'cameras_requested': list(cameras.keys()),
            'cameras': {}
        }
        self._write()

    def _write(self):
        with open(self.path, 'w') as f:
            json.dump(self.data, f, indent=2)

    def update_camera(self, camera_id: str, camera_source: str, camera_result: Dict):
        with self.lock:
            self.data['cameras'][camera_id] = {
                'camera_source': camera_source,
                **camera_result
            }
            self.data['last_updated'] = datetime.now().isoformat()
            self._write()


class CaptureWorker(threading.Thread):
    """Thread: Captures frames from one camera and queues its own postprocessing"""

    def __init__(self, person_id: str, camera_id: str, camera_source: str,
                 raw_dir: Path, best_dir: Path, processor: FrameProcessor,
                 shared_report: SharedReport, duration: int = 10, fps: int = 30):
        super().__init__(daemon=False, name=f"Capture-{person_id}-{camera_id}")
        self.person_id = person_id
        self.camera_id = camera_id
        self.camera_source = camera_source
        self.raw_dir = raw_dir
        self.best_dir = best_dir
        self.processor = processor
        self.shared_report = shared_report
        self.duration = duration
        self.fps = fps
        self.frame_count = 0

    def run(self):
        logger.info(f"🎬 CAPTURE START: {self.person_id} [{self.camera_id} -> {self.camera_source}]")

        try:
            cap = cv2.VideoCapture(
                int(self.camera_source) if self.camera_source.isdigit() else self.camera_source
            )
        except Exception as e:
            logger.error(f"❌ [{self.camera_id}] Error opening camera source '{self.camera_source}': {e}")
            self._fail(f"Error opening camera source: {e}")
            return

        if not cap.isOpened():
            logger.error(f"❌ [{self.camera_id}] Cannot open camera source: {self.camera_source}")
            cap.release()
            self._fail("Cannot open camera source")
            return

        start_time = time.time()

        try:
            while time.time() - start_time < self.duration:
                ret, frame = cap.read()
                if not ret:
                    logger.warning(f"[{self.camera_id}] Failed to read frame")
                    break

                self.frame_count += 1
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"frame_{self.frame_count:04d}_{timestamp}.jpg"
                filepath = self.raw_dir / filename

                cv2.imwrite(str(filepath), frame)

                if self.frame_count % 30 == 0:
                    elapsed = time.time() - start_time
                    logger.debug(f"  [{self.camera_id}] Frame {self.frame_count} ({elapsed:.1f}s)")

                time.sleep(1 / self.fps)

            logger.info(f"✓ CAPTURE END: {self.person_id} [{self.camera_id}] - {self.frame_count} frames")

            # Start this camera's postprocessing independently in the background.
            # A failure/empty result here only affects this camera - other cameras'
            # threads are unaffected and keep running.
            if self.frame_count > 0:
                self._start_postprocessing()
            else:
                logger.error(f"❌ [{self.camera_id}] No frames captured")
                self._fail("No frames captured")

        except Exception as e:
            logger.error(f"[{self.camera_id}] Capture error: {e}")
            self._fail(f"Capture error: {e}")

        finally:
            cap.release()

    def _start_postprocessing(self):
        """Start postprocessing for this camera in its own background thread"""
        worker = PostprocessWorker(
            person_id=self.person_id,
            camera_id=self.camera_id,
            camera_source=self.camera_source,
            raw_dir=self.raw_dir,
            best_dir=self.best_dir,
            processor=self.processor,
            shared_report=self.shared_report
        )
        worker.start()

    def _fail(self, reason: str):
        """Record this camera's failure in the shared report and clean up its
        (empty or partial) raw folder. Does not affect any other camera."""
        self.shared_report.update_camera(self.camera_id, self.camera_source, {
            'status': 'error',
            'error': reason,
            'total_frames_captured': self.frame_count,
            'best_frames_selected': 0,
            'top_frames': []
        })
        try:
            if self.raw_dir.exists():
                shutil.rmtree(self.raw_dir)
        except Exception as e:
            logger.error(f"[{self.camera_id}] Cleanup error: {e}")


class PostprocessWorker(threading.Thread):
    """Thread: Analyzes one camera's frames, selects best ones, merges into shared report"""

    def __init__(self, person_id: str, camera_id: str, camera_source: str, raw_dir: Path,
                 best_dir: Path, processor: FrameProcessor, shared_report: SharedReport,
                 num_best: int = 3):
        super().__init__(daemon=False, name=f"Postprocess-{person_id}-{camera_id}")
        self.person_id = person_id
        self.camera_id = camera_id
        self.camera_source = camera_source
        self.raw_dir = raw_dir
        self.best_dir = best_dir
        self.processor = processor
        self.shared_report = shared_report
        self.num_best = num_best

    def run(self):
        logger.info(f"📊 POSTPROCESS START: {self.person_id} [{self.camera_id}]")

        try:
            jpg_files = sorted(list(self.raw_dir.glob("*.jpg")))

            if not jpg_files:
                logger.warning(f"No frames for {self.person_id} [{self.camera_id}]")
                self.shared_report.update_camera(self.camera_id, self.camera_source, {
                    'status': 'error',
                    'error': 'No frames captured',
                    'total_frames_captured': 0,
                    'best_frames_selected': 0,
                    'top_frames': []
                })
                self._cleanup()
                return

            logger.info(f"  [{self.camera_id}] Analyzing {len(jpg_files)} frames...")

            analysis_results = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self.processor.analyze_frame, jpg_file): jpg_file
                    for jpg_file in jpg_files
                }

                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        analysis_results.append(result)

            if not analysis_results:
                logger.warning(f"No valid frames for {self.person_id} [{self.camera_id}]")
                self.shared_report.update_camera(self.camera_id, self.camera_source, {
                    'status': 'error',
                    'error': 'No valid frames after analysis',
                    'total_frames_captured': len(jpg_files),
                    'best_frames_selected': 0,
                    'top_frames': []
                })
                self._cleanup()
                return

            # Only rank/select frames where a person was actually detected -
            # a sharp, well-lit empty frame must never outrank a real person frame.
            person_frames = [r for r in analysis_results if r['person_detected']]

            if not person_frames:
                logger.warning(f"No person detected in any frame for {self.person_id} [{self.camera_id}]")
                self.shared_report.update_camera(self.camera_id, self.camera_source, {
                    'status': 'no_person_detected',
                    'error': 'No frames with a detected person',
                    'total_frames_captured': len(jpg_files),
                    'frames_analyzed': len(analysis_results),
                    'best_frames_selected': 0,
                    'top_frames': []
                })
                self._cleanup()
                return

            person_frames.sort(key=lambda x: x['overall_score'], reverse=True)

            # Select and copy best frames, namespaced by camera
            num_best = min(self.num_best, len(person_frames))
            logger.info(f"  [{self.camera_id}] Selecting {num_best} best frames...")

            for rank, result in enumerate(person_frames[:num_best], 1):
                source_path = Path(result['path'])
                dest_path = self.best_dir / f"{self.camera_id}_best_{rank:02d}_{result['filename']}"

                if source_path.exists():
                    shutil.copy2(source_path, dest_path)
                    logger.info(f"    [{self.camera_id}][{rank}] Score: {result['overall_score']:.2f}")

            self.shared_report.update_camera(self.camera_id, self.camera_source, {
                'status': 'ok',
                'total_frames_captured': len(jpg_files),
                'frames_analyzed': len(analysis_results),
                'frames_with_person': len(person_frames),
                'best_frames_selected': num_best,
                'top_frames': person_frames[:num_best]
            })

            logger.info(f"✓ POSTPROCESS END: {self.person_id} [{self.camera_id}]")

        except Exception as e:
            logger.error(f"[{self.camera_id}] Postprocess error: {e}")
            self.shared_report.update_camera(self.camera_id, self.camera_source, {
                'status': 'error',
                'error': f'Postprocess error: {e}',
                'total_frames_captured': len(list(self.raw_dir.glob("*.jpg"))) if self.raw_dir.exists() else 0,
                'best_frames_selected': 0,
                'top_frames': []
            })

        finally:
            self._cleanup()

    def _cleanup(self):
        try:
            if self.raw_dir.exists():
                shutil.rmtree(self.raw_dir)
                logger.info(f"  🗑️  [{self.camera_id}] Deleted raw frames ({self.raw_dir.name})")
        except Exception as e:
            logger.error(f"[{self.camera_id}] Cleanup error: {e}")


class PersonCaptureProcessor:
    """Main service - Multi-camera, multithreaded capture and postprocessing"""

    def __init__(self, base_dir: str = "person_data", capture_duration: int = 15
    ):
        self.base_dir = Path(base_dir)
        self.capture_duration = capture_duration
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.processor = FrameProcessor()

        logger.info(f"PersonCaptureProcessor initialized")
        logger.info(f"  Base dir: {self.base_dir}")

    def capture_person(self, person_id: str, trigger_at: Optional[str] = None) -> Dict:
        """
        Start capture for a person across all configured cameras (returns immediately)
        Capture and postprocessing run per-camera in background threads
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"🔴 API CALL: person_id={person_id}")
        logger.info(f"{'='*70}")

        try:
            cameras = load_camera_sources()
            logger.info(f"📷 Cameras: {cameras}")

            # Use the trigger_at value from the request as-is for the folder name (falls back to now if missing)
            timestamp = trigger_at if trigger_at else datetime.now().strftime("%Y%m%d_%H%M%S")
            person_dir = self.base_dir / f"{person_id}" / timestamp

            best_dir = person_dir / "best_frames"
            best_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"📁 Folder: {person_dir.name}")

            shared_report = SharedReport(
                path=best_dir / "processing_report.json",
                person_id=person_id,
                cameras=cameras
            )

            # Each camera is set up and started independently - if one camera
            # fails to start (bad folder permissions, etc.) it's recorded in
            # the shared report and the rest still start normally.
            camera_raw_dirs = {}
            started_cameras = []
            for camera_id, camera_source in cameras.items():
                try:
                    raw_dir = person_dir / f"{camera_id}_raw_frames"
                    raw_dir.mkdir(parents=True, exist_ok=True)
                    camera_raw_dirs[camera_id] = str(raw_dir)

                    capture_thread = CaptureWorker(
                        person_id=person_id,
                        camera_id=camera_id,
                        camera_source=camera_source,
                        raw_dir=raw_dir,
                        best_dir=best_dir,
                        processor=self.processor,
                        shared_report=shared_report,
                        duration=self.capture_duration
                    )
                    capture_thread.start()
                    started_cameras.append(camera_id)

                except Exception as e:
                    logger.error(f"❌ [{camera_id}] Failed to start capture: {e}")
                    shared_report.update_camera(camera_id, camera_source, {
                        'status': 'error',
                        'error': f'Failed to start capture: {e}',
                        'total_frames_captured': 0,
                        'best_frames_selected': 0,
                        'top_frames': []
                    })

            # Return immediately (don't wait for capture to finish)
            logger.info(f"✅ API RESPONSE: Capture started in background for {len(started_cameras)}/{len(cameras)} camera(s)")
            logger.info(f"{'='*70}\n")

            return {
                'status': 'started',
                'person_id': person_id,
                'message': 'Capturing - processing in background',
                'timestamp': datetime.now().isoformat(),
                'cameras': list(cameras.keys()),
                'directories': {
                    'best_frames': str(best_dir),
                    'raw_frames': camera_raw_dirs
                }
            }

        except Exception as e:
            logger.error(f"❌ ERROR: {str(e)}")
            return {
                'status': 'error',
                'person_id': person_id,
                'error': str(e),
                'timestamp': datetime.now().isoformat()
            }


# Global service instance
_service = None

def get_service(base_dir: str = "person_data") -> PersonCaptureProcessor:
    global _service
    if _service is None:
        _service = PersonCaptureProcessor(base_dir=base_dir)
    return _service


def capture_person_api(person_id: str, trigger_at: Optional[str] = None) -> Dict:
    """Simple API function - returns immediately"""
    service = get_service()
    return service.capture_person(person_id, trigger_at)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Person Capture Processor (Multi-camera, Multithreaded)")
    parser.add_argument("person_id", type=str, help="Person ID")
    parser.add_argument(
        "--trigger-at", type=str, default=None,
        help="Optional ISO timestamp used as the folder name (defaults to now)"
    )

    args = parser.parse_args()

    service = get_service()
    result = service.capture_person(args.person_id, args.trigger_at)

    print("\n" + "="*70)
    print("RESPONSE:")
    print("="*70)
    print(json.dumps(result, indent=2))

    # Keep running for background processing
    print("\n🔄 Processing in background...\n")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n✓ Shutdown")
