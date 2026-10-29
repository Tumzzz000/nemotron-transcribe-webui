@echo off
rem Copy this file to config.local.bat and edit it. config.local.bat is not
rem tracked by git, so `git pull` will never overwrite your settings.

rem Address of the vLLM server
set "VLLM_URL=http://localhost:8000"

rem Port of the web UI (Open WebUI uses 8080)
set "WEBUI_PORT=8090"

rem Model name; empty = first model the server has loaded
set "VLLM_MODEL="

rem 1 = start.bat also starts the vLLM server in WSL2, 0 = only the web UI
set "START_SERVER=1"

rem Checkpoint: empty = pick from the GPU, or NVFP4 / FP8 / BF16
set "MODEL_VARIANT="

rem Extra flags for `vllm serve`, e.g. from the model card's Quick Start
set "EXTRA_VLLM_ARGS="

rem Hugging Face token, only needed if the download asks for one
rem set "HF_TOKEN=hf_xxx"

rem Let other PCs on your LAN open the web UI
rem set "HOST=0.0.0.0"
