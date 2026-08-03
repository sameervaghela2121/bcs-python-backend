#!/usr/bin/env python3
"""
Person Detection and Frame Storage
Detects person in video stream and saves raw frame to local disk.
"""

import cv2
import os
import time
from pathlib import Path
from datetime import datetime
from ultralytics import YOLO


def capture_person_frames(
    rtsp_url,
    model_path="yolov8n.pt",
    output_dir="detected_frames",
    confidence=0.5,
    duration=None,
    no_detection_timeout=10,
    show_preview=True,
):
    """
    Detect persons in an RTSP stream and save a frame to disk each time one appears.

    duration: max seconds to run (None = run until the no-detection timeout or 'q').
    show_preview: open a live cv2 window (only works with a display, not from an API).

    Returns a dict of {frame_count, detection_count, output_dir}.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    model = YOLO(model_path)

    cap = cv2.VideoCapture(rtsp_url)
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open RTSP stream: {rtsp_url}")

    frame_count = 0
    detection_count = 0
    last_detection_time = None
    start_time = time.time()

    try:
        while True:
            if duration is not None and (time.time() - start_time) > duration:
                break

            ret, frame = cap.read()
            if not ret:
                break

            frame_count += 1

            # Run YOLOv8 detection
            results = model(frame, conf=confidence, classes=0)  # class 0 = person
            has_person = len(results[0].boxes) > 0

            if has_person:
                detection_count += 1
                last_detection_time = time.time()

                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                filename = f"person_detection_{detection_count}_{timestamp}.jpg"
                filepath = os.path.join(output_dir, filename)
                cv2.imwrite(filepath, frame)

                confidence_score = float(results[0].boxes[0].conf)
                print(f"[{frame_count}] Person detected (confidence: {confidence_score:.2f}) -> Saved: {filename}")

                if show_preview:
                    for box in results[0].boxes:
                        x1, y1, x2, y2 = map(int, box.xyxy[0])
                        conf = float(box.conf)
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
                        cv2.putText(frame, f"Person {conf:.2f}", (x1, y1 - 10),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            else:
                if last_detection_time is not None:
                    time_since_detection = time.time() - last_detection_time
                    if time_since_detection > no_detection_timeout:
                        print(f"\nTimeout: No person detected for {no_detection_timeout} seconds. Stopping.")
                        break

                    if show_preview:
                        remaining_time = no_detection_timeout - time_since_detection
                        cv2.putText(frame, f"No detection: {remaining_time:.1f}s", (10, 60),
                                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)

            if show_preview:
                info_text = f"Frame: {frame_count} | Detections: {detection_count}"
                cv2.putText(frame, info_text, (10, 30),
                           cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
                cv2.imshow("Person Detection", frame)

                if cv2.waitKey(1) & 0xFF == ord('q'):
                    print("\nQuitting...")
                    break

    finally:
        cap.release()
        if show_preview:
            cv2.destroyAllWindows()

    return {
        "frame_count": frame_count,
        "detection_count": detection_count,
        "output_dir": output_dir,
    }


def main():
    # Configuration
    RTSP_URL = os.environ.get("RTSP_URL")  # e.g. rtsp://user:pass@192.168.1.100:554/stream1
    MODEL = "yolov8n.pt"  # YOLOv8 model
    OUTPUT_DIR = "detected_frames"  # Local directory to store frames
    CONFIDENCE = 0.5  # Detection confidence threshold

    if not RTSP_URL:
        print("Set the RTSP_URL environment variable, e.g.:")
        print("  export RTSP_URL='rtsp://user:pass@192.168.1.100:554/stream1'")
        return

    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Loading model: {MODEL}...")
    print("Press 'q' to quit\n")

    stats = capture_person_frames(
        rtsp_url=RTSP_URL,
        model_path=MODEL,
        output_dir=OUTPUT_DIR,
        confidence=CONFIDENCE,
        show_preview=True,
    )

    print(f"\n{'='*50}")
    print(f"Detection Summary:")
    print(f"  Total frames processed: {stats['frame_count']}")
    print(f"  People detected: {stats['detection_count']}")
    print(f"  Frames saved to: {stats['output_dir']}")
    print(f"{'='*50}")


if __name__ == "__main__":
    # IMPORTANT: Update RTSP_URL in the main() function with your camera details
    # Examples:
    #   rtsp://192.168.1.100:554/stream
    #   rtsp://username:password@192.168.1.100:554/stream
    #   rtsp://192.168.1.100/stream1
    main()