"""Voxtype-backed STT provider.

Thin wrapper around :func:`tools.voxtype.transcribe_audio` so the
voice subsystem doesn't reach across the tools/ namespace itself
(tools/ is for MCP servers; core/ owns architecture). The wrapper
also normalises the exception surface — voxtype's ``TranscriptionError``
becomes a generic :class:`VoiceError` so callers don't import from
two modules to handle errors.
"""

from __future__ import annotations

import logging
from pathlib import Path

from tools.voxtype import (
    TranscriptionEmpty,
    TranscriptionError,
    transcribe_audio,
)

from .base import STTProvider, VoiceError

log = logging.getLogger(__name__)


class VoxtypeSTT(STTProvider):
    name = "voxtype"

    async def transcribe(self, audio_path: Path) -> str:
        try:
            return await transcribe_audio(audio_path)
        except TranscriptionEmpty as exc:
            # Empty transcription is a normal outcome (silence,
            # background noise) — bubble as VoiceError so the route
            # can return 422 with an empty string. Distinct from the
            # "STT broken" case below.
            raise VoiceError(f"empty: {exc}") from exc
        except TranscriptionError as exc:
            log.exception("voxtype transcription failed")
            raise VoiceError(f"transcription failed: {exc}") from exc
