import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  ModelSubsystemRow,
  ModelTierOverride,
  ModelValidationFinding,
  ModelsState,
} from "../lib/types";
import { Card, Section } from "../components/Card";
import { Button } from "../components/Button";
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

// Day 4 surfaces a brain-switch confirm modal and a
// comment-preservation modal. Modal state is local; the page
// remounts cleanly on close.
type ModalState =
  | { kind: "none" }
  | { kind: "brain"; targetKind: string }
  | { kind: "comment-confirm"; pendingAction: () => Promise<void> };

export function ModelsPage({ token, onAuthFail }: ModelsPageProps) {
  const [state, setState] = useState<ModelsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [modal, setModal] = useState<ModalState>({ kind: "none" });
  // Race guard: when a mutation is in flight, the 5s poll must
  // not clobber the optimistic state. Increment on POST start,
  // decrement on POST end. Refresh skips when count > 0.
  const pendingCountRef = useRef(0);
  // Pending discovery refresh state for the button's spinner.
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    if (pendingCountRef.current > 0) return;
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

  // Auto-clear the toast after 5 s so it doesn't linger.
  useEffect(() => {
    if (toast === null) return;
    const t = window.setTimeout(() => setToast(null), 5000);
    return () => window.clearTimeout(t);
  }, [toast]);

  const runMutation = useCallback(
    async (label: string, fn: () => Promise<{ backup_path?: string | null }>) => {
      pendingCountRef.current += 1;
      let result: { backup_path?: string | null } | null = null;
      let error: unknown = null;
      try {
        result = await fn();
      } catch (exc) {
        error = exc;
      }
      // Decrement BEFORE the converge-or-revert refresh so the
      // refresh's own pending-count guard doesn't early-bail. The
      // guard's job is to protect the 5s polling loop from
      // clobbering an in-flight mutation; the explicit
      // refresh-after-mutation here IS the converge step and must
      // always run.
      pendingCountRef.current -= 1;

      if (error instanceof ApiError && error.status === 401) {
        onAuthFail();
        return;
      }
      // Refresh in both branches: success → canonicalises any
      // server-side resolution updates; failure → reverts the
      // optimistic dropdown state to the server's view.
      await refresh();
      if (error) {
        setToast(
          `✗ ${error instanceof Error ? error.message : String(error)}`,
        );
      } else {
        const bak = result?.backup_path
          ? ` (backed up to ${result.backup_path})`
          : "";
        setToast(`✓ ${label}${bak}`);
      }
    },
    [refresh, onAuthFail],
  );

  const onSetSubsystem = useCallback(
    (subsystem: string, value: string) => {
      // Optimistic state swap so the dropdown shows the new
      // value immediately. The runMutation->refresh cycle
      // converges within the round-trip.
      setState((s) => {
        if (s === null) return s;
        return {
          ...s,
          subsystems: s.subsystems.map((row) =>
            row.name === subsystem ? { ...row, configured: value } : row,
          ),
        };
      });
      const action = () =>
        runMutation(
          `set ${subsystem} → ${value}`,
          () => api.setModel(token, { subsystem, value }),
        );
      // Comment-preservation gate: only fire the modal when the
      // current config has comments AND we haven't already run
      // a mutation in this session (the on-disk has_comments
      // self-manages — after the first edit comments are gone).
      if (state?.has_comments) {
        setModal({ kind: "comment-confirm", pendingAction: action });
      } else {
        void action();
      }
    },
    [token, runMutation, state?.has_comments],
  );

  const onResetSubsystem = useCallback(
    (subsystem: string) => {
      const action = () =>
        runMutation(
          `reset ${subsystem}`,
          () => api.resetModel(token, { subsystem }),
        );
      if (state?.has_comments) {
        setModal({ kind: "comment-confirm", pendingAction: action });
      } else {
        void action();
      }
    },
    [token, runMutation, state?.has_comments],
  );

  const onSwitchBrain = useCallback(
    (targetKind: string) => {
      setModal({ kind: "brain", targetKind });
    },
    [],
  );

  const onConfirmBrain = useCallback(
    async (kind: string) => {
      setModal({ kind: "none" });
      await runMutation(
        `brain → ${kind} (restart required)`,
        () => api.setBrain(token, { kind }),
      );
    },
    [token, runMutation],
  );

  const onConfirmComment = useCallback(
    async (action: () => Promise<void>) => {
      setModal({ kind: "none" });
      await action();
    },
    [],
  );

  const onRefreshDiscovery = useCallback(async () => {
    setRefreshing(true);
    try {
      await api.refreshModelDiscovery(token);
      await refresh();
      setToast("✓ Discovery refreshed");
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setToast(`✗ Refresh failed: ${exc instanceof Error ? exc.message : String(exc)}`);
    } finally {
      setRefreshing(false);
    }
  }, [token, refresh, onAuthFail]);

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
      {!state.model_ux_enabled && <DisabledBanner />}
      {error && <ErrorBanner message={error} />}
      {toast && <ToastBanner message={toast} onClose={() => setToast(null)} />}
      <BrainBanner
        brain={state.brain_kind}
        inventory={state.brain_inventory}
        modelUxEnabled={state.model_ux_enabled}
        onSwitch={onSwitchBrain}
      />
      <GlobalFindings findings={state.global_findings} />
      <ResolutionTable
        rows={state.subsystems}
        brain={state.brain_kind}
        availableModels={state.available_models[state.brain_kind] ?? []}
        editable={state.model_ux_enabled}
        onSet={onSetSubsystem}
        onReset={onResetSubsystem}
      />
      <TierOverrides overrides={state.tier_overrides} brain={state.brain_kind} />
      <AvailableModelsHint
        brain={state.brain_kind}
        availableModels={state.available_models[state.brain_kind] ?? []}
        onRefresh={onRefreshDiscovery}
        refreshing={refreshing}
      />
      {modal.kind === "brain" && (
        <BrainSwitchModal
          token={token}
          targetKind={modal.targetKind}
          currentKind={state.brain_kind}
          onConfirm={onConfirmBrain}
          onCancel={() => setModal({ kind: "none" })}
        />
      )}
      {modal.kind === "comment-confirm" && (
        <CommentConfirmModal
          onConfirm={() => onConfirmComment(modal.pendingAction)}
          onCancel={() => setModal({ kind: "none" })}
        />
      )}
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
        model id under the active brain. Edit per-subsystem
        assignments inline; the validator runs pre-write and
        refuses error-severity changes with the suggested fix.
      </p>
    </div>
  );
}

