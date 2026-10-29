"""Pick and download the Nemotron 3.5 Transcribe GGUF files for NeMo-Speech.cpp.

The model is two GGUF files: a language-model decoder (several precisions) and
one shared encoder file (`*-Encoder-F16.gguf`). This script looks at the GPU,
picks the best decoder that fits, downloads both (resumable), writes a server
config for the GPU size and records the choice in `<model dir>/selected.bat`.

Environment overrides:
  GGUF_REPO     Hugging Face repo to download from
  MODEL_QUANT   decoder type to use instead of the GPU-based pick, e.g. NVFP4,
                IQ4_NL, Q5, Q8_0 (matched against the file names)
  MODEL_FILE    exact decoder file name inside that repo
  MODEL_DIR     where to store models (default: <project>/models)
  HF_TOKEN      Hugging Face token, if the repo asks for one
  HF_ENDPOINT   Hugging Face server (default https://huggingface.co)

Downloads use plain HTTPS (httpx) instead of huggingface_hub, so nothing has
to be compiled on install, even on 32-bit or brand-new Python versions.
"""

import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

import httpx

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = Path(os.environ.get("MODEL_DIR") or ROOT / "models")
SELECTED = MODEL_DIR / "selected.bat"

# Official NVIDIA GGUF repo first, then a community conversion of the same model.
REPOS = [r for r in [os.environ.get("GGUF_REPO")] if r] or [
    "nvidia/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF",
    "AtomicChat/NVIDIA-Nemotron-3.5-Transcribe-30B-A4B-GGUF",
]

# Server settings per GPU size, from NeMo-Speech.cpp docs/salm/configuration.md.
PROFILES = {
    "small": {  # 24 GB class: up to ~29 minutes per request
        "max_sequences": 4, "n_ctx_per_sequence": 8192, "max_context": 32768,
        "mtp_draft": 0, "n_batch": 1024, "encoder_max_batch": 4,
    },
    "default": {},  # 32 GB class: the runtime defaults hold one hour of audio
    "large": {  # 80 GB or more: many requests at once
        "max_sequences": 240, "n_ctx_per_sequence": 2048, "n_batch": 16384, "encoder_max_batch": 128,
    },
    "cpu": {  # no usable NVIDIA GPU: few slots, one hour per request
        "max_sequences": 4, "n_ctx_per_sequence": 18432, "mtp_draft": 0,
    },
    "spill": {  # GPU smaller than the model, driver spills into system RAM: keep it lean
        "max_sequences": 4, "n_ctx_per_sequence": 18432, "mtp_draft": 0,
        "n_batch": 1024, "encoder_max_batch": 4,
    },
}

