"""Voice subsystem entrypoint.

Reads ``voice.*`` from ``~/.vexis/config.yaml`` and returns the
configured STT/TTS providers. Always returns a non-None pair —
when voice is disabled or a provider name is unknown, the null
provider stands in (its methods raise :class:`TTSUnavailable` /
:class:`STTUnavailable`, which the route layer turns into 503s).

Like :func:`core.yaml_config.brain_kind`, this re-reads disk per
call so config edits hot-reload at the next call site (chat route
on each request). The cost is one ``open()`` + YAML parse; the
file is small.
"""

from __future__ import annotations

import logging
from pathlib import Path

from vexis_agent.core import yaml_config

from .base import (
    STTProvider,
    STTUnavailable,
    TTSProvider,
    TTSUnavailable,
    VoiceError,
)
from .null import NullSTT, NullTTS

__all__ = [
    "STTProvider",
    "STTUnavailable",
    "TTSProvider",
    "TTSUnavailable",
    "VoiceError",
    "stt_provider",
    "tts_provider",
    "voice_enabled",
]

log = logging.getLogger(__name__)


def voice_enabled() -> bool:
    """True iff ``voice.enabled: true`` in config. Default false."""
    return yaml_config.voice_enabled()


def stt_provider() -> STTProvider:
    """Resolve and construct the active STT provider.

    Returns a :class:`NullSTT` when voice is disabled OR when the
    configured provider name is unknown — the route layer surfaces
    the unavailable state as 503 either way.
    """
    if not voice_enabled():
        return NullSTT()
    name = yaml_config.voice_stt_provider()
    if name == "voxtype":
        # Lazy-import so the tools.voxtype dependency only loads
        # when actually selected. Keeps the null path zero-cost.
        from .voxtype_provider import VoxtypeSTT
        return VoxtypeSTT()
    if name in ("null", ""):
        return NullSTT()
    log.warning("unknown voice.stt.provider=%r; falling back to null", name)
    return NullSTT()


def tts_provider() -> TTSProvider:
    """Resolve and construct the active TTS provider.

    Same null-fallback posture as :func:`stt_provider`.
    """
    if not voice_enabled():
        return NullTTS()
    name = yaml_config.voice_tts_provider()
    if name == "piper":
        from .piper import PiperTTS
        model_path_str = yaml_config.voice_tts_voice_model_path()
        model_path = Path(model_path_str).expanduser() if model_path_str else None
        # Optional explicit binary path — sidesteps collisions with
        # other ``piper`` binaries on the user's system (the Arch
        # ``piper`` gaming-mouse tool being the canonical example).
        # IMPORTANT: pass None (not "piper") when unset, so the
        # provider's _resolve_piper_binary kicks in and walks the
        # candidate list. Passing "piper" would short-circuit the
        # resolver because it treats any truthy value as trusted —
        # then PATH lookup finds /usr/bin/piper (the GTK tool) and
        # we'd be back to the bug this whole resolver was built to
        # avoid.
        binary_str = yaml_config.voice_tts_binary()
        binary = (
            str(Path(binary_str).expanduser()) if binary_str else None
        )
        return PiperTTS(voice_model_path=model_path, binary=binary)
    if name in ("null", ""):
        return NullTTS()
    log.warning("unknown voice.tts.provider=%r; falling back to null", name)
    return NullTTS()
