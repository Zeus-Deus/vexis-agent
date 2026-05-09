"""Tests for the core/voice provider abstraction.

Voice is an addon: off by default, opt-in via ``voice.enabled``.
The provider registry must:
  * return null providers when disabled (and 503-style errors fire)
  * resolve named providers when enabled
  * fall back to null on unknown names rather than crashing
  * hot-reload (each call re-reads config; no startup-pinned state)

Piper itself is exercised by spawning the binary, so we don't test
its actual synthesis here — that's an integration concern. We do
test that the provider raises ``TTSUnavailable`` cleanly when the
binary or model is missing, since that's the user-visible path.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core.voice import (
    STTUnavailable,
    TTSUnavailable,
    stt_provider,
    tts_provider,
    voice_enabled,
)
from core.voice.null import NullSTT, NullTTS
from core.voice.piper import PiperTTS


def _write_config(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def vexis_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.vexis/config.yaml to tmp so tests don't touch
    the real one. Returns the path so each test can rewrite it."""
    cfg = tmp_path / "config.yaml"
    monkeypatch.setattr("core.yaml_config._config_path", lambda: cfg)
    return cfg


# ──────────────────────────────────────────────────────────────────
# Defaults / disabled
# ──────────────────────────────────────────────────────────────────


def test_voice_disabled_by_default(vexis_config: Path) -> None:
    # Empty config → voice disabled.
    assert voice_enabled() is False
    assert isinstance(stt_provider(), NullSTT)
    assert isinstance(tts_provider(), NullTTS)


def test_disabled_when_enabled_false(vexis_config: Path) -> None:
    _write_config(vexis_config, "voice:\n  enabled: false\n")
    assert voice_enabled() is False
    assert isinstance(stt_provider(), NullSTT)
    assert isinstance(tts_provider(), NullTTS)


def test_null_stt_raises_unavailable(vexis_config: Path) -> None:
    provider = stt_provider()
    with pytest.raises(STTUnavailable):
        asyncio.run(provider.transcribe(Path("/nonexistent.ogg")))


def test_null_tts_raises_unavailable(vexis_config: Path) -> None:
    provider = tts_provider()
    with pytest.raises(TTSUnavailable):
        asyncio.run(provider.synthesize("hello"))


# ──────────────────────────────────────────────────────────────────
# Provider resolution when enabled
# ──────────────────────────────────────────────────────────────────


def test_voxtype_resolves_when_enabled(vexis_config: Path) -> None:
    _write_config(
        vexis_config,
        "voice:\n  enabled: true\n  stt:\n    provider: voxtype\n",
    )
    from core.voice.voxtype_provider import VoxtypeSTT
    provider = stt_provider()
    assert isinstance(provider, VoxtypeSTT)
    assert provider.name == "voxtype"


def test_piper_resolves_when_enabled(vexis_config: Path) -> None:
    _write_config(
        vexis_config,
        "voice:\n"
        "  enabled: true\n"
        "  tts:\n"
        "    provider: piper\n"
        "    voice_model_path: ~/.local/share/piper-voices/test.onnx\n",
    )
    provider = tts_provider()
    assert isinstance(provider, PiperTTS)
    assert provider.name == "piper"
    assert provider.mime_type == "audio/wav"


def test_unknown_stt_provider_falls_back_to_null(vexis_config: Path) -> None:
    _write_config(
        vexis_config,
        "voice:\n  enabled: true\n  stt:\n    provider: notathing\n",
    )
    assert isinstance(stt_provider(), NullSTT)


def test_unknown_tts_provider_falls_back_to_null(vexis_config: Path) -> None:
    _write_config(
        vexis_config,
        "voice:\n  enabled: true\n  tts:\n    provider: notathing\n",
    )
    assert isinstance(tts_provider(), NullTTS)


def test_explicit_null_provider(vexis_config: Path) -> None:
    # Voice enabled but TTS explicitly disabled — the use case for a
    # user who wants STT (voice in) but doesn't want TTS playback.
    _write_config(
        vexis_config,
        "voice:\n"
        "  enabled: true\n"
        "  stt:\n    provider: voxtype\n"
        "  tts:\n    provider: null\n",
    )
    from core.voice.voxtype_provider import VoxtypeSTT
    assert isinstance(stt_provider(), VoxtypeSTT)
    assert isinstance(tts_provider(), NullTTS)


# ──────────────────────────────────────────────────────────────────
# Piper edge cases (no binary, no model) — both should raise
# TTSUnavailable so the route returns 503 with a useful hint.
# ──────────────────────────────────────────────────────────────────


def test_piper_missing_binary_raises_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin to a non-existent path so the explicit-trust branch fires —
    # but the binary doesn't exist, so synthesize() will eventually
    # try to run it and fail with a process error. The unavailable
    # branch fires when the resolver can't find anything; we test
    # that by pinning to a path the validator rejects.
    provider = PiperTTS(
        voice_model_path=tmp_path / "voice.onnx",
        binary="/nonexistent/piper-tts-xyz",
    )
    # Explicit binary is trusted by _resolve_piper_binary, so we
    # reach the model-check first. Either branch (missing binary
    # vs missing model) raises TTSUnavailable.
    with pytest.raises(TTSUnavailable):
        asyncio.run(provider.synthesize("hello"))


def test_piper_missing_model_raises_unavailable(tmp_path: Path) -> None:
    # ``true`` is on every Linux/macOS and bypasses the file-header
    # check via the ELF magic test. The missing-model branch fires.
    provider = PiperTTS(
        voice_model_path=tmp_path / "does-not-exist.onnx",
        binary="/usr/bin/true",
    )
    with pytest.raises(TTSUnavailable, match="voice model not found"):
        asyncio.run(provider.synthesize("hello"))


