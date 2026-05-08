import { memo, useEffect, useRef, useState } from "react";
import type { ChatSession } from "../../lib/types";
import { relativeTime } from "../../lib/format";

interface ChatSidebarProps {
  sessions: ChatSession[];
  // The currently-running brain kind, if known. Surfaced as a small
  // badge so the user knows which transcript store backs the listed
  // sessions (claude-code JSONL vs opencode SQLite). The picker isn't
  // here yet — switching brain still requires the daemon restart that
  // the rest of the dashboard already exposes; this is observation-only
  // for now.
  brainKind?: string | null;
  // ``null`` while a sidebar action is in flight; otherwise the
  // session name being acted upon. Lets us disable the row + show
  // a subtle pending indicator without a global spinner.
  pendingName: string | null;
  // Drawer state for mobile. Above the md breakpoint the sidebar is
  // always visible (these flags are ignored). Below md, the sidebar
  // slides in from the left when ``open`` is true; tapping the
  // backdrop calls ``onClose``. ``onAfterAction`` fires after every
  // user action (switch, new, rename, delete) so the parent can
  // auto-close the drawer on mobile — picking a session and seeing
  // the drawer linger feels broken.
  open: boolean;
  onClose: () => void;
  onAfterAction: () => void;
  onNew: () => void;
  onSwitch: (name: string) => void;
  onRename: (oldName: string, newName: string) => void;
  onDelete: (name: string) => void;
}

function ChatSidebarImpl({
  sessions,
  brainKind,
  pendingName,
  open,
  onClose,
  onAfterAction,
  onNew,
  onSwitch,
  onRename,
  onDelete,
}: ChatSidebarProps) {
  // Wrap the action callbacks so each one auto-closes the drawer on
  // mobile after firing. Doesn't affect desktop because ``open`` is
  // always conceptually "true" there (the drawer machinery is just
  // CSS no-ops above md).
  const wrap = <A extends unknown[]>(fn: (...args: A) => void) =>
    (...args: A) => {
      fn(...args);
      onAfterAction();
    };

  return (
    <>
      {/* Backdrop. Below md, covers the conversation area when the
          drawer is open; tapping it closes. Above md it's hidden
          regardless. ``aria-hidden`` because the visible drawer
          content already conveys state to assistive tech. */}
      <div
        aria-hidden="true"
        onClick={onClose}
        className={[
          "md:hidden fixed inset-0 z-30 bg-black/60 transition-opacity",
          open ? "opacity-100" : "opacity-0 pointer-events-none",
        ].join(" ")}
      />
    <aside
      className={[
        "flex flex-col border-r border-[var(--color-border)]",
        "bg-[var(--color-surface)]",
        // Mobile: fixed-position drawer that slides in from the left.
        // ``w-72`` (288px) is the ergonomic pick on a 375px-wide phone
        // — wide enough that two-line session entries stay readable,
        // narrow enough that the conversation peek through the
        // translucent backdrop reads as "still there, just covered".
        "fixed inset-y-0 left-0 z-40 w-72 transform transition-transform duration-200",
        open ? "translate-x-0" : "-translate-x-full",
        // Desktop: in-flow at 256px, no transform, no fixed positioning.
        "md:static md:translate-x-0 md:w-64 md:shrink-0",
      ].join(" ")}
    >
      <div className="px-3 py-3 border-b border-[var(--color-border)] flex items-center gap-2">
        <button
          type="button"
          onClick={wrap(onNew)}
          disabled={pendingName !== null}
          className={[
            "w-full rounded-md border border-[var(--color-border-strong)]",
            "px-3 py-2 text-xs uppercase tracking-wider font-semibold",
            "text-[var(--color-fg)] hover:border-[var(--color-accent)]",
            "hover:text-[var(--color-accent)] transition-colors",
            "disabled:opacity-40 disabled:cursor-not-allowed",
          ].join(" ")}
        >
          + New chat
        </button>
        {/* Mobile-only close button. Above md the drawer is always
            "open" so this control is hidden — and on desktop the
            backdrop pattern doesn't apply. */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close sessions"
          className={[
            "md:hidden shrink-0 w-9 h-9 flex items-center justify-center",
            "rounded-md text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]",
            "hover:bg-[var(--color-base)] transition-colors",
          ].join(" ")}
        >
          ✕
        </button>
      </div>

      <nav className="flex-1 overflow-y-auto px-2 py-2">
        {sessions.length === 0 ? (
          <div className="px-3 py-4 text-xs text-[var(--color-fg-dim)]">
            No sessions yet.
          </div>
        ) : (
          <ul className="space-y-0.5">
            {sessions.map((s) => (
              <SessionRow
                key={s.name}
                session={s}
                pending={pendingName === s.name}
                disabled={pendingName !== null && pendingName !== s.name}
                onSwitch={wrap(() => onSwitch(s.name))}
                onRename={wrap((next) => onRename(s.name, next))}
                onDelete={wrap(() => onDelete(s.name))}
                canDelete={sessions.length > 1}
              />
            ))}
          </ul>
        )}
      </nav>

      {brainKind && (
        <div className="border-t border-[var(--color-border)] px-3 py-2">
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-fg-dim)]">
            Brain
          </div>
          <div className="text-xs text-[var(--color-fg-2)] font-mono">
            {brainKind}
          </div>
        </div>
      )}
    </aside>
    </>
  );
}

