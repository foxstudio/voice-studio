param(
    [switch]$Force,
    [switch]$DevReload,
    [switch]$Doctor,
    [switch]$Production
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    Write-Error "MLX 暂不支持 Windows 原生后端。请先安装 WSL 2，然后重新运行 .\start.ps1。"
}

$DoctorStartScript = Join-Path $ProjectRoot "start.sh"
$WslDoctorScript = & wsl.exe wslpath -a $DoctorStartScript
$WslDoctorPathExitCode = $LASTEXITCODE
$WslDoctorScript = ($WslDoctorScript | Out-String).Trim()
if ($WslDoctorPathExitCode -ne 0 -or -not $WslDoctorScript) {
    Write-Error "WSL 无法解析 Voice Studio 项目路径。请确认已安装并启动 WSL 2 发行版。"
}

if ($Doctor) {
    Write-Host "在 WSL 2 中检查 Voice Studio 的 Python 和启动依赖。"
    & wsl.exe bash $WslDoctorScript --doctor
    exit $LASTEXITCODE
}

if ($Production -and ($Force -or $DevReload)) {
    Write-Error "生产模式以前台单进程运行，不支持 -Force 或 -DevReload。"
}

$LauncherName = if ($Production) { "start-production.sh" } else { "start.sh" }
$StartScript = Join-Path $ProjectRoot $LauncherName
$WslStartScript = & wsl.exe wslpath -a $StartScript
$WslStartPathExitCode = $LASTEXITCODE
$WslStartScript = ($WslStartScript | Out-String).Trim()
if ($WslStartPathExitCode -ne 0 -or -not $WslStartScript) {
    Write-Error "WSL 无法解析 Voice Studio 项目路径，请确认已安装并启动 WSL 2 发行版。"
}

$ForwardArgs = @()
if ($Force) { $ForwardArgs += "--force" }
if ($DevReload) { $ForwardArgs += "--dev-reload" }

Write-Host "Windows 已检测：通过 WSL 2 启动 Voice Studio。"
& wsl.exe bash $WslStartScript @ForwardArgs
exit $LASTEXITCODE
