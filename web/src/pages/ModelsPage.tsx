import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  ModelSubsystemRow,
  ModelTierOverride,
  ModelValidationFinding,
  ModelsState,
} from "../lib/types";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface ModelsPageProps {
  token: string;
  onAuthFail: () => void;
}

// 5s polling — same cadence as GoalsPage. Model config changes
// per-call (subsystem_tier reads from disk on every spawn), so a
// 5s lag between a /model set in Telegram and the dashboard
// updating is well inside what feels live.
const POLL_INTERVAL_MS = 5000;

export function ModelsPage({ token, onAuthFail }: ModelsPageProps) {
  const [state, setState] = useState<ModelsState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.models(token);
      setState(data);
      setError(null);
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [token, onAuthFail]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function loop() {
      if (cancelled) return;
      await refresh();
      if (!cancelled) {
        timer = window.setTimeout(loop, POLL_INTERVAL_MS);
      }
    }
    loop();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refresh]);

  if (error && state === null) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load models: {error}
      </div>
    );
  }
  if (!state) {
    return <ModelsSkeleton />;
  }

  return (
    <div className="space-y-8">
      <Header />
      {error && <ErrorBanner message={error} />}
      <BrainBanner brain={state.brain_kind} inventory={state.brain_inventory} />
      <GlobalFindings findings={state.global_findings} />
      <ResolutionTable rows={state.subsystems} brain={state.brain_kind} />
      <TierOverrides overrides={state.tier_overrides} brain={state.brain_kind} />
      <AvailableModelsHint brain={state.brain_kind} />
    </div>
  );
}


// ---- Header ------------------------------------------------------

function Header() {
  return (
    <div className="space-y-1">
      <h1 className="font-data text-[15px] tracking-tight text-[var(--color-fg)]">
        ⊕ <span className="ml-1">Models</span>
      </h1>
      <p className="text-xs text-[var(--color-fg-dim)] font-data leading-relaxed max-w-[60ch]">
        How each subsystem's auxiliary spawn resolves to a native
        model id under the active brain. Read-only on Day 3 — set
        values via{" "}
        <code className="text-[var(--color-fg-2)]">
          /model set &lt;subsystem&gt; &lt;value&gt;
        </code>{" "}
        in Telegram. Day 4 wires up edit affordances here.
      </p>
    </div>
  );
}

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="hairline px-4 py-3 bg-[var(--color-surface)]">
      <p className="font-data text-[12px] text-[var(--color-error)]">
        <span className="uppercase-tight text-[10px] mr-2">models</span>
        {message}
      </p>
      <p className="mt-1 font-data text-[10.5px] text-[var(--color-fg-dim)]">
        The page will retry automatically every{" "}
        {Math.round(POLL_INTERVAL_MS / 1000)}s.
      </p>
    </div>
  );
}


// ---- Brain banner ------------------------------------------------

function BrainBanner({
  brain,
  inventory,
}: {
  brain: string;
  inventory: string[];
}) {
  return (
    <Section title="Brain">
      <Card>
        <div className="px-4 py-3 flex items-baseline gap-3 font-data text-[12px]">
          <span className="text-[var(--color-fg-dim)] uppercase-tight text-[10px]">
            kind
          </span>
          <span className="text-[var(--color-accent)] text-[14px]">
            {brain}
          </span>
          <span className="text-[var(--color-fg-dim)] ml-auto text-[10px]">
            available: {inventory.join(" · ")}
          </span>
        </div>
        <p className="px-4 pb-3 font-data text-[10.5px] text-[var(--color-fg-dim)]">
          brain.kind is read once at startup. Day 4 adds the
          switcher; Day 5 dogfood gates the flag flip.
        </p>
      </Card>
    </Section>
  );
}


// ---- Global validator findings ----------------------------------

function GlobalFindings({ findings }: { findings: ModelValidationFinding[] }) {
  const non_info = findings.filter((f) => f.severity !== "info");
  if (non_info.length === 0) return null;
  return (
    <Section title="Validator (whole config)">
      <Card>
        <ul className="px-4 py-3 space-y-2 font-data text-[12px]">
          {non_info.map((f, idx) => (
            <FindingRow key={idx} finding={f} />
          ))}
        </ul>
      </Card>
    </Section>
  );
}