interface SessionRowProps {
  session: ChatSession;
  pending: boolean;
  disabled: boolean;
  canDelete: boolean;
  onSwitch: () => void;
  onRename: (next: string) => void;
  onDelete: () => void;
}

function SessionRow({
  session,
  pending,
  disabled,
  canDelete,
  onSwitch,
  onRename,
  onDelete,
}: SessionRowProps) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(session.name);
  // Two-click delete: first click arms (icon flips to ✓), second
  // click within ARM_TIMEOUT_MS confirms. Beats a modal because it
  // stays in-row, costs nothing visually until you start the gesture,
  // and is testable without a confirm() dialog stack. Native
  // ``window.confirm`` was the prior approach; it was hostile to
  // programmatic testing and used unstyled OS chrome on phones.
  const [armed, setArmed] = useState(false);
  const armTimer = useRef<number | null>(null);

  // Auto-disarm after the timeout. Clearing on unmount keeps a stale
  // timer from setting state on a row that's already been deleted.
  useEffect(() => {
    if (!armed) return;
    armTimer.current = window.setTimeout(() => setArmed(false), 3000);
    return () => {
      if (armTimer.current !== null) {
        window.clearTimeout(armTimer.current);
        armTimer.current = null;
      }
    };
  }, [armed]);

  const commitRename = () => {
    const trimmed = draft.trim();
    setEditing(false);
    if (!trimmed || trimmed === session.name) {
      setDraft(session.name);
      return;
    }
    onRename(trimmed);
  };

  // relativeTime accepts the ISO string directly; safe-handles null/undef.
  const created = relativeTime(session.created_at);

  // Whole-row click semantics. Tapping anywhere in the row's
  // highlighted area (name, timestamp, padding) selects the session;
  // the icon buttons stop propagation so they don't double-fire. The
  // row is keyboard-reachable too — Tab focuses, Enter/Space activates,
  // matching native button behavior. Disabled when already active or
  // when another row's action is in flight.
  const rowDisabled = disabled || pending || session.is_active || editing;
  const rowOnActivate = () => {
    if (rowDisabled) return;
    onSwitch();
  };

  return (
    <li>
      <div
        // Behaves like a button when the row is selectable, like a
        // plain container when it's the active session (no further
        // selection happens). ``role`` and ``tabIndex`` reflect that.
        role={rowDisabled ? undefined : "button"}
        tabIndex={rowDisabled ? -1 : 0}
        onClick={rowOnActivate}
        onKeyDown={(e) => {
          if (rowDisabled) return;
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            rowOnActivate();
          }
        }}
        aria-label={
          session.is_active
            ? `${session.name} (active)`
            : `Switch to ${session.name}`
        }
        title={`${session.name} · ${created}`}
        className={[
          "group rounded-md px-2 py-1.5",
          session.is_active
            ? "bg-[var(--color-surface-2)] border-l-2 border-[var(--color-accent)] pl-1.5"
            : "hover:bg-[var(--color-surface-2)] cursor-pointer",
          disabled ? "opacity-40 cursor-not-allowed" : "",
          // Focus ring for keyboard users; subtle accent outline so it
          // doesn't fight the active-session amber border-l-2.
          "focus:outline-none focus-visible:ring-1 focus-visible:ring-[var(--color-accent)]",
        ].join(" ")}
      >
        <div className="flex items-center gap-2">
          {editing ? (
            <input
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitRename}
              // The input must not bubble its click/keydown to the row,
              // otherwise typing or focusing the input would re-fire
              // onSwitch via the outer onClick.
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === "Enter") commitRename();
                if (e.key === "Escape") {
                  setDraft(session.name);
                  setEditing(false);
                }
              }}
              className={[
                "flex-1 bg-[var(--color-base)] border border-[var(--color-border-strong)]",
                "rounded px-1.5 py-0.5 text-xs text-[var(--color-fg)]",
                "outline-none focus:border-[var(--color-accent)]",
              ].join(" ")}
            />
          ) : (
            <span
              className={[
                "flex-1 text-left text-xs truncate",
                session.is_active
                  ? "text-[var(--color-fg)] font-semibold"
                  : "text-[var(--color-fg-2)] group-hover:text-[var(--color-fg)]",
              ].join(" ")}
            >
              {session.name}
            </span>
          )}
          {!editing && (
            <div
              className={[
                "flex gap-1 transition-opacity",
                // Hover-reveal only above md (desktop), where ``hover``
                // is a real state. On phones (touch) ``:hover`` doesn't
                // fire so we must keep the actions visible — otherwise
                // they're unreachable. The ``armed`` state always pins
                // visible regardless of breakpoint so the user keeps
                // the confirm affordance during the 3s window.
                armed
                  ? "opacity-100"
                  : [
                      "opacity-100",
                      "md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100",
                    ].join(" "),
              ].join(" ")}
            >
              <IconButton
                label="Rename"
                onClick={(e) => {
                  // stopPropagation prevents the row's whole-row
                  // onClick from firing onSwitch when the user just
                  // wanted to rename. Same idea on delete below.
                  e.stopPropagation();
                  setDraft(session.name);
                  setEditing(true);
                }}
                disabled={disabled || pending}
              >
                ✎
              </IconButton>
              <IconButton
                label={armed ? "Confirm delete" : "Delete"}
                onClick={(e) => {
                  e.stopPropagation();
                  if (!armed) {
                    setArmed(true);
                    return;
                  }
                  setArmed(false);
                  onDelete();
                }}
                disabled={disabled || pending || !canDelete}
                tone={armed ? "danger" : "default"}
                title={
                  armed
                    ? "Click again within 3s to delete (the transcript stays on disk)"
                    : "Delete session"
                }
              >
                {armed ? "✓" : "✕"}
              </IconButton>
            </div>
          )}
        </div>
        {created && !editing && (
          <div className="text-[10px] text-[var(--color-fg-dim)] mt-0.5 pl-0">
            {created}
          </div>
        )}
      </div>
    </li>
  );
}

