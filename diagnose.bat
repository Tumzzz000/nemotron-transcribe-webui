@echo off
setlocal
cd /d "%~dp0"
title Nemotron Transcribe - diagnose
rem Tests every web UI function against the running model server.
rem Start start.bat first, then drag a short audio file onto this file.

set "MODEL_PORT=8000"
set "MODEL_URL="
if exist config.local.bat call config.local.bat
if not defined MODEL_URL set "MODEL_URL=http://localhost:%MODEL_PORT%"

set "AUDIO=%~1"
if not defined AUDIO set /p "AUDIO=Drag a short audio file into this window and press Enter: "
set "AUDIO=%AUDIO:"=%"
set "AUDIO_LANG="
set /p "AUDIO_LANG=Language of the audio (ja, en, zh, ko, th ... Enter = not set): "

".venv\Scripts\python.exe" tools\diagnose.py "%AUDIO%" %AUDIO_LANG%
if exist diagnose-report.txt start "" notepad diagnose-report.txt
pause
