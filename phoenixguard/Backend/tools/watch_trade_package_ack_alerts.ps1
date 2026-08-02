[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Operator watcher prints concise status lines to stdout logs.")]
param(
    [Parameter(Mandatory = $true)]
    [string]$EntryEventsPath,

    [Parameter(Mandatory = $true)]
    [string]$AlertLog,

    [string]$Mt4StatusPath = "",

    [string]$Mt4CommandPath = "",

    [string]$Mt4AuditPath = "",

    [int]$PollSeconds = 2,

    [int]$DefaultValidSeconds = 900,

    [switch]$IgnoreExisting
)

$ErrorActionPreference = "Continue"
$seen = @{}
$firstScan = $true

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$focusSource = @'
using System;
using System.Runtime.InteropServices;
public static class PhoenixGuardAlertFocus {
  [DllImport("user32.dll")] public static extern bool ShowWindowAsync(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
}
'@
Add-Type -TypeDefinition $focusSource -ErrorAction SilentlyContinue

function Add-JsonLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [object]$Payload
    )
    $dir = Split-Path -Parent $Path
    if ($dir) {
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
    }
    ($Payload | ConvertTo-Json -Depth 12 -Compress) | Add-Content -LiteralPath $Path -Encoding UTF8
}

function Get-TextValue {
    param($Value, [string]$Default = "")
    if ($null -eq $Value) {
        return $Default
    }
    $text = [string]$Value
    if ([string]::IsNullOrWhiteSpace($text)) {
        return $Default
    }
    return $text
}

function Get-EntryKey {
    param($EventRow)
    if ($EventRow.manual_alert_key) {
        return [string]$EventRow.manual_alert_key
    }
    if ($EventRow.manual_alert -and $EventRow.manual_alert.key) {
        return [string]$EventRow.manual_alert.key
    }
    $entry = $EventRow.entry
    $packet = Get-TextValue $entry.packet_id
    if ($packet.Length -gt 0) {
        return "entry|$packet|$($EventRow.seq)|$($EventRow.frame)|$($EventRow.captured_at_utc)"
    }
    return "entry|seq_$($EventRow.seq)|frame_$($EventRow.frame)|$($entry.side)|$($entry.lane_name)|$($EventRow.captured_at_utc)"
}

function Get-NestedProperty {
    param($Object, [string]$Name)
    if ($null -eq $Object -or [string]::IsNullOrWhiteSpace($Name)) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-Mt4CommandKey {
    param($Command)
    $packet = Get-TextValue $Command.packet_id
    $heartbeat = Get-NestedProperty $Command "heartbeat"
    $validUntil = Get-TextValue (Get-NestedProperty $Command "valid_until_epoch_sec") (Get-TextValue (Get-NestedProperty $heartbeat "valid_until_epoch_sec") "")
    $updated = Get-TextValue $Command.timestamp_utc (Get-TextValue $Command.bridge_written_epoch "")
    if ($packet.Length -gt 0) {
        return "mt4_command|$packet|$validUntil"
    }
    $execution = Get-NestedProperty $Command "execution"
    $side = Get-TextValue (Get-NestedProperty $execution "side") (Get-TextValue $Command.side "")
    return "mt4_command|$($Command.bridge_sequence)|$updated|$($Command.action)|$side"
}

function Get-ValidUntil {
    param($EventRow)
    $entry = $EventRow.entry
    foreach ($candidate in @(
        $entry.valid_until_utc,
        $entry.valid_until,
        $entry.expires_at_utc,
        $EventRow.valid_until_utc,
        $EventRow.expires_at_utc
    )) {
        $text = Get-TextValue $candidate
        if (-not $text) {
            continue
        }
        try {
            return [DateTimeOffset]::Parse($text).ToLocalTime()
        } catch {
        }
    }
    foreach ($candidate in @(
        $EventRow.captured_at_utc,
        $EventRow.captured_at,
        $entry.captured_at_utc,
        $entry.captured_at
    )) {
        $text = Get-TextValue $candidate
        if (-not $text) {
            continue
        }
        try {
            return ([DateTimeOffset]::Parse($text).ToLocalTime()).AddSeconds([Math]::Max(60, $DefaultValidSeconds))
        } catch {
        }
    }
    foreach ($candidate in @(
        $EventRow.captured_epoch,
        $entry.captured_epoch
    )) {
        $captured = Get-DateTimeFromUnixSeconds $candidate
        if ($null -ne $captured) {
            return $captured.AddSeconds([Math]::Max(60, $DefaultValidSeconds))
        }
    }
    return [DateTimeOffset]::Now.AddSeconds([Math]::Max(60, $DefaultValidSeconds))
}

function Get-DateTimeFromUnixSeconds {
    param($Value)
    if ($null -eq $Value) {
        return $null
    }
    try {
        $seconds = [double]$Value
        if ($seconds -le 0) {
            return $null
        }
        $milliseconds = [int64][Math]::Round($seconds * 1000.0)
        return [DateTimeOffset]::FromUnixTimeMilliseconds($milliseconds).ToLocalTime()
    } catch {
        return $null
    }
}

function Test-BackgroundCaptureOnly {
    $configured = [string]$env:PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY
    if ([string]::IsNullOrWhiteSpace($configured)) {
        return $true
    }
    return $configured.Trim().ToLowerInvariant() -notin @("0", "false", "off", "no")
}

function Focus-BrokerWindowForCapture {
    if (Test-BackgroundCaptureOnly) {
        return
    }
    $query = Get-TextValue $env:PHOENIXGUARD_ALERT_REFOCUS_WINDOW_QUERY "The Most Innovative Trading Platform"
    if (-not $query) {
        return
    }
    try {
        $process = Get-Process msedge,chrome -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like "*$query*" } |
            Select-Object -First 1
        if ($null -eq $process) {
            return
        }
        [PhoenixGuardAlertFocus]::ShowWindowAsync($process.MainWindowHandle, 3) | Out-Null
        Start-Sleep -Milliseconds 150
        [PhoenixGuardAlertFocus]::SetForegroundWindow($process.MainWindowHandle) | Out-Null
    } catch {
    }
}