// Memoised export. The session list is the heaviest part of the
// chat UI (50+ rows here) — without memo, every keystroke in the
// composer rerendered the whole sidebar because the parent's
// callbacks/array references churned. ``memo`` with React's default
// shallow compare is enough now that ChatPage memoises ``sortedSessions``
// and uses ``useCallback`` for handlers — the prop identities are
// stable across renders that don't actually concern the sidebar.
export const ChatSidebar = memo(ChatSidebarImpl);

function IconButton({
  children,
  label,
  onClick,
  disabled,
  tone = "default",
  title,
}: {
  children: React.ReactNode;
  label: string;
  // Event-arg signature lets callers stopPropagation when this button
  // sits inside a clickable parent row (which it does in ChatSidebar
  // — the whole row is clickable, but rename/delete must not fire
  // the row's onSwitch).
  onClick: (e: React.MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  // ``danger`` flips the button to the warm-error palette so the
  // armed state of the two-click delete reads visually distinct
  // from the resting icon. Always sticky-visible (no opacity-0 on
  // armed buttons) so the user doesn't lose the affordance during
  // the 3s confirm window if they move the cursor.
  tone?: "default" | "danger";
  title?: string;
}) {
  const toneClasses =
    tone === "danger"
      ? [
          "text-[var(--color-error)] bg-[var(--color-base)]",
          "border border-[var(--color-error)]",
          "hover:text-[var(--color-fg)] hover:bg-[var(--color-error)]",
        ].join(" ")
      : [
          "text-[var(--color-fg-dim)]",
          "hover:text-[var(--color-fg)] hover:bg-[var(--color-base)]",
        ].join(" ");
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={label}
      title={title}
      className={[
        // Touch-target floor: 32px square on mobile (still cramped
        // by Apple HIG's 44pt floor but enough to hit reliably given
        // the icons sit at the edge of a tappable row anyway). Slim
        // back to 20px on desktop where a mouse cursor doesn't need
        // padding.
        "w-8 h-8 md:w-5 md:h-5 flex items-center justify-center rounded",
        toneClasses,
        "disabled:opacity-30 disabled:cursor-not-allowed",
        "text-sm md:text-xs transition-colors",
      ].join(" ")}
    >
      {children}
    </button>
  );
}
