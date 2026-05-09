"""Discover Piper voice models on disk.

Used by the dashboard's Voice settings page so users can pick a voice
from a dropdown instead of typing a path. We scan the standard
``~/.local/share/piper-voices/`` directory plus any directory the user
has already configured under ``voice.tts.voice_model_path`` (so a
non-standard install location still surfaces in the picker).

A "voice" is a pair of files: ``<name>.onnx`` and ``<name>.onnx.json``.
The .json sidecar carries the model's sample rate, language, and
speaker count — only the .onnx is strictly required to synthesize,
but the .json is what makes the voice human-recognisable in a UI
(language code, voice name, quality tier).

We return entries even when the .json is missing — the UI still gets
a usable label from the filename — but we surface ``has_config: false``
so the user knows to download the missing config.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Default scan locations. The first is the canonical Piper convention
# (matches the README install instructions); the second is a fallback
# for users who used a system package manager that lands models in /usr.
_DEFAULT_SEARCH_PATHS = (
    Path.home() / ".local" / "share" / "piper-voices",
    Path("/usr/share/piper-voices"),
)


@dataclass(frozen=True, slots=True)
class PiperVoice:
    """One discovered voice — wire-format for the dashboard."""

    # Absolute path to the .onnx file. Goes straight into
    # ``voice.tts.voice_model_path`` when the user picks this voice.
    path: str
    # Filename stem (e.g. "en_GB-alan-medium"). Display label.
    name: str
    # Whether the .onnx.json sidecar is present. False means the
    # voice will load but the language/sample-rate metadata is absent
    # (Piper uses defaults from its embedded config in that case).
    has_config: bool
    # ISO language code parsed from the filename or sidecar
    # (e.g. "en_GB", "fr_FR"). Empty string when undetectable.
    language: str
    # Bytes on disk. Useful for the user to confirm a partial download
    # didn't happen (full medium voice ~63 MB).
    size: int


def _parse_language_from_name(stem: str) -> str:
    """Filename convention: ``<lang>-<voice>-<quality>`` (e.g.
    ``en_GB-alan-medium``). Take the first hyphen-separated token."""
    return stem.split("-", 1)[0] if "-" in stem else ""


def _read_language_from_config(json_path: Path) -> str:
    """Best-effort read of the ``language.code`` field from the
    sidecar. Falls back to filename parsing on any error."""
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    lang = data.get("language", {})
    if isinstance(lang, dict):
        code = lang.get("code")
        if isinstance(code, str):
            return code
    return ""


def find_piper_voices(extra_paths: list[Path] | None = None) -> list[PiperVoice]:
    """Walk all known piper-voices directories and return each .onnx
    found, deduplicated by absolute path.

    The dashboard passes the user's currently-configured
    ``voice_model_path`` parent dir as ``extra_paths`` so a custom
    install location still surfaces — even if the user originally
    typed a path that bypassed the canonical layout.

    Sorted alphabetically by name so the picker order is stable.
    """
    seen: set[Path] = set()
    voices: list[PiperVoice] = []
    paths: list[Path] = list(_DEFAULT_SEARCH_PATHS)
    if extra_paths:
        paths.extend(extra_paths)

    for root in paths:
        if not root.is_dir():
            continue
        # rglob handles both flat layouts (``en_GB-alan-medium.onnx``
        # right under the root) and the nested huggingface layout
        # (``en/en_GB/alan/medium/en_GB-alan-medium.onnx``).
        for onnx in root.rglob("*.onnx"):
            absolute = onnx.resolve()
            if absolute in seen:
                continue
            seen.add(absolute)
            sidecar = onnx.with_suffix(".onnx.json")
            language = (
                _read_language_from_config(sidecar)
                if sidecar.is_file()
                else _parse_language_from_name(onnx.stem)
            )
            try:
                size = onnx.stat().st_size
            except OSError:
                size = 0
            voices.append(
                PiperVoice(
                    path=str(absolute),
                    name=onnx.stem,
                    has_config=sidecar.is_file(),
                    language=language,
                    size=size,
                )
            )

    voices.sort(key=lambda v: v.name)
    return voices
