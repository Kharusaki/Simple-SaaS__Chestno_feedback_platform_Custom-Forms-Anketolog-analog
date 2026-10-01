# Installs Python 3.12 silently, without administrator rights.
#
# Called from start.bat / start_tunnel.bat when no suitable Python is found.
# ASCII only, like the .bat files: Windows PowerShell 5.1 reads a .ps1
# without a BOM as the system ANSI codepage, so Russian text here would turn
# into mojibake. Human-readable Russian messages are printed by
# scripts\launcher.py once Python is available.

$ErrorActionPreference = 'Stop'

$Version = '3.12.10'
$Target = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'
$PythonExe = Join-Path $Target 'python.exe'

function Test-UsablePython {
    param([string]$Exe)

    if (-not (Test-Path -LiteralPath $Exe)) { return $false }
    try {
        $v = & $Exe -c 'import sys; print(sys.version_info[0] * 100 + sys.version_info[1])' 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        return ([int]$v -ge 312)
    } catch {
        return $false
    }
}

if (Test-UsablePython -Exe $PythonExe) {
    Write-Host '  Python is already installed.'
    exit 0
}

if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    $Arch = 'arm64'
} else {
    $Arch = 'amd64'
}

$InstallerUrl = "https://www.python.org/ftp/python/$Version/python-$Version-$Arch.exe"
$InstallerPath = Join-Path $env:TEMP "python-$Version-$Arch.exe"

Write-Host '  Downloading Python 3.12, about 26 MB...'

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $client = New-Object System.Net.WebClient
    $client.DownloadFile($InstallerUrl, $InstallerPath)
    $client.Dispose()
} catch {
    Write-Host "  Download failed: $($_.Exception.Message)"
    Write-Host '  Check the internet connection and try again.'
    exit 1
}

# A captive portal or a proxy can save an HTML page under the .exe name.
# The real installer is about 26 MB, so a small file means trouble.
$size = (Get-Item -LiteralPath $InstallerPath).Length
if ($size -lt 10MB) {
    Write-Host "  The downloaded file is only $([math]::Round($size / 1KB)) KB - it is not the installer."
    Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
    Write-Host '  Probably a network restriction. Try another connection.'
    exit 1
}

Write-Host '  Installing Python, about 1-2 minutes. No administrator rights needed.'

# SimpleInstall=0 is required, otherwise TargetDir is ignored and the
# installer may ask questions that nobody is there to answer.
$arguments = @(
    '/quiet'
    'SimpleInstall=0'
    'InstallAllUsers=0'
    "TargetDir=$Target"
    'PrependPath=1'
    'Include_launcher=0'
    'InstallLauncherAllUsers=0'
    'Include_test=0'
    'Include_doc=0'
    'Include_tcltk=0'
    'AssociateFiles=0'
    'Shortcuts=0'
    'Include_pip=1'
    'Include_symbols=0'
    'Include_debug=0'
)

try {
    $process = Start-Process -FilePath $InstallerPath -ArgumentList $arguments -Wait -PassThru
} catch {
    Write-Host "  Install failed: $($_.Exception.Message)"
    exit 1
}

# 0 = success, 3010 = success but a reboot is suggested. Both are fine.
if ($process.ExitCode -ne 0 -and $process.ExitCode -ne 3010) {
    Write-Host "  Installer returned code $($process.ExitCode)."
    exit 1
}

if (-not (Test-UsablePython -Exe $PythonExe)) {
    Write-Host '  Python was installed but did not start. Restart Windows and try again.'
    exit 1
}

Remove-Item -LiteralPath $InstallerPath -Force -ErrorAction SilentlyContinue
Write-Host "  Python $Version installed."
exit 0