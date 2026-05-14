import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { ModelPicker, ReasoningPicker } from "../components/ModelPicker";
import type {
  ComputerUseActivity,
  ComputerUseSettings,
  ComputerUseSettingsUpdate,
} from "../lib/types";

interface ComputerUsePageProps {
  token: string;
  onAuthFail: () => void;
}

export function ComputerUsePage({ token, onAuthFail }: ComputerUsePageProps) {
  const [state, setState] = useState<ComputerUseSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingMessage, setSavingMessage] = useState<string | null>(null);
  // ``draft`` is the in-flight edit. Reset when the server payload
  // comes back so we always show authoritative state after a write.
  const [draft, setDraft] = useState<ComputerUseSettingsUpdate>({});

  const refresh = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const data = await api.computerUseSettings(token, signal);
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

  // Resolved effective values — draft overlays server state.
  const effectiveModel = draft.model ?? state?.model ?? "";
  const effectiveReasoning =
    draft.reasoning_level ?? state?.reasoning_level ?? "";
  const effectiveDynEnabled =
    draft.dynamic?.enabled ?? state?.dynamic.enabled ?? false;
  const effectiveDynModel =
    draft.dynamic?.model ?? state?.dynamic.model ?? "";
  const effectiveDynReasoning =
    draft.dynamic?.reasoning_level ?? state?.dynamic.reasoning_level ?? "";
  const effectiveMinElements =
    draft.dynamic?.min_elements ?? state?.dynamic.min_elements ?? 5;

  // Reasoning sub-pickers only render when the selected model carries
  // a non-empty ``reasoning_levels`` list.
  const models = state?.available_models ?? [];
  const reasoningForModel = (id: string) =>
    models.find((m) => m.id === id)?.reasoning_levels ?? [];

  const dirty = Object.keys(draft).length > 0;

  const save = useCallback(async () => {
    if (!dirty) return;
    setSavingMessage("Saving…");
    try {
      const result = await api.computerUseSettingsSet(token, draft);
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
        {error ? `⚠️ ${error}` : "Loading computer-use settings…"}
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
          Computer Use
        </h1>
        <p className="text-sm text-[var(--color-fg-2)] leading-relaxed">
          Per-feature model selection for desktop computer-use turns —
          clicking buttons, reading windows, driving native apps. Off by
          default: every turn keeps using your brain's account default
          until you opt in here. Settings save to{" "}
          <code className="font-mono text-[var(--color-fg)]">
            ~/.vexis/config.yaml
          </code>{" "}
          and apply on the next turn — no daemon restart.
        </p>
        <p className="text-xs text-[var(--color-fg-dim)] leading-relaxed mt-2">
          These overrides only bite when a turn is{" "}
          <em>actually doing computer-use work</em> — gated on a recent{" "}
          <code className="font-mono">vexis-ui</code> snapshot. Plain
          Telegram and text chat are never affected.
        </p>
      </header>

      {/* ── Pinned model ─────────────────────────────────────────── */}
      <Section title="Computer-use model">
        <ModelPicker
          name="cu-model"
          description="The model used for computer-use turns. Default keeps the brain's account model — pick a specific one to, e.g., run desktop work on a cheaper or faster model than your chat default."
          available={models}
          selected={effectiveModel}
          onChange={(value) =>
            setDraft((d) => ({
              ...d,
              model: value,
              // Switching model invalidates the reasoning pick.
              reasoning_level: "",
            }))
          }
        />
        {reasoningForModel(effectiveModel).length > 0 && (
          <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
            <ReasoningPicker
              levels={reasoningForModel(effectiveModel)}
              selected={effectiveReasoning}
              onChange={(value) =>
                setDraft((d) => ({ ...d, reasoning_level: value }))
              }
            />
          </div>
        )}
      </Section>

      {/* ── Dynamic switching ────────────────────────────────────── */}
      <Section title="Dynamic model switching">
        <ToggleRow
          checked={effectiveDynEnabled}
          onChange={(v) =>
            setDraft((d) => ({
              ...d,
              dynamic: { ...d.dynamic, enabled: v },
            }))
          }
          label={
            effectiveDynEnabled
              ? "Dynamic switching on"
              : "Dynamic switching off"
          }
          hint="When the accessibility tree is rich enough to describe the whole interface in text, no screenshot is needed — so the turn can run on a faster model. When a screenshot fallback was used, it falls back to the pinned model above."
        />

        {effectiveDynEnabled && (
          <div className="mt-4 space-y-4">
            <ThresholdField
              value={effectiveMinElements}
              onChange={(v) =>
                setDraft((d) => ({
                  ...d,
                  dynamic: { ...d.dynamic, min_elements: v },
                }))
              }
            />
            <div className="pt-4 border-t border-[var(--color-border)]">
              <ModelPicker
                name="cu-dynamic-model"
                description="The fast model used when the last snapshot was a rich accessibility tree. Default falls through to the pinned model above (then the brain default)."
                available={models}
                selected={effectiveDynModel}
                onChange={(value) =>
                  setDraft((d) => ({
                    ...d,
                    dynamic: {
                      ...d.dynamic,
                      model: value,
                      reasoning_level: "",
                    },
                  }))
                }
              />
              {reasoningForModel(effectiveDynModel).length > 0 && (
                <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
                  <ReasoningPicker
                    levels={reasoningForModel(effectiveDynModel)}
                    selected={effectiveDynReasoning}
                    onChange={(value) =>
                      setDraft((d) => ({
                        ...d,
                        dynamic: {
                          ...d.dynamic,
                          reasoning_level: value,
                        },
                      }))
                    }
                  />
                </div>
              )}
            </div>
          </div>
        )}
      </Section>

      {/* ── Live activity readout ────────────────────────────────── */}
      <Section title="Live activity">
        <ActivityReadout activity={state.last_activity} />
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
        aria-label={label}
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
          <div className="text-xs text-[var(--color-fg-dim)] mt-0.5 leading-relaxed">
            {hint}
          </div>
        )}
      </div>
    </div>
  );
}

