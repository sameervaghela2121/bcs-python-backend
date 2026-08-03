#!/usr/bin/env python3
"""
Best Frame Selection Analysis
Analyzes all saved detection frames and selects best frames based on quality criteria:
- Brightness
- Person visibility/size
- Face clarity
- Sharpness
"""

import cv2
import os
import json
import numpy as np
from pathlib import Path
from datetime import datetime
from ultralytics import YOLO


class FrameAnalyzer:
    """Analyzes frames and scores them based on quality criteria"""
    
    def __init__(self, model_path="yolov8n.pt"):
        """Initialize with YOLOv8 model for person detection"""
        self.model = YOLO(model_path)
        print(f"✓ Loaded model: {model_path}")
    
    def calculate_brightness(self, frame):
        """Calculate brightness score (0-100)"""
        # Convert to grayscale and calculate mean brightness
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        brightness = np.mean(gray)
        # Normalize to 0-100, optimal around 128
        score = 100 - abs(brightness - 128) / 128 * 100
        return max(0, min(100, score))
    
    def calculate_contrast(self, frame):
        """Calculate contrast score (0-100)"""
        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Calculate standard deviation (contrast)
        contrast = np.std(gray)
        # Normalize to 0-100, optimal contrast around 50-60
        score = (contrast / 100) * 100
        return max(0, min(100, score))
    
    def calculate_sharpness(self, frame):
        """Calculate sharpness score using Laplacian variance (0-100)"""
        # Convert to grayscale
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        # Apply Laplacian filter
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        # Calculate variance (sharpness metric)
        sharpness = np.var(laplacian)
        # Normalize to 0-100
        score = min(100, (sharpness / 500) * 100)
        return score
    
    def calculate_person_size(self, frame, detections):
        """Calculate person size score based on bounding box area (0-100)"""
        if not detections or len(detections) == 0:
            return 0
        
        frame_area = frame.shape[0] * frame.shape[1]
        best_score = 0
        
        for det in detections:
            x1, y1, x2, y2 = det['bbox']
            person_area = (x2 - x1) * (y2 - y1)
            # Optimal person size is around 15-30% of frame
            area_ratio = (person_area / frame_area) * 100
            
            # Score higher for larger persons (15-30% is ideal)
            if area_ratio > 30:
                score = 100 - (area_ratio - 30) / 50 * 100
            else:
                score = (area_ratio / 30) * 100
            
            best_score = max(best_score, score)
        
        return max(0, min(100, best_score))
    
    def calculate_person_centering(self, frame, detections):
        """Calculate centering score based on person position (0-100)"""
        if not detections or len(detections) == 0:
            return 0
        
        frame_height, frame_width = frame.shape[:2]
        best_score = 0
        
        for det in detections:
            x1, y1, x2, y2 = det['bbox']
            # Calculate center of person
            person_center_x = (x1 + x2) / 2
            person_center_y = (y1 + y2) / 2
            
            # Calculate distance from frame center
            frame_center_x = frame_width / 2
            frame_center_y = frame_height / 2
            
            # Distance as percentage of frame
            dist_x = abs(person_center_x - frame_center_x) / frame_width * 100
            dist_y = abs(person_center_y - frame_center_y) / frame_height * 100
            
            # Score: closer to center = higher score
            score = 100 - (dist_x + dist_y) / 2
            best_score = max(best_score, score)
        
        return max(0, min(100, best_score))
    
    def detect_persons(self, frame):
        """Detect persons in frame"""
        results = self.model(frame, conf=0.5, classes=0)
        detections = []
        
        for r in results:
            for box in r.boxes:
                if box.conf > 0.5:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    conf = float(box.conf)
                    detections.append({
                        'bbox': (x1, y1, x2, y2),
                        'confidence': conf,
                        'class': 'person'
                    })
        
        return detections
    
    def analyze_frame(self, frame_path):
        """Analyze single frame and return quality scores"""
        frame = cv2.imread(str(frame_path))
        
        if frame is None:
            print(f"✗ Cannot read frame: {frame_path}")
            return None
        
        # Detect persons
        detections = self.detect_persons(frame)
        
        # Calculate individual scores
        brightness = self.calculate_brightness(frame)
        contrast = self.calculate_contrast(frame)
        sharpness = self.calculate_sharpness(frame)
        person_size = self.calculate_person_size(frame, detections)
        person_center = self.calculate_person_centering(frame, detections)
        person_confidence = detections[0]['confidence'] * 100 if detections else 0
        
        # Weighted overall score
        # Weighting: person detection (30%), person size (20%), sharpness (20%), centering (15%), brightness (10%), contrast (5%)
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
            'person_detected': len(detections) > 0,
            'num_persons': len(detections)
        }


