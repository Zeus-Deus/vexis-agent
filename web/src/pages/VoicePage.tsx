import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { ModelPicker, ReasoningPicker } from "../components/ModelPicker";
import type {
  PiperVoice,
  VoiceSettings,
  VoiceSettingsUpdate,
} from "../lib/types";

interface VoicePageProps {
  token: string;
  onAuthFail: () => void;
}

const DOWNLOAD_HINT = `mkdir -p ~/.local/share/piper-voices
cd ~/.local/share/piper-voices
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx
wget https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/en_GB-alan-medium.onnx.json`;

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function VoicePage({ token, onAuthFail }: VoicePageProps) {
  const [state, setState] = useState<VoiceSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingMessage, setSavingMessage] = useState<string | null>(null);
  // ``draft`` is the in-flight edit. Edits update it locally; Save
  // flushes through the API. Reset when the server payload comes
  // back so we always show authoritative state after a write.
  const [draft, setDraft] = useState<VoiceSettingsUpdate>({});

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const data = await api.voiceSettings(token, signal);
        setState(data);
        setDraft({});
        setError(null);
      } catch (exc) {
        if (exc instanceof DOMException && exc.name === "AbortError") return;
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [token, onAuthFail],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    refresh(ctrl.signal);
    return () => ctrl.abort();
  }, [refresh]);

  // Resolved effective values — draft overlays state.
  const effectiveEnabled = draft.enabled ?? state?.enabled ?? false;
  const effectiveSttProvider =
    draft.stt?.provider ?? state?.stt.provider ?? "voxtype";
  const effectiveTtsProvider =
    draft.tts?.provider ?? state?.tts.provider ?? "null";
  const effectiveVoicePath =
    draft.tts?.voice_model_path ?? state?.tts.voice_model_path ?? null;
  const effectiveBinary =
    draft.tts?.binary ?? state?.tts.binary ?? null;
  // Empty string is the "use brain default" sentinel — single source
  // of truth between wire format, draft, and the radio list's
  // ``selected`` value.
  const effectiveCallModel =
    draft.call_mode?.model ?? state?.call_mode.model ?? "";
  const effectiveCallReasoning =
    draft.call_mode?.reasoning_level ?? state?.call_mode.reasoning_level ?? "";
  // Reasoning sub-picker only renders when the selected model carries
  // a non-empty ``reasoning_levels`` list. Recomputes when either the
  // model changes (user picks a different one) or the available list
  // changes (refresh fired). Empty list = no reasoning picker.
  const selectedModelEntry = state?.call_mode.available_models.find(
    (m) => m.id === effectiveCallModel,
  );
  const reasoningLevelsForSelected = selectedModelEntry?.reasoning_levels ?? [];

  const dirty =
    Object.keys(draft).length > 0 &&
    JSON.stringify(draft) !== "{}";

  const save = useCallback(async () => {
    if (!dirty) return;
    setSavingMessage("Saving…");
    try {
      const result = await api.voiceSettingsSet(token, draft);
      setState(result);
      setDraft({});
      setSavingMessage("Saved.");
      window.setTimeout(() => setSavingMessage(null), 2000);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
      setSavingMessage(null);
    }
  }, [dirty, draft, token, onAuthFail]);

  if (!state) {
    return (
      <div className="text-sm text-[var(--color-fg-dim)]">
        {error ? `⚠️ ${error}` : "Loading voice settings…"}
      </div>
    );
  }

  return (
    <div className="max-w-2xl space-y-6">
      {error && (
        <div className="rounded-md border border-[var(--color-error)] bg-[var(--color-surface)] px-4 py-2 text-sm text-[var(--color-error)]">
          ⚠️ {error}
        </div>
      )}

      <header>
        <h1 className="font-data text-base text-[var(--color-fg)] mb-1">
          Voice
        </h1>
        <p className="text-sm text-[var(--color-fg-2)]">
          Speech-to-text and text-to-speech for the chat tab. Off by
          default — flip enabled on, choose providers, and a Voice voice
          if you want spoken replies. Changes save to{" "}
          <code className="font-mono text-[var(--color-fg)]">
            ~/.vexis/config.yaml
          </code>{" "}
          and apply on the next chat send (no daemon restart needed).
        </p>
      </header>

      {/* Master enabled toggle */}
      <Section title="Enabled">
        <ToggleRow
          checked={effectiveEnabled}
          onChange={(v) =>
            setDraft((d) => ({ ...d, enabled: v }))
          }
          label={
            effectiveEnabled
              ? "Voice features available"
              : "Voice features disabled"
          }
          hint={
            effectiveEnabled
              ? "Mic button + speaker toggle render in the chat composer."
              : "Mic and speaker affordances are hidden in the chat UI."
          }
        />
      </Section>

      {/* STT */}
      <Section title="Speech to text (voice in)">
        <Dropdown
          value={effectiveSttProvider}
          options={state.stt.available_providers}
          onChange={(v) =>
            setDraft((d) => ({ ...d, stt: { ...d.stt, provider: v } }))
          }
        />
        <p className="text-xs text-[var(--color-fg-dim)] mt-2">
          {effectiveSttProvider === "voxtype" &&
            "voxtype is the local Whisper-based transcriber — already on PATH."}
          {effectiveSttProvider === "null" &&
            "Disabled. The mic button stays hidden."}
        </p>
      </Section>

      {/* TTS */}
      <Section title="Text to speech (voice out)">
        <Dropdown
          value={effectiveTtsProvider}
          options={state.tts.available_providers}
          onChange={(v) =>
            setDraft((d) => ({ ...d, tts: { ...d.tts, provider: v } }))
          }
        />

        {effectiveTtsProvider === "piper" && (
          <div className="mt-4 space-y-3">
            <VoicePicker
              voices={state.available_voices}
              selected={effectiveVoicePath}
              onChange={(path) =>
                setDraft((d) => ({
                  ...d,
                  tts: { ...d.tts, voice_model_path: path },
                }))
              }
            />
            <BinaryField
              value={effectiveBinary}
              onChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  tts: { ...d.tts, binary: v || null },
                }))
              }
            />
          </div>
        )}

        {effectiveTtsProvider === "null" && (
          <p className="text-xs text-[var(--color-fg-dim)] mt-2">
            Disabled. The 🔊 toggle stays hidden — assistant replies are
            text-only.
          </p>
        )}
      </Section>

      {/* Voice call mode — per-feature backend model override */}
      <Section title="Voice call mode — backend model">
        <ModelPicker
          name="voice-call-model"
          description="Per-turn override for voice call mode only — text chat and Telegram keep using your account default. All values pulled live from the brain's discovery; nothing hardcoded."
          available={state.call_mode.available_models}
          selected={effectiveCallModel}
          onChange={(value) =>
            setDraft((d) => ({
              ...d,
              call_mode: {
                ...d.call_mode,
                model: value,
                // Switching model invalidates the reasoning pick —
                // a level valid for opus-4-7 isn't necessarily valid
                // for haiku-4-5. Reset to empty so the user
                // re-selects intentionally; the server's writer also
                // drops orphaned reasoning_level if model is unset.
                reasoning_level: "",
              },
            }))
          }
        />
        {reasoningLevelsForSelected.length > 0 && (
          <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
            <ReasoningPicker
              levels={reasoningLevelsForSelected}
              selected={effectiveCallReasoning}
              onChange={(value) =>
                setDraft((d) => ({
                  ...d,
                  call_mode: { ...d.call_mode, reasoning_level: value },
                }))
              }
            />
          </div>
        )}
      </Section>

      {/* Save bar */}
      <div className="flex items-center gap-3 pt-2 border-t border-[var(--color-border)]">
        <button
          type="button"
          onClick={save}
          disabled={!dirty || savingMessage === "Saving…"}
          className={[
            "rounded-md px-4 py-2 text-xs uppercase tracking-wider font-semibold",
            "bg-[var(--color-accent)] text-[var(--color-accent-fg)]",
            "hover:bg-[var(--color-accent-2)] hover:text-[var(--color-fg)]",
            "transition-colors",
            "disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-[var(--color-accent)]",
          ].join(" ")}
        >
          Save
        </button>
        {savingMessage && (
          <span className="text-xs text-[var(--color-fg-2)]">
            {savingMessage}
          </span>
        )}
        {dirty && !savingMessage && (
          <span className="text-xs text-[var(--color-fg-dim)]">
            Unsaved changes
          </span>
        )}
      </div>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="space-y-2">
      <h2 className="uppercase-tight text-xs text-[var(--color-fg-dim)]">
        {title}
      </h2>
      <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        {children}
      </div>
    </section>
  );
}

