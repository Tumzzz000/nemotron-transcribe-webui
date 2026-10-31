"""Thai output through a separate text LLM.

Nemotron 3.5 Transcribe transcribes very well but does not translate into Thai,
summarize or answer questions (it falls back to English). These helpers send its
transcript to any OpenAI-compatible chat API instead: Ollama, LM Studio,
llama.cpp server, or an online service such as Typhoon or OpenAI.
"""

import json
import re
from collections.abc import AsyncIterator

import httpx

# Characters of transcript per request: small enough for the 4-8k token context
# that local servers such as Ollama use by default.
CHUNK_CHARS = 6000
# Subtitle cues translated per request; neighbours give the model context.
CUE_BATCH = 25

TRANSLATE_SYSTEM = (
    "You are a professional translator. Translate the user's transcript from {src} into natural, fluent Thai. "
    "Keep the meaning, names, numbers and technical terms accurate and keep paragraph breaks. "
    "Output only the Thai translation, with no notes or explanations."
)
SUBTITLE_SYSTEM = (
    "You are a professional subtitle translator. The user sends numbered subtitle lines from {src} that "
    "belong to one continuous video. Translate every line into natural, concise spoken Thai, using the "
    "surrounding lines for context. Reply with exactly one line per number, in the form '<number>. <Thai>', "
    "and nothing else."
)
SUMMARY_SYSTEM = (
    "You summarize transcripts of recordings. Always write in Thai. Be faithful to the transcript and "
    "do not invent facts."
)
QA_SYSTEM = (
    "You answer questions about the transcript of a recording. Always answer in Thai. Use only the "
    "transcript; if it does not contain the answer, say so."
)


class LLMError(Exception):
    pass


class LLM:
    def __init__(self, client: httpx.AsyncClient, url: str, model: str, api_key: str):
        self.client = client
        self.url = url.rstrip("/")
        self.model = model
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def resolve_model(self) -> str:
        if not self.model:
            try:
                models = await list_models(self.client, self.url, self.headers)
            except httpx.HTTPError as exc:
                raise LLMError(f"เชื่อมต่อ LLM ที่ {self.url} ไม่ได้ ({type(exc).__name__}) — "
                               "เปิด Ollama/LM Studio หรือแก้ที่อยู่ LLM ในตั้งค่า")
            if not models:
                raise LLMError(f"LLM ที่ {self.url} ไม่มีโมเดลให้ใช้ — ติดตั้งโมเดลก่อน (เช่น ollama pull ...)")
            self.model = models[0]
        return self.model

    def _payload(self, messages: list[dict], stream: bool) -> dict:
        return {"model": self.model, "messages": messages, "temperature": 0.2, "stream": stream}

    async def complete(self, messages: list[dict]) -> str:
        await self.resolve_model()
        try:
            resp = await self.client.post(f"{self.url}/chat/completions", headers=self.headers,
                                          json=self._payload(messages, False))
        except httpx.ConnectError:
            raise LLMError(f"เชื่อมต่อ LLM ที่ {self.url} ไม่ได้ — เปิด Ollama/LM Studio หรือแก้ที่อยู่ในตั้งค่า")
        if resp.status_code != 200:
            raise LLMError(f"LLM ตอบกลับ HTTP {resp.status_code}: {resp.text[:500]}")
        return strip_think(resp.json()["choices"][0]["message"].get("content") or "").strip()

    async def stream(self, messages: list[dict]) -> AsyncIterator[str]:
        await self.resolve_model()
        think = ThinkFilter()
        try:
            async with self.client.stream("POST", f"{self.url}/chat/completions", headers=self.headers,
                                          json=self._payload(messages, True)) as resp:
                if resp.status_code != 200:
                    body = (await resp.aread()).decode(errors="ignore")
                    raise LLMError(f"LLM ตอบกลับ HTTP {resp.status_code}: {body[:500]}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:") or line[5:].strip() == "[DONE]":
                        continue
                    try:
                        delta = json.loads(line[5:])["choices"][0].get("delta") or {}
                    except (ValueError, KeyError, IndexError):
                        continue
                    if text := think.feed(delta.get("content") or ""):
                        yield text
        except httpx.ConnectError:
            raise LLMError(f"เชื่อมต่อ LLM ที่ {self.url} ไม่ได้ — เปิด Ollama/LM Studio หรือแก้ที่อยู่ในตั้งค่า")


async def list_models(client: httpx.AsyncClient, url: str, headers: dict | None = None) -> list[str]:
    resp = await client.get(f"{url.rstrip('/')}/models", headers=headers or {}, timeout=10)
    resp.raise_for_status()
    return [m["id"] for m in resp.json().get("data", []) if "id" in m]


def strip_think(text: str) -> str:
    """Drop <think>...</think> blocks that reasoning models put before the answer."""
    return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S)


