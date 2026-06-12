param(
    [Alias('Host')]
    [string]$ApiHost = $(if ($env:PHOENIXGUARD_MOBILE_API_HOST) { $env:PHOENIXGUARD_MOBILE_API_HOST } else { '127.0.0.1' }),
    [int]$Port = $(if ($env:PHOENIXGUARD_MOBILE_API_PORT) { [int]$env:PHOENIXGUARD_MOBILE_API_PORT } else { 8787 }),
    [string]$SessionId = "pocket-live-8788",
    [int]$FollowupWindowSec = 10,
    [switch]$StartMobileApi,
    [switch]$SkipDeviceConsent
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if ($PSVersionTable.PSEdition -ne 'Desktop') {
    $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if (-not (Test-Path -LiteralPath $windowsPowerShell)) {
        throw 'Windows PowerShell is required for System.Speech but was not found.'
    }
    & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -ApiHost $ApiHost -Port $Port -SessionId $SessionId -FollowupWindowSec $FollowupWindowSec -StartMobileApi:$StartMobileApi -SkipDeviceConsent:$SkipDeviceConsent
    exit $LASTEXITCODE
}

Add-Type -AssemblyName System.Speech

$script:BaseUrl = "http://$ApiHost`:$Port"
$script:SessionId = [string]$SessionId
$script:WakePatterns = @(
    [regex]'(?i)\bhey\s+808\b',
    [regex]'(?i)\bhey\s+eight\s+zero\s+eight\b',
    [regex]'(?i)\bhey\s+eight[- ]oh[- ]eight\b'
)
$script:AwaitingFollowupUntil = [DateTime]::MinValue
$script:IgnoreRecognitionsUntil = [DateTime]::MinValue
$script:Busy = $false
$script:RecognizerRunning = $false
$script:Synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$script:Recognizer = $null

function Invoke-PhoenixGuardApi {
    param(
        [ValidateSet('GET', 'POST')]
        [string]$Method,
        [string]$Path,
        [object]$Body
    )

    $uri = "$script:BaseUrl$Path"
    if ($null -ne $Body) {
        $json = $Body | ConvertTo-Json -Depth 8 -Compress
        return Invoke-RestMethod -Method $Method -Uri $uri -ContentType 'application/json' -Body $json -TimeoutSec 12
    }
    return Invoke-RestMethod -Method $Method -Uri $uri -TimeoutSec 12
}

function Wait-PhoenixGuardApi {
    param([int]$TimeoutSec = 25)

    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-PhoenixGuardApi -Method GET -Path '/v1/mobile/health'
            if ($health.status -eq 'ok') {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 600
        }
    }
    throw "PhoenixGuard Mobile API did not become ready at $script:BaseUrl."
}

function Initialize-PhoenixGuardApi {
    try {
        [void](Invoke-PhoenixGuardApi -Method GET -Path '/v1/mobile/health')
        return
    } catch {
        if (-not $StartMobileApi) {
            throw "PhoenixGuard Mobile API is not reachable at $script:BaseUrl. Start '.\start_phoenixguard_mobile_api.ps1' or rerun with -StartMobileApi."
        }
    }

    $apiScriptPath = Join-Path $PSScriptRoot 'start_phoenixguard_mobile_api.ps1'
    if (-not (Test-Path -LiteralPath $apiScriptPath)) {
        throw "Mobile API launcher not found at '$apiScriptPath'."
    }

    $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    Write-Host "Starting PhoenixGuard Mobile API at $script:BaseUrl"
    Start-Process -FilePath $windowsPowerShell -ArgumentList @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $apiScriptPath,
        '-Host', $ApiHost,
        '-Port', "$Port"
    ) | Out-Null
    Wait-PhoenixGuardApi
}

function Sync-VoiceSession {
    $encodedSessionId = [Uri]::EscapeDataString([string]$script:SessionId)
    return Invoke-PhoenixGuardApi -Method GET -Path "/v1/voice/status?tracker_session_id=$encodedSessionId"
}