function ThresholdField({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-1.5">
      <label
        htmlFor="cu-min-elements"
        className="text-xs uppercase tracking-wider text-[var(--color-fg-dim)] block"
      >
        Richness threshold
      </label>
      <div className="flex items-center gap-3 flex-wrap">
        <input
          id="cu-min-elements"
          type="number"
          min={1}
          value={value}
          onChange={(e) => {
            const n = parseInt(e.target.value, 10);
            if (Number.isFinite(n) && n >= 1) onChange(n);
          }}
          className={[
            "w-20 bg-[var(--color-base)] border border-[var(--color-border-strong)]",
            "rounded px-2 py-1.5 text-sm tabular-nums text-[var(--color-fg)]",
            "focus:outline-none focus:border-[var(--color-accent)]",
          ].join(" ")}
        />
        <span className="text-xs text-[var(--color-fg-dim)] leading-relaxed flex-1 min-w-[12rem]">
          Indexed widgets a snapshot must expose before it counts as
          "rich enough to skip vision". Higher = more conservative
          (the fast model kicks in less often).
        </span>
      </div>
    </div>
  );
}

function ActivityReadout({
  activity,
}: {
  activity: ComputerUseActivity | null;
}) {
  if (!activity) {
    return (
      <p className="text-xs text-[var(--color-fg-dim)] leading-relaxed">
        No <code className="font-mono">vexis-ui</code> snapshot recorded
        yet. The first time Vexis snapshots a desktop window during a
        turn, its shape shows up here.
      </p>
    );
  }
  const age =
    activity.age_seconds == null
      ? "—"
      : activity.age_seconds < 60
        ? `${Math.round(activity.age_seconds)}s ago`
        : `${Math.round(activity.age_seconds / 60)}m ago`;
  // Plain-language verdict for the headline strip.
  const verdict = !activity.fresh
    ? { text: "Stale — no recent computer-use activity", tone: "dim" }
    : activity.used_vision_fallback
      ? { text: "Screenshot fallback — pinned model applies", tone: "warn" }
      : activity.rich
        ? { text: "Rich tree — dynamic model would apply", tone: "ok" }
        : { text: "Sparse tree — pinned model applies", tone: "warn" };
  const toneClass =
    verdict.tone === "ok"
      ? "text-[var(--color-accent)]"
      : verdict.tone === "warn"
        ? "text-[var(--color-fg-2)]"
        : "text-[var(--color-fg-dim)]";
  return (
    <div className="space-y-3">
      <div className={`text-sm font-medium ${toneClass}`}>{verdict.text}</div>
      <dl className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 text-xs">
        <Stat label="Indexed elements" value={String(activity.element_count ?? "—")} />
        <Stat label="Last snapshot" value={age} />
        <Stat
          label="Mode"
          value={activity.used_vision_fallback ? "vision (screenshot)" : "AT-SPI tree"}
        />
        <Stat
          label="Stale tree"
          value={activity.stale ? "yes" : "no"}
        />
      </dl>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-[var(--color-border)] pb-1">
      <dt className="text-[var(--color-fg-dim)]">{label}</dt>
      <dd className="text-[var(--color-fg)] tabular-nums">{value}</dd>
    </div>
  );
}
