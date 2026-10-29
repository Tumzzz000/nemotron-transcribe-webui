#!/usr/bin/env bash
# Starts the vLLM server for Nemotron 3.5 Transcribe (Linux / WSL2).
# First run installs everything (server/setup.sh) and downloads the model.
#
# The model is picked from the GPU unless MODEL_VARIANT is set:
#   Blackwell (compute capability 10.x / 12.x) -> NVFP4
#   Hopper / Ada (9.x / 8.9)                   -> FP8
#   anything else                              -> BF16
#
# NOTE: the `vllm serve` flags follow what NVIDIA published at launch. The
# model card's Quick Start also enables MTP speculative decoding and a CTC
# timestamp middleware; add those flags via EXTRA_VLLM_ARGS if you need them.
set -euo pipefail
cd "$(dirname "$0")"

HOME_DIR="${NTW_HOME:-$HOME/.nemotron-transcribe}"
VENV="$HOME_DIR/venv"
PORT="${VLLM_PORT:-8000}"
REPO_BASE="nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B"

if [ ! -f "$HOME_DIR/.setup-done" ]; then
  bash ./setup.sh
fi
export PATH="$VENV/bin:$HOME/.local/bin:$PATH"

if [ -z "${MODEL_VARIANT:-}" ]; then
  cap="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader | head -1 | tr -d ' ')"
  major="${cap%%.*}"
  if [ "$major" -ge 10 ]; then MODEL_VARIANT=NVFP4
  elif [ "$cap" = "8.9" ] || [ "$major" -eq 9 ]; then MODEL_VARIANT=FP8
  else MODEL_VARIANT=BF16
  fi
  echo "GPU compute capability $cap -> using $MODEL_VARIANT checkpoint"
fi
case "$MODEL_VARIANT" in
  BF16) REPO="$REPO_BASE" ;;
  FP8|NVFP4) REPO="$REPO_BASE-$MODEL_VARIANT" ;;
  *) echo "Unknown MODEL_VARIANT: $MODEL_VARIANT (use NVFP4, FP8 or BF16)"; exit 1 ;;
esac

MODEL_DIR="${MODEL_DIR:-$HOME_DIR/models/${REPO#nvidia/}}"
if [ ! -f "$MODEL_DIR/.download-done" ]; then
  echo "Downloading $REPO to $MODEL_DIR (resumable; ~22 GB for NVFP4, more for FP8/BF16)"
  hf download "$REPO" --local-dir "$MODEL_DIR"
  touch "$MODEL_DIR/.download-done"
fi

export VLLM_PLUGINS=nemo_speechlm
export VLLM_MAX_AUDIO_DECODE_DURATION_S=3600
export VLLM_MAX_AUDIO_CLIP_FILESIZE_MB=180
export VLLM_USE_V2_MODEL_RUNNER=0
if [ "$MODEL_VARIANT" = NVFP4 ]; then
  # Set by the NVFP4 model card for RTX PRO 6000 Blackwell.
  export VLLM_DISABLED_KERNELS=FlashInferFP8ScaledMMLinearKernel
fi

echo "Starting vLLM on port $PORT (loading the model takes a few minutes)..."
# shellcheck disable=SC2086
exec vllm serve "$MODEL_DIR" \
  --served-model-name nemotron-transcribe \
  --host 0.0.0.0 --port "$PORT" \
  --trust-remote-code \
  --max-model-len "${MAX_MODEL_LEN:-65536}" \
  --reasoning-parser nemotron_v3 \
  ${EXTRA_VLLM_ARGS:-}