function Get-Mt4CommandValidUntil {
    param($Command)
    $heartbeat = Get-NestedProperty $Command "heartbeat"
    foreach ($candidate in @(
        (Get-NestedProperty $Command "valid_until_epoch_sec"),
        (Get-NestedProperty $heartbeat "valid_until_epoch_sec")
    )) {
        $parsed = Get-DateTimeFromUnixSeconds $candidate
        if ($null -ne $parsed) {
            return $parsed
        }
    }
    foreach ($candidate in @(
        (Get-NestedProperty $Command "valid_until_utc"),
        (Get-NestedProperty $Command "expires_at_utc")
    )) {
        $text = Get-TextValue $candidate
        if (-not $text) {
            continue
        }
        try {
            return [DateTimeOffset]::Parse($text).ToLocalTime()
        } catch {
        }
    }
    return [DateTimeOffset]::Now.AddSeconds([Math]::Max(60, $DefaultValidSeconds))
}

function Format-Mt4ExpectedMoveTime {
    param($Expected, $Professional)
    if ($Expected -is [string] -and -not [string]::IsNullOrWhiteSpace($Expected)) {
        return [string]$Expected
    }
    $duration = Get-TextValue (Get-NestedProperty $Expected "expected_duration_text") ""
    $seconds = Get-TextValue (Get-NestedProperty $Expected "expected_duration_sec") (Get-TextValue (Get-NestedProperty $Professional "expected_duration_sec") "")
    if (-not $duration -and $seconds) {
        $duration = "$seconds sec"
    }
    $candles = Get-TextValue (Get-NestedProperty $Expected "expected_candle_count") (Get-TextValue (Get-NestedProperty $Professional "expected_candle_count") "")
    $stage = Get-TextValue (Get-NestedProperty $Expected "current_leg_stage") ""
    $parts = @()
    if ($duration) {
        $parts += $duration
    }
    if ($candles) {
        $parts += "candles=$candles"
    }
    if ($stage) {
        $parts += "stage=$stage"
    }
    if ($parts.Count -gt 0) {
        return ($parts -join " | ")
    }
    return "unknown"
}

function Format-Mt4ProfessionalTradePlan {
    param($Professional)
    if ($null -eq $Professional) {
        return "missing"
    }
    $grade = Get-TextValue (Get-NestedProperty $Professional "professional_grade") "unknown"
    $authority = Get-TextValue (Get-NestedProperty $Professional "authority_side") (Get-TextValue (Get-NestedProperty $Professional "side") "unknown")
    $state = Get-TextValue (Get-NestedProperty $Professional "professional_thesis_state") "unknown"
    $class = Get-TextValue (Get-NestedProperty $Professional "thesis_class") "unknown"
    $candles = Get-TextValue (Get-NestedProperty $Professional "expected_candle_count") "unknown"
    return "grade=$grade | authority_side=$authority | state=$state | class=$class | expected_candles=$candles"
}

