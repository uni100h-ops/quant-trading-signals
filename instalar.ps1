param([switch]$SoloDependencias)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
Write-Host 'QUANT TRADING SIGNALS - Guided installation' -ForegroundColor Cyan
function Find-QtsPython {
    $launcher = Get-Command py -ErrorAction SilentlyContinue
    if ($launcher) {
        try {
            $resolved = & $launcher.Source -3.12 -c 'import sys;print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0 -and $resolved) { return [string]$resolved }
        } catch { }
    }
    $candidates = @("$env:LOCALAPPDATA\Programs\Python\Python312\python.exe")
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) { $candidates += $command.Source }
    foreach ($candidate in $candidates) {
        if ($candidate -notlike '*WindowsApps*' -and (Test-Path -LiteralPath $candidate)) {
            & $candidate -c 'import sys;sys.exit(0 if sys.version_info[:2]==(3,12) else 1)' 2>$null
            if ($LASTEXITCODE -eq 0) { return $candidate }
        }
    }
    return $null
}
try {
    $qtsPython = Find-QtsPython
    if (-not $qtsPython) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            throw 'Python 3.12 and WinGet are missing. Install Python 3.12 from python.org and reopen INSTALAR.bat.'
        }
        Write-Host 'Python 3.12 is required. WinGet will display its terms and install Python for this user.'
        & winget install --id Python.Python.3.12 --exact --source winget --scope user
        if ($LASTEXITCODE -ne 0) { throw 'Could not install Python 3.12. Complete installation and retry.' }
        $qtsPython = Find-QtsPython
        if (-not $qtsPython) { throw 'Python has been installed. Close this console and reopen INSTALAR.bat.' }
    }
    if (-not (Test-Path '.venv\Scripts\python.exe')) {
        & $qtsPython -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the local Python environment.' }
    }
    & '.\.venv\Scripts\python.exe' instalar.py
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Keys will not be requested until this is fixed.' }
    if (-not $SoloDependencias) {
        & '.\.venv\Scripts\python.exe' agente.py --configurar
        if ($LASTEXITCODE -ne 0) { throw 'Live setup incomplete. Resolve the issue shown and run INSTALAR.bat again.' }
    }
    Write-Host 'Dependencies installed. The agent has not been started automatically.' -ForegroundColor Green
    exit 0
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