def test_piper_empty_text_returns_empty_bytes(tmp_path: Path) -> None:
    # Whitespace-only text is bypassed before even checking the
    # binary — no synthesis attempt, no error.
    provider = PiperTTS(voice_model_path=None)
    assert asyncio.run(provider.synthesize("")) == b""
    assert asyncio.run(provider.synthesize("   \n\t")) == b""


# ──────────────────────────────────────────────────────────────────
# Binary auto-resolver — packaging-friendly piper-tts detection
# ──────────────────────────────────────────────────────────────────


def test_looks_like_piper_tts_accepts_real_piper(tmp_path: Path) -> None:
    """Script wrapper that imports the piper module → accepted."""
    from core.voice.piper import _looks_like_piper_tts
    fake = tmp_path / "piper"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from piper.__main__ import main\n",
    )
    assert _looks_like_piper_tts(str(fake)) is True


def test_looks_like_piper_tts_rejects_gtk_gaming_mouse(tmp_path: Path) -> None:
    """The Arch gaming-mouse ``piper`` (GTK app) imports gi → rejected."""
    from core.voice.piper import _looks_like_piper_tts
    fake = tmp_path / "piper"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "\n"
        "import gi\n"
        "import os\n",
    )
    assert _looks_like_piper_tts(str(fake)) is False


def test_looks_like_piper_tts_accepts_elf_binary(tmp_path: Path) -> None:
    """Standalone Piper builds are ELF binaries — accepted by magic
    bytes since they aren't the Python GTK wrapper."""
    from core.voice.piper import _looks_like_piper_tts
    fake = tmp_path / "piper"
    # ELF magic; rest is junk but the validator only reads the head.
    fake.write_bytes(b"\x7fELF\x02\x01\x01" + b"\x00" * 100)
    assert _looks_like_piper_tts(str(fake)) is True


def test_looks_like_piper_tts_rejects_unknown_script(tmp_path: Path) -> None:
    """A random Python script that isn't piper-tts shouldn't pass."""
    from core.voice.piper import _looks_like_piper_tts
    fake = tmp_path / "thing"
    fake.write_text("#!/usr/bin/env python3\nprint('hi')\n")
    assert _looks_like_piper_tts(str(fake)) is False


def test_resolve_piper_binary_trusts_explicit_unconditionally() -> None:
    """User pinned a path → don't validate, don't auto-detect, just
    return it. The PiperTTS subprocess will fail loudly later if the
    pin was wrong; trusting the override matches every other pinnable
    config knob in vexis."""
    from core.voice.piper import _resolve_piper_binary
    assert _resolve_piper_binary("/some/explicit/path") == "/some/explicit/path"


def test_piper_factory_without_binary_config_passes_none(
    vexis_config: Path,
) -> None:
    """Regression: when ``voice.tts.binary`` is unset in config, the
    factory MUST pass ``None`` to PiperTTS — not the literal string
    ``"piper"``. Passing the string short-circuits the resolver
    (which trusts any truthy ``explicit`` value) and PATH lookup
    falls through to ``/usr/bin/piper`` (the GTK gaming-mouse tool
    on Arch). Caught the hard way live; pinned here so a refactor
    can't quietly resurrect it.
    """
    vexis_config.write_text(
        "voice:\n  enabled: true\n  tts:\n    provider: piper\n",
        encoding="utf-8",
    )
    provider = tts_provider()
    assert isinstance(provider, PiperTTS)
    assert provider._explicit_binary is None


def test_piper_factory_with_binary_config_forwards_path(
    vexis_config: Path,
) -> None:
    """Symmetric: when the user pins ``voice.tts.binary`` it gets
    forwarded verbatim (after tilde expansion) so the resolver's
    explicit-trust branch fires."""
    vexis_config.write_text(
        "voice:\n"
        "  enabled: true\n"
        "  tts:\n"
        "    provider: piper\n"
        "    binary: /custom/piper\n",
        encoding="utf-8",
    )
    provider = tts_provider()
    assert isinstance(provider, PiperTTS)
    assert provider._explicit_binary == "/custom/piper"


def test_resolve_piper_binary_returns_none_when_nothing_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No PATH entry, no conda envs, no pip install → None. The
    PiperTTS provider then raises TTSUnavailable with a clear hint."""
    from core.voice import piper as piper_mod
    monkeypatch.setattr(piper_mod.shutil, "which", lambda _name: None)
    # Point HOME at a tmp dir with no conda envs.
    monkeypatch.setattr(piper_mod.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.delenv("CONDA_PREFIX", raising=False)
    assert piper_mod._resolve_piper_binary(None) is None


# ──────────────────────────────────────────────────────────────────
# Hot reload — config changes mid-run should pick up on next call
# ──────────────────────────────────────────────────────────────────


def test_provider_hot_reload(vexis_config: Path) -> None:
    # Start disabled.
    assert isinstance(stt_provider(), NullSTT)
    # Flip on; same call reads the new config.
    _write_config(
        vexis_config,
        "voice:\n  enabled: true\n  stt:\n    provider: voxtype\n",
    )
    from core.voice.voxtype_provider import VoxtypeSTT
    assert isinstance(stt_provider(), VoxtypeSTT)
    # Flip back off; same again.
    _write_config(vexis_config, "voice:\n  enabled: false\n")
    assert isinstance(stt_provider(), NullSTT)
