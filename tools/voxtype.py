"""Async wrapper: OGG/Opus → 16 kHz mono WAV (ffmpeg) → text (voxtype)."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import time
from pathlib import Path

from core.subprocess import run

log = logging.getLogger(__name__)

TRANSCRIBE_TIMEOUT_SECONDS = 60
FFMPEG_TIMEOUT_SECONDS = 30


class TranscriptionError(Exception):
    pass


class TranscriptionEmpty(TranscriptionError):
    pass


async def transcribe_audio(audio_path: Path) -> str:
    """Transcribe an OGG/Opus voice memo. Returns stripped text."""
    log.info("Transcribing %s", audio_path)
    started = time.monotonic()

    fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    wav_path = Path(fd.name)
    fd.close()

    try:
        ffmpeg_argv = [
            "ffmpeg",
            "-y",
            "-i",
            str(audio_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "wav",
            str(wav_path),
        ]
        await _run_or_raise("ffmpeg", ffmpeg_argv, FFMPEG_TIMEOUT_SECONDS)

        stdout = await _run_or_raise(
            "voxtype",
            ["voxtype", "--quiet", "transcribe", str(wav_path)],
            TRANSCRIBE_TIMEOUT_SECONDS,
        )
    finally:
        wav_path.unlink(missing_ok=True)

    out = stdout.decode(errors="replace")
    # Voxtype prints progress, then a blank line, then the transcription.
    # Fall back to whole stdout if the blank-line marker is absent.
    text = (out.split("\n\n", 1)[1] if "\n\n" in out else out).strip()
    if not text:
        raise TranscriptionEmpty("voxtype produced no transcription text")

    log.info(
        "Transcription complete in %.2fs (%d chars)",
        time.monotonic() - started,
        len(text),
    )
    return text


async def _run_or_raise(name: str, argv: list[str], timeout: int) -> bytes:
    try:
        rc, stdout, stderr = await run(name, argv, timeout)
    except asyncio.TimeoutError as exc:
        raise TranscriptionError(f"{name} timed out after {timeout}s") from exc

    log.debug("%s stderr: %s", name, stderr.decode(errors="replace"))
    if rc != 0:
        err = stderr.decode(errors="replace").strip()
        raise TranscriptionError(f"{name} exited {rc}: {err or '(no stderr)'}")
    return stdout
