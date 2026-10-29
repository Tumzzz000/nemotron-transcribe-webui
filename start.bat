@echo off
setlocal
cd /d "%~dp0"
title Nemotron Transcribe

rem ---- Defaults. To change them, copy config.example.bat to config.local.bat ----
set "VLLM_URL=http://localhost:8000"
set "WEBUI_PORT=8090"
set "VLLM_MODEL="
set "START_SERVER=1"
set "MODEL_VARIANT="
set "EXTRA_VLLM_ARGS="
if exist config.local.bat call config.local.bat

echo.
echo  ===== Nemotron Transcribe =====
echo.

rem ---- 1. Python environment for the web UI ----
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto no_python

if exist ".venv\Scripts\python.exe" goto venv_ok
echo [1/3] Creating Python environment...
%PY% -m venv .venv
if errorlevel 1 goto fail
:venv_ok

fc /b requirements.txt .venv\installed.txt >nul 2>nul
if not errorlevel 1 goto deps_ok
echo [1/3] Installing web UI packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto fail
copy /y requirements.txt .venv\installed.txt >nul
:deps_ok
echo [1/3] Web UI ready.

rem ---- 2. Model server (vLLM in WSL2) ----
".venv\Scripts\python.exe" -c "import sys,urllib.request;urllib.request.urlopen(sys.argv[1].rstrip('/')+'/v1/models',timeout=3)" "%VLLM_URL%" >nul 2>nul
if not errorlevel 1 (
  echo [2/3] vLLM server already running at %VLLM_URL%
  goto webui
)
if not "%START_SERVER%"=="1" (
  echo [2/3] START_SERVER=0, not starting a model server. Using %VLLM_URL%
  goto webui
)
where wsl >nul 2>nul
if errorlevel 1 goto no_wsl
wsl -e true >nul 2>nul
if errorlevel 1 goto no_wsl

echo [2/3] Starting vLLM server in a new window.
echo       First run installs vLLM and downloads the model: this can take 30+ minutes.
set "WSLENV=MODEL_VARIANT/u:EXTRA_VLLM_ARGS/u:HF_TOKEN/u"
start "Nemotron vLLM server" cmd /k wsl --cd "%~dp0server" bash ./start-vllm.sh
goto webui

:no_wsl
echo [2/3] WARNING: WSL2 with a Linux distro is required to run the model on Windows.
echo       Install it once from an administrator terminal:  wsl --install -d Ubuntu
echo       then restart the PC and run start.bat again.
echo       The web UI still opens and can use a vLLM server elsewhere - set it in Settings.

rem ---- 3. Web UI ----
:webui
echo [3/3] Opening http://localhost:%WEBUI_PORT%/  - close this window to stop the web UI.
echo.
".venv\Scripts\python.exe" app.py
echo.
echo Web UI stopped.
pause
exit /b 0

:no_python
echo [ERROR] Python not found. Install Python 3.10+ from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH" during install, then run start.bat again.
pause
exit /b 1

:fail
echo.
echo [ERROR] Setup failed - see the messages above.
pause
exit /b 1
