# PhoenixGuard Window Tracker - Complete User Guide

## ✅ What Was Fixed

The tracker now has **complete end-to-end debugging** with:

1. **Better error messages** - Clear feedback when something fails (e.g., "No
window matched 'Pocket Option'")
2. **Detailed logging** - Every step of the arm→select flow is logged for
troubleshooting
3. **Graceful error handling** - If selection fails, proper error states are
saved and shown
4. **Subprocess safety** - Native overlay process is properly managed, killed on
timeout
5. **State persistence** - Your selections are always saved correctly

---

## 🚀 Step-by-Step: How to Use the Tracker

### **STEP 1: Open Pocket Option in a Browser**

```bash

1. Open Microsoft Edge, Chrome, or Firefox
2. Go to your Pocket Option trading platform
3. Make sure the window is VISIBLE (not minimized, not hidden behind other windows)
4. Keep it in the foreground or easily accessible

```

**⚠️ Important**: The tracker searches for windows with "Pocket Option" in the
title. Make sure:

- The browser tab shows "Pocket Option" in the window title
- The browser window itself is visible
- If using multiple windows, keep Pocket Option in its own separate window

---

### **STEP 2: Open the Tracker Dashboard**

```bash

1. Start PhoenixGuard mobile API:
   python Backend\launch\start_phoenixguard_mobile_api.py

2. Open in browser:
   http://localhost:8000/v1/mobile/window-tracker/dashboard

3. You'll see a dashboard with:

   - "Arm Broker Focus" button
   - "Tracker On/Off" toggle
   - Live signal display
   - Recent studies history

```

---

### **STEP 3: Arm the Focus Selector**

```bash

1. Click the "Arm Broker Focus" button
2. The button should show status: "Focus selector armed"
3. Message should say: "Focus selector armed. Switch to Pocket Option, press Ctrl+V..."

```

**If ARM FAILS**, check:

- ✓ Is Pocket Option open in a browser window?
- ✓ Is the window visible (not minimized)?
- ✓ Is "Pocket Option" in the window title?
- ✓ Is the browser in the taskbar and accessible?

Error message will show: `"No visible window matched 'Pocket Option'"`

---

### **STEP 4: Activate the Overlay with Ctrl+V**

```bash

1. Switch focus to the Pocket Option browser window
   (Click on it to make it the active window)

2. Press Ctrl+V (Control + V keys together)
   ⚠️ IMPORTANT: Make sure to use the main keyboard Ctrl key, not numpad

3. A semi-transparent overlay should appear over the Pocket Option window

   - The overlay will have a crosshair cursor
   - Instructions will appear at the top

```

**If overlay doesn't appear:**

- Try pressing Ctrl+V again
- Check if overlay is behind other windows
- Try switching windows and back
- Reload the dashboard

---

### **STEP 5: Drag to Select the Chart Area**

```bash

1. Position your mouse over the trading chart in Pocket Option
2. Click and drag to select the chart area:

   - Start from top-left of the chart
   - Drag to bottom-right of the chart
   - Keep a bit of margin on all sides

3. As you drag, a rectangle outline will appear

   - Color changes as you drag: orange → cyan → green (ready)

4. Release the mouse

The overlay will show: "Selection ready - Press Enter to lock this region"

```

**Selection tips:**

- Select the main candlestick chart area
- Avoid including buttons, labels, timeframe selector
- Leave about 20px margin around the chart
- The region must be at least 20×20 pixels

---

### **STEP 6: Confirm with Enter**

```bash

1. Press Enter key to confirm the selection
   (Use main keyboard Enter, not numpad Enter)

2. The overlay will close
3. The dashboard should update showing:

   - Status: "ready"
   - Focus lock: "Locked"
   - Last chart/overlay images appear

```

**If confirmation fails:**

- Try pressing Enter again
- Reload the dashboard
- Restart from Step 3 (ARM again)

---

### **STEP 7: Start the Tracker**

```bash

1. Click "Tracker On" button
2. Status will change to: "running"
3. The tracker will start capturing and analyzing
4. "Tracker On" button becomes red "Tracker Off"
5. New study results update continuously: 3s base, 0.5s near trigger, up to 10s idle

```

---

## 📊 Dashboard Elements Explained

```text

┌─────────────────────────────────────────┐
│ PhoenixGuard Live Tracker               │
├─────────────────────────────────────────┤
│ Status: running                         │
│ Next capture: 9.2s                      │
│ Focus Lock: Locked                      │
│ Captures: 42                            │
├─────────────────────────────────────────┤
│ Action: BUY  |  Conf: 0.78              │
│ Message: "Impulse BUY detected..."      │
├─────────────────────────────────────────┤
│ [Chart Overlay] or [Raw Chart]          │
│ (Live trading analysis image)           │
├─────────────────────────────────────────┤
│ Global: BUY    Local: BUY    Timeframe: H1
│ Selector: selected                      │
├─────────────────────────────────────────┤
│ Recent Studies:                         │
│ • BUY · IMPULSE BUY (conf 0.82)        │
│ • HOLD · CONSOLIDATION (conf 0.45)    │
│ • SELL · REVERSAL (conf 0.68)         │
└─────────────────────────────────────────┘

```

**Key metrics:**

- **Action**: What the tracker is currently reading (BUY/SELL/HOLD)
- **Conf**: Confidence level 0.0-1.0
- **Global/Local**: Market direction analysis
- **Selector**: Focus lock status

---

## 🔧 Common Issues & Solutions

### "No visible window matched 'Pocket Option'"

**Cause**: Pocket Option is not open or not found

**Fix**:

```bash

1. Make sure Pocket Option is open in a browser
2. Check the window title contains "Pocket Option"
3. If in Edge: title should show "Pocket Option - Microsoft Edge"
4. Try a different browser
5. Refresh dashboard and try ARM again

```

---

### Overlay doesn't appear after Ctrl+V

**Cause**: Focus selector didn't activate

**Fix**:

```bash

1. Make sure dashboard shows: "Focus selector armed"
2. Try pressing Ctrl+V again (sometimes needs 2-3 tries)
3. Click the Pocket Option window to ensure it has focus
4. Check if overlay window is behind other windows (Alt+Tab)
5. Reload dashboard and ARM again

```

---

### Selection doesn't save after pressing Enter

**Cause**: Overlay process timed out or crashed

**Fix**:

```bash

1. Try pressing Enter again
2. Wait 5 seconds and check dashboard
3. If still not working:

   - Reload dashboard
   - ARM again (click "Arm Broker Focus")
   - Repeat steps 4-6 more slowly

```

---

### Tracker shows "error" status

**Cause**: Internal error in analysis

**Fix**:

```bash

1. Click "Refresh Now" button
2. Try "Capture Once" to test
3. Check the error message in the dashboard
4. Stop tracker ("Tracker Off")
5. Clear focus ("Clear Focus")
6. Start over from Step 3

```

---

## 📝 Debugging: Check the Logs

To see detailed debug information:

```bash

## 1. Stop tracker if running

## 2. Check the PhoenixGuard logs directory

cd "C:\Users\{user}\OneDrive\Documents\The 808 Vision 2026\phoenixguard\logs"

## 3. Look for recent errors

ls -lt

## 4. Read the latest log file

cat <latest_log_file>

## Look for keywords

## - "Focus selected" = selection worked

## - "Native overlay" = overlay activation

## - "Tracker study failed" = analysis error

## - "Window resolved" = window detection

```

---

## ✨ Tips for Best Results

1. Keep Pocket Option window on main screen - Multi-monitor setups sometimes
confuse the tracker
2. Select a clean chart area - Avoid labels, buttons, timeframe selector in the
selection
3. Use consistent timeframes - Stick to one timeframe after selection
4. Continuous adaptive captures work best - use the 3s base, 0.5s trigger floor,
and 10s idle cap
5. Test with "Capture Once" - Before starting full tracker, test with manual
capture
6. Keep dashboard page open - Leaving the page can pause tracking

---

## 🆘 Still Having Issues

Run the comprehensive debug suite:

```bash

python debug_tracker_tests.py

```

This will:

- Test all system components
- Verify native overlay script
- Check window resolution
- Validate bbox normalization
- Test error handling
- Provide detailed troubleshooting

---

## ✅ Verification: All Components Working

```text

[✓] Native Overlay Script - Valid Python, executable
[✓] Subprocess Communication - Process starts/stops correctly
[✓] Tracker Service - Sessions persist correctly
[✓] Window Resolution - Pocket Option detected when open
[✓] BBox Normalization - Chart regions properly normalized
[✓] Error Handling - Errors captured and reported

```

If all checks pass, your tracker is ready to use!

---

**Version**: 2026-04-21

**Status**: ✅ All tests passing
