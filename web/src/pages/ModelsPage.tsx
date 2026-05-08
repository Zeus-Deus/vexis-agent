import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  ModelSubsystemRow,
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
        availableModelsByProvider={
          state.available_models_by_provider[state.brain_kind] ?? {}
        }
        editable={state.model_ux_enabled}
        onSet={onSetSubsystem}
        onReset={onResetSubsystem}
      />
      {/* Tier overrides section removed in 2026-05-08 polish pass —
          tiers stopped being a user-facing input (see ResolutionRow's
          dropdown). The per-tier mapping table was implementation-
          detail surfacing without a UX purpose now that the dropdown
          surfaces only model names + (default). API still ships the
          tier_overrides field for back-compat / future re-add. */}
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
        {/* Mobile: stack the kind row and the switch-to row so the
            brain-name + switcher buttons each get their own line.
            Desktop: original single row with switcher floated right. */}
        <div className="px-4 py-3 flex flex-col sm:flex-row sm:items-baseline gap-2 sm:gap-3 font-data text-[12px]">
          <div className="flex items-baseline gap-3">
            <span className="text-[var(--color-fg-dim)] uppercase-tight text-[10px]">
              kind
            </span>
            <span className="text-[var(--color-accent)] text-[14px]">
              {brain}
            </span>
          </div>
          {modelUxEnabled && others.length > 0 && (
            <div className="sm:ml-auto flex flex-wrap items-baseline gap-x-2 gap-y-1">
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
            </div>
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

// Day 2 of model picker UX — model-name-primary dropdown.
// Aliases ("haiku" / "sonnet" / "opus" on claude-code) are omitted
// from the picker per `.plans/model-picker-ux-research.md` §5
// cleanup 5: aliases drift over time as Anthropic releases new
// models behind the same name, so picker buttons enforce version
// pinning by surfacing only full names. The typed-arg path on the
// /model slash command still accepts aliases — cleanup applies to
// the picker UI only.
const PICKER_OMITTED_ALIASES = new Set(["haiku", "sonnet", "opus"]);

// (Tier fallbacks were previously offered as a separate
// "Tier fallbacks (advanced)" optgroup at the bottom of the
// dropdown; removed in the 2026-05-08 polish pass — tiers are
// an implementation detail of fallback resolution, not a
// user-facing input. Users who want to set a tier explicitly
// can edit YAML; the dropdown surfaces only model names + the
// (default) option, which is what a tier fallback effectively
// IS in the picker world.)

// Threshold above which the per-row search/filter input renders.
// claude-code (~9 models) doesn't need it; opencode (~250 across
// providers) does. Threshold rather than brain check because the
// trigger is option-count ergonomics, not brain identity — if a
// future brain ships 50 models the search input shows up
// automatically without a code change here.
const PICKER_SEARCH_THRESHOLD = 30;

// Debounce delay for the per-row search input. 150 ms is below
// human-perceptible lag (~200 ms) but still coalesces rapid
// keystrokes. Pure client-side filter is sub-ms even on the
// largest realistic option set so the debounce is purely about
// avoiding re-render thrash mid-typing.
const PICKER_SEARCH_DEBOUNCE_MS = 150;

function useDebounced<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

function ResolutionTable({
  rows,
  brain,
  availableModelsByProvider,
  editable,
  onSet,
  onReset,
}: {
  rows: ModelSubsystemRow[];
  brain: string;
  availableModelsByProvider: Record<string, string[]>;
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
          <div className="px-3 sm:px-4 py-3 font-data text-[12px]">
            {/* Header row hidden on mobile — each ResolutionRow
                carries inline column labels on narrow viewports
                so the desktop column-header row is redundant
                noise there. */}
            <div className="hidden sm:grid items-baseline gap-x-4 text-[10.5px] uppercase-tight text-[var(--color-fg-dim)] pb-2 border-b border-[var(--color-border)] sm:grid-cols-[1.5fr_1.5fr_1fr_0.4fr]">
              <span>subsystem</span>
              <span>configured</span>
              <span>resolves to</span>
              <span className="text-right">status</span>
            </div>
            {rows.map((row) => (
              <ResolutionRow
                key={row.name}
                row={row}
                availableModelsByProvider={availableModelsByProvider}
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

// Strip aliases from each provider bucket. Aliases are exactly the
// strings in PICKER_OMITTED_ALIASES (no provider prefix); opencode's
// `anthropic/claude-haiku-3-5` is a full id and stays.
function filterAliases(
  byProvider: Record<string, string[]>,
): Record<string, string[]> {
  const out: Record<string, string[]> = {};
  for (const [provider, models] of Object.entries(byProvider)) {
    const filtered = models.filter((m) => !PICKER_OMITTED_ALIASES.has(m));
    if (filtered.length > 0) out[provider] = filtered;
  }
  return out;
}

// Apply the search query to the provider-grouped data. Match
// case-insensitively against the full model id (which contains the
// provider prefix on opencode, so typing "openai" filters to
// openai's bucket; typing "sonnet" filters to anything with
// "sonnet" in the id across providers). Empty groups collapse —
// we drop the bucket entirely rather than render an empty
// optgroup so the picker doesn't show a label with no options.
function applySearchFilter(
  byProvider: Record<string, string[]>,
  query: string,
): Record<string, string[]> {
  const q = query.trim().toLowerCase();
  if (q === "") return byProvider;
  const out: Record<string, string[]> = {};
  for (const [provider, models] of Object.entries(byProvider)) {
    const matched = models.filter(
      (m) =>
        m.toLowerCase().includes(q) ||
        provider.toLowerCase().includes(q),
    );
    if (matched.length > 0) out[provider] = matched;
  }
  return out;
}

function totalOptionCount(byProvider: Record<string, string[]>): number {
  let n = 0;
  for (const models of Object.values(byProvider)) n += models.length;
  return n;
}

// Check whether row.configured is already represented by any
// option the dropdown would otherwise render. If not, the picker
// surfaces it under a dedicated "Current" optgroup so the user's
// existing pick stays visible even if it's a legacy alias, a
// legacy tier value, or a model that's since been removed from
// discovery. Tiers no longer show in the dropdown by default
// (2026-05-08 polish pass), so legacy tier configs land in the
// Current bucket too — keeps the <select> value valid.
function configuredAlreadyVisible(
  configured: string,
  byProvider: Record<string, string[]>,
): boolean {
  for (const models of Object.values(byProvider)) {
    if (models.includes(configured)) return true;
  }
  return false;
}

// Render the configured-column display string for the table.
// Differs from the slash format (which is one column inline →
// uses the "(default → <resolved>)" arrow form): the table has
// a dedicated resolves-to column that carries the model name,
// so the configured cell stays minimal.
//
// Cases:
//   1. configured is null → "(default)" (the resolves-to column
//      shows what default actually maps to).
//   2. configured set → render the configured value as-is. The
//      resolves-to column shows the translated/passthrough value.
function formatConfiguredCell(
  configured: string | null,
  _resolved: string | null,
): string {
  if (configured === null) return "(default)";
  return configured;
}

// Render the resolves-to-column display. ALWAYS populated with
// the resolved model id (or "<brain default>" when null) — the
// table's two columns have clear roles: configured = what's set,
// resolves-to = what it actually is. Pre-2026-05-08 attempt to
// blank this for the configured=null and configured==resolved
// cases produced a column full of em-dashes which read as broken
// rather than concise (see /model dashboard issue 2 dogfood);
// the simpler always-populated rule is what users expect from a
// table.
function formatResolvesToCell(
  _configured: string | null,
  resolved: string | null,
): string {
  return resolved ?? "<brain default>";
}

function ResolutionRow({
  row,
  availableModelsByProvider,
  editable,
  onSet,
  onReset,
}: {
  row: ModelSubsystemRow;
  availableModelsByProvider: Record<string, string[]>;
  editable: boolean;
  onSet: (subsystem: string, value: string) => void;
  onReset: (subsystem: string) => void;
}) {
  const worst = pickWorst(row.findings);
  // Per-row search query state. Independent across rows so users
  // can edit one subsystem without their typing in another row's
  // filter affecting this one. NOT auto-focused — default focus
  // stays wherever it was on the page (the user explicitly raised
  // the keystroke-capture concern for Day 2).
  const [searchQuery, setSearchQuery] = useState("");
  const debouncedQuery = useDebounced(searchQuery, PICKER_SEARCH_DEBOUNCE_MS);

  // Build the dropdown's groups: alias-filtered providers, with
  // the search query applied. Total count drives the search-input
  // visibility — no input on small option sets.
  const filteredByProvider = filterAliases(availableModelsByProvider);
  const showSearch = totalOptionCount(filteredByProvider) > PICKER_SEARCH_THRESHOLD;
  const visibleByProvider = applySearchFilter(filteredByProvider, debouncedQuery);

  // Provider order: same as backend (anthropic first, then
  // alphabetical). Object.entries respects insertion order, and
  // discovery_grouped_for_validator emits in priority order, so
  // we iterate in the order the API delivered.
  const providerEntries = Object.entries(visibleByProvider);

  // Surface the user's currently configured value under a
  // dedicated "Current" optgroup if it's not already visible
  // (e.g. legacy "sonnet" alias on a row, or a discovered model
  // that fell out of the cache). Keeps the dropdown's own value
  // valid (HTML <select> warns when value points at a
  // non-existent option).
  const currentNeedsBucket =
    row.configured !== null &&
    !configuredAlreadyVisible(row.configured, filteredByProvider);

  return (
    /* Mobile (< sm): single-column stack with inline cell labels
       (CONFIGURED / RESOLVES TO) above each value, status badge
       floats top-right next to the subsystem name. Desktop
       (sm:+): the original 4-column grid; cell labels hidden
       (the header row above carries them). */
    <div className="grid grid-cols-1 sm:grid-cols-[1.5fr_1.5fr_1fr_0.4fr] items-baseline gap-x-4 gap-y-1 sm:gap-y-0 py-3 sm:py-1.5 border-b border-[var(--color-border)] last:border-b-0">
      {/* Subsystem name + mobile-only status badge */}
      <div className="flex items-center justify-between gap-2">
        <span className="text-[var(--color-fg)]">{row.name}</span>
        <span className="sm:hidden">
          <StatusBadge worst={worst} findings={row.findings} />
        </span>
      </div>
      {/* Configured column (the dropdown / read-only value) */}
      <div className="text-[var(--color-fg-2)] flex flex-col items-stretch gap-1">
        <span className="sm:hidden text-[10px] uppercase-tight text-[var(--color-fg-dim)]">
          configured
        </span>
        {editable ? (
          <>
            {showSearch && (
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="filter models…"
                aria-label={`Filter models for ${row.name}`}
                className="font-data text-[11px] bg-[var(--color-base)] border border-[var(--color-border)] px-1.5 py-0.5 text-[var(--color-fg)]"
              />
            )}
            <div className="flex items-center gap-2">
              <select
                aria-label={`Set ${row.name}`}
                value={row.configured ?? ""}
                onChange={(e) => onSet(row.name, e.target.value)}
                /* Mobile: full width so the touch target is wide
                   and provider/model labels don't truncate. Desktop:
                   natural width within the column. */
                className="flex-1 sm:flex-none font-data text-[12px] bg-[var(--color-base)] border border-[var(--color-border)] px-1.5 py-1 sm:py-0.5 text-[var(--color-fg)] min-w-0"
              >
                <option value="" disabled>
                  (default)
                </option>
                {currentNeedsBucket && row.configured !== null && (
                  <optgroup label="Current">
                    <option value={row.configured}>{row.configured}</option>
                  </optgroup>
                )}
                {providerEntries.map(([provider, models]) => (
                  <optgroup key={provider} label={provider}>
                    {models.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
              {row.configured !== null && (
                <button
                  type="button"
                  onClick={() => onReset(row.name)}
                  title="reset to default"
                  aria-label={`Reset ${row.name}`}
                  className="text-[10px] uppercase-tight text-[var(--color-fg-dim)] hover:text-[var(--color-error)] shrink-0"
                >
                  reset
                </button>
              )}
            </div>
          </>
        ) : (
          row.configured ?? (
            <Dim>{formatConfiguredCell(null, row.resolved_model_id)}</Dim>
          )
        )}
      </div>
      {/* Resolves-to column — always populated. Mobile gets inline label. */}
      <div className="text-[var(--color-fg-2)] break-all">
        <span className="sm:hidden text-[10px] uppercase-tight text-[var(--color-fg-dim)] block">
          resolves to
        </span>
        {formatResolvesToCell(row.configured, row.resolved_model_id)}
      </div>
      {/* Status badge — desktop renders here, mobile renders inline
          with the subsystem name above. */}
      <span className="hidden sm:block sm:text-right">
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

// (TierOverrides component removed in 2026-05-08 polish pass —
// tiers are no longer a user-facing input and the section was
// surfacing implementation-detail data with no UX purpose. The
// API still ships the tier_overrides field so a future re-add
// (or external consumer) can use it.)


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
