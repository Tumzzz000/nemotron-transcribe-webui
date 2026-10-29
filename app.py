"""Nemotron Transcribe Web UI.

A small local web app that sends audio to a model server running NVIDIA
Nemotron 3.5 Transcribe and streams the answer back. Two servers are supported:

- NeMo-Speech.cpp (`nemo-speech serve`): native Windows/Linux/macOS, GGUF model.
- vLLM with the NeMo Speech plugin: Linux/WSL2, Hugging Face checkpoint.
"""

import base64
import json
import os
import subprocess
import tempfile
import threading
import webbrowser
from pathlib import Path

import httpx
import imageio_ffmpeg
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

BASE_DIR = Path(__file__).parent
HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("WEBUI_PORT", "8090"))
DEFAULT_SERVER_URL = (
    os.environ.get("MODEL_URL") or os.environ.get("VLLM_URL") or "http://localhost:8000"
).rstrip("/")
DEFAULT_MODEL = os.environ.get("MODEL_NAME") or os.environ.get("VLLM_MODEL") or ""
OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "1") == "1"

# 16 kHz mono 16-bit PCM = 32,000 bytes per second of audio.
WAV_BYTES_PER_SECOND = 16000 * 2

# NVIDIA's default transcription prompt (NeMo-Speech.cpp docs/salm/prompts.md).
DEFAULT_PROMPT = (
    "Produce a verbatim transcript of the audio. Preserve named entities, abbreviations, "
    "numbers, dates, measurements, acronyms, and technical terms as clearly as possible. "
    "Keep the same language as spoken and do not translate."
)
LANGUAGE_NAMES = {
    "th": "Thai", "en": "English", "ja": "Japanese", "ko": "Korean", "zh": "Chinese",
    "vi": "Vietnamese", "id": "Indonesian", "ms": "Malay", "hi": "Hindi", "es": "Spanish",
    "fr": "French", "de": "German", "it": "Italian", "pt": "Portuguese", "ru": "Russian",
    "ar": "Arabic", "tr": "Turkish", "nl": "Dutch",
}

app = FastAPI(title="Nemotron Transcribe Web UI")


def sse(obj: dict) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def convert_to_wav16k(src: Path) -> bytes:
    """Convert any audio/video file to 16 kHz mono WAV with the bundled ffmpeg."""
    dst = src.with_suffix(".16k.wav")
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        "-map_metadata", "-1", "-bitexact", str(dst),
    ]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        detail = result.stderr.decode(errors="ignore").strip()[-600:]
        raise HTTPException(400, f"แปลงไฟล์เสียงไม่สำเร็จ: {detail}")
    try:
        return dst.read_bytes()
    finally:
        dst.unlink(missing_ok=True)


async def server_info(server_url: str) -> dict:
    """Return the model ids and the server kind ("vllm" or "nemo-speech")."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{server_url}/v1/models")
        resp.raise_for_status()
        data = resp.json().get("data", [])
    kind = "vllm" if any(m.get("owned_by") == "vllm" for m in data) else "nemo-speech"
    return {"kind": kind, "models": [m["id"] for m in data if "id" in m]}


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
async def config():
    return {"server_url": DEFAULT_SERVER_URL, "model": DEFAULT_MODEL}


@app.get("/api/status")
async def status(server_url: str = DEFAULT_SERVER_URL):
    try:
        return {"ok": True, **await server_info(server_url.rstrip("/"))}
    except Exception as exc:  # report any connection problem to the UI
        return JSONResponse({"ok": False, "error": str(exc) or type(exc).__name__})


def build_payload(kind: str, model: str, wav: bytes, prompt: str, language: str,
                  timestamps: bool, system: str, temperature: float, max_tokens: int) -> dict:
    b64 = base64.b64encode(wav).decode()
    if kind == "nemo-speech":
        # NeMo-Speech.cpp: exactly one user message with one input_audio part and an
        # optional instruction; it refuses max_tokens, system messages and unknown fields.
        content = [{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]
        if prompt:
            content.append({"type": "text", "text": prompt})
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "temperature": temperature,
            "stream": True,
        }
        if language:
            payload["language"] = language
        if timestamps:
            payload["timestamp_granularities"] = ["word"]
        return payload

    text = prompt or DEFAULT_PROMPT
    if language and not prompt:
        text += f" The speech is in {LANGUAGE_NAMES.get(language, language)}."
    messages = [{"role": "system", "content": system}] if system.strip() else []
    messages.append({
        "role": "user",
        "content": [
            {"type": "audio_url", "audio_url": {"url": "data:audio/wav;base64," + b64}},
            {"type": "text", "text": text},
        ],
    })
    return {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }


@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    prompt: str = Form(""),
    language: str = Form(""),
    timestamps: bool = Form(False),
    system: str = Form(""),
    server_url: str = Form(DEFAULT_SERVER_URL),
    model: str = Form(DEFAULT_MODEL),
    temperature: float = Form(0.0),
    max_tokens: int = Form(16384),
):
    server_url = server_url.rstrip("/")
    suffix = Path(file.filename or "audio").suffix or ".bin"

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        with src.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        wav = await run_in_threadpool(convert_to_wav16k, src)

    duration = max(0.0, (len(wav) - 44) / WAV_BYTES_PER_SECOND)

    try:
        info = await server_info(server_url)
    except Exception as exc:
        raise HTTPException(502, f"เชื่อมต่อ model server ที่ {server_url} ไม่ได้: {exc}")
    if not model:
        if not info["models"]:
            raise HTTPException(502, "model server ยังไม่มีโมเดลที่โหลดอยู่")
        model = info["models"][0]

    payload = build_payload(info["kind"], model, wav, prompt.strip(), language.strip(),
                            timestamps, system, temperature, max_tokens)

    async def stream():
        yield sse({"meta": {"model": model, "kind": info["kind"], "duration": duration}})
        try:
            timeout = httpx.Timeout(None, connect=10)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", f"{server_url}/v1/chat/completions", json=payload
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="ignore")
                        yield sse({"error": f"model server ตอบกลับ HTTP {resp.status_code}: {body[:2000]}"})
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n\n"
        except httpx.ConnectError:
            yield sse({"error": f"เชื่อมต่อ model server ที่ {server_url} ไม่ได้ — server รันอยู่หรือเปล่า?"})
        except Exception as exc:
            yield sse({"error": f"เกิดข้อผิดพลาด: {exc}"})

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    url = f"http://localhost:{PORT}/"
    print(f"\n  Nemotron Transcribe Web UI  ->  {url}")
    print(f"  Model server                ->  {DEFAULT_SERVER_URL}\n")
    if OPEN_BROWSER:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
