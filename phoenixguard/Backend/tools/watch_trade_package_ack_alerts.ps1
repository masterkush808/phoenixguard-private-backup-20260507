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

function Get-Mt4CommandKey {
    param($Command)
    $packet = Get-TextValue $Command.packet_id
    $updated = Get-TextValue $Command.timestamp_utc
    if ($packet.Length -gt 0) {
        return "mt4_command|$packet|$updated"
    }
    return "mt4_command|$($Command.bridge_sequence)|$updated|$($Command.action)|$($Command.side)"
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
    return [DateTimeOffset]::Now.AddSeconds([Math]::Max(60, $DefaultValidSeconds))
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
    $form.TopMost = $true
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
    $timer.Add_Tick({
        try {
            [console]::beep(1200, 250)
            [console]::beep(900, 180)
        } catch {
        }
        if ([DateTimeOffset]::Now -gt $ValidUntil) {
            $form.Tag = "EXPIRED"
            $form.Close()
        }
    })

    $form.Controls.Add($textBox)
    $form.Controls.Add($button)
    $form.Add_Shown({
        $form.Activate()
        $timer.Start()
    })
    $form.Add_FormClosed({
        $timer.Stop()
        $timer.Dispose()
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
        Show-AckAlert -Title $title -Body (Format-EntryAlertBody $row) -ValidUntil (Get-ValidUntil $row) -Payload $row
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
    $status = Get-TextValue $command.bridge_status
    $action = Get-TextValue $command.action
    $side = Get-TextValue $command.side
    $packet = Get-TextValue $command.packet_id
    if (-not $packet -and ($status -eq "NO_EXECUTION_PACKET" -or -not $action)) {
        return
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
    $validUntil = [DateTimeOffset]::Now.AddSeconds([Math]::Max(60, $DefaultValidSeconds))
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
    Show-AckAlert -Title "PhoenixGuard MT4 Attempt" -Body $body -ValidUntil $validUntil -Payload $command
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
