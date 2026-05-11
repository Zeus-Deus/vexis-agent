import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  BackgroundTaskSummary,
  ForegroundChat,
  LogLine,
  StatusState,
} from "../lib/types";
import { classNames, relativeTime, uptime } from "../lib/format";
import { Badge } from "../components/Badge";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface StatusPageProps {
  token: string;
  onAuthFail: () => void;
}

const POLL_INTERVAL_MS = 5000;

export function StatusPage({ token, onAuthFail }: StatusPageProps) {
  const [state, setState] = useState<StatusState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    async function load() {
      try {
        const data = await api.status(token);
        if (cancelled) return;
        setState(data);
        setError(null);
      } catch (exc: unknown) {
        if (cancelled) return;
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        if (!cancelled) {
          timer = window.setTimeout(load, POLL_INTERVAL_MS);
        }
      }
    }
    load();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [token, onAuthFail]);

  if (error && !state) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load status: {error}
      </div>
    );
  }
  if (!state) {
    return <StatusSkeleton />;
  }

  return (
    <div className="space-y-8">
      <DaemonHeader state={state} />

      <div className="grid gap-8 lg:grid-cols-2">
        <Section
          title="Foreground"
          trailing={`${state.foreground_chats.length} chat${
            state.foreground_chats.length === 1 ? "" : "s"
          } in flight`}
        >
          {state.foreground_chats.length === 0 ? (
            <EmptyState glyph="○" title="No foreground brain calls right now" />
          ) : (
            <ul className="space-y-2">
              {state.foreground_chats.map((chat, i) => (
                <ForegroundCard key={chat.chat_id} chat={chat} delay={i * 30} />
              ))}
            </ul>
          )}
        </Section>

        <Section
          title="Background tasks"
          trailing={`${state.background_tasks.length} known`}
        >
          {state.background_tasks.length === 0 ? (
            <EmptyState glyph="◇" title="No background tasks" />
          ) : (
            <ul className="space-y-2">
              {state.background_tasks.map((task, i) => (
                <BackgroundCard key={task.name} task={task} delay={i * 30} />
              ))}
            </ul>
          )}
        </Section>
      </div>

      <LogStream lines={state.log_lines} />
    </div>
  );
}

function DaemonHeader({ state }: { state: StatusState }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-x-8 gap-y-3 px-5 py-4">
        <div className="flex items-baseline gap-3">
          <Badge tone="active" glyph="●">
            running
          </Badge>
          <span className="font-data text-[13px] text-[var(--color-fg)]">
            up {uptime(state.uptime_seconds)}
          </span>
        </div>
        <div className="flex items-baseline gap-2 font-data text-[12px] text-[var(--color-fg-2)]">
          <span className="text-[var(--color-fg-dim)] uppercase-tight text-[10px]">
            sessions
          </span>
          <span className="text-[var(--color-fg)]">{state.session_count}</span>
          <span className="text-[var(--color-fg-dim)]">·</span>
          <span className="text-[var(--color-fg-dim)] uppercase-tight text-[10px]">
            active
          </span>
          <span className="text-[var(--color-fg)]">{state.active_session}</span>
        </div>
        <div className="ml-auto font-data text-[11px] text-[var(--color-fg-dim)]">
          started {new Date(state.started_at).toLocaleString()}
        </div>
      </div>
    </Card>
  );
}

function ForegroundCard({
  chat,
  delay,
}: {
  chat: ForegroundChat;
  delay: number;
}) {
  return (
    <Card delay={delay}>
      <div className="flex items-baseline gap-3 px-4 py-3 flex-wrap">
        <span className="font-data text-[12.5px] text-[var(--color-fg)]">
          chat {chat.chat_id}
        </span>
        {chat.drain_active ? (
          <Badge tone="active" glyph="●">
            drain
          </Badge>
        ) : (
          <Badge tone="subtle">idle</Badge>
        )}
        {chat.slot_reserved && (
          <Badge tone="accent" glyph="▲">
            slot
          </Badge>
        )}
        {chat.cancelled && (
          <Badge tone="warn" glyph="✕">
            cancel pending
          </Badge>
        )}
        {chat.queue_depth > 0 && (
          <span className="font-data text-[11px] text-[var(--color-fg-dim)]">
            queue {chat.queue_depth}
          </span>
        )}
        {chat.slot_pid && (
          <span className="ml-auto font-data text-[11px] text-[var(--color-fg-dim)]">
            pid {chat.slot_pid}
          </span>
        )}
      </div>
    </Card>
  );
}

