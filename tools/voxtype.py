"""Async wrapper: OGG/Opus → 16 kHz mono WAV (ffmpeg) → text (voxtype)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import tempfile
import time
from pathlib import Path

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
            "ffmpeg", "-y", "-i", str(audio_path),
            "-ar", "16000", "-ac", "1", "-f", "wav", str(wav_path),
        ]
        await _run("ffmpeg", ffmpeg_argv, FFMPEG_TIMEOUT_SECONDS)

        stdout = await _run(
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


async def _run(name: str, argv: list[str], timeout: int) -> bytes:
    """Run a subprocess in its own process group; raise TranscriptionError on
    timeout/non-zero. Returns captured stdout bytes."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        await _kill_group(proc)
        raise TranscriptionError(f"{name} timed out after {timeout}s") from exc

    log.debug("%s stderr: %s", name, stderr.decode(errors="replace"))
    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise TranscriptionError(
            f"{name} exited {proc.returncode}: {err or '(no stderr)'}"
        )
    return stdout


async def _kill_group(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            log.error("subprocess (pid=%s) ignored SIGKILL", proc.pid)
