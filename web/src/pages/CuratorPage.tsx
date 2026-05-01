import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  CuratorRunDetail,
  CuratorRunSummary,
  CuratorState,
} from "../lib/types";
import {
  classNames,
  prettyRunFolder,
  relativeTime,
} from "../lib/format";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { Markdown } from "../components/Markdown";

interface CuratorPageProps {
  token: string;
  onAuthFail: () => void;
}

export function CuratorPage({ token, onAuthFail }: CuratorPageProps) {
  const [state, setState] = useState<CuratorState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [runDetails, setRunDetails] = useState<Record<string, CuratorRunDetail>>(
    {},
  );
  const [expanded, setExpanded] = useState<string | null>(null);
  const [forceMessage, setForceMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.curator(token);
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
    refresh();
  }, [refresh]);

  const handleForceRun = useCallback(async () => {
    setRunning(true);
    setForceMessage(null);
    try {
      const result = await api.forceCuratorRun(token);
      setForceMessage(
        `Pass complete · phase 1 archived ${result.phase1.archived}, marked ${result.phase1.marked_stale} stale${
          result.phase2.ran
            ? ` · phase 2 archived ${result.phase2.archived_names.length}, created ${result.phase2.created_names.length}`
            : " · phase 2 skipped"
        }.`,
      );
      await refresh();
    } catch (exc: unknown) {
      if (exc instanceof ApiError) {
        if (exc.status === 401) {
          onAuthFail();
          return;
        }
        setForceMessage(`Force run failed: ${exc.message}`);
      } else {
        setForceMessage("Force run failed.");
      }
    } finally {
      setRunning(false);
    }
  }, [token, refresh, onAuthFail]);

  const handleExpand = useCallback(
    async (folder: string) => {
      if (expanded === folder) {
        setExpanded(null);
        return;
      }
      setExpanded(folder);
      if (!runDetails[folder]) {
        try {
          const detail = await api.curatorRun(token, folder);
          setRunDetails((prev) => ({ ...prev, [folder]: detail }));
        } catch {
          // best-effort
        }
      }
    },
    [expanded, runDetails, token],
  );

  if (error) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load curator: {error}
      </div>
    );
  }
  if (!state) {
    return <CuratorSkeleton />;
  }

  return (
    <div className="space-y-8">
      <Section title="Curator status">
        <Card>
          <div className="grid gap-x-8 gap-y-3 md:grid-cols-[max-content_1fr_max-content] items-center px-5 py-4">
            <div className="flex items-center gap-3">
              {state.paused ? (
                <Badge tone="stale" glyph="❚❚">
                  paused
                </Badge>
              ) : (
                <Badge tone="active" glyph="●">
                  enabled
                </Badge>
              )}
              {state.running && (
                <Badge tone="warn" glyph="▲">
                  running
                </Badge>
              )}
            </div>
            <dl className="grid gap-y-1 gap-x-6 md:grid-cols-2 font-data text-[12.5px]">
              <Row label="Last run" value={relativeTime(state.last_run_at)} />
              <Row
                label="Next eligible"
                value={relativeTime(state.next_eligible_at)}
              />
              <Row label="Interval" value={`${state.interval_hours}h`} />
              <Row
                label="Stale / archive"
                value={`${state.stale_after_days}d / ${state.archive_after_days}d`}
              />
              <Row label="Archived skills" value={`${state.archived_count}`} />
            </dl>
            <div className="flex items-end justify-end gap-2 md:row-span-2 md:col-start-3 md:row-start-1">
              <Button
                variant="primary"
                onClick={handleForceRun}
                loading={running || state.running}
                disabled={running || state.running}
              >
                {running || state.running ? "Running…" : "Force run now"}
              </Button>
            </div>
            {state.last_run_summary && (
              <p className="md:col-span-3 font-data text-[11.5px] text-[var(--color-fg-dim)] leading-relaxed">
                {state.last_run_summary}
              </p>
            )}
            {forceMessage && (
              <p className="md:col-span-3 font-data text-[12px] text-[var(--color-accent)]">
                {forceMessage}
              </p>
            )}
          </div>
        </Card>
      </Section>

      <Section
        title="Run history"
        trailing={`${state.runs.length} run${state.runs.length === 1 ? "" : "s"} on disk`}
      >
        {state.runs.length === 0 ? (
          <EmptyState
            glyph="◇"
            title="No curator runs yet"
            hint="The first scheduled pass fires after the seed interval. Force run to skip the wait."
          />
        ) : (
          <ul className="space-y-2">
            {state.runs.map((run, idx) => (
              <RunRow
                key={run.folder}
                run={run}
                expanded={expanded === run.folder}
                detail={runDetails[run.folder]}
                onExpand={() => handleExpand(run.folder)}
                delay={Math.min(idx, 6) * 35}
              />
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[var(--color-fg-dim)] uppercase-tight text-[10px]">
        {label}
      </span>
      <span className="text-[var(--color-fg)]">{value}</span>
    </div>
  );
}

interface RunRowProps {
  run: CuratorRunSummary;
  expanded: boolean;
  detail?: CuratorRunDetail;
  onExpand: () => void;
  delay: number;
}

function RunRow({ run, expanded, detail, onExpand, delay }: RunRowProps) {
  return (
    <Card delay={delay}>
      <button
        onClick={onExpand}
        className={classNames(
          "w-full text-left px-4 py-3 transition-colors",
          "hover:bg-[var(--color-surface-2)] focus:outline-none focus-visible:bg-[var(--color-surface-2)]",
        )}
        aria-expanded={expanded}
      >
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
          <span className="font-data text-[12.5px] text-[var(--color-fg)]">
            {prettyRunFolder(run.folder)}
          </span>
          <span className="font-data text-[10.5px] text-[var(--color-fg-dim)]">
            {relativeTime(run.started_at)}
          </span>
          <div className="ml-auto flex items-baseline gap-3 font-data text-[11px] text-[var(--color-fg-2)]">
            <Stat label="checked" value={run.phase1.checked} />
            <Stat label="stale" value={run.phase1.marked_stale} />
            <Stat label="archived" value={run.phase1.archived} />
            <Stat label="reactivated" value={run.phase1.reactivated} />
            {run.phase2_ran ? (
              <Badge tone="accent" glyph="◆">
                phase 2
              </Badge>
            ) : (
              <Badge tone="subtle" glyph="◇">
                phase 1 only
              </Badge>
            )}
          </div>
        </div>
        {(run.phase2_archived.length > 0 || run.phase2_created.length > 0) && (
          <div className="mt-2 font-data text-[11px] text-[var(--color-fg-dim)] flex flex-wrap gap-x-4 gap-y-1">
            {run.phase2_created.length > 0 && (
              <span>
                <span className="text-[var(--color-fg-2)]">created:</span>{" "}
                {run.phase2_created.join(", ")}
              </span>
            )}
            {run.phase2_archived.length > 0 && (
              <span>
                <span className="text-[var(--color-fg-2)]">archived:</span>{" "}
                {run.phase2_archived.join(", ")}
              </span>
            )}
          </div>
        )}
        {run.phase2_error && (
          <p className="mt-1 font-data text-[11px] text-[var(--color-error)]">
            phase 2 error: {run.phase2_error}
          </p>
        )}
      </button>
      {expanded && (
        <div className="border-t border-[var(--color-border)] bg-[var(--color-base)]/40 px-4 py-4">
          {detail ? (
            <Markdown source={detail.report_md || "_(empty REPORT.md)_"} />
          ) : (
            <p className="text-xs text-[var(--color-fg-dim)] animate-pulse-slow">
              Loading report…
            </p>
          )}
          {detail?.run_json && (
            <details className="mt-4">
              <summary className="font-data text-[10px] uppercase-tight text-[var(--color-fg-dim)] cursor-pointer hover:text-[var(--color-fg-2)]">
                run.json
              </summary>
              <pre className="mt-2 font-data text-[11px] text-[var(--color-fg-2)] bg-[var(--color-base)] hairline p-3 overflow-x-auto whitespace-pre-wrap">
                {JSON.stringify(detail.run_json, null, 2)}
              </pre>
            </details>
          )}
        </div>
      )}
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <span>
      <span className="text-[var(--color-fg-dim)]">{label}:</span>{" "}
      <span className="text-[var(--color-fg)]">{value}</span>
    </span>
  );
}

function CuratorSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-24 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="h-14 hairline bg-[var(--color-surface)] animate-pulse-slow"
        />
      ))}
    </div>
  );
}
