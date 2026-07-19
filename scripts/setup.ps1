$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $projectRoot "backend"
$frontendRoot = Join-Path $projectRoot "frontend"
$backendPython = Join-Path $backendRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $backendPython -PathType Leaf)) {
    uv venv --python 3.12 (Join-Path $backendRoot ".venv")
}

uv pip install --python $backendPython -r (Join-Path $backendRoot "requirements-dev.txt")

Push-Location -LiteralPath $projectRoot
try { npm install } finally { Pop-Location }
Push-Location -LiteralPath $frontendRoot
try { npm install } finally { Pop-Location }

Write-Host "langbai-TTS-Studio 开发环境已准备完成。" -ForegroundColor Green
Write-Host "运行 scripts\start-dev.ps1 启动软件。"