function ToggleRow({
  checked,
  onChange,
  label,
  hint,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  label: string;
  hint?: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        onClick={() => onChange(!checked)}
        className={[
          "shrink-0 mt-1 w-10 h-5 rounded-full transition-colors relative",
          checked
            ? "bg-[var(--color-accent)]"
            : "bg-[var(--color-border-strong)]",
        ].join(" ")}
      >
        <span
          className={[
            "absolute top-0.5 w-4 h-4 rounded-full bg-[var(--color-fg)] transition-transform",
            checked ? "left-5" : "left-0.5",
          ].join(" ")}
        />
      </button>
      <div className="min-w-0">
        <div className="text-sm text-[var(--color-fg)]">{label}</div>
        {hint && (
          <div className="text-xs text-[var(--color-fg-dim)] mt-0.5">
            {hint}
          </div>
        )}
      </div>
    </div>
  );
}

function Dropdown({
  value,
  options,
  onChange,
}: {
  value: string;
  options: string[];
  onChange: (v: string) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className={[
        "w-full bg-[var(--color-base)] border border-[var(--color-border-strong)]",
        "rounded px-2 py-1.5 text-sm text-[var(--color-fg)]",
        "focus:outline-none focus:border-[var(--color-accent)]",
      ].join(" ")}
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  );
}

