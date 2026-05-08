"""Voice provider abstractions — STT (speech-to-text) and TTS (text-to-speech).

Voice is an *addon* in vexis-agent terms: off by default, opt-in via
``voice.enabled: true`` in ``~/.vexis/config.yaml``. Each side (STT
and TTS) is a separately-pluggable provider so a user can run, e.g.,
voxtype for STT but Piper for TTS, or null on either side to disable
just one capability.

Mirrors the brain abstraction in :mod:`core.brain.base` — pure ABCs
here, concrete implementations in sibling modules, factory in
``__init__``. This file imports nothing brain-related; the chat
layer composes them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class VoiceError(Exception):
    """Base class for voice subsystem errors. Catch this at the
    edges so a busted provider (missing binary, missing model file,
    bad input) degrades to "voice unavailable" rather than crashing
    the chat turn."""


class STTUnavailable(VoiceError):
    """The STT provider is not configured or not callable. Raised by
    the null provider; routes translate this into a 503 so the UI
    can hide the mic button cleanly."""


class TTSUnavailable(VoiceError):
    """The TTS provider equivalent of :class:`STTUnavailable`."""


class STTProvider(ABC):
    """Speech-to-text. Single method: hand it a path to an audio
    file on disk, get back the transcribed string."""

    # Human-readable name surfaced to /api/v1/chat/voice/info so the
    # UI can label the active provider without round-tripping config.
    name: str = "abstract"

    @abstractmethod
    async def transcribe(self, audio_path: Path) -> str:
        """Transcribe ``audio_path``. Implementations decide what
        formats they accept; the chat route saves uploads as their
        original mime-extension and lets the provider sort it out
        (voxtype-via-ffmpeg, for instance, accepts every ffmpeg
        format)."""


class TTSProvider(ABC):
    """Text-to-speech. Returns audio bytes ready to ship to the
    browser as a Blob; format declared by ``mime_type`` so the UI
    can set the right ``Content-Type`` on the ``<audio>`` element."""

    name: str = "abstract"
    mime_type: str = "audio/wav"

    @abstractmethod
    async def synthesize(self, text: str) -> bytes:
        """Synthesize ``text`` to audio bytes in ``mime_type``
        format. Empty string → empty bytes (callers should bypass)."""
