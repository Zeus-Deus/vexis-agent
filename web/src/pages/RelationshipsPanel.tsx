// v3c Day 4b — RelationshipsPanel.
//
// Sits at the bottom of the Learning tab. Two stacked sections:
//
//   1. Live RELATIONSHIPS.md — read-only person cards. Click to expand
//      the fact list inline. No edit/delete buttons; those land via
//      Telegram slash commands or a future v4 dashboard surface.
//
//   2. Candidates queue — pending entries from the silent extractor.
//      Per-row: display name, qualifier chips, session count, eligibility
//      badge, expand → per-fact toggle + edit-in-place + source-turn
//      pointer + approve/reject controls. Footer "Show rejected" toggle.
//
// Polling cadence matches the existing LearningPage (5s setTimeout
// loop, not setInterval, so re-renders don't pile up). Auth is handled
// upstream by the bearer token that LearningPage passes in.
//
// Approve UX defends the v3c §7 "approval is now security-relevant"
// risk row: a one-line reminder under the approve button names the
// permanence of the action. Approve/reject flows show optimistic
// progress + rollback on 4xx response.
//
// Missing-qualifier collision: when /approve returns 409, render a
// modal that lets the user pick a qualifier for the existing live
// entry, fires /resolve_qualifier, then retries /approve.

import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, ApproveError, api } from "../lib/api";
import type {
  ApproveCollisionPayload,
  RelationshipCandidate,
  RelationshipPerson,
} from "../lib/types";
import { classNames, relativeTime } from "../lib/format";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface RelationshipsPanelProps {
  token: string;
  onAuthFail: () => void;
}

const POLL_INTERVAL_MS = 5000;

interface PanelState {
  live: RelationshipPerson[];
  candidates: RelationshipCandidate[];
}

