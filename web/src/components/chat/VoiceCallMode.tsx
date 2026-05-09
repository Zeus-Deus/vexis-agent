/**
 * Full-screen, hands-free voice conversation mode.
 *
 * Tap to enter, tap End Call to leave. Inside, a state machine drives
 * a continuous listen → transcribe → think → speak → listen loop:
 *
 *   listening   ── VAD detects speech ──────────────►  recording
 *   recording   ── speech ends ───────────────────────►  transcribing
 *   transcribing──────────────►  thinking
 *   thinking    ── /chat/voice returns ───────────────►  speaking
 *   speaking    ── audio ends ───────────────────────►  listening
 *   speaking    ── user speaks (barge-in) ────────────►  recording
 *
 * The component owns the state machine, the VAD controller, and the
 * active <audio> element for TTS playback. It does NOT own the
 * conversation history (that lives in ChatPage's per-session
 * buffer); we forward each turn back via ``onTurn`` so the regular
 * chat view shows what was said.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../../lib/api";
import { startVad, type VadController, type VadState } from "../../lib/voiceVad";

interface VoiceCallModeProps {
  token: string;
  /** True while open; the parent fully unmounts the component when
   *  closed so the VAD/audio cleanup runs through the unmount path. */
  open: boolean;
  /** Called with each completed turn so ChatPage can render the
   *  exchange as bubbles. Either side can be null when an error
   *  occurred mid-loop (e.g. transcription returned empty). */
  onTurn: (transcript: string | null, reply: string | null) => void;
  /** End-call: parent should unmount the component. */
  onClose: () => void;
  /** Per-turn model override (sourced from voice.call_mode.model in
   *  ~/.vexis/config.yaml via the voice-info probe). Empty string =
   *  use the brain's account default — same as Telegram and the
   *  text-chat tab. Any other value forwards to /chat/voice as a
   *  multipart ``model`` form field. */
  modelOverride: string;
  /** Per-turn reasoning effort. Empty = no --effort flag.
   *  Symmetric with modelOverride; forwarded as ``reasoning_level``
   *  multipart form field. */
  reasoningOverride: string;
}

const STATE_LABEL: Record<VadState, string> = {
  idle: "Ready",
  listening: "Listening…",
  recording: "Hearing you",
  transcribing: "Transcribing…",
  thinking: "Thinking…",
  speaking: "Speaking",
  error: "Error",
};

