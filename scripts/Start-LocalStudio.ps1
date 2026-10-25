[CmdletBinding()]
param(
    [ValidateSet("Desktop", "Web")]
    [string]$Mode = "Desktop",
    [string]$DataRoot,
    [ValidateRange(1024, 65535)]
    [int]$Port = 18765,
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$studioRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $studioRoot "frontend"
$backendRoot = Join-Path $studioRoot "backend"
$backendPython = Join-Path $backendRoot ".venv\Scripts\python.exe"
$electronExecutable = Join-Path $studioRoot "node_modules\electron\dist\electron.exe"
$frontendEntry = Join-Path $frontendRoot "dist\index.html"
$healthUrl = "http://127.0.0.1:$Port/health"
$webUrl = "http://127.0.0.1:$Port/longform"

if ([string]::IsNullOrWhiteSpace($DataRoot)) {
    $documentsRoot = [Environment]::GetFolderPath("MyDocuments")
    if ([string]::IsNullOrWhiteSpace($documentsRoot)) {
        throw "找不到当前用户的文档目录，请用 -DataRoot 指定本机数据目录。"
    }
    $DataRoot = Join-Path $documentsRoot "langbai-TTS-Studio\data"
}
if ($DataRoot -notmatch '^[A-Za-z]:[\\/]' -or $DataRoot.StartsWith("\\") -or $DataRoot -match '["\r\n]') {
    throw "DataRoot 必须为本机磁盘的绝对路径，不能使用 UNC 或换行。"
}
$resolvedDataRoot = [IO.Path]::GetFullPath($DataRoot).TrimEnd([char[]]"\/")
if ($resolvedDataRoot -eq [IO.Path]::GetPathRoot($resolvedDataRoot).TrimEnd([char[]]"\/")) {
    throw "不能把磁盘根目录直接作为软件数据目录。"
}
$logRoot = Join-Path $resolvedDataRoot "logs"

function Get-LocalServiceHealth {
    try {
        return Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 2
    } catch {
        return $null
    }
}

function Test-LocalPort {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        try {
            if (-not $pending.AsyncWaitHandle.WaitOne(500)) { return $false }
            $client.EndConnect($pending)
            return $client.Connected
        } finally {
            $pending.AsyncWaitHandle.Close()
        }
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Test-CompatibleService($Health) {
    if ($null -eq $Health) { return $false }
    $names = @($Health.PSObject.Properties.Name)
    foreach ($required in @("service", "protocolVersion", "schemaVersion", "dataRoot")) {
        if ($names -notcontains $required) { return $false }
    }
    if ($Health.service -ne "langbai-TTS-Studio-v2" -or
        $Health.protocolVersion -ne 2 -or $Health.schemaVersion -ne 2) { return $false }
    try {
        $serviceDataRoot = [IO.Path]::GetFullPath([string]$Health.dataRoot).TrimEnd([char[]]"\/")
        return [string]::Equals($serviceDataRoot, $resolvedDataRoot, [StringComparison]::OrdinalIgnoreCase)
    } catch {
        return $false
    }
}

function Quote-LocalPath([string]$Value) {
    # Start-Process joins ArgumentList on Windows. These are validated filesystem
    # paths, quoted as individual arguments; no shell expression is constructed.
    if ($Value -match '["\r\n]') { throw "启动路径包含不支持的字符。" }
    return '"' + $Value.TrimEnd([char[]]"\/") + '"'
}

$health = Get-LocalServiceHealth
$reuseService = Test-CompatibleService $health
if (-not $reuseService -and (Test-LocalPort)) {
    throw "端口 $Port 已有不兼容的服务或使用另一个数据目录。请先关闭该服务，或明确选择不同端口；启动器不会结束它。"
}
if (($Mode -eq "Desktop" -or -not $reuseService) -and -not (Test-Path -LiteralPath $backendPython -PathType Leaf)) {
    throw "缺少 backend\.venv\Scripts\python.exe。此入口要求已准备源码依赖，请先按 README 的开发准备步骤安装。"
}
if ($Mode -eq "Desktop" -and -not (Test-Path -LiteralPath $electronExecutable -PathType Leaf)) {
    throw "缺少本地 Electron。请先准备源码依赖；启动器不会自动联网安装依赖。"
}
if (($Mode -eq "Desktop" -or -not $reuseService) -and -not (Test-Path -LiteralPath $frontendEntry -PathType Leaf)) {
    $npmCommand = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $npmCommand) { throw "缺少已构建的前端，也未找到 npm.cmd。请先准备 Node.js 与前端依赖。" }
    Write-Host "正在构建本地前端，不会启动 Vite 或下载模型。"
    & $npmCommand.Source --prefix $frontendRoot run build
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $frontendEntry -PathType Leaf)) {
        throw "前端构建失败，请检查输出；任务服务未被替换。"
    }
}

