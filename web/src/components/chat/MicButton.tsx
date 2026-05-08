import { useCallback, useEffect, useRef, useState } from "react";

interface MicButtonProps {
  // Disabled while a previous send/voice request is round-tripping.
  // Recording the next clip while the brain replies is technically
  // fine, but queuing two voice messages without seeing the first
  // reply is confusing UX — block until idle.
  disabled: boolean;
  // Called with the recorded audio Blob when the user releases the
  // button. The parent owns the upload + transcript-injection logic
  // (ChatPage hands it to api.chatVoice).
  onRecordingComplete: (audio: Blob) => void;
  // Surfaced to the parent so it can show an inline error (mic
  // permission denied, no media-devices API on this browser).
  onError: (message: string) => void;
}

// MediaRecorder's preferred mime priority. Browsers vary:
// - Chrome/Edge: audio/webm;codecs=opus is best supported
// - Firefox: audio/ogg;codecs=opus
// - Safari (iOS 14+): audio/mp4 (AAC), no opus support
// We try opus variants first because they round-trip cleanly through
// ffmpeg → voxtype's whisper pipeline. Safari falls back to mp4.
const MIME_CANDIDATES = [
  "audio/webm;codecs=opus",
  "audio/webm",
  "audio/ogg;codecs=opus",
  "audio/ogg",
  "audio/mp4",
];

function pickMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined;
  for (const m of MIME_CANDIDATES) {
    if (MediaRecorder.isTypeSupported(m)) return m;
  }
  return undefined;
}

export function MicButton({
  disabled,
  onRecordingComplete,
  onError,
}: MicButtonProps) {
  const [recording, setRecording] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef<number>(0);
  const elapsedTimerRef = useRef<number | null>(null);

  // Cleanup on unmount — release the mic stream so the OS-level
  // recording indicator clears even if the user navigates away
  // mid-record.
  useEffect(() => {
    return () => {
      streamRef.current?.getTracks().forEach((t) => t.stop());
      if (elapsedTimerRef.current !== null) {
        window.clearInterval(elapsedTimerRef.current);
      }
    };
  }, []);

  const startRecording = useCallback(async () => {
    if (disabled || recording) return;
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      onError("Microphone API not available in this browser.");
      return;
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      onError(
        e instanceof Error && e.name === "NotAllowedError"
          ? "Microphone permission denied."
          : "Could not access microphone.",
      );
      return;
    }
    streamRef.current = stream;
    const mimeType = pickMimeType();
    let recorder: MediaRecorder;
    try {
      recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
    } catch (e) {
      stream.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      onError("MediaRecorder failed to initialise.");
      return;
    }
    recorderRef.current = recorder;
    chunksRef.current = [];

    recorder.ondataavailable = (ev) => {
      if (ev.data && ev.data.size > 0) chunksRef.current.push(ev.data);
    };
    recorder.onstop = () => {
      // Stream is no longer needed — release immediately so the
      // mic indicator clears in the browser's tab UI.
      streamRef.current?.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
      const blob = new Blob(chunksRef.current, {
        type: recorder.mimeType || "audio/webm",
      });
      chunksRef.current = [];
      // Below ~250ms of audio is almost always a misclick. Still
      // upload — server-side voxtype/whisper will return empty and
      // the route surfaces 422 which we can show inline.
      if (blob.size > 0) onRecordingComplete(blob);
    };

    recorder.start();
    startedAtRef.current = Date.now();
    setRecording(true);
    setElapsed(0);
    elapsedTimerRef.current = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startedAtRef.current) / 1000));
    }, 250);
  }, [disabled, recording, onError, onRecordingComplete]);

  const stopRecording = useCallback(() => {
    if (!recording) return;
    setRecording(false);
    if (elapsedTimerRef.current !== null) {
      window.clearInterval(elapsedTimerRef.current);
      elapsedTimerRef.current = null;
    }
    const recorder = recorderRef.current;
    recorderRef.current = null;
    if (recorder && recorder.state !== "inactive") {
      try {
        recorder.stop();
      } catch {
        // already stopped
      }
    }
  }, [recording]);

  // Pointer events handle mouse, touch, and pen uniformly. We listen
  // on the button itself for down and on window for up — that way a
  // user who slides off the button while recording still gets a clean
  // stop instead of a stuck mic. We also stop on tab-hide and
  // pagehide: phones suspend backgrounded tabs aggressively and an
  // already-stopped recorder reads better than a half-recorded blob
  // sitting in memory until resume.
  useEffect(() => {
    if (!recording) return;
    const handleUp = () => stopRecording();
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") stopRecording();
    };
    window.addEventListener("pointerup", handleUp);
    window.addEventListener("pointercancel", handleUp);
    window.addEventListener("pagehide", handleUp);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.removeEventListener("pointerup", handleUp);
      window.removeEventListener("pointercancel", handleUp);
      window.removeEventListener("pagehide", handleUp);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [recording, stopRecording]);

  return (
    <button
      type="button"
      onPointerDown={(e) => {
        // Prevent the textarea from gaining focus on a touch tap so
        // the soft keyboard doesn't pop up while recording.
        e.preventDefault();
        startRecording();
      }}
      disabled={disabled}
      aria-label={recording ? `Recording ${elapsed}s — release to send` : "Hold to record voice message"}
      title={recording ? `Recording ${elapsed}s` : "Hold to record"}
      className={[
        "shrink-0 rounded-md flex items-center justify-center",
        // Match send-button sizing: comfortable touch target on mobile.
        "px-3 py-2.5 md:px-2.5 md:py-1.5",
        "transition-colors select-none",
        recording
          ? "bg-[var(--color-error)] text-[var(--color-fg)]"
          : [
              "border border-[var(--color-border-strong)]",
              "text-[var(--color-fg-2)] hover:text-[var(--color-fg)]",
              "hover:border-[var(--color-accent)] hover:bg-[var(--color-base)]",
            ].join(" "),
        "disabled:opacity-40 disabled:cursor-not-allowed",
      ].join(" ")}
    >
      {recording ? (
        <span className="flex items-center gap-1.5">
          {/* Pulsing dot to read as "live recording" — tabular-nums
              on the timer keeps width stable as digits change. */}
          <span className="w-2 h-2 rounded-full bg-current animate-pulse" />
          <span className="text-xs font-mono tabular-nums">{elapsed}s</span>
        </span>
      ) : (
        // Microphone glyph (U+1F3A4). One char keeps the bundle thin
        // and reads at any size unlike a tiny SVG icon.
        <span aria-hidden className="text-base leading-none">🎤</span>
      )}
    </button>
  );
}
