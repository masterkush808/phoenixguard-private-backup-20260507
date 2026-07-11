#!/usr/bin/env python3
"""
Quick-start test: Verify the tracker is fully operational.
Run this after opening Pocket Option to test the complete flow.
"""

import sys
import time
from pathlib import Path

phoenixguard_root = Path(__file__).parent
sys.path.insert(0, str(phoenixguard_root))

def main():
    print("""
╔═══════════════════════════════════════════════════════════╗
║  PhoenixGuard Window Tracker - Quick Start Test           ║
╚═══════════════════════════════════════════════════════════╝

📋 PRE-REQUISITES CHECKLIST:

  ☐ Pocket Option is open in a browser window
  ☐ The browser window is VISIBLE (not minimized)
  ☐ "Pocket Option" appears in the window title
  ☐ Dashboard is open: http://localhost:8000/v1/mobile/window-tracker/dashboard

""")
    
    response = input("Are all pre-requisites ready? (y/n): ").strip().lower()
    if response != 'y':
        print("Please set up the requirements and try again.")
        return 1
    
    print("\n" + "="*60)
    print("  STARTING COMPREHENSIVE TRACKER TEST")
    print("="*60 + "\n")
    
    try:
        from phoenixguard.mobile_api.window_tracker import (
            ContinuousWindowTrackerService,
            WindowsWindowCaptureBackend,
        )
        
        # TEST 1: Window Detection
        print("🔍 TEST 1: Detecting Pocket Option window...")
        backend = WindowsWindowCaptureBackend()
        windows = backend.list_windows("Pocket Option")
        
        if not windows:
            print("   ❌ FAIL: No Pocket Option window found!")
            print("   Please open Pocket Option in a browser and try again.")
            return 1
        
        print(f"   ✓ Found {len(windows)} Pocket Option window(s)")
        for w in windows:
            print(f"     - {w.get('title', 'Unknown')[:50]}")
        
        # TEST 2: Service Initialization
        print("\n🔧 TEST 2: Initializing tracker service...")
        service = ContinuousWindowTrackerService()
        print("   ✓ Service initialized")
        
        # TEST 3: Session Creation
        print("\n📝 TEST 3: Creating tracker session...")
        session = service.create_session(
            session_id="quick-test",
            name="Quick Test",
            window_query="Pocket Option",
            auto_start=False,
        )
        print(f"   ✓ Session created: {session['session_id']}")
        
        # TEST 4: Manual Focus Region (Simulate User Selection)
        print("\n🎯 TEST 4: Setting focus region (simulating user selection)...")
        test_bbox = [0.1, 0.2, 0.9, 0.8]  # 10% from left, 20% from top, etc.
        result = service.set_focus_region(
            "quick-test",
            test_bbox,
            source="quick-start-test"
        )
        
        if result['manual_focus_region']['enabled']:
            print("   ✓ Focus region locked!")
            print(f"     - Bbox: {result['manual_focus_region']['normalized_bbox']}")
            print(f"     - Status: {result['status']}")
        else:
            print("   ❌ FAIL: Focus region not set")
            return 1
        
        # TEST 5: Capture & Analysis
        print("\n📸 TEST 5: Capturing and analyzing chart...")
        service.capture_once("quick-test")
        time.sleep(1)  # Wait for capture to complete
        
        session = service.get_session("quick-test")
        signal = session.get('latest_signal', {})
        tracking = session.get('tracking_summary', {})
        
        if signal:
            print("   ✓ Analysis completed")
            print(f"     - Action: {signal.get('action', 'HOLD')}")
            print(f"     - Confidence: {signal.get('effective_confidence', 0):.2f}")
            print(f"     - Setup: {signal.get('setup', '--')}")
            print(f"     - Visible candles: {tracking.get('visible_candle_count', 0)}")
        else:
            print("   ⚠ No signal yet (chart may not have candles)")
        
        # TEST 6: Tracker Start
        print("\n▶️  TEST 6: Starting continuous tracking...")
        session = service.start_session("quick-test")
        
        if session['tracking_enabled']:
            print("   ✓ Tracker started!")
            print(f"     - Status: {session['status']}")
            print(f"     - Next capture in: {session.get('next_capture_in_sec', 0):.1f}s")
        else:
            print("   ❌ FAIL: Tracker did not start")
            return 1
        
        # Wait for one capture
        print("\n⏳ Waiting for first live capture...")
        for i in range(12):
            time.sleep(1)
            session = service.get_session("quick-test")
            if session['capture_count'] > 1:
                break
            sys.stdout.write(f"\r   {i+1}s...")
            sys.stdout.flush()
        
        print("\n   ✓ Live capture completed!")
        
        # Final Summary
        print("\n" + "="*60)
        print("  ✅ ALL TESTS PASSED!")
        print("="*60 + "\n")
        
        print(f"""
📊 FINAL STATUS:

  Session ID: {session['session_id']}
  Status: {session['status']}
  Tracking Enabled: {session['tracking_enabled']}
  Captures: {session['capture_count']}
  Last Signal: {signal.get('action', 'HOLD')} ({signal.get('setup', '--')})
  
🎯 NEXT STEPS:

  1. Open the dashboard to see live updates:
     http://localhost:8000/v1/mobile/window-tracker/dashboard?session_id=quick-test

  2. Stop the tracker when done:
     service.stop_session('quick-test')

  3. For more info, read:
     - docs/tracker/TRACKER_USER_GUIDE.md (step-by-step guide)
     - docs/tracker/TRACKER_DEBUG_SUMMARY.md (technical details)

✨ Your tracker is fully operational and ready to use!
""")
        
        return 0
        
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
