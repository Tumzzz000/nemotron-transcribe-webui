#!/usr/bin/env bash
# One-time setup of vLLM + NeMo Speech for Nemotron 3.5 Transcribe (Linux / WSL2).
#
# Follows the requirements published for the model: vLLM 0.32.0, the NeMo Speech
# main branch (provides the `nemo_speechlm` vLLM plugin) and Python 3.13.
# If NVIDIA changes the install steps, compare with "Environment Setup" in:
#   https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B
set -euo pipefail

HOME_DIR="${NTW_HOME:-$HOME/.nemotron-transcribe}"
VENV="$HOME_DIR/venv"
SPEECH_DIR="$HOME_DIR/Speech"
VLLM_VERSION="${VLLM_VERSION:-0.32.0}"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31m[ERROR] %s\033[0m\n' "$*" >&2; exit 1; }

command -v nvidia-smi >/dev/null || die "nvidia-smi not found. Install the NVIDIA driver on Windows (WSL uses it automatically) or on Linux."
nvidia-smi --query-gpu=name,memory.total,compute_cap --format=csv,noheader

for tool in git curl; do
  if ! command -v "$tool" >/dev/null; then
    say "Installing $tool"
    sudo apt-get update -y && sudo apt-get install -y "$tool"
  fi
done

if ! command -v uv >/dev/null; then
  say "Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

mkdir -p "$HOME_DIR"
if [ ! -x "$VENV/bin/python" ]; then
  say "Creating Python 3.13 environment in $VENV"
  uv venv --python 3.13 "$VENV"
fi

say "Installing vLLM $VLLM_VERSION (large download)"
uv pip install --python "$VENV/bin/python" "vllm==$VLLM_VERSION" "huggingface_hub[cli]"

if [ -d "$SPEECH_DIR/.git" ]; then
  say "Updating NeMo Speech"
  git -C "$SPEECH_DIR" pull --ff-only
else
  say "Cloning NeMo Speech"
  git clone --depth 1 https://github.com/NVIDIA-NeMo/Speech.git "$SPEECH_DIR"
fi
say "Installing NeMo Speech (vLLM plugin)"
uv pip install --python "$VENV/bin/python" -e "$SPEECH_DIR"

"$VENV/bin/python" -c "import vllm; print('vLLM', vllm.__version__)"
touch "$HOME_DIR/.setup-done"
say "Setup finished"
