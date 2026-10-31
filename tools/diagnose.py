"""Check every web UI function against the running servers.

Sends the first part of an audio file through each Nemotron preset exactly the
way the web UI does, then reports per function: HTTP status, time, the start of
the answer, how much of it is Thai script, and whether the Thai looks garbled
(UTF-8 decoded with the wrong code page). Then checks the text LLM that writes
the Thai translations, summaries and answers (LLM_URL / LLM_MODEL / LLM_API_KEY).
The report is written to diagnose-report.txt.

Usage: python tools/diagnose.py <audio file> [language code, e.g. ja] [seconds]
"""

import base64
import io
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

import asyncio

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import (DEFAULT_LLM_KEY, DEFAULT_LLM_MODEL, DEFAULT_LLM_URL,  # noqa: E402
                 LANGUAGE_NAMES, convert_to_wav16k)
import llm as text_llm  # noqa: E402

SERVER = (os.environ.get("MODEL_URL") or "http://localhost:8000").rstrip("/")
REPORT = ROOT / "diagnose-report.txt"

SPEAKER_PROMPT = (
    "Transcribe this audio. Include all spoken words and verbal fillers. Write numbers as spoken. "
    "Use no punctuation and no capitalization. Start every speaker turn with a speaker tag such as "
    "<spk:0>, <spk:1>, <spk:2>, etc.; assign one stable tag per speaker and keep speaker identities "
    "consistent. Output only the speaker-tagged transcript."
)
READABLE_PROMPT = (
    "Create a readable transcript of this recording with punctuation. Omit verbal fillers and "
    "accidental repetitions, but retain the remaining wording and meaning. Return only the transcript."
)


def presets(lang_name: str) -> list[tuple[str, str, bool, bool]]:
    """(name, instruction, timestamps, expects Thai output) for the presets Nemotron answers itself
    (static/index.html). Translation, summary and Q&A go through the text LLM, checked separately."""
    in_lang = f" The speech is in {lang_name}." if lang_name else ""
    return [
        ("ถอดเสียง", "", False, False),
        ("ถอดเสียง + เวลา", "", True, False),
        ("แยกผู้พูด", SPEAKER_PROMPT + in_lang, False, False),
        ("ถอดแบบอ่านง่าย", READABLE_PROMPT + in_lang, False, False),
    ]


async def check_llm(transcript: str, lang_name: str, out) -> None:
    url = DEFAULT_LLM_URL
    out(f"\n=== Text LLM for Thai output ({url}) ===")
    async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as client:
        llm = text_llm.LLM(client, url, DEFAULT_LLM_MODEL, DEFAULT_LLM_KEY)
        try:
            models = await text_llm.list_models(client, url, llm.headers)
            out(f"models: {', '.join(models) or '(none)'}")
            await llm.resolve_model()
            t0 = time.time()
            thai = await llm.complete([
                {"role": "system", "content": text_llm.TRANSLATE_SYSTEM.format(src=lang_name or "the original language")},
                {"role": "user", "content": transcript[:800]},
            ])
        except (text_llm.LLMError, httpx.HTTPError) as exc:
            out(f"LLM not usable: {exc}")
            out("Start Ollama / LM Studio, or set LLM_URL (and LLM_MODEL, LLM_API_KEY) in config.local.bat.")
            return
        ratio = thai_ratio(thai)
        status = "OK" if ratio >= 0.5 and not GARBLED.search(thai) else (
            "GARBLED THAI" if GARBLED.search(thai) else "NOT THAI")
        out(f"[แปลเป็นไทย via {llm.model}] {time.time() - t0:.1f}s, Thai letters {ratio:.0%} -> {status}")
        out("  answer: " + thai[:400].replace("\n", " / "))


THAI = re.compile(r"[฀-๿]")
# UTF-8 Thai read as cp874 shows up as "เธ"/"เน" pairs; read as cp1252/latin-1 as "à¸"/"à¹".
GARBLED = re.compile(r"(เธ|เน)[฀-๿\x80-\xff]|à¸|à¹|�")