function VoicePicker({
  voices,
  selected,
  onChange,
}: {
  voices: PiperVoice[];
  selected: string | null;
  onChange: (path: string | null) => void;
}) {
  if (voices.length === 0) {
    return (
      <div className="space-y-2">
        <div className="text-xs text-[var(--color-fg-2)]">
          No Piper voice models found. Download a voice first:
        </div>
        <pre className="bg-[var(--color-base)] border border-[var(--color-border)] rounded px-3 py-2 text-[11px] text-[var(--color-fg-2)] font-mono overflow-x-auto whitespace-pre">
{DOWNLOAD_HINT}
        </pre>
        <div className="text-xs text-[var(--color-fg-dim)]">
          en_GB-alan-medium is a JARVIS-leaning British male. Browse the{" "}
          <a
            href="https://rhasspy.github.io/piper-samples/"
            target="_blank"
            rel="noreferrer"
            className="text-[var(--color-accent)] underline-offset-2 hover:underline"
          >
            full sample catalog
          </a>{" "}
          for other options.
        </div>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <div className="text-xs text-[var(--color-fg-dim)]">
        Voice model
      </div>
      <div className="space-y-1">
        {voices.map((v) => (
          <label
            key={v.path}
            className={[
              "flex items-center gap-3 px-3 py-2 rounded-md cursor-pointer",
              "border transition-colors",
              selected === v.path
                ? "border-[var(--color-accent)] bg-[var(--color-base)]"
                : "border-transparent hover:border-[var(--color-border-strong)]",
            ].join(" ")}
          >
            <input
              type="radio"
              name="voice"
              value={v.path}
              checked={selected === v.path}
              onChange={() => onChange(v.path)}
              className="accent-[var(--color-accent)]"
            />
            <div className="flex-1 min-w-0">
              <div className="text-sm text-[var(--color-fg)] truncate">
                {v.name}
              </div>
              <div className="text-[10px] text-[var(--color-fg-dim)] tabular-nums">
                {v.language || "—"} · {formatBytes(v.size)}
                {!v.has_config && " · ⚠ missing .json sidecar"}
              </div>
            </div>
          </label>
        ))}
      </div>
    </div>
  );
}

function BinaryField({
  value,
  onChange,
}: {
  value: string | null;
  onChange: (v: string) => void;
}) {
  return (
    <details className="text-xs">
      <summary className="cursor-pointer text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)] select-none">
        Advanced — piper binary path
      </summary>
      <div className="mt-2 space-y-2">
        <input
          type="text"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value)}
          placeholder="(default: piper from PATH)"
          className={[
            "w-full bg-[var(--color-base)] border border-[var(--color-border-strong)]",
            "rounded px-2 py-1.5 text-xs font-mono text-[var(--color-fg)]",
            "focus:outline-none focus:border-[var(--color-accent)]",
            "placeholder:text-[var(--color-fg-dim)]",
          ].join(" ")}
        />
        <p className="text-[var(--color-fg-dim)] leading-relaxed">
          Pin this when ``piper`` on your PATH points at the wrong
          binary (Arch ships a different ``piper`` for gaming mice
          under <code className="font-mono">/usr/bin/piper</code>).
          For the conda-env install:{" "}
          <code className="font-mono">
            ~/miniconda3/envs/vexis-agent_env/bin/piper
          </code>
          .
        </p>
      </div>
    </details>
  );
}
