@echo off
setlocal
cd /d "%~dp0"
title Nemotron Transcribe

rem ---- Defaults. To change them, copy config.example.bat to config.local.bat ----
set "WEBUI_PORT=8090"
set "MODEL_PORT=8000"
set "MODEL_URL="
set "MODEL_NAME="
set "START_SERVER=1"
set "BACKEND=nemo"
set "NS_CHANNEL=stable"
set "MODEL_DIR=%~dp0models"
set "MODEL_VARIANT="
set "EXTRA_VLLM_ARGS="
if exist config.local.bat call config.local.bat
if not defined MODEL_URL set "MODEL_URL=http://localhost:%MODEL_PORT%"

echo.
echo  ===== Nemotron Transcribe =====
echo.

rem ---- 1. Python environment for the web UI ----
rem Prefer a 64-bit Python when one is installed.
set "PY="
py -3-64 -c "pass" >nul 2>nul && set "PY=py -3-64"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto no_python

rem Replace an old 32-bit environment once a 64-bit Python is available.
if not exist ".venv\Scripts\python.exe" goto venv_create
if not "%PY%"=="py -3-64" goto venv_ok
".venv\Scripts\python.exe" -c "import struct,sys;sys.exit(struct.calcsize('P')!=8)" >nul 2>nul
if not errorlevel 1 goto venv_ok
echo [1/4] Replacing the 32-bit Python environment with a 64-bit one...
rmdir /s /q .venv
:venv_create
echo [1/4] Creating Python environment...
%PY% -m venv .venv
if errorlevel 1 goto fail
:venv_ok
".venv\Scripts\python.exe" -c "import struct,sys;sys.exit(struct.calcsize('P')!=8)" >nul 2>nul
if errorlevel 1 echo [1/4] Note: 32-bit Python in use. It works, but 64-bit Python is recommended.

fc /b requirements.txt .venv\installed.txt >nul 2>nul
if not errorlevel 1 goto deps_ok
echo [1/4] Installing web UI packages...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --only-binary=:all: -r requirements.txt
if errorlevel 1 goto fail
copy /y requirements.txt .venv\installed.txt >nul
:deps_ok
echo [1/4] Web UI ready.

rem ---- Model server already running? ----
".venv\Scripts\python.exe" -c "import sys,urllib.request;urllib.request.urlopen(sys.argv[1].rstrip('/')+'/v1/models',timeout=3)" "%MODEL_URL%" >nul 2>nul
if not errorlevel 1 (
  echo [2/4] Model server already running at %MODEL_URL%
  goto webui
)
if not "%START_SERVER%"=="1" (
  echo [2/4] START_SERVER=0, not starting a model server. Using %MODEL_URL%
  goto webui
)
if /i "%BACKEND%"=="vllm" goto server_vllm

rem ---- 2. NeMo-Speech.cpp runtime: native Windows, no WSL needed ----
set "NS_EXE=%LOCALAPPDATA%\Programs\NeMoSpeech\bin\nemo-speech.exe"
if exist "%NS_EXE%" goto ns_ok
echo [2/4] Installing NVIDIA NeMo-Speech.cpp runtime...
set "NS_SCRIPT=%TEMP%\nemo-speech-install.ps1"
powershell -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/NVIDIA/NeMo-Speech.cpp/raw/main/scripts/install.ps1' -OutFile $env:NS_SCRIPT"
if errorlevel 1 goto fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%NS_SCRIPT%" -BinaryOnly -Channel %NS_CHANNEL%
if errorlevel 1 goto fail
if not exist "%NS_EXE%" goto fail
:ns_ok
echo [2/4] NeMo-Speech.cpp ready.

rem ---- 3. Model files (GGUF), picked for this GPU ----
echo [3/4] Checking model files. The first download is about 20-35 GB.
".venv\Scripts\python.exe" tools\get_model.py
if errorlevel 1 goto fail
call "%MODEL_DIR%\selected.bat"

echo [3/4] Starting the model server in a new window (loading takes a few minutes).
start "Nemotron model server" cmd /k ""%NS_EXE%" serve --asr-model "%DECODER%" --asr.salm.encoder_path "%ENCODER%" --config "%SALM_CONFIG%" --host 127.0.0.1 --port %MODEL_PORT% --read-timeout 600 --write-timeout 3600"
goto webui

rem ---- 2-3. Optional: vLLM inside WSL2 (BACKEND=vllm) ----
:server_vllm
where wsl >nul 2>nul
if errorlevel 1 goto no_wsl
rem A broken WSL install prints errors or prompts instead of running the command,
rem so require the exact expected output.
set "WSL_OK="
for /f "usebackq delims=" %%L in (`wsl -e echo wsl-ok ^<nul 2^>nul`) do if "%%L"=="wsl-ok" set "WSL_OK=1"
if not defined WSL_OK goto no_wsl
echo [2/4] Starting vLLM server in WSL in a new window.
echo       First run installs vLLM and downloads the model: this can take 30+ minutes.
set "VLLM_PORT=%MODEL_PORT%"
set "WSLENV=MODEL_VARIANT/u:EXTRA_VLLM_ARGS/u:HF_TOKEN/u:VLLM_PORT/u"
start "Nemotron vLLM server" cmd /k wsl --cd "%~dp0server" bash ./start-vllm.sh
goto webui

:no_wsl
echo [2/4] WARNING: BACKEND=vllm needs a working WSL2 with Ubuntu, which was not found.
echo       Remove BACKEND=vllm from config.local.bat to use the native Windows runtime,
echo       or see "WSL troubleshooting" in README.md.

rem ---- 4. Web UI ----
:webui
echo [4/4] Opening http://localhost:%WEBUI_PORT%/  - close this window to stop the web UI.
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
