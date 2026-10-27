@echo off
setlocal
set ROOT=%~dp0
set PYTHON=%ROOT%.venv\Scripts\python.exe
set CREATED_VENV=0
if exist "%ROOT%.venv\Scripts\python.exe" (
  "%PYTHON%" -c "import sys; print(sys.executable)" >nul 2>&1
  if errorlevel 1 (
    echo Found demo\.venv, but it is unavailable on this machine. Rebuilding the local environment ...
    python -m venv --clear "%ROOT%.venv"
    if errorlevel 1 (
      echo Failed to repair the virtual environment. Please install Python 3 first.
      exit /b 1
    )
    set CREATED_VENV=1
  ) else (
    echo Found a usable demo\.venv. Reusing it; skip environment creation and dependency download.
  )
) else (
  echo Creating local virtual environment in demo\.venv ...
  python -m venv "%ROOT%.venv"
  if errorlevel 1 (
    echo Failed to create the virtual environment. Please install Python 3 first.
    exit /b 1
  )
  set CREATED_VENV=1
)
if "%CREATED_VENV%"=="1" if exist "%ROOT%requirements.txt" (
  echo Installing demo dependencies from Tsinghua PyPI mirror ...
  "%PYTHON%" -m pip install -r "%ROOT%requirements.txt" -i "https://pypi.tuna.tsinghua.edu.cn/simple"
  if errorlevel 1 (
    echo Dependency installation failed. Please check the network or demo\requirements.txt.
    exit /b 1
  )
)
if not exist "%ROOT%requirements.txt" (
  echo No external dependency file found. The demo uses Python standard library only.
)
echo Starting Smart Course Platform demo at http://127.0.0.1:8000
set OPEN_BROWSER=1
"%PYTHON%" "%ROOT%server.py"

