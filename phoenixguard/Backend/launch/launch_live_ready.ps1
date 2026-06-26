[Diagnostics.CodeAnalysis.SuppressMessageAttribute("PSAvoidUsingWriteHost", "", Justification = "Compatibility wrapper prints the canonical launch path.")]
param(
	[string]$BrokerWindowQuery = $(if ($env:PHOENIXGUARD_BROKER_WINDOW_QUERY) { $env:PHOENIXGUARD_BROKER_WINDOW_QUERY } else { 'The Most Innovative Trading Platform' }),
	[string]$SessionId = $(if ($env:PHOENIXGUARD_TRACKER_SESSION_ID) { $env:PHOENIXGUARD_TRACKER_SESSION_ID } else { 'pocket-live-8788' }),
	[double]$CaptureIntervalSec = $(if ($env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 15.0 }),
	[switch]$NoBrowser,
	[switch]$SkipPreview
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
Set-Location $ProjectRoot

$finalLaunchProfile = 'FINAL_LIVE'
Write-Host "PhoenixGuard launch profile: $finalLaunchProfile"
Write-Warning "launch_live_ready.ps1 is retired. Delegating to the single FINAL_LIVE source of truth."
& (Join-Path -Path $ProjectRoot -ChildPath 'Backend\launch\launch_phoenixguard_live_ready.ps1') `
	-BrokerWindowQuery $BrokerWindowQuery `
	-SessionId $SessionId `
	-CaptureIntervalSec $CaptureIntervalSec `
	-NoBrowser:$NoBrowser `
	-SkipPreview:$SkipPreview
exit $LASTEXITCODE
