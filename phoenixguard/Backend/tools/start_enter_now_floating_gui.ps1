param(
    [string]$SessionId = $(if ($env:PHOENIXGUARD_ENTER_NOW_SESSION_ID) { $env:PHOENIXGUARD_ENTER_NOW_SESSION_ID } else { "pocket-live-8788" }),
    [string]$BaseUrl = $(if ($env:PHOENIXGUARD_ENTER_NOW_BASE_URL) { $env:PHOENIXGUARD_ENTER_NOW_BASE_URL } elseif ($env:PHOENIXGUARD_MOBILE_API_BASE_URL) { $env:PHOENIXGUARD_MOBILE_API_BASE_URL } else { "http://127.0.0.1:8793" }),
    [int]$PollMilliseconds = 15000,
    [switch]$IgnoreExisting,
    [switch]$NoBeep,
    [switch]$NoSystemMessage
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
. (Join-Path -Path $RepoRoot -ChildPath "Backend\launch\Resolve-PhoenixGuardPython.ps1")
$PythonRuntime = Resolve-PhoenixGuardPythonRuntime -ProjectRoot $RepoRoot
$Python = [string]$PythonRuntime.VenvPython

$ArgsList = @(
    (Join-Path $RepoRoot "Backend\tools\enter_now_floating_gui.py"),
    "--session-id", $SessionId,
    "--base-url", $BaseUrl,
    "--poll-ms", [string]$PollMilliseconds
)

if ($IgnoreExisting) {
    $ArgsList += "--ignore-existing"
}
if ($NoBeep) {
    $ArgsList += "--no-beep"
}
if ($NoSystemMessage) {
    $ArgsList += "--no-system-message"
}

& $Python @ArgsList
