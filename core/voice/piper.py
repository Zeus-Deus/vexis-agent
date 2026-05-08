"""Piper-backed TTS provider.

Piper is a fast, local, neural TTS engine (Rhasspy project). Each
voice is a pair of ``.onnx`` (model) + ``.onnx.json`` (config) files;
the binary streams 16-bit PCM WAV to stdout when invoked with
``--output_file -``. We pipe the text in via stdin to avoid argv
limits on long replies.

The user installs piper themselves (Arch: ``yay -S piper-tts-bin``,
or ``pip install piper-tts`` in the vexis env). Voice models live
under ``~/.local/share/piper-voices/`` by default; the config knob
is ``voice.tts.voice_model_path`` in ``~/.vexis/config.yaml``.

We don't bundle a default model — it's a 60–80 MB asset and licensing
varies per voice. The provider raises :class:`TTSUnavailable` with a
helpful hint when the model is missing.

PATH-collision handling: Arch ships an unrelated ``piper`` binary at
``/usr/bin/piper`` (a GTK config tool for gaming mice). When the user
hasn't pinned ``voice.tts.binary`` we auto-detect the right one by
reading the script header — see :func:`_resolve_piper_binary` and
:func:`_looks_like_piper_tts`. This is what lets the AUR package
install cleanly without per-user config baking.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from .base import TTSProvider, TTSUnavailable, VoiceError

log = logging.getLogger(__name__)


def _looks_like_piper_tts(path: str) -> bool:
    """Read the first ~500 bytes to disambiguate piper-tts from
    other binaries also named ``piper`` — notably the GTK gaming-mouse
    config tool on Arch, which crashes with ``ModuleNotFoundError: gi``
    when invoked headless.

    Heuristics (in order):
      1. ELF magic → trust it (piper-tts standalone builds + most
         non-Python binaries; the GTK tool is a Python wrapper so
         this rules it out).
      2. Python script importing ``gi`` → reject (the gaming-mouse
         signature; ``gi`` is only used by GTK apps).
      3. Python script mentioning ``piper`` module → accept (pip's
         entry-point wrapper does ``from piper.__main__ import main``).
      4. Anything else → reject (caller falls through to the next
         candidate).

    Reads only the head of the file so a multi-MB binary doesn't
    cost real I/O on every TTS call.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(500)
    except OSError:
        return False
    if head.startswith(b"\x7fELF"):
        return True
    text = head.decode("utf-8", errors="replace")
    if "import gi" in text:
        return False
    if "from piper" in text or "piper.__main__" in text or "import piper" in text:
        return True
    return False


def _resolve_piper_binary(explicit: str | None) -> str | None:
    """Find a working piper-tts binary, validating each candidate
    against :func:`_looks_like_piper_tts`. Returns the first hit's
    absolute path, or ``None`` when nothing usable is found (the
    provider then raises TTSUnavailable with a useful hint).

    Resolution order, ranked from most-likely to ship-as-correct:
      1. ``explicit`` path from config — trust the user.
      2. ``CONDA_PREFIX/bin/piper`` when a conda env is active —
         catches the common ``pip install piper-tts`` flow.
      3. PATH lookups for ``piper`` and ``piper-tts``.
      4. Common conda env locations (``~/miniconda3/envs/*/bin/piper``
         and the anaconda equivalent) — catches the case where the
         daemon was started without ``conda activate`` but a vexis env
         exists. Useful for our specific dev box; harmless for
         packaged installs (no envs → loop is empty).
      5. ``~/.local/bin/piper`` (pip --user) and
         ``/usr/local/bin/piper`` (system pip without --user).

    The resolver costs at most a few file-header reads — cheap
    enough to run on every provider construction (which happens
    once per chat send, not per token).
    """
    if explicit:
        return explicit

    candidates: list[str | None] = []

    # 2. CONDA_PREFIX if active env
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        candidates.append(str(Path(conda_prefix) / "bin" / "piper"))

    # 3. PATH lookups
    candidates.append(shutil.which("piper"))
    candidates.append(shutil.which("piper-tts"))

    # 4. Walk known conda env roots so the daemon finds piper even
    #    when CONDA_PREFIX isn't set (e.g. systemd unit launches it
    #    without activate). Iteration is bounded by env count.
    home = Path.home()
    for conda_root in (home / "miniconda3" / "envs", home / "anaconda3" / "envs"):
        if not conda_root.is_dir():
            continue
        try:
            for env_dir in conda_root.iterdir():
                guess = env_dir / "bin" / "piper"
                if guess.is_file():
                    candidates.append(str(guess))
        except OSError:
            continue

    # 5. Pip install locations
    candidates.append(str(home / ".local" / "bin" / "piper"))
    candidates.append("/usr/local/bin/piper")

    seen: set[str] = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if _looks_like_piper_tts(path):
            return path
    return None

# Bound on synthesis time. Piper is fast (a few seconds for typical
# replies on CPU) but a stuck subprocess shouldn't hang the route.
_PIPER_TIMEOUT_SECONDS = 30

# Cap on input length. Very long replies should be split client-side
# before synthesis; this is a defensive ceiling that rejects pathological
# inputs (1 MB of text would mean the brain misbehaved upstream).
_MAX_TEXT_BYTES = 16 * 1024


class PiperTTS(TTSProvider):
    name = "piper"
    mime_type = "audio/wav"

    def __init__(
        self,
        *,
        voice_model_path: Path | None,
        binary: str | None = None,
    ) -> None:
        """``binary`` is an optional explicit path. ``None`` triggers
        :func:`_resolve_piper_binary` which auto-detects the right
        piper-tts even when the system has the unrelated GTK
        ``piper`` tool on PATH (Arch's gaming-mouse config app)."""
        self._voice_model_path = voice_model_path
        self._explicit_binary = binary

    async def synthesize(self, text: str) -> bytes:
        text = text.strip()
        if not text:
            return b""
        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            raise VoiceError(
                f"text exceeds {_MAX_TEXT_BYTES} bytes; split before synthesis",
            )

        binary = _resolve_piper_binary(self._explicit_binary)
        if binary is None:
            raise TTSUnavailable(
                "No working piper-tts binary found. Install with "
                "`pip install piper-tts` in your vexis env, "
                "`yay -S piper-tts-bin` (Arch), or "
                "`pip install --user piper-tts`. If you have piper-tts "
                "installed but Arch's unrelated `/usr/bin/piper` "
                "(gaming-mouse GTK tool) is shadowing it, set "
                "`voice.tts.binary` in ~/.vexis/config.yaml to the "
                "absolute path of your piper-tts install.",
            )
        if self._voice_model_path is None or not self._voice_model_path.is_file():
            raise TTSUnavailable(
                "Piper voice model not found. Set "
                "``voice.tts.voice_model_path`` in ~/.vexis/config.yaml "
                "to a downloaded .onnx voice file (see "
                "https://github.com/rhasspy/piper#voices).",
            )

        argv = [
            binary,
            "--model",
            str(self._voice_model_path),
            "--output_file",
            "-",  # stream WAV bytes to stdout
        ]
        log.info("piper synthesize: %d chars, model=%s", len(text), self._voice_model_path.name)

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(text.encode("utf-8")),
                timeout=_PIPER_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise VoiceError(
                f"piper timed out after {_PIPER_TIMEOUT_SECONDS}s",
            ) from exc

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            raise VoiceError(
                f"piper exited {proc.returncode}: {err or '(no stderr)'}",
            )
        if not stdout:
            raise VoiceError("piper returned no audio bytes")
        return stdout