class ThinkFilter:
    """Streaming version of strip_think."""

    def __init__(self):
        self.buf, self.inside = "", False

    def feed(self, piece: str) -> str:
        self.buf += piece
        out = ""
        while self.buf:
            if self.inside:
                end = self.buf.find("</think>")
                if end < 0:
                    self.buf = self.buf[-8:]
                    return out
                self.buf, self.inside = self.buf[end + 8:], False
                continue
            start = self.buf.find("<think>")
            if start < 0:
                # Hold back a possible partial "<think>" at the end.
                keep = next((k for k in range(min(7, len(self.buf)), 0, -1)
                             if "<think>".startswith(self.buf[-k:])), 0)
                out, self.buf = out + self.buf[:len(self.buf) - keep], self.buf[len(self.buf) - keep:]
                return out
            out += self.buf[:start]
            self.buf, self.inside = self.buf[start + 7:], True
        return out


def chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Split text into pieces of at most `size` characters at line or sentence ends."""
    parts, cur = [], ""
    for piece in re.split(r"(?<=[\n.!?。！？])", text):
        if len(cur) + len(piece) > size and cur:
            parts.append(cur)
            cur = ""
        while len(piece) > size:
            parts.append(piece[:size])
            piece = piece[size:]
        cur += piece
    if cur.strip():
        parts.append(cur)
    return parts


def with_extra(system: str, extra: str) -> str:
    return system + (f"\nAdditional instructions from the user: {extra}" if extra.strip() else "")


async def translate_text(llm: LLM, text: str, src: str, extra: str) -> AsyncIterator[str]:
    system = with_extra(TRANSLATE_SYSTEM.format(src=src), extra)
    for i, part in enumerate(chunks(text)):
        if i:
            yield "\n\n"
        async for piece in llm.stream([{"role": "system", "content": system},
                                       {"role": "user", "content": part}]):
            yield piece


async def summarize(llm: LLM, text: str, extra: str) -> AsyncIterator[str]:
    system = with_extra(SUMMARY_SYSTEM, extra)
    parts = chunks(text)
    if len(parts) > 1:  # long recording: notes per part first, then one summary of the notes
        notes = []
        for part in parts:
            notes.append(await llm.complete([
                {"role": "system", "content": SUMMARY_SYSTEM},
                {"role": "user", "content": "จดประเด็นสำคัญของบทถอดเสียงส่วนนี้เป็นภาษาไทย แบบหัวข้อย่อยสั้นๆ:\n\n" + part},
            ]))
        text = "\n\n".join(f"[ส่วนที่ {i + 1}]\n{n}" for i, n in enumerate(notes))
        ask = "ประเด็นต่อไปนี้มาจากแต่ละส่วนของการบันทึกเสียงเดียวกัน เขียนสรุปภาพรวมเป็นภาษาไทย "
    else:
        ask = "สรุปเนื้อหาของบทถอดเสียงต่อไปนี้เป็นภาษาไทย "
    ask += "โดยมีย่อหน้าสรุปสั้นๆ ตามด้วยประเด็นสำคัญเป็นหัวข้อย่อย:\n\n"
    async for piece in llm.stream([{"role": "system", "content": system},
                                   {"role": "user", "content": ask + text}]):
        yield piece


async def answer(llm: LLM, text: str, question: str) -> AsyncIterator[str]:
    question = question.strip() or "เนื้อหานี้เกี่ยวกับอะไร"
    parts = chunks(text)
    if len(parts) > 1:  # long recording: collect what each part says about the question
        found = []
        for part in parts:
            note = await llm.complete([
                {"role": "system", "content": QA_SYSTEM},
                {"role": "user", "content": f"คำถาม: {question}\n\nจดเฉพาะข้อมูลในส่วนนี้ที่เกี่ยวกับคำถาม "
                                            f"ถ้าไม่มีให้ตอบว่า ไม่มี\n\n{part}"},
            ])
            if note and not note.startswith("ไม่มี"):
                found.append(note)
        text = "\n\n".join(found) or "(ไม่พบข้อมูลที่เกี่ยวข้องในบทถอดเสียง)"
    async for piece in llm.stream([{"role": "system", "content": QA_SYSTEM},
                                   {"role": "user", "content": f"บทถอดเสียง:\n{text}\n\nคำถาม: {question}"}]):
        yield piece


async def translate_cues(llm: LLM, cues: list[dict], src: str, extra: str) -> AsyncIterator[tuple[int, str, str | None]]:
    """Yield (cue index, Thai text, error) for every cue, translating CUE_BATCH cues per request."""
    system = with_extra(SUBTITLE_SYSTEM.format(src=src), extra)
    for start in range(0, len(cues), CUE_BATCH):
        batch = cues[start:start + CUE_BATCH]
        lines = "\n".join(f"{i + 1}. {c['text'].strip()}" for i, c in enumerate(batch))
        try:
            reply = await llm.complete([{"role": "system", "content": system},
                                        {"role": "user", "content": lines}])
        except LLMError as exc:
            for i in range(len(batch)):
                yield start + i, "", str(exc)
            continue
        got = {}
        for m in re.finditer(r"^\s*(\d+)\s*[.)\]:-]\s*(.+?)\s*$", reply, flags=re.M):
            got.setdefault(int(m.group(1)), m.group(2))
        for i in range(len(batch)):
            text = got.get(i + 1, "")
            yield start + i, text, None if text else "LLM ไม่ได้ส่งคำแปลบรรทัดนี้กลับมา"
