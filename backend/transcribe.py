"""Speech to text. The core calls OpenAI; the browser never sees the key."""
import os

import httpx

MODEL = "gpt-4o-mini-transcribe"
MAX_BYTES = 10 * 1024 * 1024


class TranscriptionError(Exception):
    def __init__(self, status: int, code: str):
        self.status = status
        self.code = code


def language_code(value: str) -> str | None:
    primary = value.strip().lower().split("-")[0]
    return primary if len(primary) == 2 and primary.isalpha() else None


def transcribe(audio: bytes, media_type: str, language: str) -> str:
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise TranscriptionError(503, "transcription_unavailable")
    if not audio:
        raise TranscriptionError(400, "empty_audio")
    if len(audio) > MAX_BYTES:
        raise TranscriptionError(413, "audio_too_large")

    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    data = {"model": MODEL}
    code = language_code(language)
    if code:
        data["language"] = code
    try:
        response = httpx.post(
            f"{base}/audio/transcriptions",
            headers={"Authorization": f"Bearer {key}"},
            files={"file": ("speech", audio, media_type or "application/octet-stream")},
            data=data,
            timeout=60,
        )
    except httpx.HTTPError:
        raise TranscriptionError(502, "transcription_failed") from None
    if response.status_code != 200:
        raise TranscriptionError(502, "transcription_failed")
    try:
        text = response.json().get("text")
    except ValueError:
        raise TranscriptionError(502, "transcription_failed") from None
    if not isinstance(text, str):
        raise TranscriptionError(502, "transcription_failed")
    return text.strip()