// ---- Resolution table -------------------------------------------

function ResolutionTable({
  rows,
  brain,
}: {
  rows: ModelSubsystemRow[];
  brain: string;
}) {
  return (
    <Section
      title="Subsystem resolution"
      trailing={`${rows.length} subsystems on ${brain}`}
    >
      <Card>
        {rows.length === 0 ? (
          <EmptyState glyph="○" title="No subsystems registered." />
        ) : (
          <div className="px-4 py-3 font-data text-[12px]">
            <div
              className="grid items-baseline gap-x-4 text-[10.5px] uppercase-tight text-[var(--color-fg-dim)] pb-2 border-b border-[var(--color-border)]"
              style={{ gridTemplateColumns: "1.5fr 1fr 1fr 0.4fr" }}
            >
              <span>subsystem</span>
              <span>configured</span>
              <span>resolves to</span>
              <span className="text-right">status</span>
            </div>
            {rows.map((row) => (
              <ResolutionRow key={row.name} row={row} />
            ))}
          </div>
        )}
      </Card>
    </Section>
  );
}

function ResolutionRow({ row }: { row: ModelSubsystemRow }) {
  // Pick the highest-severity finding for the status indicator.
  const worst = pickWorst(row.findings);
  return (
    <div
      className="grid items-baseline gap-x-4 py-1.5 border-b border-[var(--color-border)] last:border-b-0"
      style={{ gridTemplateColumns: "1.5fr 1fr 1fr 0.4fr" }}
    >
      <span className="text-[var(--color-fg)]">{row.name}</span>
      <span className="text-[var(--color-fg-2)]">
        {row.configured ?? <Dim>(default)</Dim>}
      </span>
      <span className="text-[var(--color-fg-2)]">
        {row.resolved_model_id ?? <Dim>&lt;brain default&gt;</Dim>}
      </span>
      <span className="text-right">
        <StatusBadge worst={worst} findings={row.findings} />
      </span>
    </div>
  );
}

function StatusBadge({
  worst,
  findings,
}: {
  worst: "error" | "warning" | "info" | null;
  findings: ModelValidationFinding[];
}) {
  if (worst === null) {
    return (
      <span title="OK" className="text-[var(--color-fg-dim)]">
        ✓
      </span>
    );
  }
  // Tooltip carries the problem + suggested_fix copy. Native title
  // attribute — Day 4 may upgrade to a richer popover when edits land.
  const title = findings
    .filter((f) => f.severity === worst)
    .map((f) => `${f.problem}\n→ ${f.suggested_fix}`)
    .join("\n\n");
  const glyph = worst === "error" ? "✗" : worst === "warning" ? "⚠" : "ⓘ";
  const color =
    worst === "error"
      ? "var(--color-error)"
      : worst === "warning"
      ? "var(--color-warn)"
      : "var(--color-fg-dim)";
  return (
    <span title={title} style={{ color }}>
      {glyph}
    </span>
  );
}

function pickWorst(
  findings: ModelValidationFinding[],
): "error" | "warning" | "info" | null {
  if (findings.some((f) => f.severity === "error")) return "error";
  if (findings.some((f) => f.severity === "warning")) return "warning";
  if (findings.some((f) => f.severity === "info")) return "info";
  return null;
}


// ---- Tier overrides (collapsible) -------------------------------

