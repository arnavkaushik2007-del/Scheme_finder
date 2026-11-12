"""Voice helpers: microphone recording (browser MediaRecorder -> /api/stt) and read-aloud (browser speechSynthesis).

Only a few lines of browser JavaScript are injected through NiceGUI; there is no JS build step.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Awaitable, Callable
from typing import Any

from nicegui import ui

from app.ui import api_client
from app.ui.i18n import error_text, lang, t

_START = """
(async () => {
  window.__sfChunks = [];
  const s = await navigator.mediaDevices.getUserMedia({audio: true});
  const r = new MediaRecorder(s);
  r.ondataavailable = e => window.__sfChunks.push(e.data);
  r.start(); window.__sfRec = r; window.__sfStream = s;
  return true;
})()
"""
_STOP = """
(async () => {
  const r = window.__sfRec; if (!r) return null;
  const done = new Promise(res => r.onstop = res); r.stop(); await done;
  window.__sfStream.getTracks().forEach(t => t.stop());
  const blob = new Blob(window.__sfChunks, {type: r.mimeType || 'audio/webm'});
  const buf = await blob.arrayBuffer(); let bin = ''; const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  return {type: blob.type, data: btoa(bin)};
})()
"""


async def mic_button(on_text: Callable[[str], Awaitable[None] | None]) -> None:
    """Record -> transcribe -> callback(text). Hidden when voice is disabled on the server."""
    st = await api_client.call("GET", "/api/voice/status", auth=False)
    if not (st.ok and st.data["enabled"]):
        return
    state: dict[str, Any] = {"rec": False}
    hint = ui.label(t("listening")).classes("text-xs text-[var(--sf-orange)]")
    hint.set_visibility(False)

    async def toggle() -> None:
        if not state["rec"]:
            try:
                await ui.run_javascript(_START, timeout=15)
            except Exception:  # noqa: BLE001 - permission denied / no microphone
                ui.notify("Microphone not available", type="warning")
                return
            state["rec"] = True
            btn.props("color=negative icon=stop")
            btn.text = t("stop")
            hint.set_visibility(True)
            return
        state["rec"] = False
        btn.props("color=primary icon=mic")
        btn.text = t("speak")
        hint.set_visibility(False)
        audio = await ui.run_javascript(_STOP, timeout=30)
        if not audio:
            return
        btn.props("loading")
        res = await api_client.call("POST", "/api/stt", data={"language": lang()},
                                    files={"file": ("speech.webm", base64.b64decode(audio["data"]), audio["type"])})
        btn.props(remove="loading")
        if not res.ok:
            ui.notify(error_text(res.code, res.detail), type="negative")
            return
        ui.notify(f"{t('heard')}: {res.data['text']}", type="info")
        out = on_text(res.data["text"])
        if out is not None:
            await out

    btn = ui.button(t("speak"), icon="mic", on_click=toggle).props("outline no-caps").classes("sf-btn sf-btn-outline")


def speak_button(text: str, language: str) -> None:
    """Read text aloud with the browser's own Hindi/English voice."""
    code = "hi-IN" if language == "hi" else "en-IN"
    js = (f"(() => {{ const u = new SpeechSynthesisUtterance({json.dumps(text)}); u.lang = {json.dumps(code)}; "
          f"speechSynthesis.cancel(); speechSynthesis.speak(u); }})()")
    ui.button(icon="volume_up", on_click=lambda: ui.run_javascript(js)).props("flat round dense size=sm") \
        .tooltip(t("listen"))
