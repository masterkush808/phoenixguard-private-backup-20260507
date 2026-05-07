#!/usr/bin/env python3
"""Complete debug of the window tracker arm -> select flow."""

import sys
import json
from pathlib import Path

phoenixguard_root = Path(__file__).parent
sys.path.insert(0, str(phoenixguard_root))

from phoenixguard.mobile_api.window_tracker import (
    ContinuousWindowTrackerService,
    WindowsNativeFocusSelectionBackend,
    WindowsWindowCaptureBackend,
)

def debug_section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")

def debug_step(step: str, status: str = "→") -> None:
    print(f"  {status} {step}")

try:
    debug_section("1. SYSTEM & PLATFORM CHECK")
    
    backend = WindowsWindowCaptureBackend()
    focus_backend = WindowsNativeFocusSelectionBackend()
    
    debug_step(f"Windows backend available: {backend.is_windows()}")
    debug_step(f"Focus selector supported: {focus_backend.is_supported()}")
    
    if not focus_backend.is_supported():
        raise RuntimeError("Focus selector not supported on this platform (not Windows)")
    
    debug_section("2. WINDOW DETECTION")
    
    windows = backend.list_windows()
    debug_step(f"Total visible windows: {len(windows)}", "✓")
    
    pocket_option_windows = [w for w in windows if "pocket option" in w.get("title", "").lower()]
    debug_step(f"Pocket Option windows found: {len(pocket_option_windows)}", "✓")
    
    if pocket_option_windows:
        for i, w in enumerate(pocket_option_windows, 1):
            hwnd = w.get("hwnd", 0)
            title = w.get("title", "Unknown")
            size = f"{w.get('width', 0)}x{w.get('height', 0)}"
            debug_step(f"  {i}. hwnd={hwnd} title={title[:40]} size={size}")
    else:
        debug_step("No Pocket Option windows found - ARM will fail!", "⚠")
    
    debug_section("3. SERVICE INITIALIZATION")
    
    service = ContinuousWindowTrackerService()
    debug_step(f"Service created: {type(service).__name__}", "✓")
    debug_step(f"Root dir: {service.root_dir}", "✓")
    debug_step(f"Capture backend: {type(service.capture_backend).__name__}", "✓")
    debug_step(f"Focus selector: {type(service.focus_selector_backend).__name__}", "✓")
    
    debug_section("4. SESSION CREATION")
    
    session = service.create_session(
        session_id="debug-tracker",
        name="Debug Session",
        window_query="Pocket Option",
        auto_start=False,
    )
    debug_step(f"Session ID: {session['session_id']}", "✓")
    debug_step(f"Status: {session['status']}")
    debug_step(f"Focus enabled: {session['manual_focus_region']['enabled']}")
    debug_step(f"Focus selector status: {session['focus_selector']['status']}")
    
    session_id = session['session_id']
    
    debug_section("5. WINDOW RESOLUTION TEST")
    
    payload = service.load_session_payload(session_id)
    descriptor = service.resolve_window_descriptor(payload)
    
    if descriptor:
        debug_step(f"Window resolved: hwnd={descriptor.get('hwnd')}", "✓")
        debug_step(f"  Title: {descriptor.get('title', 'Unknown')[:50]}")
        debug_step(f"  Size: {descriptor.get('width')}x{descriptor.get('height')}")
    else:
        debug_step(f"Window resolution FAILED - No window found!", "✗")
        debug_step("  This is why ARM will fail!")
    
    debug_section("6. ARM ATTEMPT (Focus Selector)")
    
    if not descriptor:
        debug_step("SKIPPING ARM - no window descriptor available", "⚠")
    else:
        debug_step(f"Attempting to arm focus selector...")
        debug_step(f"  Session: {session_id}")
        debug_step(f"  Window hwnd: {descriptor.get('hwnd')}")
        debug_step(f"  Window title: {descriptor.get('title', 'Unknown')[:40]}")
        
        try:
            # This should trigger the arm process
            updated = service.arm_focus_selector(session_id)
            debug_step(f"ARM SUCCEEDED", "✓")
            debug_step(f"  Focus selector status: {updated['focus_selector']['status']}")
            debug_step(f"  Focus selector message: {updated['focus_selector']['message'][:60]}")
            
            # Check if the native overlay process was launched
            if updated['focus_selector']['status'] in ['armed', 'selecting']:
                debug_step(f"Native overlay should be active now", "→")
                debug_step(f"NEXT: In Pocket Option window, press Ctrl+V to activate overlay")
            else:
                debug_step(f"Focus selector in state: {updated['focus_selector']['status']}", "⚠")
                
        except Exception as e:
            debug_step(f"ARM FAILED with error: {e}", "✗")
            import traceback
            print("\nTraceback:")
            traceback.print_exc()
    
    debug_section("7. FOCUS SELECTOR STATE")
    
    session = service.get_session(session_id)
    focus_state = session.get('focus_selector', {})
    
    debug_step(f"Status: {focus_state.get('status', 'unknown')}")
    debug_step(f"Supported: {focus_state.get('supported', False)}")
    debug_step(f"Armed: {focus_state.get('armed', False)}")
    debug_step(f"Active: {focus_state.get('active', False)}")
    debug_step(f"Message: {focus_state.get('message', '')[:60]}")
    debug_step(f"Last error: {focus_state.get('last_error', '') or '(none)'}")
    
    debug_section("8. MANUAL FOCUS TEST (Simulate Selection)")
    
    test_bbox = [0.1, 0.15, 0.9, 0.85]
    debug_step(f"Simulating selection with bbox: {test_bbox}")
    
    try:
        result = service.set_focus_region(session_id, test_bbox, source="debug-script")
        debug_step(f"FOCUS REGION SET SUCCESSFULLY", "✓")
        debug_step(f"  Focus enabled: {result['manual_focus_region']['enabled']}")
        debug_step(f"  Bbox: {result['manual_focus_region']['normalized_bbox']}")
        debug_step(f"  Source: {result['manual_focus_region']['source']}")
        debug_step(f"  Session status: {result['status']}")
    except Exception as e:
        debug_step(f"SET FOCUS REGION FAILED: {e}", "✗")
    
    debug_section("9. PERSISTENCE CHECK")
    
    session_reload = service.get_session(session_id)
    if session_reload['manual_focus_region']['enabled']:
        debug_step(f"✓ Focus region persisted correctly", "✓")
    else:
        debug_step(f"✗ Focus region NOT persisted!", "✗")
    
    debug_section("10. LOGS CHECK")
    
    session_dir = service.session_dir(session_id)
    session_file = session_dir / "session.json"
    
    if session_file.exists():
        debug_step(f"Session file exists: {session_file}", "✓")
        with open(session_file) as f:
            data = json.load(f)
            debug_step(f"  Keys: {list(data.keys())[:8]}")
    else:
        debug_step(f"Session file NOT found: {session_file}", "✗")
    
    debug_section("SUMMARY & TROUBLESHOOTING")
    
    issues: list[str] = []
    
    if not backend.is_windows():
        issues.append("❌ Not running on Windows - native focus selector unavailable")
    
    if not pocket_option_windows:
        issues.append("❌ No Pocket Option windows found - check if Pocket Option is open")
    
    if not descriptor:
        issues.append("❌ Window descriptor not resolved - check window title matching")
    
    if issues:
        print("\n🔴 ISSUES DETECTED:\n")
        for issue in issues:
            print(f"  {issue}")
    else:
        print("\n✅ All system checks passed!")
        print("\n📍 NEXT STEPS TO TEST:")
        print("  1. Open Pocket Option in a browser window")
        print("  2. Open http://localhost:8000/v1/mobile/window-tracker/dashboard")
        print("  3. Click 'Arm Broker Focus'")
        print("  4. Switch to Pocket Option and press Ctrl+V")
        print("  5. Drag to select chart area and press Enter")
        print("  6. Check if selection is locked")
    
    print("\n" + "="*70 + "\n")
    
except Exception as e:
    print(f"\n❌ FATAL ERROR: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
