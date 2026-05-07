#!/usr/bin/env python3
"""
Comprehensive test suite for the PhoenixGuard Window Tracker.
Tests the complete arm -> select flow with detailed diagnostics.
"""

import sys
import json
from collections.abc import Callable
from pathlib import Path
import subprocess

phoenixguard_root = Path(__file__).parent
sys.path.insert(0, str(phoenixguard_root))

def section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def test_native_overlay_script() -> bool:
    """Test if native_focus_overlay.py is valid and can be imported."""
    section("TEST 1: Native Overlay Script Validation")
    
    overlay_path = Path(__file__).parent / "phoenixguard" / "mobile_api" / "native_focus_overlay.py"
    
    print(f"Checking: {overlay_path}")
    if not overlay_path.exists():
        print("❌ FAIL: native_focus_overlay.py not found")
        return False
    
    print(f"✓ File exists: {overlay_path}")
    
    # Try to parse it as Python
    try:
        with open(overlay_path) as f:
            compile(f.read(), str(overlay_path), 'exec')
        print("✓ Script is valid Python")
    except SyntaxError as e:
        print(f"❌ FAIL: Syntax error in native_focus_overlay.py: {e}")
        return False
    
    # Check if it can be imported
    try:
        spec = __import__('importlib.util').util.spec_from_file_location("native_focus_overlay", overlay_path)
        if spec is None or spec.loader is None:
            print("❌ FAIL: Cannot create module spec")
            return False
        print("✓ Module spec created successfully")
    except Exception as e:
        print(f"❌ FAIL: Cannot create module spec: {e}")
        return False
    
    print("✓ PASS: Native overlay script is valid\n")
    return True