def analyze_detection_folder(detection_dir, output_best_dir="best_frames"):
    """
    Analyze all frames in detection directory and save best frames
    
    Args:
        detection_dir: Directory containing detected frames
        output_best_dir: Directory to save best frames
    """
    
    detection_path = Path(detection_dir)
    best_frames_path = Path(output_best_dir)
    best_frames_path.mkdir(parents=True, exist_ok=True)
    
    # Check if detection directory exists
    if not detection_path.exists():
        print(f"✗ Detection directory not found: {detection_dir}")
        return
    
    # Get all JPG files
    jpg_files = list(detection_path.glob("*.jpg"))
    
    if not jpg_files:
        print(f"✗ No JPG files found in: {detection_dir}")
        return
    
    print(f"✓ Found {len(jpg_files)} frames to analyze in: {detection_dir}\n")
    
    # Initialize analyzer
    analyzer = FrameAnalyzer()
    
    # Analyze all frames
    analysis_results = []
    for idx, jpg_file in enumerate(jpg_files):
        print(f"[{idx+1}/{len(jpg_files)}] Analyzing: {jpg_file.name}...", end=" ")
        result = analyzer.analyze_frame(jpg_file)
        
        if result:
            analysis_results.append(result)
            print(f"Score: {result['overall_score']:.2f}")
        else:
            print("Failed")
    
    if not analysis_results:
        print("✗ No frames analyzed successfully")
        return
    
    # Sort by overall score (descending)
    analysis_results.sort(key=lambda x: x['overall_score'], reverse=True)
    
    # Save top 5 best frames
    num_best = min(5, len(analysis_results))
    print(f"\n{'='*70}")
    print(f"TOP {num_best} BEST FRAMES:")
    print(f"{'='*70}\n")
    
    for rank, result in enumerate(analysis_results[:num_best], 1):
        print(f"🏆 Rank {rank}: {result['filename']}")
        print(f"   Overall Score: {result['overall_score']:.2f}/100")
        print(f"   Person Confidence: {result['scores']['person_confidence']:.2f}")
        print(f"   Sharpness: {result['scores']['sharpness']:.2f}")
        print(f"   Person Size: {result['scores']['person_size']:.2f}")
        print(f"   Brightness: {result['scores']['brightness']:.2f}")
        print(f"   Contrast: {result['scores']['contrast']:.2f}")
        print(f"   Centering: {result['scores']['person_centering']:.2f}")
        print()
        
        # Copy best frame to best_frames directory
        source_path = Path(result['path'])
        dest_path = best_frames_path / f"best_frame_{rank}_{result['filename']}"
        
        if source_path.exists():
            import shutil
            shutil.copy2(source_path, dest_path)
            print(f"   ✓ Copied to: {dest_path}\n")
    
    # Save analysis report as JSON
    report_path = best_frames_path / "analysis_report.json"
    report = {
        'timestamp': datetime.now().isoformat(),
        'total_frames_analyzed': len(analysis_results),
        'detection_directory': str(detection_path),
        'best_frames_directory': str(best_frames_path),
        'report_path': str(report_path),
        'top_frames': analysis_results[:num_best],
        'all_frames': analysis_results
    }
    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)

    print(f"{'='*70}")
    print(f"✓ Analysis complete!")
    print(f"  Best frames saved to: {best_frames_path}")
    print(f"  Report saved to: {report_path}")
    print(f"  Total frames analyzed: {len(analysis_results)}")
    print(f"{'='*70}")

    return report


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Analyze detection frames and select best")
    parser.add_argument("--detection-dir", type=str, default="detected_frames",
                       help="Directory containing detected frames")
    parser.add_argument("--output-dir", type=str, default="best_frames",
                       help="Output directory for best frames")
    parser.add_argument("--model", type=str, default="yolov8n.pt",
                       help="YOLOv8 model path")
    
    args = parser.parse_args()
    
    analyze_detection_folder(args.detection_dir, args.output_dir)


if __name__ == "__main__":
    main()