function DisabledBanner() {
  return (
    <div className="hairline px-4 py-3 bg-[var(--color-surface)]">
      <p className="font-data text-[12px] text-[var(--color-warn)]">
        <span className="uppercase-tight text-[10px] mr-2">disabled</span>
        Edit affordances are off. Set{" "}
        <code className="text-[var(--color-fg-2)]">model_ux.enabled: true</code>{" "}
        in <code className="text-[var(--color-fg-2)]">~/.vexis/config.yaml</code>{" "}
        and restart vexis to enable.
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

function ToastBanner({
  message,
  onClose,
}: {
  message: string;
  onClose: () => void;
}) {
  const isError = message.startsWith("✗");
  return (
    <div
      className="hairline px-4 py-2 bg-[var(--color-surface)] flex items-center gap-3"
      role="status"
    >
      <span
        className="font-data text-[12px]"
        style={{
          color: isError ? "var(--color-error)" : "var(--color-fg)",
        }}
      >
        {message}
      </span>
      <button
        type="button"
        onClick={onClose}
        className="ml-auto font-data text-[10px] uppercase-tight text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]"
        aria-label="dismiss toast"
      >
        ✕
      </button>
    </div>
  );
}


// ---- Brain banner ------------------------------------------------

function BrainBanner({
  brain,
  inventory,
  modelUxEnabled,
  onSwitch,
}: {
  brain: string;
  inventory: string[];
  modelUxEnabled: boolean;
  onSwitch: (kind: string) => void;
}) {
  const others = inventory.filter((k) => k !== brain);
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
          {modelUxEnabled && others.length > 0 && (
            <span className="ml-auto flex items-baseline gap-2">
              <span className="text-[10px] uppercase-tight text-[var(--color-fg-dim)]">
                switch to:
              </span>
              {others.map((k) => (
                <button
                  key={k}
                  type="button"
                  onClick={() => onSwitch(k)}
                  className="font-data text-[12px] text-[var(--color-fg-2)] hover:text-[var(--color-accent)] underline-offset-2 hover:underline"
                >
                  {k}
                </button>
              ))}
            </span>
          )}
        </div>
        <p className="px-4 pb-3 font-data text-[10.5px] text-[var(--color-fg-dim)]">
          brain.kind is read once at startup. Switching writes the
          new value but requires a daemon restart to take effect.
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
  availableModels,
  editable,
  onSet,
  onReset,
}: {
  rows: ModelSubsystemRow[];
  brain: string;
  availableModels: string[];
  editable: boolean;
  onSet: (subsystem: string, value: string) => void;
  onReset: (subsystem: string) => void;
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
              style={{ gridTemplateColumns: "1.5fr 1.5fr 1fr 0.4fr" }}
            >
              <span>subsystem</span>
              <span>configured</span>
              <span>resolves to</span>
              <span className="text-right">status</span>
            </div>
            {rows.map((row) => (
              <ResolutionRow
                key={row.name}
                row={row}
                availableModels={availableModels}
                editable={editable}
                onSet={onSet}
                onReset={onReset}
              />
            ))}
          </div>
        )}
      </Card>
    </Section>
  );
}

