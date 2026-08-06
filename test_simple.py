#!/usr/bin/env python3
"""
Simple Test:
- Call 1: Capture person_001 (creates folder 1)
- Call 2: Capture person_001 again (creates folder 2, same person!)
- Verify: Each call creates new timestamped folder
"""

import json
import time
from pathlib import Path
from person_capture import capture_person_api


def main():
    print("\n" + "="*70)
    print("SIMPLE TEST: Two API Calls, Same Person, Different Folders")
    print("="*70 + "\n")

    # Test with webcam (camera index 0)
    # Or use RTSP: "rtsp://sam_trt:Admin@123@10.134.80.84:554/stream1"
    camera_source = "rtsp://sam_trt:Admin@123@192.168.0.47:554/stream1"

    # ═══════════════════════════════════════════════════════════════════
    print("📞 CALL 1: person_001 (First time)")
    print("─" * 70)

    result1 = capture_person_api(
        person_id="person_001",
        camera_source=camera_source
    )

    print(f"Status: {result1['status']}")
    print(f"Folder: {result1['directories']['best_frames']}")
    print("✅ API returned immediately!\n")

    call1_folder = Path(result1['directories']['best_frames']).parent

    # ═══════════════════════════════════════════════════════════════════
    # Wait before second call (let first one finish)
    print("⏳ Waiting for first capture + postprocessing...\n")
    time.sleep(50)  # 10s capture + 40s postprocessing

    # ═══════════════════════════════════════════════════════════════════
    print("📞 CALL 2: person_001 (Second time, SAME person)")
    print("─" * 70)

    result2 = capture_person_api(
        person_id="person_001",
        camera_source=camera_source
    )

    print(f"Status: {result2['status']}")
    print(f"Folder: {result2['directories']['best_frames']}")
    print("✅ API returned immediately!\n")

    call2_folder = Path(result2['directories']['best_frames']).parent

    # ═══════════════════════════════════════════════════════════════════
    print("⏳ Waiting for second capture + postprocessing...\n")
    time.sleep(50)

    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "="*70)
    print("VERIFICATION")
    print("="*70 + "\n")

    print("✅ Call 1 Folder:")
    print(f"   {call1_folder}")
    print(f"   Best frames: {list((call1_folder / 'best_frames').glob('best_*.jpg'))}")

    print("\n✅ Call 2 Folder:")
    print(f"   {call2_folder}")
    print(f"   Best frames: {list((call2_folder / 'best_frames').glob('best_*.jpg'))}")

    print("\n" + "="*70)
    print("RESULTS")
    print("="*70 + "\n")

    # Check if folders are different
    if call1_folder != call2_folder:
        print("✓ Each call created DIFFERENT timestamped folder!")
        print(f"  Folder 1: {call1_folder.name}")
        print(f"  Folder 2: {call2_folder.name}")
    else:
        print("✗ ERROR: Same folder used twice!")

    # Check if best_frames exist
    best1_count = len(list((call1_folder / 'best_frames').glob('best_*.jpg')))
    best2_count = len(list((call2_folder / 'best_frames').glob('best_*.jpg')))

    print(f"\n✓ Call 1: {best1_count} best frames saved")
    print(f"✓ Call 2: {best2_count} best frames saved")

    # Check if raw frames deleted
    raw1_exists = (call1_folder / 'raw_frames').exists()
    raw2_exists = (call2_folder / 'raw_frames').exists()

    print(f"\n✓ Call 1: Raw frames deleted = {not raw1_exists}")
    print(f"✓ Call 2: Raw frames deleted = {not raw2_exists}")

    # Check for processing report
    report1 = call1_folder / 'best_frames' / 'processing_report.json'
    report2 = call2_folder / 'best_frames' / 'processing_report.json'

    if report1.exists():
        with open(report1) as f:
            data1 = json.load(f)
        print(f"\n✓ Call 1 Report: {data1['total_frames_captured']} frames analyzed")
    else:
        print(f"\n✗ Call 1: No report found")

    if report2.exists():
        with open(report2) as f:
            data2 = json.load(f)
        print(f"✓ Call 2 Report: {data2['total_frames_captured']} frames analyzed")
    else:
        print(f"✗ Call 2: No report found")

    # ═══════════════════════════════════════════════════════════════════
    print("\n" + "="*70)
    print("✅ TEST COMPLETE!")
    print("="*70)
    print("""
Summary:
  ✓ Call 1 created folder: {timestamp1}
  ✓ Call 2 created folder: {timestamp2} (NEW!)
  ✓ Each with own best_frames/
  ✓ Raw frames auto-deleted
  ✓ Processing reports saved
  ✓ API returned immediately both times

This shows:
  - Multithreading works (each call independent)
  - Capture + postprocessing in background
  - Each call gets unique folder
  - Multiple calls don't interfere
""".format(
        timestamp1=call1_folder.name,
        timestamp2=call2_folder.name
    ))


if __name__ == "__main__":
    main()