function TierOverrides({
  overrides,
  brain,
}: {
  overrides: Record<string, ModelTierOverride>;
  brain: string;
}) {
  const [open, setOpen] = useState(false);
  const tiers = ["tiny", "small", "medium", "large"];
  const overridden = tiers.filter(
    (t) => overrides[t]?.configured !== null && overrides[t]?.configured !== undefined,
  );
  return (
    <Section
      title="Tier overrides"
      trailing={
        overridden.length === 0
          ? "(none set)"
          : `${overridden.length} overridden`
      }
    >
      <Card>
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
          className="w-full px-4 py-2 text-left font-data text-[11px] uppercase-tight text-[var(--color-fg-dim)] hover:text-[var(--color-fg)] flex items-center gap-2"
        >
          <span>{open ? "▾" : "▸"}</span>
          <span>{open ? "hide" : "show"} per-tier mapping</span>
        </button>
        {open && (
          <div className="px-4 pb-3 font-data text-[12px]">
            <div
              className="grid items-baseline gap-x-4 text-[10.5px] uppercase-tight text-[var(--color-fg-dim)] py-1 border-b border-[var(--color-border)]"
              style={{ gridTemplateColumns: "1fr 1.5fr 1.5fr" }}
            >
              <span>tier</span>
              <span>configured</span>
              <span>{brain} default</span>
            </div>
            {tiers.map((t) => {
              const ov = overrides[t];
              return (
                <div
                  key={t}
                  className="grid items-baseline gap-x-4 py-1 border-b border-[var(--color-border)] last:border-b-0"
                  style={{ gridTemplateColumns: "1fr 1.5fr 1.5fr" }}
                >
                  <span className="text-[var(--color-fg)]">{t}</span>
                  <span className="text-[var(--color-fg-2)]">
                    {ov?.configured ?? <Dim>(default)</Dim>}
                  </span>
                  <span className="text-[var(--color-fg-dim)]">
                    {ov?.default ?? <Dim>—</Dim>}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </Section>
  );
}


// ---- Available models hint --------------------------------------

function AvailableModelsHint({ brain }: { brain: string }) {
  const lines =
    brain === "claude-code" ? CLAUDE_CODE_HINT : brain === "opencode" ? OPENCODE_HINT : NULL_HINT;
  return (
    <Section title="Available models">
      <Card>
        <ul className="px-4 py-3 space-y-1 font-data text-[12px] text-[var(--color-fg-2)]">
          {lines.map((line, idx) => (
            <li key={idx}>{line}</li>
          ))}
        </ul>
        <p className="px-4 pb-3 font-data text-[10.5px] text-[var(--color-fg-dim)]">
          Day 4 wires up live discovery (parses{" "}
          <code className="text-[var(--color-fg-2)]">opencode models</code> for
          opencode; ships a hardcoded curated list for claude-code).
        </p>
      </Card>
    </Section>
  );
}

const CLAUDE_CODE_HINT = [
  "Aliases: sonnet, opus, haiku",
  "Full names: claude-haiku-4-5, claude-sonnet-4-6, claude-opus-4-1",
  "Reference: https://docs.anthropic.com/claude/models",
];
const OPENCODE_HINT = [
  "Format: provider/model (e.g. anthropic/claude-haiku-3-5)",
  "Run `opencode models` in a shell to see the live list (~270 models)",
  "Day 4 will surface the picker here",
];
const NULL_HINT = ["Test fake — no real model spawns. Switch brain.kind to use models."];


// ---- Per-finding row --------------------------------------------

function FindingRow({ finding }: { finding: ModelValidationFinding }) {
  const color =
    finding.severity === "error"
      ? "var(--color-error)"
      : finding.severity === "warning"
      ? "var(--color-warn)"
      : "var(--color-fg-dim)";
  const glyph =
    finding.severity === "error" ? "✗" : finding.severity === "warning" ? "⚠" : "ⓘ";
  return (
    <li className="space-y-1">
      <p style={{ color }}>
        <span className="mr-2">{glyph}</span>
        <span className="uppercase-tight text-[10px] mr-2">
          [{finding.subsystem ?? "global"}]
        </span>
        {finding.problem}
      </p>
      <p className="ml-6 text-[10.5px] text-[var(--color-fg-dim)]">
        → {finding.suggested_fix}
      </p>
    </li>
  );
}


// ---- Misc -------------------------------------------------------

function Dim({ children }: { children: React.ReactNode }) {
  return <span className="text-[var(--color-fg-dim)]">{children}</span>;
}

function ModelsSkeleton() {
  return (
    <div className="space-y-4 font-data text-[12px] text-[var(--color-fg-dim)]">
      <p>Loading models…</p>
    </div>
  );
}
