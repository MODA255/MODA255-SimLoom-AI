[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("catia", "ansys", "adams", "vi-grade")]
    [string]$Software,
    [string]$ConfigPath = $env:SIMLOOM_CONFIG,
    [string]$ProjectDir = $env:SIMLOOM_PROJECT_DIR,
    [string]$ParametersPath = $env:SIMLOOM_PARAMETERS,
    [string]$SoftwareVersion = "",
    [ValidateSet("available", "borrowed")]
    [string]$LicenseStatus = "available",
    [string]$SmokeTestId = ""
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Error $Message
    exit 2
}

if ($env:OS -ne "Windows_NT") {
    Fail "This harness must run on a Windows self-hosted runner."
}
if (-not $ConfigPath) { Fail "Set SIMLOOM_CONFIG to the worker/controller configuration file." }
if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) { Fail "Config file not found: $ConfigPath" }
if (-not $ProjectDir) { Fail "Set SIMLOOM_PROJECT_DIR to an isolated smoke-test project directory." }
New-Item -ItemType Directory -Force -Path $ProjectDir | Out-Null

$config = Get-Content -Raw -LiteralPath $ConfigPath | ConvertFrom-Json
$bridge = $config.windows_bridge
if (-not $bridge) { Fail "windows_bridge is missing from the configuration." }
$host = [string]$bridge.host
if (-not $host -or $host -eq "0.0.0.0") { $host = "127.0.0.1" }
$baseUrl = "http://{0}:{1}" -f $host, [int]$bridge.port
$headers = @{}
$token = $env:SIMLOOM_BRIDGE_TOKEN
if (-not $token) { $token = [string]$bridge.token }
if ($token) { $headers["X-Bridge-Token"] = $token }

try {
    $health = Invoke-RestMethod -Method Get -Uri "$baseUrl/health" -Headers $headers -TimeoutSec 15
    $capabilities = Invoke-RestMethod -Method Get -Uri "$baseUrl/capabilities" -Headers $headers -TimeoutSec 15
} catch {
    Fail "Windows worker is not reachable at $baseUrl : $($_.Exception.Message)"
}
if ([string]$health.platform -ne "Windows") { Fail "Worker platform is not Windows: $($health.platform)" }
$tool = $capabilities.tools.$Software
if (-not $tool) { Fail "Worker did not advertise tool '$Software'." }
if (-not $tool.actions -or $tool.actions.Count -eq 0) { Fail "No configured actions were advertised for '$Software'." }

$prompts = @{
    "catia" = "CATIA 实机 smoke test：按参数修改模型并导出 STEP"
    "ansys" = "Ansys 实机 smoke test：运行已配置分析并导出结果"
    "adams" = "Adams 实机 smoke test：运行已配置多体模型并导出指标"
    "vi-grade" = "VI-grade 实机 smoke test：运行已配置基准场景并导出指标"
}
$prompt = $prompts[$Software]
$parameters = @{}
if ($ParametersPath) {
    if (-not (Test-Path -LiteralPath $ParametersPath -PathType Leaf)) { Fail "Parameters file not found: $ParametersPath" }
    $parameters = Get-Content -Raw -LiteralPath $ParametersPath | ConvertFrom-Json -AsHashtable
}
$parametersJson = $parameters | ConvertTo-Json -Depth 20 -Compress
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$cli = Join-Path $repoRoot ".venv\Scripts\engineering-agent.exe"
if (-not (Test-Path -LiteralPath $cli -PathType Leaf)) { $cli = "engineering-agent" }

Write-Host "Checking action readiness for $Software ..."
$preflightText = & $cli --config $ConfigPath --project-dir $ProjectDir preflight $prompt --parameters $parametersJson --probe 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Write-Error $preflightText; exit 3 }
$preflight = $preflightText | ConvertFrom-Json
if (-not $preflight.ready) { Write-Error $preflightText; exit 3 }

Write-Host "Running the configured $Software smoke test ..."
$runText = & $cli --config $ConfigPath --project-dir $ProjectDir run $prompt --parameters $parametersJson --execute 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Write-Error $runText; exit 4 }
$report = $runText | ConvertFrom-Json
if ($report.status -ne "committed") { Write-Error $runText; exit 4 }

$result = @($report.results | Where-Object { $_.tool -eq $Software -and $_.status -eq "completed" }) | Select-Object -First 1
if (-not $result) { Fail "No completed $Software result was found in the run report." }
$requestId = [string]$result.details.remote_job.request_id
$vendorAction = [string]$result.details.action
if (-not $requestId) { Fail "The completed result did not contain a durable remote request ID." }
if (-not $SoftwareVersion) {
    $versionVariable = "SIMLOOM_{0}_VERSION" -f ($Software -replace '-', '_').ToUpper()
    $SoftwareVersion = [Environment]::GetEnvironmentVariable($versionVariable)
}
if (-not $SoftwareVersion) { Fail "Provide -SoftwareVersion or SIMLOOM_<SOFTWARE>_VERSION." }
if (-not $SmokeTestId) { $SmokeTestId = "simloom-$Software-smoke" }

$evidence = @{
    schema_version = "simloom-ai/worker/v1"
    operation = "evidence.record"
    evidence_schema = "simloom-ai/machine-evidence/v1"
    request_id = $requestId
    tool = $Software
    action = $vendorAction
    software_version = $SoftwareVersion
    license_status = $LicenseStatus
    smoke_test_id = $SmokeTestId
    operator = [Environment]::UserName
    notes = "Self-hosted Windows smoke test completed by SimLoom harness."
} | ConvertTo-Json -Depth 20

Write-Host "Recording machine evidence for request $requestId ..."
$recorded = Invoke-RestMethod -Method Post -Uri $baseUrl -Headers $headers -ContentType "application/json" -Body $evidence -TimeoutSec 30
if (-not $recorded.valid) { Write-Error ($recorded | ConvertTo-Json -Depth 20); exit 5 }
Write-Output ($recorded | ConvertTo-Json -Depth 20)
