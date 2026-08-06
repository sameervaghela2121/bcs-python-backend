#!/usr/bin/env python3
"""
Person Detection & Frame Processing Service
Multithreaded - Capture & Postprocessing in background
API returns immediately after starting capture

Workflow:
1. API Call starts CaptureWorker thread (10 seconds)
2. API returns immediately
3. Capture finishes → Queues PostprocessWorker thread
4. Postprocessing runs in background (analyze, select best, delete raw)
5. Each call creates new timestamped folder
6. Multiple people processed in parallel
"""

import cv2
import os
import json
import time
import threading
import shutil
from pathlib import Path
from datetime import datetime
import numpy as np
from concurrent.futures import ThreadPoolExecutor, as_completed
from queue import Queue
from typing import Dict, Optional
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(threadName)-12s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class FrameProcessor:
    """Analyzes frames with lazy YOLOv8 loading"""

    def __init__(self):
        self.model = None

    def _load_model(self):
        if self.model is None:
            try:
                from ultralytics import YOLO
                logger.info("Loading YOLOv8...")
                self.model = YOLO("yolov8n.pt", verbose=False)
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
        score = min(100, (sharpness / 500) * 100)
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
            person_confidence * 0.30 +
            person_size * 0.20 +
            sharpness * 0.20 +
            person_center * 0.15 +
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


class CaptureWorker(threading.Thread):
    """Thread: Captures frames for 10 seconds and queues postprocessing"""

    def __init__(self, person_id: str, raw_dir: Path, best_dir: Path, camera_source: str,
                 processor: FrameProcessor, duration: int = 10, fps: int = 30):
        super().__init__(daemon=False, name=f"Capture-{person_id}")
        self.person_id = person_id
        self.raw_dir = raw_dir
        self.best_dir = best_dir
        self.camera_source = camera_source
        self.processor = processor
        self.duration = duration
        self.fps = fps
        self.frame_count = 0

    def run(self):
        logger.info(f"🎬 CAPTURE START: {self.person_id}")

        cap = cv2.VideoCapture(self.camera_source if self.camera_source.startswith("")
                              else int(self.camera_source))

        if not cap.isOpened():
            logger.error(f"❌ Cannot open: {self.camera_source}")
            return

        start_time = time.time()

        try:
            while time.time() - start_time < self.duration:
                ret, frame = cap.read()
                if not ret:
                    logger.warning(f"Failed to read frame")
                    break

                self.frame_count += 1
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"frame_{self.frame_count:04d}_{timestamp}.jpg"
                filepath = self.raw_dir / filename

                cv2.imwrite(str(filepath), frame)

                if self.frame_count % 30 == 0:
                    elapsed = time.time() - start_time
                    logger.debug(f"  [{self.person_id}] Frame {self.frame_count} ({elapsed:.1f}s)")

                time.sleep(1 / self.fps)

            logger.info(f"✓ CAPTURE END: {self.person_id} - {self.frame_count} frames")

            # Start postprocessing in background
            if self.frame_count > 0:
                self._start_postprocessing()

        except Exception as e:
            logger.error(f"Capture error: {e}")

        finally:
            cap.release()

    def _start_postprocessing(self):
        """Start postprocessing in background thread"""
        worker = PostprocessWorker(
            person_id=self.person_id,
            raw_dir=self.raw_dir,
            best_dir=self.best_dir,
            processor=self.processor
        )
        worker.start()


