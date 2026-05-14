// Shared model + reasoning pickers.
//
// Extracted from VoicePage so the Voice tab's call-mode picker and the
// Computer Use tab share one implementation — a future Claude release
// surfaces in both pickers with zero page edits. The only per-use
// difference is the radio-group ``name`` (two pickers can co-exist on
// one page) and the intro ``description`` copy.

import { useMemo, useState } from "react";
import type { AvailableModel } from "../lib/types";

// Token count → "1.0M", "200K", "64K" etc. Compact so the per-row
// metadata stays scannable.
export function formatTokens(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${m % 1 === 0 ? m.toFixed(0) : m.toFixed(1)}M`;
  }
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

function formatCost(per_million: number | null | undefined): string | null {
  if (per_million == null || !Number.isFinite(per_million)) return null;
  if (per_million === 0) return "0";
  const fmt =
    per_million < 1
      ? per_million.toFixed(2)
      : per_million < 10
        ? per_million.toFixed(1)
        : per_million.toFixed(0);
  return fmt.replace(/\.0$/, "");
}

export function ModelPicker({
  available,
  selected,
  onChange,
  name,
  description,
}: {
  available: AvailableModel[];
  // Empty string = "use brain default"; any other value = explicit
  // model id override.
  selected: string;
  onChange: (value: string) => void;
  // Radio-group name — MUST be unique per picker instance on a page.
  name: string;
  // Intro copy above the search bar. Caller-supplied so each surface
  // explains its own semantics.
  description: React.ReactNode;
}) {
  const [query, setQuery] = useState("");

  // Optional "free models only" filter — only useful for opencode
  // where some models are free. Surfaced when at least one free model
  // exists (claude-code never has any, so the toggle stays hidden).
  const [freeOnly, setFreeOnly] = useState(false);
  const hasFreeModels = useMemo(
    () => available.some((m) => m.free),
    [available],
  );

  // Filter pipeline — case-insensitive substring match against id,
  // display_name, AND provider. Memoised so a 237-model opencode list
  // doesn't refilter on every render.
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
      <p className="text-xs text-[var(--color-fg-2)]">{description}</p>

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
          name={name}
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

      {/* Search bar + free-models filter. */}
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

      {/* Scrollable results. */}
      <div
        className={[
          "space-y-1 max-h-[420px] overflow-y-auto",
          "rounded-md",
          available.length > 6
            ? "border border-[var(--color-border)] p-1"
            : "",
        ].join(" ")}
      >
        {available.length === 0 ? (
          <div className="text-xs text-[var(--color-fg-dim)] px-3 py-2">
            No models discovered yet. The list populates from the active
            brain's discovery API; the Models tab can refresh the cache.
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-xs text-[var(--color-fg-dim)] px-3 py-2">
            No matches for <code className="font-mono">{query}</code>. Clear
            the search or try a substring of the model name.
          </div>
        ) : (
          filtered.map((m) => (
            <ModelRadio
              key={m.id}
              model={m}
              name={name}
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

function ModelRadio({
  model,
  name,
  selected,
  onSelect,
}: {
  model: AvailableModel;
  name: string;
  selected: boolean;
  onSelect: () => void;
}) {
  // Build the per-row metadata strip from whatever discovery surfaced.
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
        name={name}
        value={model.id}
        checked={selected}
        onChange={onSelect}
        className="accent-[var(--color-accent)] mt-0.5"
      />
      <div className="flex-1 min-w-0">
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
          {/* Right-side badge column — Free pinned above provider. */}
          <div className="flex flex-col items-stretch gap-1 shrink-0 min-w-[3.5rem]">
            {model.free && (
              <span
                className={[
                  "text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded",
                  "bg-[var(--color-accent)] text-[var(--color-accent-fg)]",
                  "font-semibold text-center",
                ].join(" ")}
                title="Universally free — no provider key or subscription required"
              >
                Free
              </span>
            )}
            {model.provider && (
              <span
                className={[
                  "text-[9px] uppercase tracking-wider px-1.5 py-0.5 rounded",
                  "bg-[var(--color-surface-2)] text-[var(--color-fg-2)]",
                  "border border-[var(--color-border)] text-center truncate",
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

export function ReasoningPicker({
  levels,
  selected,
  onChange,
}: {
  // Levels exactly as discovery reports them — we don't reorder so the
  // picker always reflects what the model actually exposes.
  levels: string[];
  // Empty string = "use the model's default reasoning".
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
        pick — usually the right call.
      </p>
      <div className="flex flex-wrap gap-2">
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