export function VoiceCallMode({
  token,
  open,
  onTurn,
  onClose,
  modelOverride,
  reasoningOverride,
}: VoiceCallModeProps) {
  const [state, setState] = useState<VadState>("idle");
  const [error, setError] = useState<string | null>(null);
  /** Mute the user's mic — VAD keeps running but we ignore the
   *  speech-end events. Useful when you want to listen to the
   *  assistant without barge-in. */
  const [micMuted, setMicMuted] = useState(false);
  /** Track whether the assistant's reply is currently being read
   *  aloud, separate from ``state`` so barge-in can cancel cleanly
   *  without racing the state-machine transitions. */
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const vadRef = useRef<VadController | null>(null);
  /** ``micMuted`` lives in state for re-rendering, but the VAD
   *  callbacks fire from outside React's update cycle — so we mirror
   *  it into a ref the callback can read synchronously. */
  const micMutedRef = useRef(false);
  /** AbortController for the in-flight /chat/voice or /chat/tts
   *  request, so barge-in or End-Call cancels promptly. */
  const inflightRef = useRef<AbortController | null>(null);
  /** Stable callback reference so the VAD effect doesn't tear down
   *  on every render. We update via the ref pattern. */
  const onTurnRef = useRef(onTurn);
  useEffect(() => { onTurnRef.current = onTurn; }, [onTurn]);

  useEffect(() => { micMutedRef.current = micMuted; }, [micMuted]);

  // Stop any active TTS playback + abort any in-flight request.
  // Used by barge-in (transition to recording) and End-Call.
  const stopAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      try {
        audio.pause();
        audio.src = "";
      } catch {
        // Audio element may already be detached.
      }
      audioRef.current = null;
    }
    const inflight = inflightRef.current;
    if (inflight) {
      inflight.abort();
      inflightRef.current = null;
    }
  }, []);

  // Process one turn end-to-end: STT → brain → TTS → playback.
  const processTurn = useCallback(
    async (audioBlob: Blob) => {
      setState("transcribing");
      const ctrl = new AbortController();
      inflightRef.current = ctrl;
      try {
        const file = new File([audioBlob], "voice.wav", {
          type: "audio/wav",
        });
        // We can't pass an AbortSignal to chatVoice today (uses
        // XMLHttpRequest in api.ts for upload progress) — accept
        // the trade-off: a fast user can cancel via End-Call, but
        // a barge-in mid-transcribe sees the upload finish before
        // the new recording starts. The window is brief.
        // Forward modelOverride only when set so the server's
        // Form(default=None) falls through cleanly otherwise.
        const { transcript, reply } = await api.chatVoice(token, file, {
          model: modelOverride || undefined,
          reasoning_level: reasoningOverride || undefined,
        });
        onTurnRef.current(transcript, reply);
        // Synthesize and play the reply.
        setState("thinking");
        const ttsBlob = await api.chatTts(token, reply);
        if (!ttsBlob) {
          // Empty TTS = nothing to play; back to listening.
          setState("listening");
          return;
        }
        if (ctrl.signal.aborted) return;
        setState("speaking");
        const url = URL.createObjectURL(ttsBlob);
        const audio = new Audio(url);
        audioRef.current = audio;
        audio.addEventListener("ended", () => {
          URL.revokeObjectURL(url);
          if (audioRef.current === audio) {
            audioRef.current = null;
            setState("listening");
          }
        });
        audio.addEventListener("error", () => {
          URL.revokeObjectURL(url);
          if (audioRef.current === audio) {
            audioRef.current = null;
            setState("listening");
          }
        });
        await audio.play().catch(() => {
          // Autoplay blocked — most likely on iOS Safari before the
          // first user interaction. The component requires a tap to
          // open, so this should be live. If it's not, fall through
          // to listening rather than freeze.
          URL.revokeObjectURL(url);
          if (audioRef.current === audio) {
            audioRef.current = null;
            setState("listening");
          }
        });
      } catch (exc) {
        if (exc instanceof DOMException && exc.name === "AbortError") return;
        if (exc instanceof ApiError) {
          // 422 from /chat/voice = empty transcription. Common —
          // a too-short utterance, background noise. Just go back
          // to listening; don't toast the user.
          if (exc.status === 422) {
            onTurnRef.current(null, null);
            setState("listening");
            return;
          }
        }
        const msg = exc instanceof Error ? exc.message : String(exc);
        setError(msg);
        setState("error");
      } finally {
        if (inflightRef.current === ctrl) inflightRef.current = null;
      }
    },
    [token, modelOverride, reasoningOverride],
  );

  // Bring up VAD when the modal opens, tear it down when it closes
  // OR the component unmounts. Mic permission prompt happens here.
  useEffect(() => {
    if (!open) return;
    let active = true;
    setState("listening");
    setError(null);
    (async () => {
      try {
        const ctrl = await startVad({
          onSpeechStart: () => {
            if (!active) return;
            // While the assistant is speaking, user-speech-onset is
            // barge-in: stop the current playback, abort any pending
            // request, and switch to recording. The onSpeechEnd
            // handler below kicks off the next round.
            if (audioRef.current) stopAudio();
            if (micMutedRef.current) return;
            setState("recording");
          },
          onSpeechEnd: (audioBlob: Blob) => {
            if (!active || micMutedRef.current) return;
            void processTurn(audioBlob);
          },
          onVADMisfire: () => {
            // False positive — we'd flagged speech but it wasn't
            // long enough to count. Quietly return to listening.
            if (active) setState("listening");
          },
          onError: (msg) => {
            if (active) {
              setError(msg);
              setState("error");
            }
          },
        });
        if (!active) {
          // Caller closed before VAD finished initialising — clean up.
          await ctrl.stop();
          return;
        }
        vadRef.current = ctrl;
      } catch {
        // startVad already routed via onError; nothing more to do.
      }
    })();
    return () => {
      active = false;
      stopAudio();
      const ctrl = vadRef.current;
      vadRef.current = null;
      if (ctrl) void ctrl.stop();
    };
  }, [open, processTurn, stopAudio]);

  if (!open) return null;

  return (
    <div
      className={[
        "fixed inset-0 z-50 flex flex-col items-center justify-between",
        "bg-[var(--color-base)]/95 backdrop-blur",
        "text-[var(--color-fg)]",
        // Safe-area top + bottom so the End-Call button doesn't
        // collide with the iOS home indicator.
        "pt-[max(env(safe-area-inset-top),1.5rem)]",
        "pb-[max(env(safe-area-inset-bottom),1.5rem)]",
        "px-6",
      ].join(" ")}
      role="dialog"
      aria-modal="true"
      aria-label="Voice call mode"
    >
      <header className="text-center">
        <div className="text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)]">
          Voice call
        </div>
        <div className="text-sm text-[var(--color-fg-2)] mt-1">
          {error ?? "Speak naturally — I'll listen, reply, and keep going."}
        </div>
      </header>

      <Orb state={state} />

      <div className="text-center">
        <div className="font-data text-base text-[var(--color-fg)] tabular-nums">
          {STATE_LABEL[state]}
        </div>
        <div className="text-[11px] text-[var(--color-fg-dim)] mt-1">
          {error
            ? "Tap End Call and try again."
            : state === "speaking"
            ? "Just start talking to interrupt."
            : "I'll detect when you're done speaking."}
        </div>
      </div>

      <div className="flex items-center gap-4">
        <button
          type="button"
          onClick={() => setMicMuted((m) => !m)}
          aria-label={micMuted ? "Unmute microphone" : "Mute microphone"}
          className={[
            "w-14 h-14 rounded-full flex items-center justify-center",
            "border-2 transition-colors text-xl",
            micMuted
              ? "bg-[var(--color-error)] border-[var(--color-error)] text-[var(--color-fg)]"
              : "bg-[var(--color-surface)] border-[var(--color-border-strong)] text-[var(--color-fg)] hover:border-[var(--color-accent)]",
          ].join(" ")}
        >
          <span aria-hidden>{micMuted ? "🚫" : "🎤"}</span>
        </button>
        <button
          type="button"
          onClick={() => {
            stopAudio();
            onClose();
          }}
          aria-label="End call"
          className={[
            "h-14 px-8 rounded-full flex items-center justify-center gap-2",
            "bg-[var(--color-error)] text-[var(--color-fg)] font-semibold",
            "hover:opacity-90 transition-opacity",
          ].join(" ")}
        >
          <span aria-hidden>📞</span>
          End Call
        </button>
      </div>
    </div>
  );
}

