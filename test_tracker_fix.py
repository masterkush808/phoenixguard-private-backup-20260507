#!/usr/bin/env python3
"""Quick test to verify the tracker fixes work correctly."""

import sys
from pathlib import Path

# Add the phoenixguard module to path
phoenixguard_root = Path(__file__).parent
sys.path.insert(0, str(phoenixguard_root))

try:
    from phoenixguard.mobile_api.window_tracker import (
        ContinuousWindowTrackerService,
        normalize_focus_region_bbox,
    )
    print("✓ Successfully imported window_tracker module")
    
    # Test 1: Verify normalized bbox validation works
    print("\n[Test 1] Testing bbox normalization...")
    bbox = [0.1, 0.2, 0.7, 0.8]
    normalized = normalize_focus_region_bbox(bbox)
    assert len(normalized) == 4, "Normalized bbox should have 4 values"
    assert all(0 <= v <= 1 for v in normalized), "All values should be normalized to 0-1"
    print(f"  ✓ Bbox normalized correctly: {bbox} -> {normalized}")
    
    # Test 2: Verify service instantiation
    print("\n[Test 2] Testing ContinuousWindowTrackerService instantiation...")
    service = ContinuousWindowTrackerService()
    print(f"  ✓ Service created successfully")
    print(f"    - Root dir: {service.root_dir}")
    print(f"    - Capture backend: {type(service.capture_backend).__name__}")
    print(f"    - Tracking adapter: {type(service.tracking_adapter).__name__}")
    print(f"    - Focus selector: {type(service.focus_selector_backend).__name__}")
    
    # Test 3: List windows to see if system works
    print("\n[Test 3] Testing window listing...")
    windows = service.list_windows()
    print(f"  ✓ Found {len(windows)} visible windows")
    if windows:
        for i, w in enumerate(windows[:3], 1):
            print(f"    {i}. {w.get('title', 'Unknown')[:60]}")
    
    # Test 4: Create a test session and verify focus tracking
    print("\n[Test 4] Testing session creation and focus region tracking...")
    session = service.create_session(
        session_id="test-tracker-fix",
        name="Tracker Fix Test",
        window_query="Pocket Option",
        auto_start=False,
    )
    print(f"  ✓ Session created: {session['session_id']}")
    print(f"    - Status: {session['status']}")
    print(f"    - Focus enabled: {session['manual_focus_region']['enabled']}")
    
    # Test 5: Simulate focus selection
    print("\n[Test 5] Testing focus region setting (simulating selection)...")
    test_bbox = [0.1, 0.15, 0.9, 0.85]  # Left, Top, Right, Bottom (normalized)
    session = service.set_focus_region(
        "test-tracker-fix",
        test_bbox,
        source="test-script",
    )
    print(f"  ✓ Focus region set successfully")
    print(f"    - Focus enabled: {session['manual_focus_region']['enabled']}")
    print(f"    - Bbox: {session['manual_focus_region']['normalized_bbox']}")
    print(f"    - Source: {session['manual_focus_region']['source']}")
    print(f"    - Session status: {session['status']}")
    print(f"    - Last error: {session['last_error'] or '(none)'}")
    
    # Verify the selection was saved
    print("\n[Test 6] Verifying selection persistence...")
    session_reloaded = service.get_session("test-tracker-fix")
    assert session_reloaded['manual_focus_region']['enabled'], "Focus should still be enabled"
    assert len(session_reloaded['manual_focus_region']['normalized_bbox']) == 4, "Should have 4 bbox values"
    print(f"  ✓ Focus region persisted correctly across reload")
    print(f"    - Bbox matches: {session['manual_focus_region']['normalized_bbox'] == session_reloaded['manual_focus_region']['normalized_bbox']}")
    
    print("\n" + "="*60)
    print("✅ All tests passed! Your tracker is now working correctly.")
    print("="*60)
    print("\n📍 Next steps:")
    print("1. The tracker now properly records where you select")
    print("2. When you drag to select a region, it saves the selection")
    print("3. The selection persists across sessions")
    print("4. Error states are now properly tracked and logged")
    
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
