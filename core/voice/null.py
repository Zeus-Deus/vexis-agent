"""No-op STT/TTS providers — the default when ``voice.enabled`` is
false (or true with provider set to ``null``).

Both methods raise :class:`STTUnavailable` / :class:`TTSUnavailable`
rather than returning sentinel values, so the route layer can map
the exception to a 503 without ambiguity. The chat UI treats 503
on either endpoint as "hide that affordance" — no mic button, no
TTS playback — which is the correct posture for a user who hasn't
opted into voice.
"""

from __future__ import annotations

from pathlib import Path

from .base import STTProvider, STTUnavailable, TTSProvider, TTSUnavailable


class NullSTT(STTProvider):
    name = "null"

    async def transcribe(self, audio_path: Path) -> str:
        raise STTUnavailable(
            "STT is disabled. Set ``voice.enabled: true`` and "
            "``voice.stt.provider`` (e.g. ``voxtype``) in "
            "~/.vexis/config.yaml."
        )


class NullTTS(TTSProvider):
    name = "null"
    mime_type = "audio/wav"

    async def synthesize(self, text: str) -> bytes:
        raise TTSUnavailable(
            "TTS is disabled. Set ``voice.enabled: true`` and "
            "``voice.tts.provider`` (e.g. ``piper``) in "
            "~/.vexis/config.yaml."
        )
