<#
.SYNOPSIS
Start, inspect, stop or export controlled traffic-history sessions.
.EXAMPLE
.\scripts\traffic_session.ps1 -Hours 2
.EXAMPLE
.\scripts\traffic_session.ps1 -Minutes 90 -Name "evening"
.EXAMPLE
.\scripts\traffic_session.ps1 -Action status
.EXAMPLE
.\scripts\traffic_session.ps1 -Action stop
#>
[CmdletBinding()]
param(
    [ValidateSet("start", "status", "stop", "export")]
    [string]$Action = "start",
    [double]$Hours,
    [double]$Minutes,
    [double]$MinRate = 5,
    [double]$MaxRate = 60,
    [double]$IntervalSeconds = 5,
    [Nullable[int]]$Seed = $null,
    [string]$Name,
    [string]$Session,
    [string[]]$Sessions,
    [string]$Output,
    [string]$OutputRoot = "runtime/research/traffic-sessions",
    [switch]$DryRun,
    [switch]$Lean
)

$ErrorActionPreference = "Stop"
if ($PSBoundParameters.ContainsKey("Hours") -and $PSBoundParameters.ContainsKey("Minutes")) {
    throw "Choose -Hours or -Minutes, not both."
}
if ($Action -ne "start" -and ($Lean -or $DryRun -or $PSBoundParameters.ContainsKey("Hours") -or $PSBoundParameters.ContainsKey("Minutes"))) {
    throw "Duration, -Lean and -DryRun apply only to -Action start."
}

# Use invariant decimal arguments even when the Windows locale uses commas.
function NumberArgument([double]$Value) {
    return $Value.ToString("R", [System.Globalization.CultureInfo]::InvariantCulture)
}

$cliArgs = @("-m", "research.collect_session", $Action)
if ($Action -eq "start") {
    if ($PSBoundParameters.ContainsKey("Hours")) {
        $cliArgs += @("--hours", (NumberArgument $Hours))
    } elseif ($PSBoundParameters.ContainsKey("Minutes")) {
        $cliArgs += @("--minutes", (NumberArgument $Minutes))
    } else {
        $cliArgs += @("--hours", "2")
    }
    $cliArgs += @("--min-rate", (NumberArgument $MinRate), "--max-rate", (NumberArgument $MaxRate),
                  "--sample-seconds", (NumberArgument $IntervalSeconds), "--output-root", $OutputRoot)
    if ($null -ne $Seed) { $cliArgs += @("--seed", "$Seed") }
    if ($Name) { $cliArgs += @("--name", $Name) }
    if ($DryRun) { $cliArgs += "--dry-run" }
    if ($Lean) { $cliArgs += "--lean" }
} elseif ($Action -eq "export") {
    if (-not $Sessions -or -not $Output) { throw "Export needs -Sessions <directories> and -Output <new.jsonl>." }
    $cliArgs += @("--sessions") + $Sessions + @("--output", $Output)
} else {
    $cliArgs += @("--output-root", $OutputRoot)
    if ($Session) { $cliArgs += @("--session", $Session) }
}

Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & python @cliArgs
    $commandExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $commandExitCode