export function RelationshipsPanel({ token, onAuthFail }: RelationshipsPanelProps) {
  const [state, setState] = useState<PanelState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeRejected, setIncludeRejected] = useState(false);

  // Per-slug expand/collapse state for both panes.
  const [expandedLive, setExpandedLive] = useState<Set<string>>(new Set());
  const [expandedCandidates, setExpandedCandidates] = useState<Set<string>>(new Set());

  // Per-slug selected fact_ids (default: all selected when expanded).
  const [selectedFactsBySlug, setSelectedFactsBySlug] = useState<
    Record<string, Set<string>>
  >({});

  // Per-slug edit-in-progress text by fact_id.
  const [editTextBySlugFact, setEditTextBySlugFact] = useState<
    Record<string, Record<string, string>>
  >({});

  // Optimistic-action state: slugs currently being mutated.
  const [busySlugs, setBusySlugs] = useState<Set<string>>(new Set());

  // Modal state for the missing-qualifier collision flow.
  const [collision, setCollision] = useState<ApproveCollisionPayload | null>(null);

  // Last action message (success / failure) — surfaced as a small
  // banner under the candidates list. Auto-clears after the next poll.
  const [actionBanner, setActionBanner] = useState<{
    tone: "success" | "error";
    text: string;
  } | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [live, candidates] = await Promise.all([
        api.relationshipsLive(token),
        api.relationshipsCandidates(token, { includeRejected }),
      ]);
      setState({ live: live.people, candidates: candidates.candidates });
      setError(null);
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [token, onAuthFail, includeRejected]);

  // 5s polling loop matching LearningPage.tsx pattern. setTimeout, not
  // setInterval — the next refresh waits for the previous to finish so
  // re-renders never pile up.
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

  // Clear the action banner after the next successful refresh.
  useEffect(() => {
    if (actionBanner === null) return;
    const id = window.setTimeout(() => setActionBanner(null), 4000);
    return () => window.clearTimeout(id);
  }, [actionBanner]);

  const toggleLiveExpand = (slug: string) => {
    setExpandedLive((prev) => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  };

  const toggleCandidateExpand = (candidate: RelationshipCandidate) => {
    setExpandedCandidates((prev) => {
      const next = new Set(prev);
      if (next.has(candidate.slug)) next.delete(candidate.slug);
      else next.add(candidate.slug);
      return next;
    });
    // On first expand, default-select all non-rejected facts.
    setSelectedFactsBySlug((prev) => {
      if (prev[candidate.slug] !== undefined) return prev;
      const allActive = new Set(
        candidate.facts
          .filter((f) => f.rejected_at === null)
          .map((f) => f.fact_id),
      );
      return { ...prev, [candidate.slug]: allActive };
    });
  };

  const toggleFact = (slug: string, factId: string) => {
    setSelectedFactsBySlug((prev) => {
      const current = prev[slug] ?? new Set<string>();
      const next = new Set(current);
      if (next.has(factId)) next.delete(factId);
      else next.add(factId);
      return { ...prev, [slug]: next };
    });
  };

  const onApprove = useCallback(
    async (candidate: RelationshipCandidate, factIds?: string[]) => {
      const slug = candidate.slug;
      setBusySlugs((p) => new Set(p).add(slug));
      try {
        const ids = factIds ?? Array.from(selectedFactsBySlug[slug] ?? new Set());
        const body: { fact_ids?: string[]; qualifier?: string | null } = {};
        if (ids.length > 0) body.fact_ids = ids;
        if (candidate.qualifier) body.qualifier = candidate.qualifier;
        await api.relationshipsApprove(token, slug, body);
        setActionBanner({
          tone: "success",
          text: `Approved ${candidate.display_name}.`,
        });
        await refresh();
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        if (exc instanceof ApproveError) {
          if (
            exc.status === 409 &&
            exc.payload &&
            (exc.payload as ApproveCollisionPayload).error ===
              "missing_existing_qualifier"
          ) {
            setCollision(exc.payload as ApproveCollisionPayload);
            return;
          }
          if (exc.status === 422) {
            setActionBanner({
              tone: "error",
              text: (exc.payload.reply_text as string) ?? "Blocked by content scanner.",
            });
            return;
          }
          setActionBanner({
            tone: "error",
            text: (exc.payload.reply_text as string) ?? "Approve failed.",
          });
          return;
        }
        setActionBanner({
          tone: "error",
          text: exc instanceof Error ? exc.message : String(exc),
        });
      } finally {
        setBusySlugs((p) => {
          const n = new Set(p);
          n.delete(slug);
          return n;
        });
      }
    },
    [token, onAuthFail, refresh, selectedFactsBySlug],
  );

  const onReject = useCallback(
    async (candidate: RelationshipCandidate, factIds?: string[]) => {
      const slug = candidate.slug;
      setBusySlugs((p) => new Set(p).add(slug));
      try {
        await api.relationshipsReject(
          token,
          slug,
          factIds && factIds.length > 0 ? { fact_ids: factIds } : {},
        );
        setActionBanner({
          tone: "success",
          text: factIds
            ? `Rejected ${factIds.length} fact${factIds.length === 1 ? "" : "s"}.`
            : `Rejected ${candidate.display_name}.`,
        });
        await refresh();
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setActionBanner({
          tone: "error",
          text: exc instanceof Error ? exc.message : String(exc),
        });
      } finally {
        setBusySlugs((p) => {
          const n = new Set(p);
          n.delete(slug);
          return n;
        });
      }
    },
    [token, onAuthFail, refresh],
  );

  const onEdit = useCallback(
    async (slug: string, factId: string, newText: string) => {
      setBusySlugs((p) => new Set(p).add(slug));
      try {
        await api.relationshipsEdit(token, slug, {
          fact_id: factId,
          new_text: newText,
        });
        setActionBanner({ tone: "success", text: "Fact edited." });
        // Drop the in-progress text now that it landed.
        setEditTextBySlugFact((prev) => {
          const next = { ...prev };
          if (next[slug]) {
            const slugMap = { ...next[slug] };
            delete slugMap[factId];
            next[slug] = slugMap;
          }
          return next;
        });
        await refresh();
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setActionBanner({
          tone: "error",
          text: exc instanceof Error ? exc.message : String(exc),
        });
      } finally {
        setBusySlugs((p) => {
          const n = new Set(p);
          n.delete(slug);
          return n;
        });
      }
    },
    [token, onAuthFail, refresh],
  );

  const onResolveCollision = useCallback(
    async (existingQualifier: string) => {
      if (collision === null) return;
      const slug = collision.slug;
      setBusySlugs((p) => new Set(p).add(slug));
      try {
        await api.relationshipsResolveQualifier(token, slug, {
          existing_qualifier: existingQualifier,
        });
        // Find the candidate from the current state to retry approve.
        const candidate = state?.candidates.find((c) => c.slug === slug);
        if (!candidate) {
          setActionBanner({
            tone: "error",
            text: "Resolved qualifier but candidate vanished from queue.",
          });
          setCollision(null);
          await refresh();
          return;
        }
        await api.relationshipsApprove(token, slug, {
          qualifier: collision.proposed_qualifier ?? candidate.qualifier ?? null,
        });
        setActionBanner({
          tone: "success",
          text: `Resolved & approved ${candidate.display_name}.`,
        });
        setCollision(null);
        await refresh();
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setActionBanner({
          tone: "error",
          text: exc instanceof Error ? exc.message : String(exc),
        });
      } finally {
        setBusySlugs((p) => {
          const n = new Set(p);
          n.delete(slug);
          return n;
        });
      }
    },
    [collision, token, onAuthFail, refresh, state],
  );

  if (error) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load relationships: {error}
        <Button
          size="sm"
          className="ml-3"
          onClick={() => {
            setError(null);
            void refresh();
          }}
        >
          Retry
        </Button>
      </div>
    );
  }
  if (state === null) {
    return (
      <Section title="Relationships">
        <Card className="px-4 py-6 text-sm text-[var(--color-fg-dim)]">
          Loading…
        </Card>
      </Section>
    );
  }

  return (
    <div className="space-y-8">
      <Section
        title="Relationships — live"
        trailing={
          <span>
            {state.live.length} {state.live.length === 1 ? "person" : "people"}
          </span>
        }
      >
        {state.live.length === 0 ? (
          <EmptyState
            glyph="○"
            title="No relationships saved yet."
            hint="Vexis is watching for recurring people in your conversations. Approved candidates will land here."
          />
        ) : (
          <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
            {state.live.map((person) => (
              <LivePersonCard
                key={person.slug}
                person={person}
                expanded={expandedLive.has(person.slug)}
                onToggle={() => toggleLiveExpand(person.slug)}
              />
            ))}
          </div>
        )}
      </Section>

      <Section
        title="Pending candidates"
        trailing={
          <label className="inline-flex items-center gap-2 text-[10px] uppercase tracking-wider cursor-pointer">
            <input
              type="checkbox"
              checked={includeRejected}
              onChange={(e) => setIncludeRejected(e.target.checked)}
              className="accent-[var(--color-accent)]"
            />
            Show rejected
          </label>
        }
      >
        {actionBanner && (
          <div
            className={classNames(
              "hairline px-3 py-2 text-xs",
              actionBanner.tone === "success"
                ? "border-[var(--color-accent)]/40 text-[var(--color-accent)]"
                : "border-[var(--color-error)]/40 text-[var(--color-error)]",
            )}
          >
            {actionBanner.text}
          </div>
        )}
        {state.candidates.length === 0 ? (
          <EmptyState
            glyph="·"
            title="No pending candidates."
            hint="Recurring people you mention in conversation will surface here. Strong cues like “mom” or “my partner” surface immediately; soft cues need at least two distinct sessions in 30 days."
          />
        ) : (
          <Card>
            <ul className="divide-y divide-[var(--color-border)]">
              {state.candidates.map((candidate) => (
                <CandidateRow
                  key={candidate.slug}
                  candidate={candidate}
                  expanded={expandedCandidates.has(candidate.slug)}
                  onToggleExpand={() => toggleCandidateExpand(candidate)}
                  selectedFacts={selectedFactsBySlug[candidate.slug] ?? new Set()}
                  onToggleFact={(factId) => toggleFact(candidate.slug, factId)}
                  editText={editTextBySlugFact[candidate.slug] ?? {}}
                  onEditTextChange={(factId, text) =>
                    setEditTextBySlugFact((prev) => ({
                      ...prev,
                      [candidate.slug]: {
                        ...(prev[candidate.slug] ?? {}),
                        [factId]: text,
                      },
                    }))
                  }
                  onSubmitEdit={(factId, newText) =>
                    onEdit(candidate.slug, factId, newText)
                  }
                  busy={busySlugs.has(candidate.slug)}
                  onApprove={(factIds) => onApprove(candidate, factIds)}
                  onReject={(factIds) => onReject(candidate, factIds)}
                />
              ))}
            </ul>
          </Card>
        )}
      </Section>

      {collision && (
        <CollisionModal
          payload={collision}
          onResolve={onResolveCollision}
          onCancel={() => setCollision(null)}
        />
      )}
    </div>
  );
}