def thai_ratio(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    return sum(1 for c in letters if THAI.match(c)) / len(letters) if letters else 0.0


def cut(wav: bytes, seconds: float) -> bytes:
    with wave.open(io.BytesIO(wav)) as r:
        frames = r.readframes(int(seconds * r.getframerate()))
        params = r.getparams()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setparams(params)
        w.writeframes(frames)
    return buf.getvalue()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1].strip('"'))
    lang = sys.argv[2] if len(sys.argv) > 2 else ""
    seconds = float(sys.argv[3]) if len(sys.argv) > 3 else 45
    lang_name = LANGUAGE_NAMES.get(lang, lang)

    lines: list[str] = []

    def out(text: str = "") -> None:
        lines.append(text)
        try:
            print(text, flush=True)
        except UnicodeEncodeError:  # console without UTF-8: the report file still has everything
            print(text.encode("ascii", "replace").decode(), flush=True)

    out(f"Nemotron Transcribe diagnose - {time.strftime('%Y-%m-%d %H:%M')}")
    out(f"server {SERVER} | file {src.name} | language '{lang or '-'}' | first {seconds:.0f}s")
    out(f"python {sys.version.split()[0]} | stdout encoding {sys.stdout.encoding} | "
        f"preferred {__import__('locale').getpreferredencoding(False)}")

    client = httpx.Client(timeout=httpx.Timeout(900, connect=10))
    for path in ("/v1/models", "/ready"):
        try:
            r = client.get(SERVER + path)
            out(f"\nGET {path} -> {r.status_code}\n{r.text[:1500]}")
        except httpx.HTTPError as exc:
            out(f"\nGET {path} failed: {exc}")
            if path == "/v1/models":
                out("The model server is not reachable. Start it with start.bat first.")
                return 1
    models = client.get(SERVER + "/v1/models").json().get("data", [])
    model = models[0]["id"] if models else ""

    wav = cut(convert_to_wav16k(src), seconds)
    b64 = base64.b64encode(wav).decode()

    out("\n=== Nemotron functions (the same requests the web UI sends) ===")
    transcript = ""
    for name, instruction, timestamps, wants_thai in presets(lang_name):
        content = [{"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}}]
        if instruction:
            content.append({"type": "text", "text": instruction})
        payload = {"model": model, "messages": [{"role": "user", "content": content}], "temperature": 0}
        if lang:
            payload["language"] = lang
        if timestamps:
            payload["timestamp_granularities"] = ["word"]
        t0 = time.time()
        try:
            r = client.post(SERVER + "/v1/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            out(f"\n[{name}] request failed: {exc}")
            continue
        took = time.time() - t0
        if r.status_code != 200:
            out(f"\n[{name}] HTTP {r.status_code} ({took:.1f}s): {r.text[:600]}")
            continue
        body = r.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        ratio = thai_ratio(text)
        verdict = []
        if GARBLED.search(text):
            verdict.append("GARBLED THAI (encoding problem)")
        if wants_thai and ratio < 0.5:
            verdict.append("NOT THAI (model answered in another language)")
        if timestamps:
            cues = (body.get("transcription") or {}).get("subtitle_cues") or []
            verdict.append(f"{len(cues)} subtitle cues")
        out(f"\n[{name}] {took:.1f}s, Thai letters {ratio:.0%}"
            + (f" -> {'; '.join(verdict)}" if verdict else " -> OK"))
        out(f"  instruction: {instruction or '(server default prompt)'}")
        if not instruction and not timestamps:
            transcript = text
        out("  answer: " + text[:400].replace("\n", " / "))

    out("\n=== Nemotron's own speech translation targets (for reference) ===")
    short = cut(wav, min(seconds, 15))
    for target in ("th", "en"):
        try:
            data = {"target_language": target, "response_format": "json"}
            if lang:
                data["language"] = lang
            r = client.post(SERVER + "/v1/audio/translations", data=data,
                            files={"file": ("a.wav", short, "audio/wav")})
            text = r.json().get("text", "") if r.status_code == 200 else r.text
            out(f"target {target}: HTTP {r.status_code}, Thai letters {thai_ratio(text):.0%}: {text[:300]}")
        except Exception as exc:
            out(f"target {target}: failed: {exc}")

    if transcript:
        asyncio.run(check_llm(transcript, lang_name, out))
    else:
        out("\nNo transcript from Nemotron, so the text LLM was not checked.")

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out(f"\nReport saved to {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