function BackgroundCard({
  task,
  delay,
}: {
  task: BackgroundTaskSummary;
  delay: number;
}) {
  return (
    <Card delay={delay}>
      <div className="px-4 py-3">
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="font-data text-[12.5px] text-[var(--color-fg)]">
            {task.name}
          </span>
          <BackgroundStatus status={task.status} exitCode={task.exit_code} />
          <span className="font-data text-[11px] text-[var(--color-fg-dim)]">
            chat {task.chat_id}
          </span>
          {task.pid && (
            <span className="font-data text-[11px] text-[var(--color-fg-dim)]">
              pid {task.pid}
            </span>
          )}
          <span className="ml-auto font-data text-[11px] text-[var(--color-fg-dim)]">
            spawned {relativeTime(task.spawned_at)}
            {task.finished_at && ` · finished ${relativeTime(task.finished_at)}`}
          </span>
        </div>
        <p className="mt-1 font-data text-[10.5px] text-[var(--color-fg-dim)] truncate">
          {task.log_path}
        </p>
        {(task.sandbox_enabled || task.verify_summary) && (
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-data text-[10.5px]">
            {task.sandbox_enabled && (
              <span className="text-[var(--color-fg-dim)]">
                ⊞ sandbox
                {task.verify_checks_path
                  ? ` + verify (${task.verify_checks_path})`
                  : ""}
              </span>
            )}
            {task.verify_summary && (
              <span className="text-[var(--color-fg-2)]">
                checks: {task.verify_summary}
              </span>
            )}
          </div>
        )}
      </div>
    </Card>
  );
}

function BackgroundStatus({
  status,
  exitCode,
}: {
  status: BackgroundTaskSummary["status"];
  exitCode: number | null;
}) {
  switch (status) {
    case "running":
      return (
        <Badge tone="active" glyph="●">
          running
        </Badge>
      );
    case "pending":
      return (
        <Badge tone="warn" glyph="▲">
          pending
        </Badge>
      );
    case "finished":
      return (
        <Badge tone="subtle" glyph="✓">
          finished
        </Badge>
      );
    case "cancelled":
      return (
        <Badge tone="stale" glyph="✕">
          cancelled
        </Badge>
      );
    case "failed":
      return (
        <Badge tone="error" glyph="✕">
          failed{exitCode !== null && ` ${exitCode}`}
        </Badge>
      );
  }
}

function LogStream({ lines }: { lines: LogLine[] }) {
  return (
    <Section
      title="Recent log"
      trailing={`last ${lines.length} lines · ~/.local/state/vexis-agent/vexis.log`}
    >
      {lines.length === 0 ? (
        <EmptyState glyph="·" title="No log output yet" />
      ) : (
        <Card>
          <pre className="font-data text-[11.5px] leading-[1.5] p-4 overflow-x-auto max-h-[480px] overflow-y-auto">
            {lines.map((line, idx) => (
              <LogRow key={idx} line={line} />
            ))}
          </pre>
        </Card>
      )}
    </Section>
  );
}

function LogRow({ line }: { line: LogLine }) {
  const tone = (() => {
    switch (line.level.toUpperCase()) {
      case "ERROR":
      case "CRITICAL":
        return "text-[var(--color-error)]";
      case "WARNING":
      case "WARN":
        return "text-[var(--color-warn)]";
      case "INFO":
        return "text-[var(--color-fg-2)]";
      default:
        return "text-[var(--color-fg-dim)]";
    }
  })();
  return (
    <div className="grid grid-cols-[12ch_7ch_minmax(0,18ch)_1fr] gap-3">
      <span className="text-[var(--color-fg-dim)]">{line.ts.slice(11)}</span>
      <span className={classNames("uppercase tracking-wide", tone)}>
        {line.level}
      </span>
      <span className="text-[var(--color-fg-dim)] truncate" title={line.logger}>
        {line.logger}
      </span>
      <span className="whitespace-pre-wrap break-words text-[var(--color-fg)]">
        {line.message}
      </span>
    </div>
  );
}

function StatusSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-16 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <div
            key={i}
            className="h-32 hairline bg-[var(--color-surface)] animate-pulse-slow"
          />
        ))}
      </div>
      <div className="h-64 hairline bg-[var(--color-surface)] animate-pulse-slow" />
    </div>
  );
}
