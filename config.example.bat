@echo off
rem Copy this file to config.local.bat and edit it. config.local.bat is not
rem tracked by git, so `git pull` will never overwrite your settings.

rem Port of the web UI (Open WebUI uses 8080)
set "WEBUI_PORT=8090"

rem Port of the model server started by start.bat
set "MODEL_PORT=8000"

rem Use a model server that runs elsewhere, e.g. another PC on your LAN
rem set "MODEL_URL=http://192.168.1.50:8000"

rem 1 = start.bat also starts the model server, 0 = only the web UI
set "START_SERVER=1"

rem nemo = NVIDIA NeMo-Speech.cpp, native Windows (default)
rem vllm = vLLM inside WSL2 (needs Ubuntu in WSL)
set "BACKEND=nemo"

rem NeMo-Speech.cpp release channel. Keep nightly: the stable 0.2.0 release
rem cannot run Nemotron 3.5 Transcribe yet.
set "NS_CHANNEL=nightly"

rem Where model files are stored (20-35 GB)
rem set "MODEL_DIR=D:\models\nemotron"

rem Pick the GGUF yourself instead of by GPU (file name inside GGUF_REPO)
rem set "GGUF_REPO=nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF"
rem set "MODEL_FILE=NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-AD-IQ4_NL.gguf"

rem Hugging Face token, only needed if the download asks for one
rem set "HF_TOKEN=hf_xxx"

rem Let other PCs on your LAN open the web UI
rem set "HOST=0.0.0.0"

rem BACKEND=vllm only: NVFP4 / FP8 / BF16 and extra `vllm serve` flags
set "MODEL_VARIANT="
set "EXTRA_VLLM_ARGS="

rem Force where the model runs: cpu, cuda:0 ... (default: picked by start.bat)
rem set "DEVICE=cpu"