function Show-AckAlert {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Title,
        [Parameter(Mandatory = $true)]
        [string]$Body,
        [Parameter(Mandatory = $true)]
        [DateTimeOffset]$ValidUntil,
        [Parameter(Mandatory = $true)]
        [object]$Payload
    )

    $form = New-Object System.Windows.Forms.Form
    $form.Text = $Title
    $form.StartPosition = "CenterScreen"
    $topMostSetting = (Get-TextValue $env:PHOENIXGUARD_ALERT_TOPMOST "0").ToLowerInvariant()
    $form.TopMost = @("1", "true", "yes", "on") -contains $topMostSetting
    $form.Width = 900
    $form.Height = 690
    $form.BackColor = [System.Drawing.Color]::FromArgb(10, 10, 8)
    $form.ForeColor = [System.Drawing.Color]::FromArgb(255, 216, 128)
    $form.Font = New-Object System.Drawing.Font("Consolas", 14, [System.Drawing.FontStyle]::Bold)

    $textBox = New-Object System.Windows.Forms.TextBox
    $textBox.Multiline = $true
    $textBox.ReadOnly = $true
    $textBox.ScrollBars = "Vertical"
    $textBox.BorderStyle = "None"
    $textBox.BackColor = [System.Drawing.Color]::FromArgb(6, 8, 6)
    $textBox.ForeColor = [System.Drawing.Color]::FromArgb(255, 216, 128)
    $textBox.Font = New-Object System.Drawing.Font("Consolas", 15, [System.Drawing.FontStyle]::Bold)
    $textBox.Dock = "Fill"
    $textBox.Text = $Body

    $button = New-Object System.Windows.Forms.Button
    $button.Text = "I SEE IT"
    $button.Dock = "Bottom"
    $button.Height = 56
    $button.BackColor = [System.Drawing.Color]::FromArgb(255, 205, 92)
    $button.ForeColor = [System.Drawing.Color]::Black
    $button.Font = New-Object System.Drawing.Font("Segoe UI", 14, [System.Drawing.FontStyle]::Bold)
    $button.Add_Click({
        $form.Tag = "ACKED"
        $form.Close()
    })

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 1000
    $expiryState = @{ NoticeShown = $false }
    $timer.Add_Tick({
        try {
            [console]::beep(1200, 250)
            [console]::beep(900, 180)
        } catch {
        }
        if ((-not [bool]$expiryState.NoticeShown) -and [DateTimeOffset]::Now -gt $ValidUntil) {
            $expiryState.NoticeShown = $true
            $textBox.Text = @"
PHOENIXGUARD TRADE PACKAGE - EXPIRED

DO NOT ENTER FROM THIS WINDOW.
The packet window has passed. This alert is retained as operator evidence
and will remain visible until you click I SEE IT.

$Body
"@
        }
    })
    $refocusTimer = New-Object System.Windows.Forms.Timer
    $refocusTimer.Interval = 1500
    $refocusTimer.Add_Tick({
        $refocusTimer.Stop()
        Focus-BrokerWindowForCapture
    })

    $form.Controls.Add($textBox)
    $form.Controls.Add($button)
    $form.Add_Shown({
        if ($form.TopMost) {
            $form.Activate()
        }
        $timer.Start()
        $refocusTimer.Start()
    })
    $form.Add_FormClosed({
        $timer.Stop()
        $timer.Dispose()
        $refocusTimer.Stop()
        $refocusTimer.Dispose()
    })

    Add-JsonLine -Path $AlertLog -Payload ([ordered]@{
        at = (Get-Date).ToString("o")
        status = "OPENED"
        title = $Title
        valid_until = $ValidUntil.ToString("o")
        payload = $Payload
    })
    [void]$form.ShowDialog()
    $result = Get-TextValue $form.Tag "CLOSED"
    Add-JsonLine -Path $AlertLog -Payload ([ordered]@{
        at = (Get-Date).ToString("o")
        status = $result
        title = $Title
        valid_until = $ValidUntil.ToString("o")
        payload = $Payload
    })
}