function ResolutionRow({
  row,
  availableModels,
  editable,
  onSet,
  onReset,
}: {
  row: ModelSubsystemRow;
  availableModels: string[];
  editable: boolean;
  onSet: (subsystem: string, value: string) => void;
  onReset: (subsystem: string) => void;
}) {
  const worst = pickWorst(row.findings);
  const tiers = ["tiny", "small", "medium", "large"];
  // Build a deduplicated, alphabetised dropdown source: abstract
  // tiers + discovery list + (current configured value if it
  // doesn't match either, so the dropdown can render the
  // user's existing pick).
  const optionSet = new Set<string>([...tiers, ...availableModels]);
  if (row.configured) optionSet.add(row.configured);
  const options = Array.from(optionSet).sort();
  return (
    <div
      className="grid items-baseline gap-x-4 py-1.5 border-b border-[var(--color-border)] last:border-b-0"
      style={{ gridTemplateColumns: "1.5fr 1.5fr 1fr 0.4fr" }}
    >
      <span className="text-[var(--color-fg)]">{row.name}</span>
      <span className="text-[var(--color-fg-2)] flex items-center gap-2">
        {editable ? (
          <>
            <select
              aria-label={`Set ${row.name}`}
              value={row.configured ?? ""}
              onChange={(e) => onSet(row.name, e.target.value)}
              className="font-data text-[12px] bg-[var(--color-base)] border border-[var(--color-border)] px-1.5 py-0.5 text-[var(--color-fg)]"
            >
              <option value="" disabled>
                (default)
              </option>
              {options.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
            {row.configured !== null && (
              <button
                type="button"
                onClick={() => onReset(row.name)}
                title="reset to default"
                aria-label={`Reset ${row.name}`}
                className="text-[10px] uppercase-tight text-[var(--color-fg-dim)] hover:text-[var(--color-error)]"
              >
                reset
              </button>
            )}
          </>
        ) : (
          row.configured ?? <Dim>(default)</Dim>
        )}
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

function AvailableModelsHint({
  brain,
  availableModels,
  onRefresh,
  refreshing,
}: {
  brain: string;
  availableModels: string[];
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const lines =
    brain === "claude-code" ? CLAUDE_CODE_HINT : brain === "opencode" ? OPENCODE_HINT : NULL_HINT;
  return (
    <Section
      title="Available models"
      trailing={`${availableModels.length} for ${brain}`}
    >
      <Card>
        <div className="px-4 py-3 flex items-start gap-4">
          <ul className="space-y-1 font-data text-[12px] text-[var(--color-fg-2)] flex-1">
            {lines.map((line, idx) => (
              <li key={idx}>{line}</li>
            ))}
          </ul>
          <Button
            onClick={onRefresh}
            disabled={refreshing}
            aria-label="refresh model discovery"
          >
            {refreshing ? "refreshing…" : "refresh"}
          </Button>
        </div>
        <p className="px-4 pb-3 font-data text-[10.5px] text-[var(--color-fg-dim)]">
          Discovery cached 5 minutes per brain. Refresh re-runs{" "}
          <code className="text-[var(--color-fg-2)]">opencode models --refresh</code>{" "}
          for the live list.
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
  "Live discovery cached 5 min; refresh to re-fetch",
];
const NULL_HINT = ["Test fake — no real model spawns. Switch brain.kind to use models."];


// ---- Brain-switch confirm modal ---------------------------------

function BrainSwitchModal({
  token,
  targetKind,
  currentKind,
  onConfirm,
  onCancel,
}: {
  token: string;
  targetKind: string;
  currentKind: string;
  onConfirm: (kind: string) => void;
  onCancel: () => void;
}) {
  // Preview-mode: ask the backend what would happen at the new
  // brain. We don't have a separate preview endpoint — the
  // validator runs server-side as part of the actual switch.
  // For the modal preview we GET /api/v1/models with the
  // current brain and surface a hint that warnings WILL surface
  // post-switch if the user has legacy raw-string subsystem
  // values (rule 4 trap). The actual warnings list comes back
  // from the POST body when the user confirms.
  const [warnings, setWarnings] = useState<ModelValidationFinding[] | null>(null);
  useEffect(() => {
    let cancelled = false;
    api
      .models(token)
      .then((data) => {
        if (cancelled) return;
        // Surface any current-brain warnings so the user sees
        // what's already broken; the actual post-switch
        // validator runs server-side on confirm.
        const all = [
          ...data.global_findings,
          ...data.subsystems.flatMap((r) => r.findings),
        ];
        const non_info = all.filter((f) => f.severity !== "info");
        setWarnings(non_info);
      })
      .catch(() => {
        if (!cancelled) setWarnings([]);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  // Detect the legacy-keys → opencode trap inline so the user sees
  // it BEFORE confirming, not just after.
  const switchingToOpencode = targetKind === "opencode";

  return (
    <Modal onCancel={onCancel} ariaLabel="brain switch confirmation">
      <h2 className="font-data text-[14px] text-[var(--color-fg)] mb-3">
        Switch brain → {targetKind}
      </h2>
      <p className="text-[12px] text-[var(--color-fg-2)] font-data mb-3">
        Currently on <code>{currentKind}</code>. Switching writes
        the new value to ~/.vexis/config.yaml.
      </p>
      <p className="text-[12px] text-[var(--color-warn)] font-data mb-3">
        ⚠ <strong>Restart required.</strong> brain.kind is read once
        at startup; the new brain only takes effect after you restart
        vexis.
      </p>
      {switchingToOpencode && (
        <p className="text-[12px] text-[var(--color-fg-2)] font-data mb-3">
          Note: opencode requires{" "}
          <code className="text-[var(--color-fg)]">provider/model</code>{" "}
          shape for any subsystem assignment. Legacy raw-string keys
          (e.g.{" "}
          <code className="text-[var(--color-fg)]">
            models.learning_review: sonnet
          </code>
          ) will surface as errors after the switch. See{" "}
          <code className="text-[var(--color-fg)]">docs/migration.md</code>.
        </p>
      )}
      {warnings && warnings.length > 0 && (
        <div className="mb-3">
          <p className="text-[10.5px] uppercase-tight text-[var(--color-fg-dim)] mb-1">
            current validator output ({warnings.length} non-info finding{warnings.length === 1 ? "" : "s"}):
          </p>
          <ul className="space-y-1 font-data text-[11px] max-h-32 overflow-y-auto">
            {warnings.map((w, i) => (
              <li
                key={i}
                style={{
                  color: w.severity === "error" ? "var(--color-error)" : "var(--color-warn)",
                }}
              >
                [{w.subsystem ?? "global"}] {w.problem}
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="flex justify-end gap-2 mt-4">
        <Button onClick={onCancel}>Cancel</Button>
        <Button onClick={() => onConfirm(targetKind)}>
          Confirm switch
        </Button>
      </div>
    </Modal>
  );
}


// ---- Comment-preservation confirm modal -------------------------

function CommentConfirmModal({
  onConfirm,
  onCancel,
}: {
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Modal onCancel={onCancel} ariaLabel="comment preservation confirmation">
      <h2 className="font-data text-[14px] text-[var(--color-fg)] mb-3">
        Your config has comments
      </h2>
      <p className="text-[12px] text-[var(--color-fg-2)] font-data mb-3">
        Saving via the dashboard rewrites{" "}
        <code className="text-[var(--color-fg)]">~/.vexis/config.yaml</code>{" "}
        with PyYAML, which strips YAML comments. The slash command
        runs the same writer.
      </p>
      <p className="text-[12px] text-[var(--color-fg-2)] font-data mb-3">
        A backup will be written to{" "}
        <code className="text-[var(--color-fg)]">~/.vexis/config.yaml.bak</code>{" "}
        with comments preserved before the rewrite.
      </p>
      <p className="text-[12px] text-[var(--color-fg-2)] font-data mb-3">
        Or close this and edit the config file directly to keep
        comments inline.
      </p>
      <div className="flex justify-end gap-2 mt-4">
        <Button onClick={onCancel}>Close (edit directly)</Button>
        <Button onClick={onConfirm}>
          Confirm + back up
        </Button>
      </div>
    </Modal>
  );
}


// ---- Modal scaffold ---------------------------------------------

function Modal({
  children,
  onCancel,
  ariaLabel,
}: {
  children: React.ReactNode;
  onCancel: () => void;
  ariaLabel: string;
}) {
  // Esc to dismiss; click outside the card to dismiss.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);
  return (
    <div
      className="fixed inset-0 bg-black/60 z-50 grid place-items-center px-4"
      onClick={onCancel}
      role="dialog"
      aria-modal="true"
      aria-label={ariaLabel}
    >
      <div
        className="hairline bg-[var(--color-surface)] max-w-lg w-full p-6"
        onClick={(e) => e.stopPropagation()}
      >
        {children}
      </div>
    </div>
  );
}


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
