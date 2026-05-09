import { useCallback, useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  AvailableModel,
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
        <CallModePicker
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

// Token count → "1.0M", "200K", "64K", "12.5K" etc. Compact form so
// the per-row metadata stays scannable.
function formatTokens(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)}M`;
  }
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

function CallModePicker({
  available,
  selected,
  onChange,
}: {
  available: AvailableModel[];
  // Empty string = "use brain default"; any other value = explicit
  // model id override.
  selected: string;
  onChange: (value: string) => void;
}) {
  const [query, setQuery] = useState("");

  // Optional "free models only" filter — only useful for opencode
  // where 34/237 models are free; surfaced as a small toggle next
  // to the search bar when AT LEAST ONE free model exists in the
  // current available list (claude-code never has any, so the
  // toggle stays hidden there).
  const [freeOnly, setFreeOnly] = useState(false);
  const hasFreeModels = useMemo(
    () => available.some((m) => m.free),
    [available],
  );

  // Filter pipeline — case-insensitive substring match against id,
  // display_name, AND provider so 'openrouter' or 'github-copilot'
  // narrow the list naturally. Memoised so a 237-model opencode
  // list doesn't refilter on every render. Default option is
  // rendered separately above the results so it's never hidden by
  // an aggressive filter or the freeOnly toggle.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return available.filter((m) => {
      if (freeOnly && !m.free) return false;
      if (!q) return true;
      if (m.id.toLowerCase().includes(q)) return true;
      if (m.display_name?.toLowerCase().includes(q)) return true;
      if (m.provider?.toLowerCase().includes(q)) return true;
      return false;
    });
  }, [available, query, freeOnly]);

  return (
    <div className="space-y-3">
      <p className="text-xs text-[var(--color-fg-2)]">
        Per-turn override for voice call mode only — text chat and
        Telegram keep using your account default. All values pulled
        live from the brain's discovery; nothing hardcoded.
      </p>

      {/* Default option pinned above the search — never filtered out. */}
      <label
        className={[
          "flex items-start gap-3 px-3 py-2 rounded-md cursor-pointer",
          "border transition-colors",
          selected === ""
            ? "border-[var(--color-accent)] bg-[var(--color-base)]"
            : "border-transparent hover:border-[var(--color-border-strong)]",
        ].join(" ")}
      >
        <input
          type="radio"
          name="call-model"
          value=""
          checked={selected === ""}
          onChange={() => onChange("")}
          className="accent-[var(--color-accent)] mt-0.5"
        />
        <div className="flex-1 min-w-0">
          <div className="text-sm text-[var(--color-fg)]">Default</div>
          <div className="text-[10px] text-[var(--color-fg-dim)] mt-0.5">
            Use whatever the brain is configured to use globally.
          </div>
        </div>
      </label>

      {/* Search bar + filter chips. Search matches id +
          display_name + provider so typing 'openrouter' narrows
          to that provider on opencode. */}
      {available.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <div className="flex-1 relative">
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder="Search id, display name, or provider…"
                className={[
                  "w-full bg-[var(--color-base)] border border-[var(--color-border-strong)]",
                  "rounded px-2.5 py-1.5 pl-7 text-xs text-[var(--color-fg)]",
                  "placeholder:text-[var(--color-fg-dim)]",
                  "focus:outline-none focus:border-[var(--color-accent)]",
                ].join(" ")}
              />
              <span
                aria-hidden
                className="absolute left-2 top-1/2 -translate-y-1/2 text-[var(--color-fg-dim)] text-[11px]"
              >
                ⌕
              </span>
            </div>
            <span className="text-[10px] text-[var(--color-fg-dim)] tabular-nums shrink-0">
              {filtered.length === available.length
                ? `${available.length} models`
                : `${filtered.length} / ${available.length}`}
            </span>
          </div>
          {hasFreeModels && (
            <label className="flex items-center gap-2 text-xs text-[var(--color-fg-2)] cursor-pointer select-none">
              <input
                type="checkbox"
                checked={freeOnly}
                onChange={(e) => setFreeOnly(e.target.checked)}
                className="accent-[var(--color-accent)]"
              />
              <span>
                Free models only
                <span className="text-[var(--color-fg-dim)] ml-1">
                  ({available.filter((m) => m.free).length})
                </span>
              </span>
            </label>
          )}
        </div>
      )}

      {/* Scrollable results. max-height keeps a 273-entry opencode
          list contained while still leaving room for the reasoning
          picker beneath. */}
      <div
        className={[
          "space-y-1 max-h-[420px] overflow-y-auto",
          "rounded-md",
          // Subtle inset border tells the user "this scrolls".
          available.length > 6
            ? "border border-[var(--color-border)] p-1"
            : "",
        ].join(" ")}
      >
        {available.length === 0 ? (
          <div className="text-xs text-[var(--color-fg-dim)] px-3 py-2">
            No models discovered yet. The list populates from the
            active brain's discovery API; the Models tab can refresh
            the cache.
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-xs text-[var(--color-fg-dim)] px-3 py-2">
            No matches for <code className="font-mono">{query}</code>.
            Clear the search or try a substring of the model name.
          </div>
        ) : (
          filtered.map((m) => (
            <ModelRadio
              key={m.id}
              model={m}
              selected={selected === m.id}
              onSelect={() => onChange(m.id)}
            />
          ))
        )}
      </div>

      {selected !== "" && (
        <button
          type="button"
          onClick={() => onChange("")}
          className={[
            "text-xs px-2 py-1 rounded transition-colors",
            "text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]",
            "hover:bg-[var(--color-base)]",
          ].join(" ")}
        >
          ↺ Reset to default
        </button>
      )}
    </div>
  );
}

function formatCost(per_million: number | null | undefined): string | null {
  if (per_million == null || !Number.isFinite(per_million)) return null;
  if (per_million === 0) return "0";
  // Two decimals for sub-dollar costs ("$0.80"), one decimal for
  // larger ("$15.0"). Trim trailing zero on integers ("$5").
  const fmt =
    per_million < 1 ? per_million.toFixed(2) :
    per_million < 10 ? per_million.toFixed(1) :
    per_million.toFixed(0);
  // Trim trailing ".0" so $5.0 becomes $5
  return fmt.replace(/\.0$/, "");
}

function ModelRadio({
  model,
  selected,
  onSelect,
}: {
  model: AvailableModel;
  selected: boolean;
  onSelect: () => void;
}) {
  // Build the per-row metadata strip from whatever discovery
  // surfaced. Each piece is hidden when its source field is null,
  // so a model with no context window simply shows fewer chips
  // rather than rendering "—".
  const meta: string[] = [];
  if (model.max_input_tokens) {
    meta.push(`${formatTokens(model.max_input_tokens)} context`);
  }
  if (model.max_tokens) {
    meta.push(`${formatTokens(model.max_tokens)} output`);
  }
  if (model.reasoning_levels.length > 0) {
    meta.push(`reasoning: ${model.reasoning_levels.join("/")}`);
  }
  // Cost line — only when at least one side is non-zero (free
  // models get the "Free" badge instead, no need to also say
  // "$0/M in"). opencode-only today.
  const ci = formatCost(model.cost_input_per_million);
  const co = formatCost(model.cost_output_per_million);
  if (!model.free && (ci || co)) {
    const parts: string[] = [];
    if (ci) parts.push(`$${ci}/M in`);
    if (co) parts.push(`$${co}/M out`);
    meta.push(parts.join(" · "));
  }
  return (
    <label
      className={[
        "flex items-start gap-3 px-3 py-2 rounded-md cursor-pointer",
        "border transition-colors",
        selected
          ? "border-[var(--color-accent)] bg-[var(--color-base)]"
          : "border-transparent hover:border-[var(--color-border-strong)]",
      ].join(" ")}
    >
      <input
        type="radio"
        name="call-model"
        value={model.id}
        checked={selected}
        onChange={onSelect}
        className="accent-[var(--color-accent)] mt-0.5"
      />
      <div className="flex-1 min-w-0">
        {/* Display name as the primary label when discovery
            provides one; otherwise the canonical id sits alone.
            Provider + free badges align to the right of the
            heading row. */}
        <div className="flex items-center gap-2 min-w-0">
          <div className="flex-1 min-w-0">
            {model.display_name ? (
              <>
                <div className="text-sm text-[var(--color-fg)] truncate">
                  {model.display_name}
                </div>
                <div className="text-[10px] text-[var(--color-fg-dim)] font-mono truncate">
                  {model.id}
                </div>
              </>
            ) : (
              <div className="text-sm text-[var(--color-fg)] font-mono truncate">
                {model.id}
              </div>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {model.free && (
              <span
                className={[
                  "text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded",
                  "bg-[var(--color-accent)] text-[var(--color-accent-fg)]",
                  "font-semibold",
                ].join(" ")}
                title="No per-token cost on this provider"
              >
                Free
              </span>
            )}
            {model.provider && (
              <span
                className={[
                  "text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded",
                  "bg-[var(--color-surface-2)] text-[var(--color-fg-2)]",
                  "border border-[var(--color-border)]",
                ].join(" ")}
                title={`Routed via ${model.provider}`}
              >
                {model.provider}
              </span>
            )}
          </div>
        </div>
        {meta.length > 0 && (
          <div className="text-[10px] text-[var(--color-fg-dim)] mt-1 tabular-nums">
            {meta.join(" · ")}
          </div>
        )}
      </div>
    </label>
  );
}

function ReasoningPicker({
  levels,
  selected,
  onChange,
}: {
  // Levels exactly as discovery reports them — typically
  // ["low", "medium", "high"] or ["low", "medium", "high", "max"].
  // We don't reorder so the picker always reflects what the model
  // actually exposes.
  levels: string[];
  // Empty string = "use the model's default reasoning" — distinct
  // from any of the named levels.
  selected: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <div className="text-xs uppercase tracking-wider text-[var(--color-fg-dim)]">
        Reasoning effort
      </div>
      <p className="text-[11px] text-[var(--color-fg-dim)]">
        Higher = more thoughtful but slower. Default lets the model
        pick — usually the right call for chat.
      </p>
      <div className="flex flex-wrap gap-2">
        {/* "Default" option first; matches the model picker's pattern. */}
        <ReasoningChip
          label="Default"
          checked={selected === ""}
          onClick={() => onChange("")}
        />
        {levels.map((level) => (
          <ReasoningChip
            key={level}
            label={level}
            checked={selected === level}
            onClick={() => onChange(level)}
          />
        ))}
      </div>
    </div>
  );
}

function ReasoningChip({
  label,
  checked,
  onClick,
}: {
  label: string;
  checked: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={[
        "px-3 py-1.5 rounded-md text-xs transition-colors capitalize",
        "border",
        checked
          ? "border-[var(--color-accent)] bg-[var(--color-accent)]/10 text-[var(--color-fg)]"
          : "border-[var(--color-border-strong)] text-[var(--color-fg-2)] hover:text-[var(--color-fg)] hover:border-[var(--color-accent)]",
      ].join(" ")}
    >
      {label}
    </button>
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
