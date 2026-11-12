"""Speech-to-text with faster-whisper (spec 3.8 "voice in"), behind the ENABLE_VOICE flag.

Audio is processed in memory and never stored. Text-to-speech ("voice out") uses the browser's
built-in speech synthesis (hi-IN / en-IN voices), so no audio is sent anywhere for that.
"""

from __future__ import annotations

import io
import logging
import threading
from typing import Any

from app.config import get_settings
from app.services.errors import ServiceError

log = logging.getLogger(__name__)
_model: Any = None
_lock = threading.Lock()
MAX_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 60


def status() -> dict[str, Any]:
    s = get_settings()
    try:
        import faster_whisper  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    return {"enabled": s.enable_voice and installed, "flag": s.enable_voice, "installed": installed,
            "model": s.whisper_model, "loaded": _model is not None, "tts": "browser speechSynthesis"}


def _get() -> Any:
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            s = get_settings()
            _model = WhisperModel(s.whisper_model, device=s.whisper_device, compute_type="int8")
            log.info("whisper model loaded: %s", s.whisper_model)
        return _model


def transcribe(data: bytes, language: str | None = None) -> dict[str, Any]:
    st = status()
    if not st["enabled"]:
        raise ServiceError("voice_disabled", 503, installed=st["installed"])
    if not data or len(data) > MAX_BYTES:
        raise ServiceError("file_too_large", 413, max_mb=10)
    model = _get()
    try:
        segments, info = model.transcribe(io.BytesIO(data), language=language if language in ("hi", "en") else None,
                                          beam_size=1, vad_filter=True)
        if info.duration > MAX_SECONDS:
            raise ServiceError("audio_too_long", 422, max_seconds=MAX_SECONDS)
        text = " ".join(seg.text.strip() for seg in segments).strip()
    except ServiceError:
        raise
    except Exception as exc:  # noqa: BLE001 - undecodable audio
        log.warning("transcription failed: %s", type(exc).__name__)
        raise ServiceError("bad_audio", 422) from exc
    return {"text": text, "language": info.language if info.language in ("hi", "en") else "en",
            "language_probability": round(info.language_probability, 3), "duration": round(info.duration, 1),
            "model": get_settings().whisper_model}
