import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  ActiveSkill,
  ArchivedSkill,
  SkillBody,
  SkillsState,
} from "../lib/types";
import {
  classNames,
  formatNumber,
  relativeTime,
} from "../lib/format";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";
import { Markdown } from "../components/Markdown";

interface SkillsPageProps {
  token: string;
  onAuthFail: () => void;
}

export function SkillsPage({ token, onAuthFail }: SkillsPageProps) {
  const [state, setState] = useState<SkillsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bodies, setBodies] = useState<Record<string, SkillBody>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pendingByName, setPendingByName] = useState<Record<string, string>>({});

  const refresh = useCallback(async () => {
    try {
      const data = await api.skills(token);
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

  const handleExpand = useCallback(
    async (name: string) => {
      if (expanded === name) {
        setExpanded(null);
        return;
      }
      setExpanded(name);
      if (!bodies[name]) {
        try {
          const body = await api.skillBody(token, name);
          setBodies((prev) => ({ ...prev, [name]: body }));
        } catch {
          // best-effort; the inline error is fine
        }
      }
    },
    [expanded, bodies, token],
  );

  const handlePinToggle = useCallback(
    async (skill: ActiveSkill) => {
      setPendingByName((p) => ({ ...p, [skill.name]: "pin" }));
      try {
        if (skill.pinned) {
          await api.unpinSkill(token, skill.name);
        } else {
          await api.pinSkill(token, skill.name);
        }
        await refresh();
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) onAuthFail();
      } finally {
        setPendingByName((p) => {
          const next = { ...p };
          delete next[skill.name];
          return next;
        });
      }
    },
    [token, refresh, onAuthFail],
  );

  const handleRestore = useCallback(
    async (name: string) => {
      setPendingByName((p) => ({ ...p, [name]: "restore" }));
      try {
        await api.restoreSkill(token, name);
        await refresh();
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) onAuthFail();
      } finally {
        setPendingByName((p) => {
          const next = { ...p };
          delete next[name];
          return next;
        });
      }
    },
    [token, refresh, onAuthFail],
  );

  if (error) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load skills: {error}
      </div>
    );
  }
  if (!state) {
    return <SkillsSkeleton />;
  }

  const activeSorted = [...state.active].sort((a, b) => {
    if (a.pinned !== b.pinned) return a.pinned ? -1 : 1;
    return a.name.localeCompare(b.name);
  });

  return (
    <div className="grid gap-8 lg:grid-cols-[3fr_2fr]">
      <Section
        title="Active skills"
        trailing={`${state.active.length} total · ${state.active.filter((s) => s.pinned).length} pinned`}
      >
        {activeSorted.length === 0 ? (
          <EmptyState
            glyph="◇"
            title="No skills yet"
            hint="Vexis creates skills as he learns repeatable workflows. The curator's umbrella-builder will keep this set tight over time."
          />
        ) : (
          <ul className="space-y-2">
            {activeSorted.map((skill, idx) => (
              <SkillRow
                key={skill.name}
                skill={skill}
                expanded={expanded === skill.name}
                body={bodies[skill.name]}
                pending={pendingByName[skill.name]}
                onExpand={() => handleExpand(skill.name)}
                onPinToggle={() => handlePinToggle(skill)}
                delay={Math.min(idx, 8) * 35}
              />
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Archived"
        trailing={`${state.archived.length}`}
      >
        {state.archived.length === 0 ? (
          <EmptyState
            glyph="■"
            title="Archive is empty"
            hint="When the curator archives skills they land here. Restore brings one back to the active tree."
          />
        ) : (
          <ul className="space-y-2">
            {state.archived.map((skill, idx) => (
              <ArchivedRow
                key={skill.name}
                skill={skill}
                pending={pendingByName[skill.name]}
                onRestore={() => handleRestore(skill.name)}
                delay={Math.min(idx, 8) * 35}
              />
            ))}
          </ul>
        )}
      </Section>
    </div>
  );
}

interface SkillRowProps {
  skill: ActiveSkill;
  expanded: boolean;
  body?: SkillBody;
  pending?: string;
  onExpand: () => void;
  onPinToggle: () => void;
  delay: number;
}

function SkillRow({
  skill,
  expanded,
  body,
  pending,
  onExpand,
  onPinToggle,
  delay,
}: SkillRowProps) {
  return (
    <Card delay={delay}>
      <button
        onClick={onExpand}
        className={classNames(
          "w-full text-left px-4 py-3 transition-colors",
          "hover:bg-[var(--color-surface-2)]",
          "focus:outline-none focus-visible:bg-[var(--color-surface-2)]",
        )}
        aria-expanded={expanded}
      >
        <div className="flex items-baseline gap-3 flex-wrap">
          {skill.category && (
            <span className="font-data text-[11px] text-[var(--color-fg-dim)]">
              {skill.category}/
            </span>
          )}
          <span className="font-data text-[13px] text-[var(--color-fg)] font-medium">
            {skill.name}
          </span>
          <StateBadge state={skill.state} />
          {skill.pinned && (
            <Badge tone="accent" glyph="◉">
              pinned
            </Badge>
          )}
          <span className="ml-auto font-data text-[11px] text-[var(--color-fg-dim)] flex items-baseline gap-3">
            <span>
              <span className="text-[var(--color-fg-2)]">use:</span>{" "}
              {formatNumber(skill.use_count)}
            </span>
            <span>
              <span className="text-[var(--color-fg-2)]">view:</span>{" "}
              {formatNumber(skill.view_count)}
            </span>
            <span>{relativeTime(skill.last_used_at)}</span>
          </span>
        </div>
        <p className="mt-1.5 text-[13px] text-[var(--color-fg-2)] leading-snug">
          {skill.description}
        </p>
      </button>

      {expanded && (
        <div className="border-t border-[var(--color-border)] bg-[var(--color-base)]/40">
          <div className="px-4 py-3 flex items-center gap-2 border-b border-[var(--color-border)]">
            <Button
              size="sm"
              variant={skill.pinned ? "ghost" : "primary"}
              onClick={(e) => {
                e.stopPropagation();
                onPinToggle();
              }}
              loading={pending === "pin"}
            >
              {skill.pinned ? "Unpin" : "Pin"}
            </Button>
            <span className="font-data text-[10.5px] text-[var(--color-fg-dim)] truncate">
              {skill.path}
            </span>
          </div>
          <div className="px-4 py-4 max-h-[600px] overflow-y-auto">
            {body ? (
              <Markdown source={body.body} />
            ) : (
              <p className="text-xs text-[var(--color-fg-dim)] animate-pulse-slow">
                Loading skill body…
              </p>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function StateBadge({ state }: { state: ActiveSkill["state"] }) {
  if (state === "active") {
    return (
      <Badge tone="active" glyph="●">
        active
      </Badge>
    );
  }
  if (state === "stale") {
    return (
      <Badge tone="stale" glyph="○">
        stale
      </Badge>
    );
  }
  return (
    <Badge tone="archived" glyph="■">
      archived
    </Badge>
  );
}

interface ArchivedRowProps {
  skill: ArchivedSkill;
  pending?: string;
  onRestore: () => void;
  delay: number;
}

function ArchivedRow({ skill, pending, onRestore, delay }: ArchivedRowProps) {
  return (
    <Card delay={delay}>
      <div className="flex items-start justify-between gap-3 px-4 py-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-baseline gap-3">
            <span className="font-data text-[12.5px] text-[var(--color-fg-2)]">
              {skill.name}
            </span>
            <span className="font-data text-[10.5px] text-[var(--color-fg-dim)]">
              archived {relativeTime(skill.archived_at)}
            </span>
          </div>
          {skill.description && (
            <p className="mt-1 text-xs text-[var(--color-fg-dim)] leading-snug line-clamp-2">
              {skill.description}
            </p>
          )}
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={onRestore}
          loading={pending === "restore"}
        >
          Restore
        </Button>
      </div>
    </Card>
  );
}

function SkillsSkeleton() {
  return (
    <div className="grid gap-8 lg:grid-cols-[3fr_2fr]">
      {[0, 1].map((c) => (
        <div key={c} className="space-y-3">
          <div className="h-4 w-32 bg-[var(--color-border)] animate-pulse-slow" />
          {[0, 1, 2, 3].map((i) => (
            <div
              key={i}
              className="h-16 hairline bg-[var(--color-surface)] animate-pulse-slow"
            />
          ))}
        </div>
      ))}
    </div>
  );
}