function Request-VoiceDeviceConsent {
    if ($SkipDeviceConsent) {
        return $true
    }

    $deviceNames = @()
    try {
        $deviceNames = @(Get-CimInstance Win32_SoundDevice -ErrorAction Stop | ForEach-Object { [string]$_.Name } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    } catch {
    }

    Write-Host ""
    Write-Host "PhoenixGuard voice bridge is ready for session '$script:SessionId'."
    Write-Host "It needs permission to use your default microphone input and current audio output device."
    if ($deviceNames.Count -gt 0) {
        Write-Host "Detected sound devices:"
        $deviceNames | Select-Object -Unique | ForEach-Object { Write-Host "  - $_" }
    }
    $answer = Read-Host "Allow PhoenixGuard voice to use your microphone and connected output now? [Y/N]"
    if ($answer -match '^(?i)y(?:es)?$') {
        return $true
    }
    Write-Host "Voice bridge permission denied. The dashboard and tracker will continue without live microphone control."
    return $false
}

function Test-WakeWord {
    param([string]$Text)

    foreach ($pattern in $script:WakePatterns) {
        if ($pattern.IsMatch($Text)) {
            return $true
        }
    }
    return $false
}

function Remove-WakeWord {
    param([string]$Text)

    $value = [string]$Text
    foreach ($pattern in $script:WakePatterns) {
        if ($pattern.IsMatch($value)) {
            $value = $pattern.Replace($value, '', 1)
            break
        }
    }
    return $value.Trim(" `t`r`n,.;:!?")
}

function Write-PhoenixGuardSpeech {
    param([string]$Text)

    $message = [string]$Text
    if ([string]::IsNullOrWhiteSpace($message)) {
        return
    }
    Write-Host "[808] $message"
    $estimatedSeconds = [Math]::Max(2, [Math]::Ceiling($message.Length / 18.0))
    $script:IgnoreRecognitionsUntil = (Get-Date).AddSeconds($estimatedSeconds)
    $script:Synth.Speak($message)
    $script:IgnoreRecognitionsUntil = (Get-Date).AddSeconds(1)
}

function Get-VoiceStatus {
    $encodedSessionId = [Uri]::EscapeDataString([string]$script:SessionId)
    return Invoke-PhoenixGuardApi -Method GET -Path "/v1/voice/status?tracker_session_id=$encodedSessionId"
}

function Enable-VoiceRuntime {
    $status = Get-VoiceStatus
    $snapshot = $status.snapshot
    if ($null -eq $snapshot) {
        throw "Voice status payload did not include a snapshot for session '$script:SessionId'."
    }
    return Invoke-PhoenixGuardApi -Method POST -Path '/v1/voice/preferences' -Body @{
        voice_enabled = $true
        listening_enabled = $true
        automatic_timer_enabled = [bool]$snapshot.automatic_timer_enabled
        tracker_capture_interval_sec = [double]($snapshot.tracker_capture_interval_sec)
        timezone_name = [string]$snapshot.timezone_name
        tracker_session_id = $script:SessionId
    }
}

function Invoke-VoiceCommand {
    param([string]$CommandText)

    return Invoke-PhoenixGuardApi -Method POST -Path '/v1/voice/command' -Body @{
        command = $CommandText
        tracker_session_id = $script:SessionId
    }
}

function Invoke-ClientAction {
    param([object]$Payload)

    if ($null -eq $Payload) {
        return
    }
    $clientAction = $Payload.client_action
    if ($null -eq $clientAction) {
        return
    }
    $actionType = [string]$clientAction.type
    if ($actionType -eq 'open_url' -and -not [string]::IsNullOrWhiteSpace([string]$clientAction.url)) {
        Start-Process ([string]$clientAction.url) | Out-Null
    }
}

function Get-RecognizerCulture {
    $installed = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
    if ($null -eq $installed -or $installed.Count -eq 0) {
        throw 'No Windows speech recognizers are installed on this system.'
    }
    $preferred = $installed | Where-Object { $_.Culture.Name -eq 'en-US' } | Select-Object -First 1
    if ($null -ne $preferred) {
        return $preferred.Culture
    }
    return $installed[0].Culture
}

function Set-RecognizerActive {
    param([bool]$Enabled)

    if ($null -eq $script:Recognizer) {
        return
    }
    if ($Enabled -and -not $script:RecognizerRunning) {
        $script:Recognizer.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Multiple)
        $script:RecognizerRunning = $true
        Write-Host "Microphone listening is active."
        return
    }
    if (-not $Enabled -and $script:RecognizerRunning) {
        try {
            $script:Recognizer.RecognizeAsyncCancel()
        } catch {
        }
        $script:RecognizerRunning = $false
        Write-Host "Microphone listening is paused."
    }
}

Initialize-PhoenixGuardApi
try {
    $voiceStatus = Sync-VoiceSession
    if ($null -ne $voiceStatus.snapshot) {
        Write-Host "Voice runtime synced to tracker session '$script:SessionId'."
    }
} catch {
    Write-Warning "Voice session sync failed: $($_.Exception.Message)"
}

