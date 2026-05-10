import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  ScheduleRecord,
  SchedulesState,
} from "../lib/types";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface SchedulesPageProps {
  token: string;
  onAuthFail: () => void;
}

// Same cadence as GoalsPage — scheduled fires happen on minute-scale,
// 5s is fast enough that a manual pause is visible inside the user's
// attention span and slow enough that the backend isn't hammered.
const POLL_INTERVAL_MS = 5000;
const PROMPT_PREVIEW_LEN = 80;

export function SchedulesPage({ token, onAuthFail }: SchedulesPageProps) {
  const [state, setState] = useState<SchedulesState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.schedules(token);
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

  const runAction = useCallback(
    async (
      id: string,
      action: "pause" | "resume" | "clear",
    ) => {
      setPendingId(id);
      try {
        if (action === "pause") await api.pauseSchedule(token, id);
        else if (action === "resume") await api.resumeSchedule(token, id);
        else await api.clearSchedule(token, id);
        await refresh();
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
        try {
          await refresh();
        } catch {
          /* refresh failure surfaces via next poll */
        }
      } finally {
        setPendingId(null);
      }
    },
    [refresh, token, onAuthFail],
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

  if (state === null && error === null) {
    return (
      <Card>
        <div className="text-[var(--color-fg-dim)] font-data text-sm">
          Loading schedules…
        </div>
      </Card>
    );
  }

  if (state === null) {
    return (
      <Card>
        <div className="text-[var(--color-fg)] font-data text-sm">
          Schedules unavailable: {error}
        </div>
      </Card>
    );
  }

  const empty =
    state.active.length === 0 &&
    state.paused.length === 0 &&
    state.expired.length === 0 &&
    state.cleared.length === 0;

  if (!state.enabled) {
    return (
      <Card>
        <EmptyState
          glyph="◷"
          title="Scheduling is disabled."
          hint={
            <>
              Set{" "}
              <code className="font-data text-[var(--color-fg)]">
                schedules.enabled: true
              </code>{" "}
              in <code className="font-data text-[var(--color-fg)]">~/.vexis/config.yaml</code>{" "}
              to enable. Existing schedules in{" "}
              <code className="font-data text-[var(--color-fg)]">schedules.json</code>{" "}
              are retained while disabled — re-enabling resumes them.
            </>
          }
        />
      </Card>
    );
  }

  if (empty) {
    return (
      <Card>
        <EmptyState
          glyph="◷"
          title="No schedules yet."
          hint={
            <>
              Create one by telling vexis what to schedule, in chat or in Telegram:{" "}
              <code className="font-data text-[var(--color-fg)]">
                /schedule remind me every weekday at 9am
              </code>
              .
            </>
          }
        />
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {error && (
        <Card>
          <div className="text-[var(--color-fg)] font-data text-xs">
            <Badge tone="warn">action error</Badge>{" "}
            <span className="ml-2">{error}</span>
          </div>
        </Card>
      )}

      <Card>
        <Section title={`Active · ${state.active.length}`}>
          {state.active.length === 0 ? (
            <div className="text-[var(--color-fg-dim)] font-data text-xs">
              No active schedules.
            </div>
          ) : (
            <ul className="space-y-3" data-testid="schedules-active-list">
              {state.active.map((r) => (
                <ScheduleRow
                  key={r.id}
                  record={r}
                  pending={pendingId === r.id}
                  onAction={runAction}
                  actions={["pause", "clear"]}
                />
              ))}
            </ul>
          )}
        </Section>
      </Card>

      {state.paused.length > 0 && (
        <Card>
          <Section title={`Paused · ${state.paused.length}`}>
            <ul className="space-y-3" data-testid="schedules-paused-list">
              {state.paused.map((r) => (
                <ScheduleRow
                  key={r.id}
                  record={r}
                  pending={pendingId === r.id}
                  onAction={runAction}
                  actions={["resume", "clear"]}
                />
              ))}
            </ul>
          </Section>
        </Card>
      )}

      {state.expired.length > 0 && (
        <Card>
          <Section title={`Expired · ${state.expired.length}`}>
            <ul className="space-y-3" data-testid="schedules-expired-list">
              {state.expired.map((r) => (
                <ScheduleRow
                  key={r.id}
                  record={r}
                  pending={pendingId === r.id}
                  onAction={runAction}
                  actions={["clear"]}
                />
              ))}
            </ul>
          </Section>
        </Card>
      )}

      {state.cleared.length > 0 && (
        <Card>
          <Section
            title={`Audit · ${state.cleared.length} cleared${
              state.retractions_7d > 0
                ? ` · ${state.retractions_7d} retractions / 7d`
                : ""
            }`}
          >
            <ul
              className="space-y-2 opacity-70"
              data-testid="schedules-cleared-list"
            >
              {state.cleared.slice(0, 30).map((r) => (
                <li
                  key={r.id}
                  className="font-data text-xs text-[var(--color-fg-dim)]"
                >
                  <span className="font-data text-[var(--color-fg)]">
                    {r.id.slice(0, 6)}
                  </span>{" "}
                  · {r.schedule_display} · "
                  {truncate(r.prompt, 60)}"
                </li>
              ))}
            </ul>
          </Section>
        </Card>
      )}
    </div>
  );
}

interface RowProps {
  record: ScheduleRecord;
  pending: boolean;
  actions: Array<"pause" | "resume" | "clear">;
  onAction: (id: string, action: "pause" | "resume" | "clear") => void;
}

function ScheduleRow({ record, pending, actions, onAction }: RowProps) {
  const nfa = record.next_fire_at
    ? formatRelative(record.next_fire_at)
    : "—";
  const lastFireDisplay = record.last_fire_at
    ? `${formatAbsolute(record.last_fire_at)} (${record.last_status ?? "?"})`
    : "never fired";

  return (
    <li
      className="border-l-2 border-[var(--color-border)] pl-3"
      data-testid={`schedule-row-${record.id}`}
    >
      <div className="font-data text-sm text-[var(--color-fg)]">
        <span className="text-[var(--color-accent)]">◷</span>{" "}
        <span className="text-[var(--color-fg-dim)]">{record.id.slice(0, 6)}</span>{" "}
        {record.schedule_display}{" "}
        {record.tz && (
          <span className="text-[var(--color-fg-dim)]">· {record.tz}</span>
        )}
      </div>
      <div className="font-data text-xs text-[var(--color-fg-dim)] mt-1">
        next fire: {nfa}
      </div>
      <div className="font-data text-xs text-[var(--color-fg)] mt-1 italic">
        "{truncate(record.prompt, PROMPT_PREVIEW_LEN)}"
      </div>
      <div className="font-data text-xs text-[var(--color-fg-dim)] mt-1">
        last fire: {lastFireDisplay}
        {record.consecutive_errors > 0 && (
          <span className="ml-3 text-[var(--color-fg)]">
            <Badge tone="warn">{record.consecutive_errors} errors</Badge>
          </span>
        )}
      </div>
      {record.last_error && (
        <div
          className="font-data text-xs text-[var(--color-fg-dim)] mt-1 truncate"
          title={record.last_error}
        >
          last error: {truncate(record.last_error, 100)}
        </div>
      )}
      {record.paused_reason && (
        <div className="font-data text-xs text-[var(--color-fg-dim)] mt-1">
          paused: {record.paused_reason}
        </div>
      )}
      <div className="mt-2 flex gap-2">
        {actions.includes("pause") && (
          <Button
            variant="ghost"
            size="sm"
            loading={pending}
            onClick={() => onAction(record.id, "pause")}
            data-testid={`schedule-action-pause-${record.id}`}
          >
            Pause
          </Button>
        )}
        {actions.includes("resume") && (
          <Button
            variant="ghost"
            size="sm"
            loading={pending}
            onClick={() => onAction(record.id, "resume")}
            data-testid={`schedule-action-resume-${record.id}`}
          >
            Resume
          </Button>
        )}
        {actions.includes("clear") && (
          <Button
            variant="danger"
            size="sm"
            loading={pending}
            onClick={() => {
              if (
                window.confirm(
                  "Clear this schedule? The record stays in the audit log.",
                )
              ) {
                onAction(record.id, "clear");
              }
            }}
            data-testid={`schedule-action-clear-${record.id}`}
          >
            Clear
          </Button>
        )}
      </div>
    </li>
  );
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1) + "…";
}

function formatAbsolute(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}

function formatRelative(iso: string): string {
  try {
    const d = new Date(iso);
    const now = Date.now();
    const deltaMs = d.getTime() - now;
    const abs = Math.abs(deltaMs);
    const minutes = Math.round(abs / 60_000);
    const hours = Math.round(abs / 3_600_000);
    const days = Math.round(abs / 86_400_000);
    const sign = deltaMs >= 0 ? "in" : "";
    const past = deltaMs < 0 ? " ago" : "";
    let phrase: string;
    if (minutes < 60) phrase = `${minutes}m`;
    else if (hours < 24) phrase = `${hours}h`;
    else phrase = `${days}d`;
    return `${formatAbsolute(iso)} (${sign} ${phrase}${past})`.trim();
  } catch {
    return iso;
  }
}
