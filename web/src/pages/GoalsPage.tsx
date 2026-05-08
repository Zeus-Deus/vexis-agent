import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { GoalRecord, GoalsState, GoalStatus } from "../lib/types";
import { classNames, relativeTime } from "../lib/format";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface GoalsPageProps {
  token: string;
  onAuthFail: () => void;
}

// Goals state changes turn-by-turn (every 30s-2min in practice).
// 5s polling matches what a user expects from "live": fast enough
// that a continuation enqueueing is visible inside their attention
// span, slow enough that the dashboard doesn't hammer the backend.
const POLL_INTERVAL_MS = 5000;

// History is forensic — capped server-side at 20.
const HISTORY_TRUNCATE_LEN = 80;

export function GoalsPage({ token, onAuthFail }: GoalsPageProps) {
  const [state, setState] = useState<GoalsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<
    "pause" | "resume" | "clear" | null
  >(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.goals(token);
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

  // Wrapped action runners — set pendingAction, hit endpoint,
  // optimistically swap in the returned record, then re-fetch the
  // full state so history updates too.
  const runAction = useCallback(
    async (
      kind: "pause" | "resume" | "clear",
      fn: () => Promise<GoalRecord>,
    ) => {
      setPendingAction(kind);
      try {
        await fn();
        await refresh();
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        // 4xx tells us the server's view of state has diverged from
        // ours — the most common case being a 409 from Day 5.5's
        // terminal-state guard ("Goal is already done"). Surface the
        // detail message AND re-fetch so the UI catches up
        // immediately instead of waiting for the next 5s poll. The
        // refresh moves the goal into history; the error toast
        // explains what happened until the next render clears it.
        setError(exc instanceof Error ? exc.message : String(exc));
        try {
          await refresh();
        } catch {
          // Ignore refresh failure here — the original error is
          // what the user needs to see, and the polling loop will
          // try again in 5s.
        }
      } finally {
        setPendingAction(null);
      }
    },
    [refresh, onAuthFail],
  );

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
        Could not load goals: {error}
      </div>
    );
  }
  if (!state) {
    return <GoalsSkeleton />;
  }

  return (
    <div className="space-y-8">
      <Header />
      {error && <ErrorBanner message={error} />}
      <ActiveSection
        record={state.active}
        pending={pendingAction}
        onPause={() => runAction("pause", () => api.pauseGoal(token))}
        onResume={() => runAction("resume", () => api.resumeGoal(token))}
        onClear={() => runAction("clear", () => api.clearGoal(token))}
      />
      <HistorySection records={state.history} />
    </div>
  );
}

// ---- Header ------------------------------------------------------

function Header() {
  return (
    <div className="space-y-1">
      <h1 className="font-data text-[15px] tracking-tight text-[var(--color-fg)]">
        ⊙ <span className="ml-1">Standing goals</span>
      </h1>
      <p className="text-xs text-[var(--color-fg-dim)] font-data leading-relaxed max-w-[60ch]">
        Multi-turn objectives Vexis works on across continuations until done,
        paused, or the budget runs out. Set one with{" "}
        <code className="text-[var(--color-fg-2)]">/goal &lt;text&gt;</code>{" "}
        in Telegram; control it from here or from your phone.
      </p>
    </div>
  );
}

// ---- Banners -----------------------------------------------------

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="hairline px-4 py-3 bg-[var(--color-surface)]">
      <p className="font-data text-[12px] text-[var(--color-error)]">
        <span className="uppercase-tight text-[10px] mr-2">goals</span>
        {message}
      </p>
      <p className="mt-1 font-data text-[10.5px] text-[var(--color-fg-dim)]">
        The page will retry automatically every {Math.round(POLL_INTERVAL_MS / 1000)}s.
      </p>
    </div>
  );
}

// ---- Active panel ------------------------------------------------

function ActiveSection({
  record,
  pending,
  onPause,
  onResume,
  onClear,
}: {
  record: GoalRecord | null;
  pending: "pause" | "resume" | "clear" | null;
  onPause: () => void;
  onResume: () => void;
  onClear: () => void;
}) {
  return (
    <Section
      title="Active"
      trailing={record ? `${record.turns_used} / ${record.max_turns} turns` : undefined}
    >
      {record === null ? (
        <EmptyState
          glyph="·"
          title="No active goal."
          hint={
            <>
              Set one with{" "}
              <code className="font-data text-[var(--color-fg)]">
                /goal &lt;text&gt;
              </code>{" "}
              in Telegram. Goal creation is intentionally Telegram-only — the
              dashboard is for observation and control.
            </>
          }
        />
      ) : (
        <Card>
          <div className="px-5 py-4 space-y-4">
            <div className="flex items-baseline gap-3 flex-wrap">
              <StatusBadge status={record.status} />
              {record.status === "paused" && record.paused_reason && (
                <span className="font-data text-[10.5px] text-[var(--color-fg-dim)]">
                  paused reason — {record.paused_reason}
                </span>
              )}
            </div>

            <div className="text-[14px] text-[var(--color-fg)] leading-snug">
              {record.goal}
            </div>

            <Budget turns={record.turns_used} max={record.max_turns} />

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-2 pt-1">
              <KV label="created" value={relativeTime(record.created_at)} />
              <KV label="last turn" value={relativeTime(record.last_turn_at)} />
              {record.last_verdict && (
                <KV label="last verdict" value={record.last_verdict} mono />
              )}
              {record.last_reason && (
                <KV label="last reason" value={record.last_reason} />
              )}
            </div>

            <div className="flex items-center gap-2 pt-2 border-t border-[var(--color-border)]">
              <Button
                variant="ghost"
                size="sm"
                disabled={record.status !== "active"}
                loading={pending === "pause"}
                onClick={onPause}
              >
                Pause
              </Button>
              <Button
                variant="ghost"
                size="sm"
                disabled={record.status !== "paused"}
                loading={pending === "resume"}
                onClick={onResume}
              >
                Resume
              </Button>
              <Button
                variant="danger"
                size="sm"
                loading={pending === "clear"}
                onClick={onClear}
              >
                Clear
              </Button>
              <span className="ml-auto font-data text-[10px] text-[var(--color-fg-dim)] uppercase tracking-widest">
                actions write paused_reason="dashboard-…"
              </span>
            </div>
          </div>
        </Card>
      )}
    </Section>
  );
}