ENCODER_GB = 1.7
# Bump when choose_decoder changes, so existing installs pick again on the next start.
PICK_RULES = "2"
HF = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
HEADERS = {"Authorization": f"Bearer {os.environ['HF_TOKEN']}"} if os.environ.get("HF_TOKEN") else {}


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    # A 32-bit Python on 64-bit Windows does not see System32 (WOW64 redirection);
    # Sysnative is the alias that reaches the real one.
    root = os.environ.get("SystemRoot", r"C:\Windows")
    for path in (rf"{root}\Sysnative\nvidia-smi.exe", rf"{root}\System32\nvidia-smi.exe",
                 r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.exists(path):
            return path
    return None


def detect_gpu() -> dict | None:
    smi = find_nvidia_smi()
    if not smi:
        return None
    try:
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip().splitlines()
    except (OSError, subprocess.SubprocessError):
        return None
    gpus = []
    for line in out:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            gpus.append({"name": parts[0], "vram_gb": float(parts[1]) / 1024, "cc": float(parts[2])})
        except ValueError:
            continue
    return max(gpus, key=lambda g: g["vram_gb"]) if gpus else None


def list_gguf(repo: str) -> dict[str, int]:
    """Return {path: size in bytes} of the repo's GGUF files."""
    url = f"{HF}/api/models/{repo}/tree/main"
    params = {"recursive": "true", "expand": "false"}
    files = {}
    with httpx.Client(headers=HEADERS, timeout=30, follow_redirects=True) as client:
        while url:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            for item in resp.json():
                size = (item.get("lfs") or {}).get("size") or item.get("size")
                if item.get("type") == "file" and item["path"].lower().endswith(".gguf") and size:
                    files[item["path"]] = int(size)
            url, params = resp.links.get("next", {}).get("url"), None
    return files


def download(repo: str, path: str, size: int, dest: Path) -> Path:
    """Download one file with resume support; skip it when already complete."""
    if dest.exists() and dest.stat().st_size == size:
        log(f"{dest.name}: already downloaded.")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    url = f"{HF}/{repo}/resolve/main/{quote(path)}"
    for attempt in range(1, 6):
        have = part.stat().st_size if part.exists() else 0
        headers = {**HEADERS, **({"Range": f"bytes={have}-"} if have else {})}
        try:
            with httpx.stream("GET", url, headers=headers, follow_redirects=True,
                              timeout=httpx.Timeout(60, connect=30)) as resp:
                if resp.status_code == 416:  # nothing left to fetch
                    break
                resp.raise_for_status()
                if have and resp.status_code != 206:  # server ignored Range: start over
                    have = 0
                with part.open("ab" if have else "wb") as out:
                    t0, done, last = time.time(), have, 0.0
                    for chunk in resp.iter_bytes(4 * 1024 * 1024):
                        out.write(chunk)
                        done += len(chunk)
                        now = time.time()
                        if now - last > 1:
                            speed = (done - have) / max(now - t0, 1e-6) / 1024**2
                            print(f"\r  {dest.name}: {done / 1024**3:.2f} / {size / 1024**3:.2f} GB "
                                  f"({done * 100 / size:.0f}%) {speed:.1f} MB/s   ", end="", flush=True)
                            last = now
            print()
            break
        except (httpx.HTTPError, OSError) as exc:
            print()
            log(f"Download interrupted ({type(exc).__name__}: {str(exc)[:120]}); retry {attempt}/5...")
            time.sleep(min(30, 3 * attempt))
    if not part.exists() or part.stat().st_size != size:
        raise RuntimeError(f"{dest.name} is incomplete. Run start.bat again to resume the download.")
    part.replace(dest)
    return dest


def is_decoder(path: str) -> bool:
    name = Path(path).name.lower()
    return not any(tag in name for tag in ("encoder", "dflash", "draft", "mmproj"))


def choose_decoder(decoders: dict[str, float], gpu: dict | None) -> tuple[str, str]:
    """Return (decoder path, profile name).

    Blackwell GPUs default to an NVFP4 decoder, which they run natively. When it
    does not fit in GPU memory it still runs on the GPU ("spill" profile) and the
    NVIDIA driver keeps the rest in system RAM. Other GPUs that are too small run
    on the CPU.
    """
    blackwell = gpu is not None and gpu["cc"] >= 10
    nvfp4_all = {p: s for p, s in decoders.items() if "nvfp4" in p.lower()}
    # Spilling only pays off when most of the model still sits on the GPU (12 GB or more).
    spill_ok = blackwell and nvfp4_all and gpu["vram_gb"] >= 12
    if gpu is None or (gpu["vram_gb"] < 20 and not spill_ok):
        small = [p for p in decoders if "iq4_nl" in p.lower()] or list(decoders)
        return min(small, key=decoders.get), "cpu"

    vram = gpu["vram_gb"]
    if blackwell and nvfp4_all:
        smallest = min(nvfp4_all, key=nvfp4_all.get)
        if vram < 20 or nvfp4_all[smallest] + ENCODER_GB + 2.5 > vram:
            return smallest, "spill"
    profile = "large" if vram >= 78 else "default" if vram >= 30 else "small"
    margin = {"small": 2.5, "default": 4.5, "large": 12}[profile]
    fits = {p: s for p, s in decoders.items() if s + ENCODER_GB + margin <= vram}
    # NVFP4 kernels need Blackwell; elsewhere the GGUF k-quants/IQ4_NL are the way.
    if not blackwell:
        fits = {p: s for p, s in fits.items() if "nvfp4" not in p.lower()}
    nvfp4 = [p for p in fits if "nvfp4" in p.lower()]
    if blackwell and nvfp4:
        return max(nvfp4, key=fits.get), profile
    if fits:
        return max(fits, key=fits.get), profile  # largest that fits = best quality
    log("WARNING: no decoder clearly fits this GPU's memory; trying the smallest one.")
    return min(decoders, key=decoders.get), "small"


def write_config(profile: str) -> Path:
    path = MODEL_DIR / f"salm-{profile}.yaml"
    lines = ["# Generated by tools/get_model.py for this GPU.", "asr:", "  salm:"]
    keys = PROFILES[profile]
    if not keys:
        lines.append("    max_sequences: 16")
    lines += [f"    {k}: {v}" for k, v in keys.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def total_ram_gb() -> float | None:
    try:
        if os.name == "nt":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus(dwLength=ctypes.sizeof(MemoryStatus))
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return status.ullTotalPhys / 1024**3
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024**3
    except Exception:
        return None


def write_selected(decoder: Path, encoder: Path, config: Path, device: str) -> None:
    SELECTED.write_text(
        "@echo off\r\n"
        "rem Written by tools/get_model.py. Delete this file to pick the model again.\r\n"
        f'set "DECODER={decoder}"\r\n'
        f'set "ENCODER={encoder}"\r\n'
        f'set "SALM_CONFIG={config}"\r\n'
        f'set "DEVICE={device}"\r\n'
        f'set "PICK_RULES={PICK_RULES}"\r\n',
        encoding="utf-8",
    )


def wanted_decoder(path: str) -> bool:
    """True when the decoder matches MODEL_FILE / MODEL_QUANT (or neither is set)."""
    name = Path(path).name
    if os.environ.get("MODEL_FILE"):
        return name == os.environ["MODEL_FILE"] or path == os.environ["MODEL_FILE"]
    if os.environ.get("MODEL_QUANT"):
        return os.environ["MODEL_QUANT"].lower() in name.lower()
    return True


def read_selected() -> dict | None:
    if not SELECTED.exists():
        return None
    values = dict(re.findall(r'set "(\w+)=(.*)"', SELECTED.read_text(encoding="utf-8")))
    if not all(k in values and Path(values[k]).exists() for k in ("DECODER", "ENCODER", "SALM_CONFIG")):
        return None
    if not wanted_decoder(values["DECODER"]):
        log("The requested model differs from the one in use; picking it again.")
        return None
    if values.get("PICK_RULES") != PICK_RULES and not (os.environ.get("MODEL_FILE") or os.environ.get("MODEL_QUANT")):
        log("Model selection rules were updated; checking the best model for this GPU again.")
        return None
    return values


def main() -> int:
    if read_selected():
        log(f"Model ready ({SELECTED}).")
        return 0

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    gpu = detect_gpu()
    if gpu:
        log(f"GPU: {gpu['name']}, {gpu['vram_gb']:.0f} GB, compute capability {gpu['cc']}")
    else:
        log("No NVIDIA GPU found: the model will run on the CPU and be slow.")
    for repo in REPOS:
        try:
            sizes = list_gguf(repo)
            files = {p: b / 1024**3 for p, b in sizes.items()}
        except Exception as exc:  # missing, gated, or offline: try the next source
            log(f"{repo}: not available ({type(exc).__name__}: {str(exc)[:160]})")
            continue
        encoders = [p for p in files if "encoder" in Path(p).name.lower()]
        decoders = {p: s for p, s in files.items() if is_decoder(p) and wanted_decoder(p)}
        if encoders and decoders:
            break
        if encoders and any(is_decoder(p) for p in files):
            have = ", ".join(sorted(Path(p).name for p in files if is_decoder(p)))
            log(f"{repo}: no decoder matches MODEL_FILE/MODEL_QUANT. It has: {have}")
            continue
        log(f"{repo}: no decoder/encoder GGUF pair found, trying the next source.")
    else:
        wanted = os.environ.get("MODEL_FILE") or os.environ.get("MODEL_QUANT")
        if wanted:
            log(f"ERROR: no repo has a '{wanted}' decoder. Remove MODEL_QUANT/MODEL_FILE from")
            log("config.local.bat to go back, or set GGUF_REPO to a repo that has that file.")
        else:
            log("ERROR: could not find the model GGUF files. Set GGUF_REPO in config.local.bat.")
        return 1

    if os.environ.get("MODEL_FILE") or os.environ.get("MODEL_QUANT"):
        decoder = min(decoders, key=decoders.get)  # the smallest file of the requested type
        profile = choose_decoder({decoder: decoders[decoder]}, gpu)[1]
        if "nvfp4" in decoder.lower() and (gpu is None or gpu["cc"] < 10):
            log("WARNING: NVFP4 runs natively only on Blackwell GPUs (RTX 50xx, RTX PRO 6000, B200).")
    else:
        decoder, profile = choose_decoder(decoders, gpu)
    encoder = min(encoders, key=len)

    device = os.environ.get("DEVICE") or {"cpu": "cpu", "spill": "cuda:0"}.get(profile, "")
    if profile == "spill":
        log(f"The {decoders[decoder]:.1f} GB decoder does not fit in {gpu['vram_gb']:.0f} GB of GPU memory: it runs on the")
        log("GPU and the NVIDIA driver keeps the rest in system RAM. Keep 'CUDA Memory Fallback'")
        log("(NVIDIA app > Graphics > Global Settings) at Driver Default or Prefer fallback.")
    elif device == "cpu":
        log("The model is larger than the GPU memory, so it runs on the CPU and system RAM (much slower).")
    if device == "cpu":
        ram = total_ram_gb()
        need = decoders[decoder] + ENCODER_GB + 4
        if ram is not None:
            log(f"System RAM: {ram:.0f} GB (CPU mode needs about {need:.0f} GB).")
            if ram < need:
                log("WARNING: not enough RAM for CPU mode; the server will probably fail to load the model.")
                answer = input("  Download the model anyway? [y/N] ").strip().lower()
                if answer not in ("y", "yes"):
                    log("Stopped. See README.md (GPU smaller than 24 GB) for other options.")
                    return 1
    target = MODEL_DIR / repo.split("/")[-1]
    log(f"Source:  https://huggingface.co/{repo}")
    log(f"Decoder: {decoder} ({decoders[decoder]:.1f} GB)")
    log(f"Encoder: {encoder} ({files[encoder]:.1f} GB)")
    log(f"Saving to {target} - downloads resume if interrupted.")
    paths = {}
    for name in (encoder, decoder):
        paths[name] = download(repo, name, sizes[name], target / name)

    config = write_config(profile)
    write_selected(paths[decoder], paths[encoder], config, device)
    log(f"Server settings: {profile} profile ({config.name}).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except RuntimeError as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