class PostprocessWorker(threading.Thread):
    """Thread: Analyzes frames and selects best ones (background)"""

    def __init__(self, person_id: str, raw_dir: Path, best_dir: Path, processor: FrameProcessor, num_best: int = 5):
        super().__init__(daemon=False, name=f"Postprocess-{person_id}")
        self.person_id = person_id
        self.raw_dir = raw_dir
        self.best_dir = best_dir
        self.processor = processor
        self.num_best = num_best

    def run(self):
        logger.info(f"📊 POSTPROCESS START: {self.person_id}")

        try:
            jpg_files = sorted(list(self.raw_dir.glob("*.jpg")))

            if not jpg_files:
                logger.warning(f"No frames for {self.person_id}")
                self._cleanup()
                return

            logger.info(f"  Analyzing {len(jpg_files)} frames...")

            analysis_results = []
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self.processor.analyze_frame, jpg_file): jpg_file
                    for jpg_file in jpg_files
                }

                completed = 0
                for future in as_completed(futures):
                    completed += 1
                    result = future.result()
                    if result:
                        analysis_results.append(result)

            if not analysis_results:
                logger.warning(f"No valid frames for {self.person_id}")
                self._cleanup()
                return

            analysis_results.sort(key=lambda x: x['overall_score'], reverse=True)

            # Select and copy best frames
            num_best = min(self.num_best, len(analysis_results))
            logger.info(f"  Selecting {num_best} best frames...")

            for rank, result in enumerate(analysis_results[:num_best], 1):
                source_path = Path(result['path'])
                dest_path = self.best_dir / f"best_{rank:02d}_{result['filename']}"

                if source_path.exists():
                    shutil.copy2(source_path, dest_path)
                    logger.info(f"    [{rank}] Score: {result['overall_score']:.2f}")

            # Save report
            report_path = self.best_dir / "processing_report.json"
            with open(report_path, 'w') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'person_id': self.person_id,
                    'total_frames_captured': len(jpg_files),
                    'best_frames_selected': num_best,
                    'top_frames': analysis_results[:num_best]
                }, f, indent=2)

            logger.info(f"✓ POSTPROCESS END: {self.person_id}")

        except Exception as e:
            logger.error(f"Postprocess error: {e}")

        finally:
            self._cleanup()

    def _cleanup(self):
        try:
            if self.raw_dir.exists():
                shutil.rmtree(self.raw_dir)
                logger.info(f"  🗑️  Deleted raw frames")
        except Exception as e:
            logger.error(f"Cleanup error: {e}")


class PersonCaptureProcessor:
    """Main service - Multithreaded capture and postprocessing"""

    def __init__(self, base_dir: str = "person_data", capture_duration: int = 15):
        self.base_dir = Path(base_dir)
        self.capture_duration = capture_duration
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.processor = FrameProcessor()

        logger.info(f"PersonCaptureProcessor initialized")
        logger.info(f"  Base dir: {self.base_dir}")

    def capture_person(self, person_id: str, camera_source: str = "0", trigger_at: Optional[str] = None) -> Dict:
        """
        Start capture for a person (returns immediately)
        Capture and postprocessing run in background threads
        """
        logger.info(f"\n{'='*70}")
        logger.info(f"🔴 API CALL: person_id={person_id}")
        logger.info(f"{'='*70}")

        try:
            # Use the trigger_at value from the request as-is for the folder name (falls back to now if missing)
            timestamp = trigger_at if trigger_at else datetime.now().strftime("%Y%m%d_%H%M%S")
            person_dir = self.base_dir / f"{person_id}" / timestamp

            raw_dir = person_dir / "raw_frames"
            best_dir = person_dir / "best_frames"

            raw_dir.mkdir(parents=True, exist_ok=True)
            best_dir.mkdir(parents=True, exist_ok=True)

            logger.info(f"📁 Folder: {person_dir.name}")

            # Start capture thread
            capture_thread = CaptureWorker(
                person_id=person_id,
                raw_dir=raw_dir,
                best_dir=best_dir,
                camera_source=camera_source,
                processor=self.processor,
                duration=self.capture_duration
            )
            capture_thread.start()

            # Return immediately (don't wait for capture to finish)
            logger.info(f"✅ API RESPONSE: Capture started in background")
            logger.info(f"{'='*70}\n")

            return {
                'status': 'started',
                'person_id': person_id,
                'message': 'Capturing - processing in background',
                'timestamp': datetime.now().isoformat(),
                'directories': {
                    'best_frames': str(best_dir)
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


def capture_person_api(person_id: str, camera_source: str = "0", trigger_at: Optional[str] = None) -> Dict:
    """Simple API function - returns immediately"""
    service = get_service()
    return service.capture_person(person_id, camera_source, trigger_at)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Person Capture Processor (Multithreaded)")
    parser.add_argument("person_id", type=str, help="Person ID")
    parser.add_argument("--source", type=str, default="0", help="Camera source (index or RTSP URL)")

    args = parser.parse_args()

    service = get_service()
    result = service.capture_person(args.person_id, args.source)

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