// ---- Live person card -------------------------------------------------------

function LivePersonCard({
  person,
  expanded,
  onToggle,
}: {
  person: RelationshipPerson;
  expanded: boolean;
  onToggle: () => void;
}) {
  const heading = person.qualifier
    ? `${person.display_name} (${person.qualifier})`
    : person.display_name;
  return (
    <Card className="px-3 py-3">
      <button
        type="button"
        onClick={onToggle}
        className="w-full text-left"
        aria-expanded={expanded}
      >
        <div className="flex items-baseline justify-between gap-2">
          <span className="font-medium text-sm text-[var(--color-fg-1)]">
            {heading}
          </span>
          <span className="text-[10px] uppercase tracking-wider text-[var(--color-fg-dim)] font-data">
            {person.facts.length}
            {" "}
            {person.facts.length === 1 ? "fact" : "facts"}
          </span>
        </div>
        <div className="mt-1 text-[11px] text-[var(--color-fg-dim)] font-data">
          confirmed {relativeTime(person.last_confirmed)}
        </div>
      </button>
      {expanded && (
        <ul className="mt-3 space-y-1.5 border-t border-[var(--color-border)] pt-3">
          {person.facts.map((fact, i) => (
            <li
              key={`${fact.confirmed_date}-${i}`}
              className="text-xs text-[var(--color-fg-2)] leading-relaxed"
            >
              <span className="font-data text-[10px] text-[var(--color-fg-dim)] mr-1.5">
                [{fact.confirmed_date} sess:{fact.source_session_short}]
              </span>
              {fact.text}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

// ---- Candidate row ----------------------------------------------------------

function CandidateRow({
  candidate,
  expanded,
  onToggleExpand,
  selectedFacts,
  onToggleFact,
  editText,
  onEditTextChange,
  onSubmitEdit,
  busy,
  onApprove,
  onReject,
}: {
  candidate: RelationshipCandidate;
  expanded: boolean;
  onToggleExpand: () => void;
  selectedFacts: Set<string>;
  onToggleFact: (factId: string) => void;
  editText: Record<string, string>;
  onEditTextChange: (factId: string, text: string) => void;
  onSubmitEdit: (factId: string, newText: string) => void | Promise<void>;
  busy: boolean;
  onApprove: (factIds?: string[]) => void | Promise<void>;
  onReject: (factIds?: string[]) => void | Promise<void>;
}) {
  const tombstoned = candidate.rejected_at !== null;
  const eligibilityTone: "active" | "subtle" | "neutral" = candidate.eligible
    ? "active"
    : "subtle";
  const eligibilityLabel = candidate.eligible
    ? "eligible"
    : candidate.fact_count === 0
      ? "drop on next sweep"
      : "below threshold";
  const selectedFactArr = Array.from(selectedFacts);

  return (
    <li
      className={classNames(
        "px-3 py-3",
        busy && "opacity-50",
        tombstoned && "bg-[var(--color-surface)]/50",
      )}
    >
      <button
        type="button"
        onClick={onToggleExpand}
        className="w-full text-left"
        aria-expanded={expanded}
      >
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-sm text-[var(--color-fg-1)]">
            {candidate.display_name}
          </span>
          {candidate.qualifier_candidates.map((q) => (
            <Badge key={q} tone="subtle">
              {q}
            </Badge>
          ))}
          {tombstoned && <Badge tone="archived">rejected</Badge>}
          {!tombstoned && (
            <Badge
              tone={eligibilityTone}
              glyph={candidate.eligible ? "●" : "○"}
            >
              {eligibilityLabel}
            </Badge>
          )}
          <span className="ml-auto text-[10px] uppercase tracking-wider text-[var(--color-fg-dim)] font-data">
            {candidate.session_count} sess · {candidate.fact_count} fact
            {candidate.fact_count === 1 ? "" : "s"}
          </span>
        </div>
        <div className="mt-1 text-[11px] text-[var(--color-fg-dim)] font-data">
          first seen {relativeTime(candidate.first_seen)} · last seen{" "}
          {relativeTime(candidate.last_seen)}
        </div>
      </button>

      {expanded && (
        <div className="mt-3 space-y-3 border-t border-[var(--color-border)] pt-3">
          <ul className="space-y-2">
            {candidate.facts.map((fact) => (
              <li
                key={fact.fact_id}
                className="flex items-start gap-2 text-xs leading-relaxed"
              >
                <input
                  type="checkbox"
                  checked={selectedFacts.has(fact.fact_id)}
                  onChange={() => onToggleFact(fact.fact_id)}
                  className="mt-1 accent-[var(--color-accent)]"
                  disabled={busy}
                />
                <div className="flex-1 min-w-0">
                  {editText[fact.fact_id] !== undefined ? (
                    <div className="flex items-start gap-2">
                      <textarea
                        value={editText[fact.fact_id]}
                        onChange={(e) =>
                          onEditTextChange(fact.fact_id, e.target.value)
                        }
                        rows={2}
                        className="flex-1 px-2 py-1 text-xs hairline bg-[var(--color-surface)] focus:outline-none focus:border-[var(--color-accent)]"
                      />
                      <Button
                        size="sm"
                        variant="primary"
                        disabled={busy || !editText[fact.fact_id]?.trim()}
                        onClick={() =>
                          onSubmitEdit(fact.fact_id, editText[fact.fact_id])
                        }
                      >
                        Save
                      </Button>
                      <Button
                        size="sm"
                        onClick={() =>
                          onEditTextChange(fact.fact_id, undefined as unknown as string)
                        }
                      >
                        Cancel
                      </Button>
                    </div>
                  ) : (
                    <div className="flex items-start justify-between gap-2">
                      <span className="text-[var(--color-fg-2)] break-words">
                        {fact.text}
                      </span>
                      <button
                        type="button"
                        onClick={() => onEditTextChange(fact.fact_id, fact.text)}
                        className="text-[10px] uppercase tracking-wider text-[var(--color-fg-dim)] hover:text-[var(--color-accent)] font-data"
                      >
                        edit
                      </button>
                    </div>
                  )}
                  <div className="mt-0.5 text-[10px] text-[var(--color-fg-dim)] font-data">
                    {fact.occurrence_count} occurrence
                    {fact.occurrence_count === 1 ? "" : "s"} · first seen{" "}
                    {relativeTime(fact.first_seen)}
                  </div>
                </div>
              </li>
            ))}
          </ul>
          {!tombstoned && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || selectedFactArr.length === 0}
                  onClick={() =>
                    onApprove(
                      selectedFactArr.length === candidate.fact_count
                        ? undefined
                        : selectedFactArr,
                    )
                  }
                >
                  Approve {selectedFactArr.length} fact
                  {selectedFactArr.length === 1 ? "" : "s"}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={busy || selectedFactArr.length === 0}
                  onClick={() =>
                    onReject(
                      selectedFactArr.length === candidate.fact_count
                        ? undefined
                        : selectedFactArr,
                    )
                  }
                >
                  Reject selected
                </Button>
                <Button
                  variant="danger"
                  size="sm"
                  disabled={busy}
                  onClick={() => onReject()}
                >
                  Reject person
                </Button>
              </div>
              <p className="text-[10px] text-[var(--color-fg-dim)] leading-relaxed">
                Approve will save these facts to Vexis's permanent memory
                ({candidate.display_name} → RELATIONSHIPS.md). The brain
                reads this file on its next session spawn.
              </p>
            </div>
          )}
        </div>
      )}
    </li>
  );
}

// ---- Missing-qualifier modal -----------------------------------------------

function CollisionModal({
  payload,
  onResolve,
  onCancel,
}: {
  payload: ApproveCollisionPayload;
  onResolve: (existingQualifier: string) => void | Promise<void>;
  onCancel: () => void;
}) {
  const candidates = useMemo(
    () => payload.existing_qualifier_candidates ?? [],
    [payload],
  );
  const [picked, setPicked] = useState<string>(candidates[0] ?? "");
  const [custom, setCustom] = useState<string>("");
  const [useCustom, setUseCustom] = useState<boolean>(candidates.length === 0);
  const [busy, setBusy] = useState<boolean>(false);
  const submit = async () => {
    const value = useCustom ? custom.trim() : picked.trim();
    if (!value) return;
    setBusy(true);
    try {
      await onResolve(value);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      role="dialog"
      aria-modal="true"
    >
      <Card className="w-[min(560px,calc(100vw-2rem))] px-5 py-5 space-y-4">
        <div>
          <h3 className="font-medium text-sm text-[var(--color-fg-1)]">
            “{payload.slug}” already exists without a qualifier
          </h3>
          <p className="mt-1 text-xs text-[var(--color-fg-2)] leading-relaxed">
            To make room for the new{" "}
            <code className="font-data text-[10px]">
              {payload.slug}-{payload.proposed_qualifier ?? "?"}
            </code>{" "}
            entry, pick a qualifier for the existing entry first. The
            existing entry will be renamed to{" "}
            <code className="font-data text-[10px]">{payload.slug}-&lt;your-pick&gt;</code>.
          </p>
        </div>
        <div className="hairline px-3 py-2 bg-[var(--color-surface)]/50 space-y-1">
          <p className="text-[10px] uppercase tracking-wider text-[var(--color-fg-dim)] font-data">
            Existing facts ({payload.existing_facts.length})
          </p>
          <ul className="text-xs text-[var(--color-fg-2)] space-y-0.5 leading-snug">
            {payload.existing_facts.map((t, i) => (
              <li key={i} className="break-words">
                · {t}
              </li>
            ))}
          </ul>
        </div>
        <div className="space-y-2">
          {candidates.length > 0 && (
            <div>
              <label className="block text-[10px] uppercase tracking-wider text-[var(--color-fg-dim)] font-data mb-1">
                Suggested qualifiers
              </label>
              <div className="flex flex-wrap gap-2">
                {candidates.map((q) => (
                  <button
                    key={q}
                    type="button"
                    onClick={() => {
                      setPicked(q);
                      setUseCustom(false);
                    }}
                    className={classNames(
                      "px-2 py-1 hairline text-xs",
                      !useCustom && picked === q
                        ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                        : "text-[var(--color-fg-2)]",
                    )}
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div>
            <label className="flex items-center gap-2 text-xs text-[var(--color-fg-2)] cursor-pointer">
              <input
                type="checkbox"
                checked={useCustom}
                onChange={(e) => setUseCustom(e.target.checked)}
                className="accent-[var(--color-accent)]"
              />
              Custom qualifier
            </label>
            {useCustom && (
              <input
                type="text"
                value={custom}
                onChange={(e) => setCustom(e.target.value)}
                placeholder="e.g. friend, sister, gym-buddy"
                className="mt-1 w-full px-2 py-1 text-xs hairline bg-[var(--color-surface)] focus:outline-none focus:border-[var(--color-accent)]"
              />
            )}
          </div>
        </div>
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onCancel} disabled={busy}>
            Cancel
          </Button>
          <Button
            variant="primary"
            onClick={submit}
            disabled={busy || (useCustom ? !custom.trim() : !picked.trim())}
          >
            Resolve and approve
          </Button>
        </div>
      </Card>
    </div>
  );
}