// ---- Budget bar (inline, mono dots — no extra component) --------

function Budget({ turns, max }: { turns: number; max: number }) {
  const cells = Math.max(1, Math.min(max, 40));  // cap visual width
  const filled = Math.round((turns / Math.max(1, max)) * cells);
  return (
    <div className="font-data text-[11px] text-[var(--color-fg-dim)] flex items-center gap-2">
      <span aria-hidden className="text-[var(--color-fg-2)] tracking-tight">
        {Array.from({ length: cells }).map((_, i) => (i < filled ? "▰" : "▱")).join("")}
      </span>
      <span className="tabular-nums text-[var(--color-fg-2)]">
        {turns} / {max}
      </span>
    </div>
  );
}

// ---- History -----------------------------------------------------

function HistorySection({ records }: { records: GoalRecord[] }) {
  return (
    <Section
      title="History"
      trailing={`${records.length} record${records.length === 1 ? "" : "s"} (last 20)`}
    >
      {records.length === 0 ? (
        <EmptyState
          glyph="·"
          title="No history yet."
          hint="Done, paused, and cleared goals appear here, sorted by their most recent turn."
        />
      ) : (
        <Card>
          <div className="overflow-x-auto">
            {/* min-w forces the table to its natural width on narrow
                viewports so overflow-x-auto kicks in (without a min,
                w-full would compress columns into 60-px slivers and
                wrap text into towers). 720px fits all 6 columns
                comfortably; below that the user scrolls horizontally. */}
            <table className="w-full min-w-[720px] text-[11.5px] font-data">
              <thead>
                <tr className="text-left uppercase-tight text-[10px] text-[var(--color-fg-dim)]">
                  <th className="px-5 py-3 font-normal whitespace-nowrap">status</th>
                  <th className="px-2 py-3 font-normal">goal</th>
                  <th className="px-2 py-3 font-normal text-right whitespace-nowrap">turns</th>
                  <th className="px-2 py-3 font-normal whitespace-nowrap">created</th>
                  <th className="px-2 py-3 font-normal whitespace-nowrap">last turn</th>
                  <th className="px-5 py-3 font-normal">reason</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[var(--color-border)]">
                {records.map((r, i) => (
                  <HistoryRow key={`${r.session_uuid}-${i}`} record={r} />
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </Section>
  );
}

function HistoryRow({ record }: { record: GoalRecord }) {
  const truncated =
    record.goal.length > HISTORY_TRUNCATE_LEN
      ? `${record.goal.slice(0, HISTORY_TRUNCATE_LEN - 1)}…`
      : record.goal;
  const reason =
    record.status === "paused"
      ? record.paused_reason
      : record.status === "done"
        ? record.last_reason
        : record.status === "cleared"
          ? "—"
          : null;
  return (
    <tr>
      <td className="px-5 py-3 align-top">
        <StatusBadge status={record.status} />
      </td>
      <td className="px-2 py-3 align-top text-[var(--color-fg)]" title={record.goal}>
        {truncated}
      </td>
      <td className="px-2 py-3 align-top tabular-nums text-[var(--color-fg-2)] text-right whitespace-nowrap">
        {record.turns_used}/{record.max_turns}
      </td>
      <td className="px-2 py-3 align-top text-[var(--color-fg-dim)] whitespace-nowrap">
        {relativeTime(record.created_at)}
      </td>
      <td className="px-2 py-3 align-top text-[var(--color-fg-dim)] whitespace-nowrap">
        {relativeTime(record.last_turn_at)}
      </td>
      <td className="px-5 py-3 align-top text-[var(--color-fg-2)]">
        {reason || "—"}
      </td>
    </tr>
  );
}

// ---- Status badge ------------------------------------------------

function StatusBadge({ status }: { status: GoalStatus }) {
  if (status === "active") {
    return (
      <Badge tone="active" glyph="⊙">
        active
      </Badge>
    );
  }
  if (status === "paused") {
    return (
      <Badge tone="warn" glyph="⏸">
        paused
      </Badge>
    );
  }
  if (status === "done") {
    return (
      <Badge tone="accent" glyph="✓">
        done
      </Badge>
    );
  }
  return (
    <Badge tone="subtle" glyph="·">
      cleared
    </Badge>
  );
}

// ---- helpers -----------------------------------------------------

function KV({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2 min-w-0">
      <span className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] shrink-0">
        {label}
      </span>
      <span
        className={classNames(
          mono ? "font-data" : "",
          "text-[12px] text-[var(--color-fg-2)] truncate",
        )}
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function GoalsSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-12 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-48 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-32 hairline bg-[var(--color-surface)] animate-pulse-slow" />
    </div>
  );
}
