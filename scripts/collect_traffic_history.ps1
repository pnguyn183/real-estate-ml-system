param(
    [int]$Hours = 6,
    [int]$IntervalSeconds = 5,
    [string]$OutputRoot = "runtime/research/traffic-history",
    [datetime]$Until = $null
)

$ErrorActionPreference = "Stop"

if ($Hours -lt 1 -and $null -eq $Until) {
    throw "Hours must be at least 1."
}
if ($IntervalSeconds -lt 1) {
    throw "IntervalSeconds must be at least 1."
}
if ($null -ne $Until) {
    $secondsRemaining = ($Until - (Get-Date)).TotalSeconds
    if ($secondsRemaining -le 0) {
        throw "Until must be later than the current time."
    }
    $sampleCount = [int][math]::Ceiling($secondsRemaining / $IntervalSeconds)
} else {
    $sampleCount = [int][math]::Ceiling(($Hours * 3600) / $IntervalSeconds)
}

Write-Host "Checking Docker Desktop..." -ForegroundColor Cyan
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop is not running. Start Docker Desktop and run this script again."
}

Write-Host "Starting Kafka, MongoDB, processors and scraper..." -ForegroundColor Cyan
docker compose up -d
if ($LASTEXITCODE -ne 0) {
    throw "docker compose up failed."
}

Write-Host "Waiting for services to become ready..." -ForegroundColor Cyan
$required = @(
    "real_estate_kafka_1",
    "real_estate_kafka_2",
    "real_estate_kafka_3",
    "real_estate_mongodb",
    "real_estate_processor_1",
    "real_estate_processor_2",
    "real_estate_processor_3",
    "real_estate_scraper"
)

$deadline = (Get-Date).AddMinutes(3)
while ((Get-Date) -lt $deadline) {
    $running = docker compose ps --status running --format "{{.Name}}"
    $missing = $required | Where-Object { $_ -notin $running }
    if ($missing.Count -eq 0) {
        break
    }
    Start-Sleep -Seconds 5
}

$running = docker compose ps --status running --format "{{.Name}}"
$missing = $required | Where-Object { $_ -notin $running }
if ($missing.Count -gt 0) {
    docker compose ps
    throw "Required services are not running: $($missing -join ', ')"
}

$runName = "run-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$output = Join-Path $OutputRoot $runName
New-Item -ItemType Directory -Force -Path $output | Out-Null

Write-Host "Collecting $sampleCount telemetry samples every $IntervalSeconds seconds." -ForegroundColor Green
Write-Host "Output: $output" -ForegroundColor Green
Write-Host "Press Ctrl+C to stop collection. The Docker stack will remain running." -ForegroundColor Yellow

python -m research.telemetry `
    --output "$output/observations.jsonl" `
    --samples $sampleCount `
    --interval $IntervalSeconds

if ($LASTEXITCODE -ne 0) {
    throw "Telemetry collection failed."
}

Write-Host "Collection completed: $output/observations.jsonl" -ForegroundColor Green
Write-Host "Next step: run traffic forecasting against this observations.jsonl after enough history has been collected." -ForegroundColor Cyan