function Format-EntryAlertBody {
    param($EventRow)
    $entry = $EventRow.entry
    $side = Get-TextValue $entry.side "UNKNOWN"
    $expected = Get-TextValue $entry.expected_move_time "unknown"
    if ($expected -eq "unknown") {
        $expected = Get-TextValue $entry.expected_duration_label "unknown"
    }
    $lane = Get-TextValue $entry.lane_name (Get-TextValue $entry.lane "UNKNOWN")
    $packet = Get-TextValue $entry.packet_id "UNKNOWN_PACKET"
    $score = Get-TextValue $entry.final_score (Get-TextValue $entry.score "unknown")
    $authority = Get-TextValue $entry.authority "PLAYBOOK_FINAL_DECIDER_V3"
    $maturity = Get-TextValue $entry.opportunity_maturity (Get-TextValue $entry.maturity "ENTER_NOW")
    $leg = Get-TextValue $entry.current_leg_side ""
    $legCandles = Get-TextValue $entry.current_leg_candle_count ""
    $visibleCandles = Get-TextValue $entry.visible_candle_count ""
    $room = Get-TextValue $entry.room_to_opposing_force_candles ""
    $validUntil = Get-ValidUntil $EventRow

    return @"
PHOENIXGUARD TRADE PACKAGE

STATUS: ACTIVE DIRECT ENTRY WINDOW
ACTION: ENTER $side
EXPECTED MOVE TIME: $expected
VALID UNTIL LOCAL: $($validUntil.ToString("yyyy-MM-dd HH:mm:ss zzz"))

PACKET: $packet
FRAME: $($EventRow.frame) | CAPTURE: $($EventRow.capture_count)
LANE: $lane
AUTHORITY: $authority
SCORE: $score
MATURITY: $maturity

CURRENT LEG: $leg | candles=$legCandles
VISIBLE CANDLES: $visibleCandles
ROOM TO OPPOSING FORCE: $room candle(s)

FRESHNESS: PASS
BLOCKER: NONE
NEXT REQUIRED: none

This alert stays open until you click I SEE IT, but it expires automatically if the packet window passes.
"@
}

function Scan-EntryEvents {
    if (-not (Test-Path -LiteralPath $EntryEventsPath)) {
        return
    }
    foreach ($line in Get-Content -LiteralPath $EntryEventsPath) {
        if (-not $line.Trim()) {
            continue
        }
        try {
            $row = $line | ConvertFrom-Json
        } catch {
            continue
        }
        $entry = $row.entry
        if (-not $entry) {
            continue
        }
        $allowed = [bool]$entry.allowed -or [bool]$entry.execution_authorized
        if (-not $allowed) {
            continue
        }
        if ($row.PSObject.Properties.Name -contains "manual_alert_allowed" -and -not [bool]$row.manual_alert_allowed) {
            continue
        }
        if ($entry.PSObject.Properties.Name -contains "manual_alert_allowed" -and -not [bool]$entry.manual_alert_allowed) {
            continue
        }
        $key = Get-EntryKey $row
        if ($seen.ContainsKey($key)) {
            continue
        }
        if ($firstScan -and $IgnoreExisting) {
            $seen[$key] = $true
            continue
        }
        $seen[$key] = $true
        $side = Get-TextValue $entry.side "UNKNOWN"
        $title = "PhoenixGuard ENTER $side"
        $validUntil = Get-ValidUntil $row
        if ([DateTimeOffset]::Now -gt $validUntil) {
            continue
        }
        Show-AckAlert -Title $title -Body (Format-EntryAlertBody $row) -ValidUntil $validUntil -Payload $row
    }
}