if (-not (Request-VoiceDeviceConsent)) {
    exit 0
}

try {
    $enabledState = Enable-VoiceRuntime
    if ($null -ne $enabledState.snapshot) {
        Write-Host "Voice runtime enabled for session '$script:SessionId'."
    }
} catch {
    Write-Warning "Voice runtime enable failed: $($_.Exception.Message)"
}

$culture = Get-RecognizerCulture
$script:Recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine($culture)
$dictation = New-Object System.Speech.Recognition.DictationGrammar
$script:Recognizer.LoadGrammar($dictation)
$script:Recognizer.SetInputToDefaultAudioDevice()
$script:Recognizer.InitialSilenceTimeout = [TimeSpan]::FromSeconds(3)
$script:Recognizer.BabbleTimeout = [TimeSpan]::FromSeconds(0)
$script:Recognizer.EndSilenceTimeout = [TimeSpan]::FromMilliseconds(550)
$script:Recognizer.EndSilenceTimeoutAmbiguous = [TimeSpan]::FromMilliseconds(850)

$script:Recognizer.add_SpeechRecognized({
    param($eventSender, $speechEventArgs)

    if ($script:Busy) {
        return
    }
    if ((Get-Date) -lt $script:IgnoreRecognitionsUntil) {
        return
    }
    if ($null -eq $speechEventArgs.Result) {
        return
    }
    $spokenText = [string]$speechEventArgs.Result.Text
    if ([string]::IsNullOrWhiteSpace($spokenText)) {
        return
    }
    if ([double]$speechEventArgs.Result.Confidence -lt 0.42) {
        return
    }

    Write-Host "[heard] $spokenText"
    $script:Busy = $true
    try {
        $status = Get-VoiceStatus
        $snapshot = $status.snapshot
        if ($null -eq $snapshot) {
            return
        }
        if (-not [bool]$snapshot.voice_enabled) {
            return
        }
        if (-not [bool]$snapshot.listening_enabled) {
            return
        }

        $hasWakeWord = Test-WakeWord -Text $spokenText
        $withinFollowupWindow = (Get-Date) -lt $script:AwaitingFollowupUntil

        if (-not $hasWakeWord -and -not $withinFollowupWindow) {
            return
        }

        if ($hasWakeWord) {
            $commandText = Remove-WakeWord -Text $spokenText
            $script:AwaitingFollowupUntil = (Get-Date).AddSeconds([Math]::Max(4, $FollowupWindowSec))
            if ([string]::IsNullOrWhiteSpace($commandText)) {
                Write-PhoenixGuardSpeech ([string]$snapshot.greeting)
                return
            }
        } else {
            $commandText = $spokenText.Trim()
        }

        $response = Invoke-VoiceCommand -CommandText $commandText
        Invoke-ClientAction -Payload $response.payload
        Write-PhoenixGuardSpeech ([string]$response.response_text)
        $script:AwaitingFollowupUntil = (Get-Date).AddSeconds([Math]::Max(4, $FollowupWindowSec))
    } catch {
        Write-Warning $_
        Write-PhoenixGuardSpeech 'PhoenixGuard voice control cannot reach the control service right now.'
    } finally {
        $script:Busy = $false
    }
})

Write-Host "PhoenixGuard voice bridge is live at $script:BaseUrl"
Write-Host "Tracker session: $script:SessionId"
Write-Host "Say 'Hey 808' to wake the assistant."

try {
    $lastStatusPoll = [DateTime]::MinValue
    while ($true) {
        if ((Get-Date) -ge $lastStatusPoll.AddSeconds(2)) {
            try {
                $status = Get-VoiceStatus
                $snapshot = $status.snapshot
                $shouldListen = $false
                if ($null -ne $snapshot) {
                    $shouldListen = [bool]$snapshot.voice_enabled -and [bool]$snapshot.listening_enabled
                }
                Set-RecognizerActive -Enabled:$shouldListen
            } catch {
                Set-RecognizerActive -Enabled:$false
                Write-Warning "Voice status refresh failed: $($_.Exception.Message)"
            }
            $lastStatusPoll = Get-Date
        }
        Start-Sleep -Seconds 1
    }
} finally {
    if ($null -ne $script:Recognizer) {
        try {
            $script:Recognizer.RecognizeAsyncCancel()
            $script:RecognizerRunning = $false
            $script:Recognizer.Dispose()
        } catch {
        }
    }
    if ($null -ne $script:Synth) {
        try {
            $script:Synth.Dispose()
        } catch {
        }
    }
}
