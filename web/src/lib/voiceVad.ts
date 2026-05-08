/**
 * Voice Activity Detection wrapper.
 *
 * Thin layer over @ricky0123/vad-web's MicVAD that:
 *   - centralises asset paths (worklet + onnx + ort wasm) so the rest
 *     of the app doesn't import package-internal constants;
 *   - exposes a stop() that fully tears down the AudioContext +
 *     MediaStream so backgrounding the tab releases the mic;
 *   - encodes the Float32Array audio frames into a 16 kHz mono WAV
 *     Blob ready for upload to /api/v1/chat/voice (the server's
 *     ffmpeg pipeline can consume it without an extra conversion).
 *
 * Why we self-host the assets: the daemon runs on a tailnet behind
 * 127.0.0.1 — there's no guarantee of internet egress. Default
 * @ricky0123/vad-web fetches its worklet + onnx from jsdelivr; we
 * bundle them into web/public/{vad,ort}/ at build time so call mode
 * works fully offline once the page has loaded.
 */

import { MicVAD, utils as vadUtils } from "@ricky0123/vad-web";
import * as ort from "onnxruntime-web";

// Point onnxruntime-web at the wasm we shipped in web/public/ort/.
// Setting this on the imported module mutates a singleton shared
// across the whole page, so calling once at import time is fine.
ort.env.wasm.wasmPaths = "/ort/";

export type VadState =
  | "idle"
  | "listening"
  | "recording"
  | "transcribing"
  | "thinking"
  | "speaking"
  | "error";

export interface VadController {
  /** Stop the worklet, release the mic, dispose the audio context. */
  stop: () => Promise<void>;
  /** Pause speech detection without releasing the mic. Useful while
   *  the assistant's TTS audio is playing AND the caller wants to
   *  suppress barge-in (default mode keeps detection running so a
   *  user interruption pauses playback). */
  pause: () => void;
  /** Resume detection after pause(). */
  resume: () => void;
}

export interface VadCallbacks {
  /** Speech onset — VAD became confident user is talking. */
  onSpeechStart: () => void;
  /** Speech ended — receives the captured audio as a Blob (audio/wav,
   *  16 kHz mono PCM). The caller posts this to /api/v1/chat/voice. */
  onSpeechEnd: (audioBlob: Blob) => void;
  /** A frame failed VAD's "is this speech" threshold AFTER onSpeechStart
   *  fired — i.e. a false positive. Useful for UI feedback ("waiting
   *  for you to speak again"). Optional; library calls it sparingly. */
  onVADMisfire?: () => void;
  /** Surfaced when the worklet/model fails to initialise OR the user
   *  denied mic permission. The voice-mode UI shows this inline. */
  onError?: (message: string) => void;
}

/** Encode the Float32 PCM samples MicVAD emits into a 16 kHz mono
 *  WAV Blob. The library ships an ``encodeWAV`` util that does
 *  exactly this; we wrap it to return a Blob (the raw util returns
 *  an ArrayBuffer). */
function pcmToWavBlob(samples: Float32Array): Blob {
  const wavBuffer = vadUtils.encodeWAV(samples);
  return new Blob([wavBuffer], { type: "audio/wav" });
}

/**
 * Initialise VAD on the user's mic. Returns a controller for stopping
 * cleanly. Throws when the MicVAD constructor fails (mic denied,
 * worklet asset 404, etc.); callers should wrap in try/catch and
 * route to ``onError``.
 */
export async function startVad(
  callbacks: VadCallbacks,
): Promise<VadController> {
  let mic: MicVAD;
  try {
    mic = await MicVAD.new({
      // Asset paths — match the daemon's static mounts.
      baseAssetPath: "/vad/",
      onnxWASMBasePath: "/ort/",
      // VAD model — v5 is the default but we pin so a future lib
      // upgrade doesn't silently change behaviour.
      model: "v5",
      // Threshold tuning — Silero's defaults are tuned for clean
      // speech. We lower positive threshold a touch so a quiet
      // voice still triggers on a phone mic, raise negative
      // threshold so room noise doesn't keep the recording open.
      // These are empirical; revisit if mis-fires become a
      // problem in practice.
      positiveSpeechThreshold: 0.45,
      negativeSpeechThreshold: 0.30,
      // Padding so we don't clip the first/last syllable. The
      // library accepts both *Frames and *Ms variants; we use ms
      // for human-readable tuning. 1 frame ~= 32 ms at 16 kHz.
      preSpeechPadMs: 256,
      redemptionMs: 384,
      // Reject too-short utterances entirely (rejects coughs etc.).
      minSpeechMs: 250,
      onSpeechStart: () => callbacks.onSpeechStart(),
      onSpeechEnd: (audio: Float32Array) => {
        callbacks.onSpeechEnd(pcmToWavBlob(audio));
      },
      onVADMisfire: () => callbacks.onVADMisfire?.(),
    });
  } catch (e) {
    const msg =
      e instanceof Error
        ? e.name === "NotAllowedError"
          ? "Microphone permission denied."
          : `VAD init failed: ${e.message}`
        : "VAD init failed.";
    callbacks.onError?.(msg);
    throw e;
  }

  mic.start();

  return {
    stop: async () => {
      try {
        // ``destroy`` releases the worklet + AudioContext + MediaStream.
        // Idempotent; safe to call from useEffect cleanup.
        await mic.destroy();
      } catch {
        // Already torn down — the library throws on double-destroy
        // in some versions. Ignore.
      }
    },
    pause: () => mic.pause(),
    resume: () => mic.start(),
  };
}
