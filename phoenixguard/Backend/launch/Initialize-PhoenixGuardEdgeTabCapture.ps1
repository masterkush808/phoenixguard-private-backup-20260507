function Initialize-PhoenixGuardEdgeTabCaptureEnvironment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$RuntimeDir,
        [double]$MinIntervalSec = 4.0
    )

    if (-not (Test-Path -LiteralPath $RuntimeDir)) {
        New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null
    }

    $tokenPath = Join-Path -Path $RuntimeDir -ChildPath 'edge_tab_capture.token'
    $configuredToken = [string][Environment]::GetEnvironmentVariable(
        'PHOENIXGUARD_FRAME_INGEST_TOKEN',
        'Process'
    )
    $locallyManaged = (
        [string][Environment]::GetEnvironmentVariable(
            'PHOENIXGUARD_FRAME_INGEST_TOKEN_LOCAL_MANAGED',
            'Process'
        )
    ) -eq '1'
    $storedToken = ''
    if (Test-Path -LiteralPath $tokenPath -PathType Leaf) {
        try {
            $storedToken = [string](Get-Content -LiteralPath $tokenPath -Raw -ErrorAction Stop)
            $storedToken = $storedToken.Trim()
        } catch {
            $storedToken = ''
        }
    }

    $storedTokenValid = $storedToken.Length -ge 64
    $generatedLocally = $false
    $persistedTokenReused = $false
    if ($storedTokenValid -and ([string]::IsNullOrWhiteSpace($configuredToken) -or $locallyManaged)) {
        # A locally managed credential is immutable across backend process
        # replacement. This keeps an installed Edge extension's persisted
        # credential valid and avoids any browser reload or reconfiguration.
        $configuredToken = $storedToken
        $persistedTokenReused = $true
        $locallyManaged = $true
    } elseif ([string]::IsNullOrWhiteSpace($configuredToken)) {
        $tokenBytes = New-Object byte[] 32
        $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $generator.GetBytes($tokenBytes)
        } finally {
            $generator.Dispose()
        }
        $configuredToken = [System.BitConverter]::ToString($tokenBytes).Replace('-', '').ToLowerInvariant()
        $temporaryTokenPath = "$tokenPath.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
        try {
            [System.IO.File]::WriteAllText(
                $temporaryTokenPath,
                $configuredToken,
                (New-Object System.Text.UTF8Encoding($false))
            )
            Move-Item -LiteralPath $temporaryTokenPath -Destination $tokenPath -Force
        } finally {
            Remove-Item -LiteralPath $temporaryTokenPath -Force -ErrorAction SilentlyContinue
        }
        $storedToken = $configuredToken
        $generatedLocally = $true
        [Environment]::SetEnvironmentVariable(
            'PHOENIXGUARD_FRAME_INGEST_TOKEN',
            $configuredToken,
            'Process'
        )
        [Environment]::SetEnvironmentVariable(
            'PHOENIXGUARD_FRAME_INGEST_TOKEN_LOCAL_MANAGED',
            '1',
            'Process'
        )
        $locallyManaged = $true
    } elseif ($locallyManaged -and -not $storedTokenValid) {
        # The canonical kill switch may clear runtime/live after the token was
        # generated but before child processes start. The nested launcher calls
        # this initializer again and restores only its own locally managed
        # credential; an operator-supplied environment token is never persisted.
        $temporaryTokenPath = "$tokenPath.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
        try {
            [System.IO.File]::WriteAllText(
                $temporaryTokenPath,
                $configuredToken,
                (New-Object System.Text.UTF8Encoding($false))
            )
            Move-Item -LiteralPath $temporaryTokenPath -Destination $tokenPath -Force
        } finally {
            Remove-Item -LiteralPath $temporaryTokenPath -Force -ErrorAction SilentlyContinue
        }
        $storedToken = $configuredToken
    }

    [Environment]::SetEnvironmentVariable(
        'PHOENIXGUARD_FRAME_INGEST_TOKEN',
        $configuredToken,
        'Process'
    )
    if ($locallyManaged) {
        [Environment]::SetEnvironmentVariable(
            'PHOENIXGUARD_FRAME_INGEST_TOKEN_LOCAL_MANAGED',
            '1',
            'Process'
        )
    }

    $normalizedInterval = [Math]::Max(1.0, [double]$MinIntervalSec)
    $env:PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC = (
        [string]$normalizedInterval
    ).Replace(',', '.')
    $env:PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN = '1'
    $env:PHOENIXGUARD_FRAME_INGEST_REQUIRE_CAPTURE_EPOCH = '1'
    $env:PHOENIXGUARD_FRAME_INGEST_REQUIRE_FRAME_ID = '1'

    $tokenPathForOperator = ''
    if (
        (Test-Path -LiteralPath $tokenPath -PathType Leaf) -and
        -not [string]::IsNullOrWhiteSpace($storedToken) -and
        $storedToken -ceq $configuredToken
    ) {
        $tokenPathForOperator = $tokenPath
    } elseif ($generatedLocally) {
        $tokenPathForOperator = $tokenPath
    }

    return [pscustomobject]@{
        Armed = -not [string]::IsNullOrWhiteSpace($configuredToken)
        TokenPath = $tokenPathForOperator
        GeneratedLocally = $generatedLocally
        PersistedTokenReused = $persistedTokenReused
        MinIntervalSec = $normalizedInterval
    }
}
