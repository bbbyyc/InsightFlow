[CmdletBinding()]
param(
    [switch]$NoBuild,
    [switch]$Stop,
    [switch]$WithMcp
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

function Write-Step([string]$Message) { Write-Host "`n==> $Message" -ForegroundColor Cyan }
function Fail([string]$Message) { Write-Host "`nFailed: $Message" -ForegroundColor Red; exit 1 }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker was not found. Install Docker Desktop or Docker Engine with Compose v2."
}

$oldPreference = $ErrorActionPreference
$ErrorActionPreference = "SilentlyContinue"
& docker info *> $null
$dockerExit = $LASTEXITCODE
$ErrorActionPreference = $oldPreference
if ($dockerExit -ne 0) { Fail "The Docker engine is not running or is not accessible to this user." }

if ($Stop) {
    Write-Step "Stopping InsightFlow"
    docker compose down
    exit $LASTEXITCODE
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example."
}

$envLines = Get-Content ".env"
function Get-DotEnvValue([string]$Name, [string]$Default) {
    $line = $envLines | Where-Object { $_ -match "^$([regex]::Escape($Name))=" } | Select-Object -First 1
    if (-not $line) { return $Default }
    $value = ($line -split '=', 2)[1].Trim()
    if ($value) { return $value }
    return $Default
}
$passwordLine = $envLines | Where-Object { $_ -match '^POSTGRES_PASSWORD=' } | Select-Object -First 1
if (-not $passwordLine -or $passwordLine -match '^POSTGRES_PASSWORD=$') {
    Fail "Set POSTGRES_PASSWORD in .env before starting."
}

$apiKeyLine = $envLines | Where-Object { $_ -match '^DEEPSEEK_API_KEY=' } | Select-Object -First 1
if (-not $apiKeyLine -or $apiKeyLine -match '^DEEPSEEK_API_KEY=$') {
    Fail "Set DEEPSEEK_API_KEY in .env; the complete product stack includes answer generation and LangGraph rewriting."
}

Write-Step "Building and starting PostgreSQL, Redis, migration, backend, worker, and frontend"
$composeArgs = @("compose")
if ($WithMcp) { $composeArgs += @("--profile", "mcp") }
$composeArgs += @("up", "-d", "--wait")
if (-not $NoBuild) { $composeArgs += "--build" }
& docker @composeArgs
if ($LASTEXITCODE -ne 0) {
    docker compose ps
    docker compose logs --tail 100
    Fail "Docker Compose did not reach a healthy state."
}

Write-Step "Verifying migration and readiness"
docker compose run --rm migrate python -m alembic current
if ($LASTEXITCODE -ne 0) { Fail "Database migration verification failed." }

$backendPort = Get-DotEnvValue "BACKEND_PORT" "8000"
$frontendPort = Get-DotEnvValue "FRONTEND_PORT" "3000"
try {
    $ready = Invoke-RestMethod -Uri "http://127.0.0.1:$backendPort/api/ready" -TimeoutSec 5
} catch {
    docker compose logs --tail 100 backend worker
    Fail "Backend readiness request failed."
}
if ($ready.status -ne "ready") { Fail "Backend did not report ready." }

Write-Host "`nInsightFlow is healthy" -ForegroundColor Green
Write-Host "Web:      http://localhost:$frontendPort"
Write-Host "API:      http://localhost:$backendPort"
Write-Host "API docs: http://localhost:$backendPort/docs"
Write-Host "`nLogs: docker compose logs -f"
Write-Host "Stop: .\start.ps1 -Stop"