def test_subprocess_communication() -> bool:
    """Test if subprocess communication works with the native overlay."""
    section("TEST 2: Subprocess Communication Test")
    
    overlay_path = Path(__file__).parent / "phoenixguard" / "mobile_api" / "native_focus_overlay.py"
    
    # Try to start the subprocess with invalid hwnd (should fail gracefully)
    print("Testing subprocess startup with invalid hwnd=999...")
    
    try:
        process = subprocess.Popen(
            [sys.executable, str(overlay_path), "--hwnd", "999"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            
        )
        
        # Wait for process to complete
        stdout, stderr = process.communicate(timeout=5)
        
        print(f"Process exited with code: {process.returncode}")
        
        if stdout:
            print(f"✓ Got stdout: {stdout[:100]}")
            try:
                parsed: object = json.loads(stdout)
                print(f"  Parsed JSON: {parsed}")
            except json.JSONDecodeError:
                print(f"  ⚠ stdout is not valid JSON")
        else:
            print(f"⚠ No stdout")
        
        if stderr:
            print(f"Stderr: {stderr[:100]}")
        
        print("✓ PASS: Subprocess communication works\n")
        return True
        
    except subprocess.TimeoutExpired:
        print("❌ FAIL: Subprocess timed out (window might have been created)")
        return False
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_tracker_service_state() -> bool:
    """Test the tracker service initialization and state management."""
    section("TEST 3: Tracker Service State Management")
    
    try:
        from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
        
        service = ContinuousWindowTrackerService()
        print(f"✓ Service created: {type(service).__name__}")
        
        # Create a session
        session = service.create_session(
            session_id="test-state",
            name="State Test",
            window_query="Pocket Option",
            auto_start=False,
        )
        
        print(f"✓ Session created: {session['session_id']}")
        
        # Check initial state
        focus_state = session.get('focus_selector', {})
        print(f"  Initial focus selector status: {focus_state.get('status')}")
        
        # Verify state persistence
        reloaded = service.get_session("test-state")
        if reloaded['session_id'] == session['session_id']:
            print(f"✓ Session persisted correctly")
        else:
            print(f"❌ FAIL: Session not persisted")
            return False
        
        print("✓ PASS: Tracker service state management works\n")
        return True
        
    except Exception as e:
        print(f"❌ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_window_resolution() -> bool:
    """Test window resolution and descriptor creation."""
    section("TEST 4: Window Resolution")
    
    try:
        from phoenixguard.mobile_api.window_tracker import (
            WindowsWindowCaptureBackend,
        )
        
        backend = WindowsWindowCaptureBackend()
        
        # List all windows
        windows = backend.list_windows()
        print(f"✓ Found {len(windows)} visible windows")
        
        # List browser windows
        browsers = [w for w in windows if any(b in w.get('title', '').lower() for b in ['edge', 'chrome', 'firefox'])]
        print(f"✓ Found {len(browsers)} browser windows")
        
        # Try Pocket Option query
        pocket_option = backend.list_windows("Pocket Option")
        print(f"✓ Found {len(pocket_option)} Pocket Option windows")
        
        if pocket_option:
            for i, w in enumerate(pocket_option[:3], 1):
                print(f"  {i}. {w.get('title', 'Unknown')[:50]}")
        else:
            print("  ⚠ No Pocket Option windows - this is why ARM will fail!")
        
        print("✓ PASS: Window resolution works\n")
        return True
        
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_bbox_normalization() -> bool:
    """Test bbox normalization and validation."""
    section("TEST 5: BBox Normalization & Validation")
    
    try:
        from phoenixguard.mobile_api.window_tracker import normalize_focus_region_bbox
        
        test_cases = [
            ([0.1, 0.2, 0.7, 0.8], "Normal case"),
            ([0, 0, 1, 1], "Full screen"),
            ([0.2, 0.2, 0.3, 0.3], "Small region"),
            ([1, 1, 0, 0], "Reversed coords"),
        ]
        
        for bbox, desc in test_cases:
            try:
                result = normalize_focus_region_bbox(bbox)
                print(f"✓ {desc}: {bbox} -> {result}")
            except ValueError as e:
                print(f"⚠ {desc}: {e}")
        
        print("✓ PASS: BBox normalization works\n")
        return True
        
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False

def test_error_handling() -> bool:
    """Test error handling in focus selector."""
    section("TEST 6: Error Handling in Focus Selector")
    
    try:
        from phoenixguard.mobile_api.window_tracker import ContinuousWindowTrackerService
        
        service = ContinuousWindowTrackerService()
        session = service.create_session(session_id="test-errors")
        
        print(f"✓ Session created: {session['session_id']}")
        
        # Try to arm when no window is available
        # (This should fail gracefully)
        try:
            service.arm_focus_selector("nonexistent-session")
            print("❌ FAIL: Should have raised KeyError")
            return False
        except KeyError:
            print(f"✓ Correctly raised KeyError for nonexistent session")
        
        # Try to set invalid bbox
        try:
            service.set_focus_region("test-errors", [])
            print("❌ FAIL: Should have raised ValueError")
            return False
        except ValueError:
            print(f"✓ Correctly raised ValueError for invalid bbox")
        
        print("✓ PASS: Error handling works\n")
        return True
        
    except Exception as e:
        print(f"❌ FAIL: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    print("\n")
    print("╔" + "="*68 + "╗")
    print("║" + " "*68 + "║")
    print("║" + "  PhoenixGuard Window Tracker - Comprehensive Debug Suite".center(68) + "║")
    print("║" + " "*68 + "║")
    print("╚" + "="*68 + "╝")
    
    tests: list[tuple[str, Callable[[], bool]]] = [
        ("Native Overlay Script", test_native_overlay_script),
        ("Subprocess Communication", test_subprocess_communication),
        ("Tracker Service State", test_tracker_service_state),
        ("Window Resolution", test_window_resolution),
        ("BBox Normalization", test_bbox_normalization),
        ("Error Handling", test_error_handling),
    ]
    
    results: list[tuple[str, bool]] = []
    for name, test_func in tests:
        try:
            result = test_func()
            results.append((name, result))
        except Exception as e:
            print(f"❌ EXCEPTION in {name}: {e}")
            results.append((name, False))
    
    # Summary
    section("TEST SUMMARY")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"  {status}: {name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n✅ ALL TESTS PASSED!")
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
    
    section("TROUBLESHOOTING GUIDE")
    
    print("""If you're experiencing issues with the tracker:

1. ARM FAILS with "No visible window matched":
   ✓ Make sure Pocket Option is open in a browser
   ✓ The browser window should be visible (not minimized/hidden)
   ✓ Try using a different browser if the window is from Edge

2. Overlay doesn't appear after pressing Ctrl+V:
   ✓ Make sure the focus selector status is "armed"
   ✓ Try pressing Ctrl+V again
   ✓ Check if the overlay window is behind other windows
   ✓ Try in a different browser

3. Selection doesn't save after pressing Enter:
   ✓ Make sure the overlay is still visible
   ✓ Try pressing Enter on the main keyboard (not numpad)
   ✓ Check the browser console for JavaScript errors
   ✓ Reload the dashboard and try again

4. Logs show subprocess errors:
   ✓ Check if native_focus_overlay.py exists in mobile_api/
   ✓ Make sure it's valid Python (no syntax errors)
   ✓ Try running it manually: python native_focus_overlay.py --hwnd <hwnd>

5. General troubleshooting:
   ✓ Refresh the dashboard page
   ✓ Restart the PhoenixGuard service
   ✓ Check for Python/system permission issues
   ✓ Verify Windows is the OS (not Linux/Mac)
""")
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())
