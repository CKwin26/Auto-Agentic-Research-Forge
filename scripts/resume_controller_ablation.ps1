$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$matrix = Join-Path $workspace "benchmark_runs/controller-ablation-20260717T070745.983951+0000-df5aeb"
$python = Join-Path $workspace ".venv/Scripts/python.exe"
$resumeScript = Join-Path $workspace "scripts/resume_frozen_ablation.py"
$log = Join-Path $matrix "scheduled-resume.log"
$docker = "D:/Docker/Desktop/resources/bin/docker.exe"
$expectedImageId = "sha256:ed0c4ef7a59bc0587c92b69ef994b1873d0fa9eb84c01ec8ce4f8bb5b126812d"

Set-Location -LiteralPath $workspace

try {
    "[$(Get-Date -Format o)] scheduled resume started" | Out-File -LiteralPath $log -Encoding utf8 -Append
    $imageReady = $false
    for ($attempt = 1; $attempt -le 120; $attempt++) {
        $actualImageId = (& $docker image inspect rf-airs-cpu:v1 --format "{{.Id}}" 2>$null | Select-Object -First 1)
        if ($actualImageId -eq $expectedImageId) {
            $imageReady = $true
            "[$(Get-Date -Format o)] frozen Docker image verified on attempt $attempt" | Out-File -LiteralPath $log -Encoding utf8 -Append
            break
        }
        "[$(Get-Date -Format o)] frozen Docker image unavailable on attempt $attempt; observed '$actualImageId'" | Out-File -LiteralPath $log -Encoding utf8 -Append
        Start-Sleep -Seconds 30
    }
    if (-not $imageReady) {
        throw "frozen Docker image did not become stable within the retry window"
    }

    $previousErrorPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = & $python $resumeScript $matrix 2>&1
    $exitCode = $LASTEXITCODE
    $ErrorActionPreference = $previousErrorPreference
    $output | Out-File -LiteralPath $log -Encoding utf8 -Append
    "[$(Get-Date -Format o)] scheduled resume exited with code $exitCode" | Out-File -LiteralPath $log -Encoding utf8 -Append
    exit $exitCode
}
catch {
    "[$(Get-Date -Format o)] scheduled resume failed: $($_ | Out-String)" | Out-File -LiteralPath $log -Encoding utf8 -Append
    exit 1
}
