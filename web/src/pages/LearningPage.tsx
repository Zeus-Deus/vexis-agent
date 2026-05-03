import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, ApiError } from "../lib/api";
import type {
  CoherenceVerdict,
  LearningActivityRow,
  LearningCuratorRow,
  LearningDistribution,
  LearningJudgeResult,
  LearningRates,
  LearningShadowEntry,
  LearningState,
  LearningUserCandidate,
} from "../lib/types";
import { classNames, formatNumber, relativeTime } from "../lib/format";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface LearningPageProps {
  token: string;
  onAuthFail: () => void;
}

const POLL_INTERVAL_MS = 5000;

type JudgeMapEntry = LearningJudgeResult | "pending";

export function LearningPage({ token, onAuthFail }: LearningPageProps) {
  const [state, setState] = useState<LearningState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [judgeMap, setJudgeMap] = useState<Record<string, JudgeMapEntry>>({});

  const refresh = useCallback(async () => {
    try {
      const data = await api.learning(token);
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

  const handleReJudge = useCallback(
    async (entry: LearningShadowEntry) => {
      if (!entry.entry_id) return;
      const id = entry.entry_id;
      setJudgeMap((prev) => ({ ...prev, [id]: "pending" }));
      try {
        const result = await api.learningCoherenceAudit(token, {
          lesson: entry.lesson,
          scope: entry.scope ?? "",
          evidence: entry.evidence ?? "",
          class: entry.class ?? null,
          tier: entry.tier ?? null,
          source: entry.source,
          entry_id: entry.entry_id,
        });
        setJudgeMap((prev) => ({ ...prev, [id]: result }));
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setJudgeMap((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }
    },
    [token, onAuthFail],
  );

  if (error) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load learning state: {error}
      </div>
    );
  }
  if (!state) {
    return <LearningSkeleton />;
  }

  return (
    <div className="space-y-8">
      <CuratorsPanel rows={state.curators} />
      <DistributionPanel distribution={state.distribution} />
      <ActivityPanel
        rows={state.recent_activity}
        shadowByEntryId={indexByEntryId(state.shadow_entries)}
        judgeMap={judgeMap}
        onReJudge={handleReJudge}
      />
      <div className="grid gap-6 lg:grid-cols-2">
        <UserCandidatesPanel candidates={state.user_candidates} />
        <CoherencePendingPanel
          entries={state.coherence_pending_review}
          judgeMap={judgeMap}
          onReJudge={handleReJudge}
        />
      </div>
      <CuratorSkillsPanel skills={state.curator_skills} />
      <RatesPanel rates={state.rates} />
      <ModelsPanel models={state.models} />
    </div>
  );
}

// ---- Curators panel ---------------------------------------------

function CuratorsPanel({ rows }: { rows: LearningCuratorRow[] }) {
  return (
    <Section title="Curators">
      <Card>
        <ul className="divide-y divide-[var(--color-border)]">
          {rows.map((row) => (
            <CuratorRowItem key={row.name} row={row} />
          ))}
        </ul>
      </Card>
    </Section>
  );
}

function CuratorRowItem({ row }: { row: LearningCuratorRow }) {
  const nested = row.nested_under !== null;
  return (
    <li
      className={classNames(
        "px-5 py-3",
        nested && "pl-10 bg-[var(--color-surface)]/50",
      )}
    >
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span
          className={classNames(
            "font-data tracking-tight",
            nested ? "text-[10.5px] text-[var(--color-fg-2)]" : "text-[12.5px] text-[var(--color-fg)]",
          )}
        >
          {nested && (
            <span className="text-[var(--color-fg-dim)] mr-1">└</span>
          )}
          {row.name}
        </span>
        {row.paused ? (
          <Badge tone="stale" glyph="❚❚">
            paused
          </Badge>
        ) : row.enabled ? (
          <Badge tone="active" glyph="●">
            {nested ? "on" : "enabled"}
          </Badge>
        ) : (
          <Badge tone="subtle" glyph="○">
            off
          </Badge>
        )}
        {row.running && (
          <Badge tone="warn" glyph="▲">
            running
          </Badge>
        )}
        <div className="ml-auto flex items-baseline gap-4 font-data text-[11px] text-[var(--color-fg-2)]">
          <RowStat label="last" value={relativeTime(row.last_run_at)} />
          <RowStat
            label="next"
            value={
              row.next_eligible_at
                ? relativeTime(row.next_eligible_at)
                : "—"
            }
          />
          <RowStat label="every" value={row.interval_label} />
        </div>
      </div>
      <p
        className={classNames(
          "mt-1 font-data text-[var(--color-fg-dim)] leading-relaxed",
          nested ? "text-[10.5px]" : "text-[11.5px]",
        )}
      >
        {row.summary}
      </p>
    </li>
  );
}

function RowStat({ label, value }: { label: string; value: string }) {
  return (
    <span>
      <span className="text-[var(--color-fg-dim)] uppercase-tight">{label}:</span>{" "}
      <span className="text-[var(--color-fg)]">{value}</span>
    </span>
  );
}

// ---- Distribution panel -----------------------------------------

const TIER_ORDER = ["S1", "S2", "S3", "MEM", "USER"];
const CLASS_ORDER = ["PROCEDURAL", "IDENTITY", "SITUATIONAL"];

function DistributionPanel({
  distribution,
}: {
  distribution: LearningDistribution;
}) {
  const totalClass = sumValues(distribution.by_class);
  const totalTier = sumValues(distribution.by_tier);
  return (
    <Section
      title="Distribution"
      trailing={`last ${distribution.window_ticks} tick reports`}
    >
      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="px-4 py-3">
            <h3 className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] mb-2">
              by class
            </h3>
            {totalClass === 0 ? (
              <p className="text-xs text-[var(--color-fg-dim)]">No writes yet.</p>
            ) : (
              orderedKeys(distribution.by_class, CLASS_ORDER).map((key) => (
                <DistRow
                  key={key}
                  label={key}
                  count={distribution.by_class[key] ?? 0}
                  max={totalClass}
                />
              ))
            )}
          </div>
        </Card>
        <Card>
          <div className="px-4 py-3">
            <h3 className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] mb-2">
              by tier
            </h3>
            {totalTier === 0 ? (
              <p className="text-xs text-[var(--color-fg-dim)]">No writes yet.</p>
            ) : (
              orderedKeys(distribution.by_tier, TIER_ORDER).map((key) => (
                <DistRow
                  key={key}
                  label={key}
                  count={distribution.by_tier[key] ?? 0}
                  max={totalTier}
                />
              ))
            )}
            {distribution.a2_watch && (
              <p className="mt-2 font-data text-[10.5px] text-[var(--color-warn)]">
                ⚠ S1 at 0 across procedural writes — see
                v2-hermes-verification.md A2.
              </p>
            )}
          </div>
        </Card>
      </div>
    </Section>
  );
}

function DistRow({
  label,
  count,
  max,
}: {
  label: string;
  count: number;
  max: number;
}) {
  const pct = max > 0 ? Math.max(2, (count / max) * 100) : 0;
  return (
    <div className="flex items-center gap-3 py-1 font-data text-[11.5px]">
      <span className="w-24 text-[var(--color-fg-2)] uppercase-tight">
        {label}
      </span>
      <div className="flex-1 h-3 hairline bg-[var(--color-base)] relative">
        <div
          className="absolute inset-y-0 left-0 bg-[var(--color-accent)]/[0.18]"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-10 text-right text-[var(--color-fg)] tabular-nums">
        {formatNumber(count)}
      </span>
    </div>
  );
}

// ---- Activity feed ----------------------------------------------

function ActivityPanel({
  rows,
  shadowByEntryId,
  judgeMap,
  onReJudge,
}: {
  rows: LearningActivityRow[];
  shadowByEntryId: Record<string, LearningShadowEntry>;
  judgeMap: Record<string, JudgeMapEntry>;
  onReJudge: (entry: LearningShadowEntry) => void;
}) {
  return (
    <Section
      title="Recent activity"
      trailing={
        rows.length === 0
          ? "no outcomes yet"
          : `${rows.length} of last ${rows.length} outcomes`
      }
    >
      {rows.length === 0 ? (
        <EmptyState
          glyph="▲"
          title="No tick outcomes yet"
          hint="Outcomes will appear once the learning curator fires a tick."
        />
      ) : (
        <ul className="space-y-2">
          {rows.map((row, i) => (
            <ActivityRowItem
              key={`${row.tick_folder}-${row.session_uuid_prefix}-${i}`}
              row={row}
              shadow={
                row.entry_id ? shadowByEntryId[row.entry_id] ?? null : null
              }
              judgeMap={judgeMap}
              onReJudge={onReJudge}
              delay={Math.min(i, 8) * 30}
            />
          ))}
        </ul>
      )}
    </Section>
  );
}

const OUTCOME_GLYPH: Record<string, string> = {
  wrote: "§",
  rejected: "×",
  "nothing-to-save": "·",
  cooldown: "…",
  error: "!",
};

function ActivityRowItem({
  row,
  shadow,
  judgeMap,
  onReJudge,
  delay,
}: {
  row: LearningActivityRow;
  shadow: LearningShadowEntry | null;
  judgeMap: Record<string, JudgeMapEntry>;
  onReJudge: (entry: LearningShadowEntry) => void;
  delay: number;
}) {
  const time = row.tick_at ? formatHM(row.tick_at) : "--:--";
  const verdict = (() => {
    if (row.entry_id && judgeMap[row.entry_id]) {
      const v = judgeMap[row.entry_id];
      if (v === "pending") return null;
      return v.verdict;
    }
    return row.coherence_verdict;
  })();
  const showLessonRow = row.outcome === "wrote" && row.lesson_preview;
  const showRejudge =
    shadow &&
    (verdict === "INCOHERENT" || verdict === "NEAR_MISS_REVIEW");
  const judgePending =
    row.entry_id && judgeMap[row.entry_id] === "pending";
  return (
    <Card delay={delay}>
      <div className="px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-data text-[11.5px]">
          <span
            aria-hidden
            className="text-[var(--color-fg-dim)] w-3 inline-block"
          >
            {OUTCOME_GLYPH[row.outcome] ?? "·"}
          </span>
          <span className="text-[var(--color-fg-2)] tabular-nums">{time}</span>
          {row.session_uuid_prefix && (
            <span className="text-[var(--color-fg-dim)] tabular-nums">
              {row.session_uuid_prefix}
            </span>
          )}
          {row.class && (
            <Badge tone={row.class === "IDENTITY" ? "accent" : "subtle"}>
              {row.class}
            </Badge>
          )}
          {row.tier && <Badge tone="subtle">{row.tier}</Badge>}
          <CoherenceBadge verdict={verdict} />
          {row.source && (
            <span className="ml-auto text-[var(--color-fg-dim)] text-[10.5px]">
              [{row.source}]
            </span>
          )}
        </div>
        {showLessonRow && (
          <p
            className="mt-1 font-data text-[12px] text-[var(--color-fg)] line-clamp-3 leading-relaxed"
            title={row.lesson_preview ?? undefined}
          >
            {row.lesson_preview}
          </p>
        )}
        {(row.outcome_marker || row.outcome !== "wrote") && (
          <p className="mt-1 font-data text-[10.5px] text-[var(--color-fg-dim)]">
            → {row.outcome_marker ?? row.outcome_detail}
          </p>
        )}
        {showRejudge && shadow && (
          <div className="mt-2">
            <Button
              size="sm"
              variant="ghost"
              loading={Boolean(judgePending)}
              onClick={() => onReJudge(shadow)}
            >
              {judgePending ? "judging…" : "re-judge"}
            </Button>
          </div>
        )}
      </div>
    </Card>
  );
}

// ---- User candidates panel --------------------------------------

function UserCandidatesPanel({
  candidates,
}: {
  candidates: { pending: LearningUserCandidate[]; promoted_count: number };
}) {
  return (
    <Section title="USER candidate queue">
      <Card>
        <div className="px-4 py-3">
          <p className="font-data text-[11px] text-[var(--color-fg-2)]">
            <span className="text-[var(--color-fg-dim)]">pending:</span>{" "}
            {candidates.pending.length}
            {"  ·  "}
            <span className="text-[var(--color-fg-dim)]">promoted:</span>{" "}
            {candidates.promoted_count}
          </p>
          {candidates.pending.length === 0 ? (
            <p className="mt-3 text-xs text-[var(--color-fg-dim)]">
              No identity claims awaiting promotion.
            </p>
          ) : (
            <ul className="mt-3 space-y-2">
              {candidates.pending.map((c, i) => (
                <li
                  key={i}
                  className="font-data text-[11.5px] leading-relaxed"
                >
                  <p className="text-[var(--color-fg)] line-clamp-2">
                    {c.claim_preview}
                  </p>
                  <p className="text-[10.5px] text-[var(--color-fg-dim)] mt-0.5">
                    {c.distinct_sessions}/{c.threshold} sessions
                    {"  ·  "}
                    {c.days_until_expiry}d until expiry
                  </p>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </Section>
  );
}

// ---- Coherence pending panel ------------------------------------

function CoherencePendingPanel({
  entries,
  judgeMap,
  onReJudge,
}: {
  entries: LearningShadowEntry[];
  judgeMap: Record<string, JudgeMapEntry>;
  onReJudge: (entry: LearningShadowEntry) => void;
}) {
  return (
    <Section title="Coherence flags">
      {entries.length === 0 ? (
        <EmptyState
          glyph="▲"
          title="No flagged entries"
          hint="No INCOHERENT or NEAR_MISS entries currently in shadow / staging. Re-audit any flagged entry from the activity feed."
        />
      ) : (
        <ul className="space-y-2">
          {entries.map((e) => (
            <CoherencePendingRow
              key={e.entry_id}
              entry={e}
              judgeMap={judgeMap}
              onReJudge={onReJudge}
            />
          ))}
        </ul>
      )}
    </Section>
  );
}

function CoherencePendingRow({
  entry,
  judgeMap,
  onReJudge,
}: {
  entry: LearningShadowEntry;
  judgeMap: Record<string, JudgeMapEntry>;
  onReJudge: (entry: LearningShadowEntry) => void;
}) {
  const judged = judgeMap[entry.entry_id];
  const verdict =
    judged && judged !== "pending" ? judged.verdict : entry.coherence_verdict;
  const reason =
    judged && judged !== "pending" ? judged.reason : entry.coherence_reason;
  const explanation =
    judged && judged !== "pending"
      ? judged.explanation
      : entry.coherence_explanation;
  const pending = judged === "pending";
  return (
    <Card>
      <div className="px-4 py-3">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-data text-[11px]">
          <CoherenceBadge verdict={verdict} />
          {entry.class && <Badge tone="subtle">{entry.class}</Badge>}
          {reason && (
            <span className="text-[var(--color-fg-dim)]">({reason})</span>
          )}
          <span className="ml-auto text-[var(--color-fg-dim)] text-[10.5px]">
            [{entry.source}]
          </span>
        </div>
        <p
          className="mt-1 font-data text-[12px] text-[var(--color-fg)] line-clamp-3 leading-relaxed"
          title={entry.lesson}
        >
          {entry.lesson_preview}
        </p>
        {explanation && (
          <p className="mt-1 text-[11px] text-[var(--color-fg-dim)] line-clamp-2 leading-relaxed">
            {explanation}
          </p>
        )}
        <div className="mt-2">
          <Button
            size="sm"
            variant="ghost"
            loading={pending}
            onClick={() => onReJudge(entry)}
          >
            {pending ? "judging…" : "re-judge"}
          </Button>
        </div>
      </div>
    </Card>
  );
}

// ---- Curator-authored skills panel ------------------------------

function CuratorSkillsPanel({
  skills,
}: {
  skills: LearningState["curator_skills"];
}) {
  return (
    <Section
      title="Curator-authored skills"
      trailing={`live ${skills.live.length} · staged ${skills.staged.length}`}
    >
      <div className="grid gap-3 lg:grid-cols-2">
        <Card>
          <div className="px-4 py-3">
            <h3 className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] mb-2">
              live ({skills.live.length})
            </h3>
            {skills.live.length === 0 ? (
              <p className="text-xs text-[var(--color-fg-dim)]">None yet.</p>
            ) : (
              <ul className="space-y-1 font-data text-[11.5px]">
                {skills.live.map((s) => (
                  <SkillRow key={s.name} name={s.name} origin={s.origin} />
                ))}
              </ul>
            )}
          </div>
        </Card>
        <Card>
          <div className="px-4 py-3">
            <h3 className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] mb-2">
              staged ({skills.staged.length})
            </h3>
            {skills.staged.length === 0 ? (
              <p className="text-xs text-[var(--color-fg-dim)]">None staged.</p>
            ) : (
              <ul className="space-y-1 font-data text-[11.5px]">
                {skills.staged.map((s) => (
                  <SkillRow key={s.name} name={s.name} origin={s.origin} />
                ))}
              </ul>
            )}
          </div>
        </Card>
      </div>
      <p className="mt-2 font-data text-[10.5px] text-[var(--color-fg-dim)]">
        Open the Skills tab to read or edit any of these.
      </p>
    </Section>
  );
}

function SkillRow({ name, origin }: { name: string; origin: string }) {
  return (
    <li className="flex items-baseline gap-2">
      <span className="text-[var(--color-fg)]">{name}</span>
      <span className="text-[var(--color-fg-dim)] text-[10px]">{origin}</span>
    </li>
  );
}

// ---- Rates panel ------------------------------------------------

function RatesPanel({ rates }: { rates: LearningRates }) {
  return (
    <Section
      title="Calibration"
      trailing={`last ${rates.window_ticks_scanned} tick reports`}
    >
      <Card>
        <dl className="grid gap-y-1 gap-x-6 px-4 py-3 md:grid-cols-2 font-data text-[12px]">
          <KV label="Dedup-skipped" value={String(rates.dedup_skipped)} />
          <KV
            label="Coherence flagged"
            value={String(rates.coherence_flagged)}
          />
          <KV
            label="Coherence near-miss"
            value={String(rates.coherence_near_miss)}
          />
          <KV
            label="By reason"
            value={
              Object.keys(rates.coherence_by_reason).length === 0
                ? "—"
                : Object.entries(rates.coherence_by_reason)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(", ")
            }
          />
        </dl>
      </Card>
    </Section>
  );
}

// ---- Models panel -----------------------------------------------

function ModelsPanel({ models }: { models: LearningState["models"] }) {
  const rows: Array<[string, string]> = [
    ["brain", models.brain ?? "—"],
    ["learning_review", models.learning_review ?? "—"],
    ["coherence_judge", models.coherence_judge ?? "—"],
    ["migration_classifier", models.migration_classifier ?? "—"],
  ];
  return (
    <Section title="Models">
      <Card>
        <dl className="grid gap-y-1 gap-x-6 px-4 py-3 md:grid-cols-2 font-data text-[12px]">
          {rows.map(([k, v]) => (
            <KV key={k} label={k} value={v} />
          ))}
        </dl>
        <p className="px-4 pb-3 font-data text-[10.5px] text-[var(--color-fg-dim)]">
          Edit ~/.vexis/config.yaml [models] and restart the daemon to change.
        </p>
      </Card>
    </Section>
  );
}

// ---- Shared little widgets --------------------------------------

function KV({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-baseline gap-2">
      <dt className="text-[var(--color-fg-dim)] uppercase-tight text-[10px]">
        {label}
      </dt>
      <dd className="text-[var(--color-fg)]">{value}</dd>
    </div>
  );
}

function CoherenceBadge({ verdict }: { verdict: CoherenceVerdict | null }) {
  if (!verdict || verdict === "COHERENT") return null;
  if (verdict === "INCOHERENT") {
    return (
      <Badge tone="error" glyph="!">
        flagged
      </Badge>
    );
  }
  return (
    <Badge tone="warn" glyph="◐">
      near-miss
    </Badge>
  );
}

function LearningSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-32 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-44 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-64 hairline bg-[var(--color-surface)] animate-pulse-slow" />
    </div>
  );
}

// ---- pure helpers ------------------------------------------------

function indexByEntryId(
  entries: LearningShadowEntry[],
): Record<string, LearningShadowEntry> {
  const out: Record<string, LearningShadowEntry> = {};
  for (const e of entries) {
    if (e.entry_id) out[e.entry_id] = e;
  }
  return out;
}

function sumValues(o: Record<string, number>): number {
  let total = 0;
  for (const v of Object.values(o)) total += v;
  return total;
}

function orderedKeys(
  o: Record<string, number>,
  preferred: readonly string[],
): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const k of preferred) {
    if (k in o) {
      out.push(k);
      seen.add(k);
    }
  }
  for (const k of Object.keys(o).sort()) {
    if (!seen.has(k)) out.push(k);
  }
  return out;
}

function formatHM(iso: string): string {
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return "--:--";
  const hh = String(dt.getHours()).padStart(2, "0");
  const mm = String(dt.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}
