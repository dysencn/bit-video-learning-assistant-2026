$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$venv = Join-Path $root '.venv'
$python = Join-Path $venv 'Scripts\python.exe'
$requirements = Join-Path $root 'requirements.txt'
$createdVenv = $false

if (Test-Path $python) {
    & $python -c "import sys; print(sys.executable)" *> $null
    if ($LASTEXITCODE -eq 0) {
        Write-Host 'Found a usable demo/.venv. Reusing it; skip environment creation and dependency download.'
    } else {
        Write-Host 'Found demo/.venv, but it is unavailable on this machine. Rebuilding the local environment ...'
        & python -m venv --clear $venv
        if ($LASTEXITCODE -ne 0) {
            throw '无法修复虚拟环境，请确认已安装 Python 3。'
        }
        $createdVenv = $true
    }
} else {
    Write-Host 'Creating local virtual environment in demo/.venv ...'
    & python -m venv $venv
    if ($LASTEXITCODE -ne 0) {
        throw '无法创建虚拟环境，请确认已安装 Python 3。'
    }
    $createdVenv = $true
}

if ($createdVenv -and (Test-Path $requirements)) {
    Write-Host 'Installing demo dependencies from Tsinghua PyPI mirror ...'
    & $python -m pip install -r $requirements -i 'https://pypi.tuna.tsinghua.edu.cn/simple'
    if ($LASTEXITCODE -ne 0) {
        throw '依赖安装失败，请检查网络或 demo/requirements.txt。'
    }
} elseif (-not (Test-Path $requirements)) {
    Write-Host 'No external dependency file found. The demo uses Python standard library only.'
}

Write-Host 'Starting Smart Course Platform demo at http://127.0.0.1:8000'
$env:OPEN_BROWSER = '1'
& $python (Join-Path $root 'server.py')

