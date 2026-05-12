import { useCallback, useEffect, useMemo, useState } from "react";
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

// Limits surfaced from core/skills.py — keep in lockstep so the
// dashboard's UX matches the backend's validation.
const MAX_DESCRIPTION_LENGTH = 1024;
const NAME_RE = /^[a-z][a-z0-9-]{1,63}$/;

interface SkillsPageProps {
  token: string;
  onAuthFail: () => void;
}

// What the create/edit modal is currently doing — null when closed.
// Carries the seed skill name when editing an existing one so the
// form can pre-fill from the loaded body.
type ModalMode =
  | { kind: "create" }
  | { kind: "edit"; name: string }
  | null;

export function SkillsPage({ token, onAuthFail }: SkillsPageProps) {
  const [state, setState] = useState<SkillsState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [bodies, setBodies] = useState<Record<string, SkillBody>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pendingByName, setPendingByName] = useState<Record<string, string>>({});
  const [modal, setModal] = useState<ModalMode>(null);

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

  // Hard-delete: confirms, refuses on pinned (UI hides the button
  // anyway), then re-fetches. Pin protection is enforced server-side
  // — the 409 we'd get back is double-protection.
  const handleDelete = useCallback(
    async (name: string) => {
      if (!window.confirm(
        `Delete skill "${name}"? This can't be undone (the skill is wiped, not archived).`,
      )) return;
      setPendingByName((p) => ({ ...p, [name]: "delete" }));
      try {
        await api.deleteSkill(token, name);
        // Collapse the row if it was open + clear the cached body.
        setExpanded((cur) => (cur === name ? null : cur));
        setBodies((b) => {
          const next = { ...b };
          delete next[name];
          return next;
        });
        await refresh();
      } catch (exc) {
        if (exc instanceof ApiError && exc.status === 401) onAuthFail();
        else setError(exc instanceof Error ? exc.message : String(exc));
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

  const handleEdit = useCallback((name: string) => {
    setModal({ kind: "edit", name });
  }, []);

  const handleModalSaved = useCallback(async () => {
    setModal(null);
    // Bust the body cache for the just-saved skill so the next
    // expand re-fetches.
    setBodies({});
    await refresh();
  }, [refresh]);

  // Categories the user has already used — feeds the create form's
  // category autocomplete so a new skill stays consistent with the
  // existing taxonomy.
  const knownCategories = useMemo(() => {
    if (!state) return [];
    const set = new Set<string>();
    for (const s of state.active) {
      if (s.category) set.add(s.category);
    }
    return Array.from(set).sort();
  }, [state]);

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
    <>
      <div className="grid gap-8 lg:grid-cols-[3fr_2fr]">
        <Section
          title="Active skills"
          trailing={
            <div className="flex items-baseline gap-3">
              <span className="font-data text-[11px] text-[var(--color-fg-dim)]">
                {state.active.length} total · {state.active.filter((s) => s.pinned).length} pinned
              </span>
              <Button
                variant="primary"
                size="sm"
                onClick={() => setModal({ kind: "create" })}
              >
                + New skill
              </Button>
            </div>
          }
        >
          {activeSorted.length === 0 ? (
            <EmptyState
              glyph="◇"
              title="No skills yet"
              hint="Vexis creates skills as he learns repeatable workflows — or you can write one yourself with the + New skill button above."
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
                  onEdit={() => handleEdit(skill.name)}
                  onDelete={() => handleDelete(skill.name)}
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

      {modal && (
        <SkillEditorModal
          mode={modal}
          token={token}
          knownCategories={knownCategories}
          // Pass the body of the skill being edited so the textarea
          // pre-fills. ``undefined`` is fine on create; the modal
          // also accepts a synchronous lookup miss and shows a
          // spinner while the body fetches.
          existingSkill={
            modal.kind === "edit"
              ? state.active.find((s) => s.name === modal.name) ?? null
              : null
          }
          existingBody={
            modal.kind === "edit" ? bodies[modal.name] ?? null : null
          }
          onClose={() => setModal(null)}
          onSaved={handleModalSaved}
          onAuthFail={onAuthFail}
        />
      )}
    </>
  );
}

interface SkillRowProps {
  skill: ActiveSkill;
  expanded: boolean;
  body?: SkillBody;
  pending?: string;
  onExpand: () => void;
  onPinToggle: () => void;
  onEdit: () => void;
  onDelete: () => void;
  delay: number;
}

function SkillRow({
  skill,
  expanded,
  body,
  pending,
  onExpand,
  onPinToggle,
  onEdit,
  onDelete,
  delay,
}: SkillRowProps) {
  // Bundled + installed are read-only — only show Edit/Delete on
  // workspace-authored skills. Bundled-source check is the canonical
  // signal; the source field comes back from /api/v1/skills.
  const isProtectedSource =
    skill.source === "bundled" || skill.source === "installed";
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
          {skill.source === "bundled" && (
            <Badge tone="accent" glyph="◇">
              bundled
            </Badge>
          )}
          {skill.pinned && skill.source !== "bundled" && (
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
            {isProtectedSource ? (
              // Bundled + installed skills are read-only — the
              // curator + CLI refuse to mutate them; rendering
              // Pin/Edit/Delete would be a lie. Show a labeled
              // chip + the override hint.
              <span
                className="font-data text-[10.5px] uppercase tracking-widest text-[var(--color-fg-dim)] hairline px-2 py-1"
                title={
                  skill.source === "bundled"
                    ? "Ships with vexis. To override, create a workspace skill with the same name."
                    : "Installed from an external source. To modify, fork or uninstall + re-create."
                }
              >
                read-only · {skill.source === "bundled" ? "ships with vexis" : "installed"}
              </span>
            ) : (
              <>
                {/* Pin/Unpin first — the most lightweight action,
                    serves as a kill switch against the curator. */}
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
                {/* Edit + Delete are workspace-only. Edit opens
                    the same modal as "+ New skill" pre-filled.
                    Delete confirms inline (window.confirm) and
                    refuses on pinned (server returns 409 — UI
                    nudges the user to unpin first). */}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    onEdit();
                  }}
                >
                  Edit
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={(e) => {
                    e.stopPropagation();
                    if (skill.pinned) {
                      window.alert(
                        `Skill "${skill.name}" is pinned. Unpin first, then delete.`,
                      );
                      return;
                    }
                    onDelete();
                  }}
                  loading={pending === "delete"}
                >
                  Delete
                </Button>
              </>
            )}
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


// ──────────────────────────────────────────────────────────────────
// SkillEditorModal — create + edit
// ──────────────────────────────────────────────────────────────────

interface SkillEditorModalProps {
  mode: { kind: "create" } | { kind: "edit"; name: string };
  token: string;
  knownCategories: string[];
  existingSkill: ActiveSkill | null;
  existingBody: SkillBody | null;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
  onAuthFail: () => void;
}

/** Name + category + description + body editor used for both create
 *  and edit. On edit, fetches the body if not already cached + pre-
 *  fills every field. Validates name (kebab-case) + description
 *  (≤1024 chars) live; the Save button stays disabled until the
 *  form passes. Submit hits the right endpoint based on ``mode``.
 */
function SkillEditorModal({
  mode,
  token,
  knownCategories,
  existingSkill,
  existingBody,
  onClose,
  onSaved,
  onAuthFail,
}: SkillEditorModalProps) {
  const isEdit = mode.kind === "edit";

  const [name, setName] = useState(isEdit ? mode.name : "");
  const [category, setCategory] = useState(
    existingSkill?.category ?? "",
  );
  // Description + body are split fields in the form, but stored as
  // a single SKILL.md frontmatter+body string when serialised. We
  // pre-populate from the loaded body if editing.
  const [description, setDescription] = useState(
    existingBody?.description ?? existingSkill?.description ?? "",
  );
  const [body, setBody] = useState(existingBody?.body ?? "");
  const [protect, setProtect] = useState(false);
  const [busy, setBusy] = useState(false);
  const [serverError, setServerError] = useState<string | null>(null);

  // When editing and the body wasn't pre-cached, fetch it.
  useEffect(() => {
    if (!isEdit) return;
    if (existingBody !== null) return;
    let cancelled = false;
    api
      .skillBody(token, mode.name)
      .then((b) => {
        if (cancelled) return;
        setDescription(b.description);
        setBody(b.body);
      })
      .catch((exc) => {
        if (exc instanceof ApiError && exc.status === 401) onAuthFail();
        else setServerError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      cancelled = true;
    };
  }, [isEdit, existingBody, mode, token, onAuthFail]);

  // Esc closes — nice touch.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, busy]);

  // Validation. We use the same regex + cap the backend uses so the
  // user never gets a rejection they couldn't have predicted from
  // what the form is showing them.
  const nameError = useMemo(() => {
    if (!name) return "Required.";
    if (!NAME_RE.test(name)) {
      return "Lowercase kebab-case, 2–64 chars, starts with a letter (e.g. my-skill).";
    }
    return null;
  }, [name]);
  const descError = useMemo(() => {
    if (!description.trim()) return "Required.";
    if (description.length > MAX_DESCRIPTION_LENGTH) {
      return `Max ${MAX_DESCRIPTION_LENGTH} characters.`;
    }
    return null;
  }, [description]);
  const bodyError = useMemo(() => {
    if (!body.trim()) {
      return "Body cannot be empty — describe the workflow.";
    }
    return null;
  }, [body]);

  const formValid = !nameError && !descError && !bodyError;

  // Compose the SKILL.md content from the split form fields.
  // Frontmatter is a tiny YAML block; we hand-assemble it because
  // the field set is small and a YAML lib would be overkill +
  // bundle-bloat.
  function composeContent(): string {
    return `---\nname: ${name}\ndescription: ${description.replace(/\n/g, " ")}\n---\n${body}\n`;
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!formValid || busy) return;
    setBusy(true);
    setServerError(null);
    const content = composeContent();
    try {
      if (isEdit) {
        await api.editSkill(token, mode.name, {
          content,
          // If the skill is currently pinned, send force_unpin so
          // the backend lets us through. Server re-pins after.
          force_unpin: !!existingSkill?.pinned,
        });
      } else {
        await api.createSkill(token, {
          name,
          content,
          category: category.trim() || undefined,
          protect,
        });
      }
      await onSaved();
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      // Unwrap the FastAPI {detail: {error: "..."}} shape if present.
      let message = exc instanceof Error ? exc.message : String(exc);
      try {
        const parsed = JSON.parse(message);
        if (parsed?.detail?.error) message = parsed.detail.error;
        else if (parsed?.error) message = parsed.error;
      } catch {
        // not JSON, fall through
      }
      setServerError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-40 bg-[var(--color-base)]/80 backdrop-blur-sm flex items-start justify-center pt-6 pb-6 px-3 sm:px-6 overflow-y-auto"
      // Click-outside closes; clicking inside the dialog doesn't
      // bubble up because we stop propagation on the dialog.
      onClick={(e) => {
        if (e.target === e.currentTarget && !busy) onClose();
      }}
    >
      <div
        className="hairline bg-[var(--color-surface)] w-full max-w-2xl flex flex-col"
        role="dialog"
        aria-modal="true"
      >
        <header className="px-5 py-3 border-b border-[var(--color-border)] flex items-baseline gap-3">
          <span className="font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)]">
            {isEdit ? `edit skill · ${mode.name}` : "new skill"}
          </span>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            className="ml-auto font-data text-[14px] text-[var(--color-fg-dim)] hover:text-[var(--color-fg)] disabled:opacity-50"
            aria-label="close"
          >
            ✕
          </button>
        </header>

        <form onSubmit={onSubmit} className="px-5 py-4 space-y-4">
          {serverError && (
            <div className="hairline border-[var(--color-error)]/40 bg-[var(--color-error)]/[0.06] px-3 py-2 text-[12px] text-[var(--color-error)] font-data">
              {serverError}
            </div>
          )}

          {/* Name + Category — side-by-side on sm+, stacked on mobile. */}
          <div className="grid grid-cols-1 sm:grid-cols-[1fr_1fr] gap-3">
            <Field
              label="Name"
              hint="lowercase-kebab-case"
              error={nameError}
            >
              <input
                value={name}
                onChange={(e) => setName(e.target.value.toLowerCase())}
                disabled={isEdit /* name pin'd on edit; rename = recreate */}
                placeholder="my-skill"
                className={inputClass(!!nameError)}
                autoFocus={!isEdit}
              />
              {isEdit && (
                <p className="mt-1 font-data text-[10px] text-[var(--color-fg-dim)]">
                  Renaming requires delete + recreate (telemetry stays linked to the name).
                </p>
              )}
            </Field>
            <Field label="Category" hint="optional">
              <input
                value={category}
                onChange={(e) => setCategory(e.target.value.toLowerCase())}
                disabled={isEdit}
                list="known-categories"
                placeholder="e.g. devops"
                className={inputClass(false)}
              />
              <datalist id="known-categories">
                {knownCategories.map((c) => (
                  <option key={c} value={c} />
                ))}
              </datalist>
            </Field>
          </div>

          {/* Description — single sentence, what the skill does. */}
          <Field
            label="Description"
            hint={`${description.length} / ${MAX_DESCRIPTION_LENGTH}`}
            hintTone={description.length > MAX_DESCRIPTION_LENGTH ? "error" : "dim"}
            error={descError}
          >
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={2}
              placeholder="One sentence: what does this skill do? When should the agent load it?"
              className={inputClass(!!descError) + " resize-y"}
            />
          </Field>

          {/* Body — the actual instructions. Mono so paste from
              terminal-style notes preserves alignment. */}
          <Field
            label="Body"
            hint="markdown — the procedure / playbook"
            error={bodyError}
          >
            <textarea
              value={body}
              onChange={(e) => setBody(e.target.value)}
              rows={12}
              placeholder={`# What to do\n\n1. First step\n2. Second step\n\n## Pitfalls\n\n…`}
              className={inputClass(!!bodyError) + " font-data text-[12.5px] resize-y leading-relaxed"}
            />
          </Field>

          {!isEdit && (
            // Stack label + hint vertically on narrow viewports so
            // the hint doesn't squeeze the label into a one-letter
            // column. Inline (label · hint) on sm+.
            <label className="flex items-start sm:items-baseline gap-2 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={protect}
                onChange={(e) => setProtect(e.target.checked)}
                className="size-4 accent-[var(--color-accent)] mt-0.5 sm:mt-0 shrink-0"
              />
              <span className="flex-1 min-w-0 flex flex-col sm:flex-row sm:items-baseline sm:gap-2">
                <span className="text-sm text-[var(--color-fg)] whitespace-nowrap">
                  Protect from curator
                </span>
                <span className="font-data text-[10px] text-[var(--color-fg-dim)]">
                  pins the skill so the AI can't auto-edit or archive it
                </span>
              </span>
            </label>
          )}

          {/* Footer actions. Sticky on tall scroll-bound modal so the
              user always sees the save button without scrolling. */}
          <div className="flex items-center gap-2 pt-3 border-t border-[var(--color-border)] -mx-5 px-5 sticky bottom-0 bg-[var(--color-surface)]">
            <Button
              variant="primary"
              size="sm"
              type="submit"
              disabled={!formValid || busy}
              loading={busy}
            >
              {isEdit ? "Save changes" : "Create skill"}
            </Button>
            <button
              type="button"
              onClick={onClose}
              disabled={busy}
              className="font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)] disabled:opacity-50"
            >
              cancel
            </button>
            <span className="ml-auto font-data text-[10px] text-[var(--color-fg-dim)] hidden sm:inline">
              esc to close
            </span>
          </div>
        </form>
      </div>
    </div>
  );
}

function inputClass(hasError: boolean): string {
  return classNames(
    "w-full bg-[var(--color-base)] hairline px-2 py-1.5",
    "font-sans text-[13px] text-[var(--color-fg)]",
    "placeholder:text-[var(--color-fg-dim)]",
    "focus:outline-none focus:border-[var(--color-border-strong)]",
    "disabled:opacity-50 disabled:cursor-not-allowed",
    hasError && "border-[var(--color-error)]/40",
  );
}

function Field({
  label,
  hint,
  hintTone = "dim",
  error,
  children,
}: {
  label: string;
  hint?: string;
  hintTone?: "dim" | "error";
  error?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between mb-1.5">
        <label className="uppercase-tight text-[10px] text-[var(--color-fg-2)]">
          {label}
        </label>
        {hint && (
          <span
            className={classNames(
              "font-data text-[10px] tabular-nums",
              hintTone === "error"
                ? "text-[var(--color-error)]"
                : "text-[var(--color-fg-dim)]",
            )}
          >
            {hint}
          </span>
        )}
      </div>
      {children}
      {error && (
        <p className="mt-1 font-data text-[10.5px] text-[var(--color-error)]">
          {error}
        </p>
      )}
    </div>
  );
}