New-Item -ItemType Directory -Path $logRoot -Force | Out-Null
$launchId = "{0}-{1}-{2}" -f $Mode.ToLowerInvariant(), (Get-Date -Format "yyyyMMdd-HHmmss"), $PID
$outputLog = Join-Path $logRoot "$launchId.stdout.log"
$errorLog = Join-Path $logRoot "$launchId.stderr.log"
$environmentValues = @{
    LANGBAI_USE_BUILT_UI = "1"
    LANGBAI_BACKEND_PORT = [string]$Port
    LANGBAI_TTS_PORT = [string]$Port
    LANGBAI_CAPTURE_DATA = $resolvedDataRoot
    LANGBAI_TTS_DATA = $resolvedDataRoot
    LANGBAI_BACKEND_ROOT = $backendRoot
    LANGBAI_PROJECT_ROOT = $studioRoot
    LANGBAI_FRONTEND_ROOT = (Join-Path $frontendRoot "dist")
    PYTHONUTF8 = "1"
}
$previousEnvironment = @{}
$launchedProcess = $null
try {
    foreach ($name in $environmentValues.Keys) {
        $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $environmentValues[$name], "Process")
    }
    if ($Mode -eq "Desktop") {
        $launchedProcess = Start-Process -FilePath $electronExecutable `
            -ArgumentList (Quote-LocalPath $studioRoot) -WorkingDirectory $studioRoot `
            -WindowStyle Hidden -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -PassThru
    } elseif (-not $reuseService) {
        $arguments = @((Quote-LocalPath (Join-Path $backendRoot "run.py")), "--headless", "--data-dir", (Quote-LocalPath $resolvedDataRoot), "--port", [string]$Port)
        $launchedProcess = Start-Process -FilePath $backendPython -ArgumentList ($arguments -join " ") `
            -WorkingDirectory $backendRoot -WindowStyle Hidden `
            -RedirectStandardOutput $outputLog -RedirectStandardError $errorLog -PassThru
    }
} finally {
    foreach ($name in $previousEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], "Process")
    }
}

$deadline = [DateTime]::UtcNow.AddMinutes(3)
while (-not $reuseService -and [DateTime]::UtcNow -lt $deadline) {
    $health = Get-LocalServiceHealth
    if (Test-CompatibleService $health) { $reuseService = $true; break }
    if ($null -ne $launchedProcess -and $launchedProcess.HasExited) {
        throw "启动进程已经退出，请检查 $errorLog。若另一个桌面窗口已打开，请在该窗口确认数据目录和端口。"
    }
    if ($null -ne $health) {
        throw "端口 $Port 上出现不兼容服务，启动器不会接管或结束它。请检查 $errorLog。"
    }
    Start-Sleep -Milliseconds 500
}
if (-not $reuseService) {
    throw "服务尚未就绪。首次逐文件备份可能需要更久；后台进程仍保留，请查看 $outputLog 和 $errorLog，不要重复启动另一套服务。"
}
Write-Host "任务服务已连接：$webUrl"
Write-Host "数据目录：$resolvedDataRoot"
if ($Mode -eq "Web" -and -not $NoBrowser) {
    # This is the interactive browser requested by the Web entry, not a helper.
    Start-Process -FilePath $webUrl
}