function Scan-Mt4Command {
    if (-not $Mt4CommandPath -or -not (Test-Path -LiteralPath $Mt4CommandPath)) {
        return
    }
    try {
        $command = Get-Content -LiteralPath $Mt4CommandPath -Raw | ConvertFrom-Json
    } catch {
        return
    }
    $schema = Get-TextValue $command.schema_version
    $status = Get-TextValue $command.bridge_status
    $packet = Get-TextValue $command.packet_id
    if ($status -eq "NO_EXECUTION_PACKET" -or $status -eq "BRIDGE_ERROR") {
        return
    }
    $action = Get-TextValue $command.action
    $side = Get-TextValue $command.side
    $validUntil = [DateTimeOffset]::Now.AddSeconds([Math]::Max(60, $DefaultValidSeconds))
    $body = ""
    if ($schema -eq "PG_MT4_EXECUTION_COMMAND_V1") {
        $execution = Get-NestedProperty $command "execution"
        $signal = Get-NestedProperty $command "signal_state"
        $allowance = Get-NestedProperty $command "allowance_package"
        $state = (Get-TextValue (Get-NestedProperty $execution "state") (Get-TextValue (Get-NestedProperty $signal "state") "")).ToUpperInvariant()
        $side = (Get-TextValue (Get-NestedProperty $execution "side") (Get-TextValue (Get-NestedProperty $signal "side") "UNKNOWN")).ToUpperInvariant()
        if ($state -ne "EXECUTABLE" -or ($side -ne "BUY" -and $side -ne "SELL")) {
            return
        }
        $validUntil = Get-Mt4CommandValidUntil $command
        if ([DateTimeOffset]::Now -gt $validUntil) {
            return
        }
        $action = "ENTER"
        $status = "EXECUTION_PACKET"
        $expected = Get-NestedProperty $allowance "expected_move_time"
        if ($null -eq $expected) {
            $expected = Get-NestedProperty $command "expected_move_time"
        }
        $professional = Get-NestedProperty $allowance "professional_trade_plan"
        if ($null -eq $professional) {
            $professional = Get-NestedProperty $command "professional_trade_plan"
        }
        $written = Get-TextValue $command.timestamp_utc
        if (-not $written) {
            $writtenLocal = Get-DateTimeFromUnixSeconds $command.bridge_written_epoch
            if ($null -ne $writtenLocal) {
                $written = $writtenLocal.ToString("yyyy-MM-dd HH:mm:ss zzz")
            }
        }
        $body = @"
PHOENIXGUARD MT4 BRIDGE ATTEMPT

ACTION: $action $side
PACKET: $packet
STATUS: $status
BRIDGE SEQUENCE: $($command.bridge_sequence)
VALID UNTIL LOCAL: $($validUntil.ToString("yyyy-MM-dd HH:mm:ss zzz"))
WRITTEN: $written

EXPECTED MOVE TIME: $(Format-Mt4ExpectedMoveTime -Expected $expected -Professional $professional)
PROFESSIONAL PLAN: $(Format-Mt4ProfessionalTradePlan $professional)

This alert means PhoenixGuard wrote or updated the MT4 bridge command file.
Check MT4 for EA acceptance/rejection and open trade state.
"@
    } elseif (-not $packet -and (-not $action)) {
        return
    } else {
        $body = @"
PHOENIXGUARD MT4 BRIDGE ATTEMPT

ACTION: $action $side
PACKET: $packet
STATUS: $status
BRIDGE SEQUENCE: $($command.bridge_sequence)
TIMESTAMP UTC: $($command.timestamp_utc)

This alert means PhoenixGuard wrote or updated the MT4 bridge command file.
Check MT4 for EA acceptance/rejection and open trade state.
"@
    }
    $key = Get-Mt4CommandKey $command
    if ($seen.ContainsKey($key)) {
        return
    }
    if ($firstScan -and $IgnoreExisting) {
        $seen[$key] = $true
        return
    }
    $seen[$key] = $true
    Add-JsonLine -Path $AlertLog -Payload ([ordered]@{
        at = (Get-Date).ToString("o")
        status = "MT4_COMMAND_OBSERVED"
        title = "PhoenixGuard MT4 Attempt"
        valid_until = $validUntil.ToString("o")
        payload = $command
    })
    $mt4PopupSetting = (Get-TextValue $env:PHOENIXGUARD_ALERT_MT4_POPUP "0").ToLowerInvariant()
    if (@("1", "true", "yes", "on") -contains $mt4PopupSetting) {
        Show-AckAlert -Title "PhoenixGuard MT4 Attempt" -Body $body -ValidUntil $validUntil -Payload $command
    }
}

while ($true) {
    try {
        Scan-EntryEvents
        Scan-Mt4Command
        $firstScan = $false
    } catch {
        Add-JsonLine -Path $AlertLog -Payload ([ordered]@{
            at = (Get-Date).ToString("o")
            status = "ERROR"
            error = $_.Exception.Message
        })
    }
    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}