/**
 * The pulsing orb — visual proxy for VAD state. Pure CSS so we don't
 * pull in a canvas / animation library for a single shape.
 *
 * - listening  → soft pulse, accent color
 * - recording  → faster pulse, red ring
 * - speaking   → bright glow with breath animation
 * - thinking   → spinner-ish ring
 * - error/idle → static
 */
function Orb({ state }: { state: VadState }) {
  const baseRing =
    "w-44 h-44 rounded-full flex items-center justify-center transition-all duration-300";
  const innerCore =
    "w-24 h-24 rounded-full transition-all duration-300";

  const tones: Record<VadState, { ring: string; core: string; pulse?: string }> = {
    idle: {
      ring: "border-2 border-[var(--color-border-strong)]",
      core: "bg-[var(--color-surface-2)]",
    },
    listening: {
      ring: "border-2 border-[var(--color-accent)]/60",
      core: "bg-[var(--color-accent)]/40",
      pulse: "animate-pulse",
    },
    recording: {
      ring: "border-4 border-[var(--color-error)]",
      core: "bg-[var(--color-error)]",
      pulse: "animate-pulse",
    },
    transcribing: {
      ring: "border-2 border-[var(--color-accent-2)]",
      core: "bg-[var(--color-accent-2)]/60",
      pulse: "animate-pulse",
    },
    thinking: {
      ring: "border-2 border-[var(--color-accent)] border-dashed animate-spin",
      core: "bg-[var(--color-surface-2)]",
    },
    speaking: {
      ring: "border-4 border-[var(--color-accent)] shadow-[0_0_64px_var(--color-accent-2)]",
      core: "bg-[var(--color-accent)]",
      pulse: "animate-pulse",
    },
    error: {
      ring: "border-2 border-[var(--color-error)]",
      core: "bg-[var(--color-error)]/60",
    },
  };

  const t = tones[state];
  return (
    <div className={[baseRing, t.ring, t.pulse ?? ""].join(" ")}>
      <div className={[innerCore, t.core].join(" ")} />
    </div>
  );
}
