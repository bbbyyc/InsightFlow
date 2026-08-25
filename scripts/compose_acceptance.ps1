[CmdletBinding()]
param([string]$Api = "http://127.0.0.1:8000", [string]$Web = "http://127.0.0.1:3000")
$ErrorActionPreference = "Stop"

docker compose ps
$ready = Invoke-RestMethod "$Api/api/ready"
if ($ready.status -ne "ready") { throw "Backend is not ready" }
$webResponse = Invoke-WebRequest -UseBasicParsing $Web
if ($webResponse.StatusCode -ne 200) { throw "Frontend returned $($webResponse.StatusCode)" }
docker compose exec -T backend python -m alembic current
docker compose exec -T worker celery -A app.tasks inspect ping --timeout 5
Write-Host "Compose infrastructure acceptance passed. Continue with browser upload/chat/citation checks." -ForegroundColor Green
