@echo off
setlocal EnableExtensions
cd /d "%~dp0"

REM No window title is set on purpose: cmd cannot parse non-ASCII inside a .bat
REM (verified: yields replacement characters), while an English title would expose
REM the project's English name in the title bar. The launcher window keeps the
REM system default; the GUI sets its own Chinese title.

REM ============ 0. Local byproducts area ============
REM Convention: project body and local byproducts live in SIBLING folders
REM   <parent>\PomExpress        <- project body (this folder)
REM   <parent>\PomExpress_local  <- work byproducts (venv, logs, state, ...)
REM Keeps the project body free of generated files, so cleaning residue is
REM a matter of deleting one folder instead of hunting inside the project.
for %%I in ("%~dp0.") do set "PROJ_NAME=%%~nxI"
set "LOCAL_ROOT=%~dp0..\%PROJ_NAME%_local"
if not exist "%LOCAL_ROOT%" mkdir "%LOCAL_ROOT%"
set "VENV_DIR=%LOCAL_ROOT%\m7_venv"
echo [env] Byproducts: %LOCAL_ROOT%

REM ============ 1. Find Python 3.12+ (requires >=3.12 - PEP 701) ============
set "PY_CMD="
py -3.14 --version >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3.14"
if not defined PY_CMD (
    py -3.13 --version >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3.13"
)
if not defined PY_CMD (
    py -3.12 --version >nul 2>nul
    if not errorlevel 1 set "PY_CMD=py -3.12"
)
if not defined PY_CMD (
    python --version >nul 2>nul
    if not errorlevel 1 (
        python -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>nul
        if not errorlevel 1 set "PY_CMD=python"
    )
)
if not defined PY_CMD (
    echo [ERROR] Python 3.12+ not found.
    echo Please install Python 3.12+ with Add to PATH:
    echo   https://www.python.org/downloads/
    pause
    exit /b 1
)
echo [env] Using Python: %PY_CMD%

REM ============ 2. Unified env (in the byproducts area) ============
if exist "%VENV_DIR%\Scripts\pythonw.exe" (
    "%VENV_DIR%\Scripts\python.exe" -c "import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)" >nul 2>nul
    if not errorlevel 1 goto run
    echo [warn] venv is Python older than 3.12 - rebuilding...
    rmdir /s /q "%VENV_DIR%"
)
echo.
echo [first-run] Creating venv at %VENV_DIR% ...
%PY_CMD% -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [ERROR] venv creation failed - check Python installation.
    pause
    exit /b 1
)
echo [first-run] Installing dependencies (1-3 min, please wait)...
"%VENV_DIR%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV_DIR%\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Dependency install failed - check network and retry.
    pause
    exit /b 1
)
echo [done] Dependencies installed.

:run
REM ============ 3. Launch GUI elevated ============
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"
powershell -NoProfile -Command "Start-Process -FilePath '%VENV_PYW%' -ArgumentList '-m','app','--no-elevate' -WorkingDirectory '%~dp0' -Verb RunAs"
echo Launching (confirm UAC prompt)...
exit /b 0
