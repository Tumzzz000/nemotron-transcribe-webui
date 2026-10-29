"""Nemotron Transcribe Web UI.

A small local web app that forwards audio to an OpenAI-compatible vLLM server
running NVIDIA Nemotron 3.5 Transcribe, and streams the answer back.
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
DEFAULT_VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8000").rstrip("/")
DEFAULT_MODEL = os.environ.get("VLLM_MODEL", "")
OPEN_BROWSER = os.environ.get("OPEN_BROWSER", "1") == "1"

# 16 kHz mono 16-bit PCM = 32,000 bytes per second of audio.
WAV_BYTES_PER_SECOND = 16000 * 2

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


async def list_models(vllm_url: str) -> list[str]:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(f"{vllm_url}/v1/models")
        resp.raise_for_status()
        return [m["id"] for m in resp.json().get("data", [])]


@app.get("/")
async def index():
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.get("/api/config")
async def config():
    return {"vllm_url": DEFAULT_VLLM_URL, "model": DEFAULT_MODEL}


@app.get("/api/status")
async def status(vllm_url: str = DEFAULT_VLLM_URL):
    vllm_url = vllm_url.rstrip("/")
    try:
        models = await list_models(vllm_url)
        return {"ok": True, "models": models}
    except Exception as exc:  # report any connection problem to the UI
        return JSONResponse({"ok": False, "error": str(exc) or type(exc).__name__})


@app.post("/api/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    prompt: str = Form("Transcribe the audio."),
    system: str = Form(""),
    vllm_url: str = Form(DEFAULT_VLLM_URL),
    model: str = Form(DEFAULT_MODEL),
    temperature: float = Form(0.0),
    max_tokens: int = Form(16384),
):
    vllm_url = vllm_url.rstrip("/")
    suffix = Path(file.filename or "audio").suffix or ".bin"

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"input{suffix}"
        with src.open("wb") as out:
            while chunk := await file.read(1024 * 1024):
                out.write(chunk)
        wav = await run_in_threadpool(convert_to_wav16k, src)

    duration = max(0.0, (len(wav) - 44) / WAV_BYTES_PER_SECOND)

    if not model:
        try:
            models = await list_models(vllm_url)
        except Exception as exc:
            raise HTTPException(502, f"เชื่อมต่อ vLLM ที่ {vllm_url} ไม่ได้: {exc}")
        if not models:
            raise HTTPException(502, "vLLM ไม่มีโมเดลที่โหลดอยู่")
        model = models[0]

    audio_url = "data:audio/wav;base64," + base64.b64encode(wav).decode()
    messages = []
    if system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({
        "role": "user",
        "content": [
            {"type": "audio_url", "audio_url": {"url": audio_url}},
            {"type": "text", "text": prompt},
        ],
    })
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    async def stream():
        yield sse({"meta": {"model": model, "duration": duration}})
        try:
            timeout = httpx.Timeout(None, connect=10)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream(
                    "POST", f"{vllm_url}/v1/chat/completions", json=payload
                ) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode(errors="ignore")
                        yield sse({"error": f"vLLM ตอบกลับ HTTP {resp.status_code}: {body[:2000]}"})
                        return
                    async for line in resp.aiter_lines():
                        if line:
                            yield line + "\n\n"
        except httpx.ConnectError:
            yield sse({"error": f"เชื่อมต่อ vLLM ที่ {vllm_url} ไม่ได้ — server รันอยู่หรือเปล่า?"})
        except Exception as exc:
            yield sse({"error": f"เกิดข้อผิดพลาด: {exc}"})

    return StreamingResponse(stream(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    url = f"http://localhost:{PORT}/"
    print(f"\n  Nemotron Transcribe Web UI  ->  {url}")
    print(f"  vLLM server                 ->  {DEFAULT_VLLM_URL}\n")
    if OPEN_BROWSER:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
