import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type ReactNode,
} from "react";
import { api, ApiError } from "../lib/api";
import type {
  GoalRecord,
  GoalsState,
  KanbanBoardResponse,
  KanbanComment,
  KanbanLane,
  KanbanRun,
  KanbanStatus,
  KanbanTask,
  KanbanTaskDetailResponse,
} from "../lib/types";
import { classNames, relativeTime } from "../lib/format";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface KanbanPageProps {
  token: string;
  onAuthFail: () => void;
}

// WS-driven; we still poll the board occasionally as a backstop in case
// a WS reconnect missed an event window. 30s is rare-enough not to
// burn CPU but quick enough that a missed event corrects within a
// glance.
const POLL_INTERVAL_MS = 30_000;

// After a WS event we debounce the refresh; multiple events fire in
// bursts (e.g. dispatcher tick that promotes 3 tasks), no point
// re-fetching three times.
const REFRESH_DEBOUNCE_MS = 250;

// ──────────────────────────────────────────────────────────────────
// Status / column metadata
// ──────────────────────────────────────────────────────────────────

interface ColumnDef {
  status: KanbanStatus;
  title: string;
  // Glyph aligned with the rest of the dashboard's ASCII vocabulary.
  glyph: string;
}

// Columns ordered by typical task lifecycle. Archived isn't shown by
// default; the user toggles it via the "show archived" button.
const COLUMNS: ColumnDef[] = [
  { status: "triage", title: "Triage", glyph: "·" },
  { status: "todo", title: "Todo", glyph: "□" },
  { status: "ready", title: "Ready", glyph: "○" },
  { status: "in_progress", title: "In Progress", glyph: "◐" },
  { status: "blocked", title: "Blocked", glyph: "⏸" },
  { status: "done", title: "Done", glyph: "●" },
];

// Tone mapping for the badges + column accents. Stays grayscale +
// accent for the meaningful statuses (in_progress active, blocked
// warn, done accent); the rest use neutral / subtle so the board
// reads as a calm grid until something needs attention.
function statusTone(status: KanbanStatus):
  | "neutral" | "active" | "warn" | "accent" | "subtle" | "stale" {
  switch (status) {
    case "triage": return "subtle";
    case "todo": return "neutral";
    case "ready": return "neutral";
    case "in_progress": return "active";
    case "blocked": return "warn";
    case "done": return "accent";
    case "archived": return "stale";
  }
}

function statusGlyph(status: KanbanStatus): string {
  return COLUMNS.find((c) => c.status === status)?.glyph ?? "·";
}

// ──────────────────────────────────────────────────────────────────
// Quick-add parsing — mirrors Telegram's @lane / ! syntax so the
// muscle memory transfers between mobile and desktop.
// ──────────────────────────────────────────────────────────────────

function parseQuickAdd(raw: string): {
  title: string;
  lane: string | null;
  ready: boolean;
} {
  let lane: string | null = null;
  let ready = false;
  const parts = raw.trim().split(/\s+/);
  const titleParts: string[] = [];
  for (const p of parts) {
    if (p.startsWith("@") && p.length > 1 && lane === null) {
      lane = p.slice(1);
    } else if (p === "!") {
      ready = true;
    } else {
      titleParts.push(p);
    }
  }
  return { title: titleParts.join(" ").trim(), lane, ready };
}

// ──────────────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────────────

export function KanbanPage({ token, onAuthFail }: KanbanPageProps) {
  const [board, setBoard] = useState<KanbanBoardResponse | null>(null);
  const [lanes, setLanes] = useState<KanbanLane[]>([]);
  const [goals, setGoals] = useState<GoalsState | null>(null);
  const [filterLane, setFilterLane] = useState<string | null>(null);
  const [showArchived, setShowArchived] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [quickAdd, setQuickAdd] = useState("");
  const [quickAddBusy, setQuickAddBusy] = useState(false);
  const [draggingId, setDraggingId] = useState<string | null>(null);
  // The column currently under the dragged card — drives the
  // accent-border highlight so the user knows where they'd drop.
  const [dragOverStatus, setDragOverStatus] =
    useState<KanbanStatus | null>(null);
  // The currently in-view column on small viewports. Synced with
  // scroll position so the column-nav chips highlight whichever
  // column the user has scrolled into view. Desktop ignores it.
  const [visibleStatus, setVisibleStatus] =
    useState<KanbanStatus>("triage");
  const boardScrollRef = useRef<HTMLDivElement>(null);

  // Refresh board + goal-pad. Same callback used by the polling loop
  // and by the WS event handler. Returns the loaded board so callers
  // can chain (e.g. open a detail panel right after a refresh).
  const refresh = useCallback(async () => {
    try {
      const [b, l, g] = await Promise.all([
        api.kanbanBoard(token, {
          lane: filterLane ?? undefined,
          archived: showArchived,
        }),
        api.kanbanLanes(token),
        // Goal-pad — read-only projection from the existing /goals
        // endpoint. Failing to load goals shouldn't break the board;
        // catch and leave goals=null.
        api.goals(token).catch(() => null as GoalsState | null),
      ]);
      setBoard(b);
      setLanes(l.lanes);
      setGoals(g);
      setError(null);
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [token, filterLane, showArchived, onAuthFail]);

  // Initial + filter-driven re-fetch.
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Polling backstop. WS handles the live case; polling catches
  // anything WS missed (reconnect gaps, server restarts, etc).
  useEffect(() => {
    const t = window.setInterval(refresh, POLL_INTERVAL_MS);
    return () => window.clearInterval(t);
  }, [refresh]);

  // WebSocket live updates. Reconnects with exponential backoff on
  // disconnect; refreshes the board (debounced) on every event burst.
  useEffect(() => {
    let stopped = false;
    let ws: WebSocket | null = null;
    let backoff = 1000;
    let debounceTimer: number | undefined;
    const debouncedRefresh = () => {
      if (debounceTimer !== undefined) window.clearTimeout(debounceTimer);
      debounceTimer = window.setTimeout(refresh, REFRESH_DEBOUNCE_MS);
    };
    const connect = () => {
      if (stopped) return;
      const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
      const url = `${proto}//${window.location.host}/api/v1/kanban/events?token=${encodeURIComponent(token)}`;
      ws = new WebSocket(url);
      ws.onopen = () => {
        backoff = 1000;
      };
      ws.onmessage = () => {
        debouncedRefresh();
      };
      ws.onerror = () => {
        try { ws?.close(); } catch { /* noop */ }
      };
      ws.onclose = () => {
        if (stopped) return;
        window.setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 30_000);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (debounceTimer !== undefined) window.clearTimeout(debounceTimer);
      try { ws?.close(); } catch { /* noop */ }
    };
  }, [token, refresh]);

  // Quick-add submit handler. Parses @lane / ! suffix and creates the
  // task; clears the input on success, leaves it on failure so the
  // user can edit and retry.
  const submitQuickAdd = useCallback(async () => {
    const text = quickAdd.trim();
    if (!text) return;
    const { title, lane, ready } = parseQuickAdd(text);
    if (!title) {
      setError("title cannot be empty (after stripping @lane/!)");
      return;
    }
    setQuickAddBusy(true);
    try {
      await api.kanbanCreate(token, {
        title,
        lane: lane ?? undefined,
        status: ready ? "ready" : undefined,
      });
      setQuickAdd("");
      await refresh();
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setQuickAddBusy(false);
    }
  }, [quickAdd, token, refresh, onAuthFail]);

  // Scroll the board to a specific column. Used by the column-nav
  // chips above the board — tapping a chip on mobile pages the
  // user to that column.
  const scrollToColumn = useCallback((status: KanbanStatus) => {
    const root = boardScrollRef.current;
    if (!root) return;
    const target = root.querySelector(
      `[data-column-status="${status}"]`,
    ) as HTMLElement | null;
    if (!target) return;
    // ``inline: "start"`` snaps the column to the left edge. The
    // CSS scroll-snap kicks in and keeps it pinned. ``block: "nearest"``
    // avoids scrolling the page vertically when the board is below
    // the fold.
    target.scrollIntoView({
      behavior: "smooth", inline: "start", block: "nearest",
    });
  }, []);

  // Track which column is in view so the nav chips can highlight
  // the active one. Uses IntersectionObserver against the board
  // scroll container — fires whenever a column crosses the
  // viewport's left edge.
  useEffect(() => {
    const root = boardScrollRef.current;
    if (!root) return;
    const cols = root.querySelectorAll<HTMLElement>("[data-column-status]");
    if (cols.length === 0) return;
    const io = new IntersectionObserver(
      (entries) => {
        // The "most intersecting" column wins — handles the transition
        // moment where two columns are partially visible.
        let bestRatio = 0;
        let bestStatus: KanbanStatus | null = null;
        for (const entry of entries) {
          if (entry.isIntersecting && entry.intersectionRatio > bestRatio) {
            bestRatio = entry.intersectionRatio;
            bestStatus = entry.target.getAttribute(
              "data-column-status",
            ) as KanbanStatus;
          }
        }
        if (bestStatus) setVisibleStatus(bestStatus);
      },
      {
        root,
        threshold: [0.25, 0.5, 0.75],
      },
    );
    cols.forEach((c) => io.observe(c));
    return () => io.disconnect();
  }, [board, showArchived]);

  // Drag-drop: card sets the id, column reads it on drop.
  const handleDrop = useCallback(
    async (status: KanbanStatus, taskId: string) => {
      setDraggingId(null);
      setDragOverStatus(null);
      if (!taskId) return;
      // Optimistic — we'll let the WS event correct if the server
      // refuses; for the moment update local state so the card
      // doesn't snap back during the round-trip.
      setBoard((prev) =>
        prev
          ? {
              ...prev,
              tasks: prev.tasks.map((t) =>
                t.id === taskId ? { ...t, status } : t,
              ),
            }
          : prev,
      );
      try {
        await api.kanbanSetStatus(token, taskId, status);
        await refresh();
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
        await refresh();
      }
    },
    [token, refresh, onAuthFail],
  );

  // Group tasks by status for column rendering. Memo because the
  // input list can be 100+ tasks and we don't want to re-iterate on
  // every unrelated render (e.g. quick-add typing).
  const tasksByStatus = useMemo(() => {
    const out: Record<KanbanStatus, KanbanTask[]> = {
      triage: [],
      todo: [],
      ready: [],
      in_progress: [],
      blocked: [],
      done: [],
      archived: [],
    };
    if (board) {
      for (const t of board.tasks) {
        out[t.status]?.push(t);
      }
    }
    return out;
  }, [board]);

  if (error && board === null) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load kanban: {error}
      </div>
    );
  }
  if (!board) {
    return <KanbanSkeleton />;
  }

  return (
    // Fragment so each top-level child becomes a direct child of
    // ``<main>``. ``StickyControlsBar`` MUST be a direct child of
    // ``<main>`` (or a similarly tall container) for
    // ``position:sticky`` to work — its containing block
    // determines how far it can stick. A nested wrapper div would
    // bound sticky to that wrapper's height, which collapses to
    // the visible page in our column-capped layout, and the bar
    // would scroll away once the page scrolls past the wrapper.
    <>
      <div className="space-y-6">
        <Header summary={board.summary} />
        {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

        <QuickAddBar
          value={quickAdd}
          onChange={setQuickAdd}
          onSubmit={submitQuickAdd}
          busy={quickAddBusy}
          lanes={lanes}
        />
      </div>

      <StickyControlsBar
        lanes={lanes}
        active={filterLane}
        onChangeLane={setFilterLane}
        showArchived={showArchived}
        onToggleArchived={() => setShowArchived((s) => !s)}
        columns={COLUMNS}
        archivedColumn={
          showArchived && tasksByStatus.archived.length > 0
            ? { status: "archived", title: "Archived", glyph: "▽" }
            : null
        }
        tasksByStatus={tasksByStatus}
        visibleStatus={visibleStatus}
        onJumpToColumn={scrollToColumn}
        wrapperClassName="mt-6"
      />

      {/* Board (left, scrolls horizontally) + goal-pad sidebar
          (right). The sidebar collapses below the board on
          viewports < xl so the columns get full width on tablets. */}
      <div className="grid grid-cols-1 xl:grid-cols-[1fr_280px] gap-6 items-start mt-4">
        <div
          ref={boardScrollRef}
          className={classNames(
            "overflow-x-auto -mx-3 sm:-mx-1 px-3 sm:px-1",
            // Snap-scroll: each column snaps to the left edge so the
            // user pages between them with a single swipe on touch.
            "snap-x snap-mandatory",
            // Custom scroll-padding so the snapped column lines up
            // with the page gutter rather than the screen edge.
            "scroll-pl-3 sm:scroll-pl-1",
          )}
        >
          <div className="grid grid-flow-col auto-cols-[min(86vw,300px)] sm:auto-cols-[260px] gap-3 min-w-fit pb-2">
            {COLUMNS.map((col) => (
              <BoardColumn
                key={col.status}
                column={col}
                tasks={tasksByStatus[col.status]}
                draggingId={draggingId}
                isDropTarget={dragOverStatus === col.status}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (dragOverStatus !== col.status) {
                    setDragOverStatus(col.status);
                  }
                }}
                onDragLeave={() => {
                  if (dragOverStatus === col.status) setDragOverStatus(null);
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const id = e.dataTransfer.getData("text/plain");
                  handleDrop(col.status, id);
                }}
                onCardClick={setSelectedId}
                onCardDragStart={(id) => setDraggingId(id)}
                onCardDragEnd={() => {
                  setDraggingId(null);
                  setDragOverStatus(null);
                }}
              />
            ))}
            {showArchived && tasksByStatus.archived.length > 0 && (
              <BoardColumn
                column={{ status: "archived", title: "Archived", glyph: "▽" }}
                tasks={tasksByStatus.archived}
                draggingId={draggingId}
                isDropTarget={dragOverStatus === "archived"}
                onDragOver={(e) => {
                  e.preventDefault();
                  if (dragOverStatus !== "archived") {
                    setDragOverStatus("archived");
                  }
                }}
                onDragLeave={() => {
                  if (dragOverStatus === "archived") {
                    setDragOverStatus(null);
                  }
                }}
                onDrop={(e) => {
                  e.preventDefault();
                  const id = e.dataTransfer.getData("text/plain");
                  handleDrop("archived", id);
                }}
                onCardClick={setSelectedId}
                onCardDragStart={(id) => setDraggingId(id)}
                onCardDragEnd={() => {
                  setDraggingId(null);
                  setDragOverStatus(null);
                }}
              />
            )}
          </div>
        </div>

        <GoalPad goals={goals} />
      </div>

      {selectedId && (
        <TaskDetailModal
          token={token}
          taskId={selectedId}
          lanes={lanes}
          onClose={() => setSelectedId(null)}
          onMutate={refresh}
          onAuthFail={onAuthFail}
        />
      )}
    </>
  );
}

// ──────────────────────────────────────────────────────────────────
// Header — page title + summary line
// ──────────────────────────────────────────────────────────────────

function Header({
  summary,
}: {
  summary: KanbanBoardResponse["summary"];
}) {
  // Compact summary: "triage:1 · todo:3 · ready:0 · in_progress:1"
  const parts = COLUMNS.map((c) => {
    const n = (summary as Record<string, number | undefined>)[c.status] ?? 0;
    return `${c.title.toLowerCase()}:${n}`;
  });
  return (
    <div className="space-y-1">
      <h1 className="font-data text-[15px] tracking-tight text-[var(--color-fg)]">
        ▦ <span className="ml-1">Kanban</span>
      </h1>
      <p className="text-xs text-[var(--color-fg-dim)] font-data leading-relaxed max-w-[80ch]">
        Multi-task work queue. Add tasks here or via{" "}
        <code className="text-[var(--color-fg-2)]">/kanban add</code> on
        Telegram. The dispatcher claims ready tasks and spawns one
        worker per task via the configured lane.
      </p>
      <p className="font-data text-[10.5px] text-[var(--color-fg-dim)] tracking-wide pt-1">
        {parts.join("  ·  ")}
      </p>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Banners
// ──────────────────────────────────────────────────────────────────

function ErrorBanner({
  message,
  onDismiss,
}: {
  message: string;
  onDismiss: () => void;
}) {
  return (
    <div className="hairline px-4 py-3 bg-[var(--color-surface)] flex items-baseline gap-3">
      <p className="font-data text-[12px] text-[var(--color-error)] flex-1 min-w-0">
        <span className="uppercase-tight text-[10px] mr-2">kanban</span>
        {message}
      </p>
      <button
        onClick={onDismiss}
        className="font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)]"
      >
        dismiss
      </button>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Quick add bar
// ──────────────────────────────────────────────────────────────────

function QuickAddBar({
  value,
  onChange,
  onSubmit,
  busy,
  lanes,
}: {
  value: string;
  onChange: (v: string) => void;
  onSubmit: () => void;
  busy: boolean;
  lanes: KanbanLane[];
}) {
  return (
    <Card>
      <div className="px-4 py-3 space-y-2">
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            onSubmit();
          }}
        >
          <span aria-hidden className="font-data text-[var(--color-accent)] select-none">
            +
          </span>
          <input
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder='New task — "ship the API @implementation !" sets lane and ready'
            className="flex-1 bg-transparent border-0 outline-none font-data text-[13px] text-[var(--color-fg)] placeholder:text-[var(--color-fg-dim)] focus:ring-0"
            disabled={busy}
          />
          <Button
            variant="primary"
            size="sm"
            disabled={!value.trim() || busy}
            loading={busy}
          >
            Add
          </Button>
        </form>
        <p className="font-data text-[10.5px] text-[var(--color-fg-dim)] leading-relaxed">
          Suffixes: <code className="text-[var(--color-fg-2)]">@lane</code>{" "}
          (one of: {lanes.map((l) => l.name).join(", ")}){"  ·  "}
          <code className="text-[var(--color-fg-2)]">!</code> = skip triage,
          go straight to ready
        </p>
      </div>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────
// Filter bar
// ──────────────────────────────────────────────────────────────────

// Sticky controls bar — combines the column-nav chips (jump to a
// specific column on mobile) with the lane filter and archived
// toggle. Sticks to the top of the viewport on scroll so the user
// always knows which column they're in and which filters are on.
function StickyControlsBar({
  lanes,
  active,
  onChangeLane,
  showArchived,
  onToggleArchived,
  columns,
  archivedColumn,
  tasksByStatus,
  visibleStatus,
  onJumpToColumn,
  wrapperClassName,
}: {
  lanes: KanbanLane[];
  active: string | null;
  onChangeLane: (lane: string | null) => void;
  showArchived: boolean;
  onToggleArchived: () => void;
  columns: ColumnDef[];
  archivedColumn: ColumnDef | null;
  tasksByStatus: Record<KanbanStatus, KanbanTask[]>;
  visibleStatus: KanbanStatus;
  onJumpToColumn: (status: KanbanStatus) => void;
  wrapperClassName?: string;
}) {
  const allCols = archivedColumn ? [...columns, archivedColumn] : columns;
  return (
    <div
      className={classNames(
        "sticky top-0 z-30",
        // Bleed past the page gutter so the bg covers the full
        // width even when the sticky element is the only thing
        // visible mid-scroll.
        "-mx-3 sm:-mx-5 px-3 sm:px-5 py-2.5",
        "bg-[var(--color-base)]/92 backdrop-blur-sm",
        "border-b border-[var(--color-border)] space-y-2",
        wrapperClassName,
      )}
    >
      {/* Column-nav chips. Tap = jump to column. The active column
          (whichever is most-visible in the board scroll) gets a
          stronger border + filled bg so the user always knows
          where they are while paging through columns on mobile. */}
      <div
        className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 pb-0.5"
        role="tablist"
        aria-label="Kanban columns"
      >
        {allCols.map((col) => {
          const count = tasksByStatus[col.status]?.length ?? 0;
          const isActive = visibleStatus === col.status;
          return (
            <button
              key={col.status}
              onClick={() => onJumpToColumn(col.status)}
              role="tab"
              aria-selected={isActive}
              className={classNames(
                "font-data text-[10.5px] uppercase tracking-widest",
                "px-2.5 py-1.5 hairline rounded-[2px] shrink-0",
                "flex items-center gap-1.5 transition-colors",
                "min-h-[28px]",
                isActive
                  ? "text-[var(--color-fg)] border-[var(--color-border-strong)] bg-[var(--color-surface-2)]"
                  : "text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)] hover:border-[var(--color-border-strong)]",
              )}
            >
              <span aria-hidden className="text-[11px]">{col.glyph}</span>
              <span>{col.title}</span>
              <span className="tabular-nums opacity-60">{count}</span>
            </button>
          );
        })}
      </div>

      {/* Lane filter row. ``flex-wrap`` so it folds to a second
          line on narrow screens; the scroll-x in the column nav
          above stays single-row. */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="uppercase-tight text-[10px] text-[var(--color-fg-dim)]">
          lane:
        </span>
        <FilterChip
          active={active === null}
          onClick={() => onChangeLane(null)}
        >
          all
        </FilterChip>
        {lanes.map((lane) => (
          <FilterChip
            key={lane.name}
            active={active === lane.name}
            onClick={() => onChangeLane(lane.name)}
          >
            {lane.name}
          </FilterChip>
        ))}
        <button
          onClick={onToggleArchived}
          className={classNames(
            "ml-auto font-data text-[10px] uppercase tracking-widest",
            "px-2 py-1.5 hairline min-h-[28px]",
            showArchived
              ? "text-[var(--color-fg)] border-[var(--color-border-strong)]"
              : "text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)]",
          )}
        >
          {showArchived ? "hide archived" : "archived"}
        </button>
      </div>
    </div>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={classNames(
        "font-data text-[10px] uppercase tracking-widest px-2 py-1 hairline rounded-[2px]",
        active
          ? "text-[var(--color-accent)] border-[var(--color-accent)]/40 bg-[var(--color-accent)]/[0.06]"
          : "text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)] hover:border-[var(--color-border-strong)]",
      )}
    >
      {children}
    </button>
  );
}

// ──────────────────────────────────────────────────────────────────
// Board column + cards
// ──────────────────────────────────────────────────────────────────

function BoardColumn({
  column,
  tasks,
  draggingId,
  isDropTarget,
  onDragOver,
  onDragLeave,
  onDrop,
  onCardClick,
  onCardDragStart,
  onCardDragEnd,
}: {
  column: ColumnDef;
  tasks: KanbanTask[];
  draggingId: string | null;
  isDropTarget: boolean;
  onDragOver: (e: DragEvent<HTMLDivElement>) => void;
  onDragLeave: (e: DragEvent<HTMLDivElement>) => void;
  onDrop: (e: DragEvent<HTMLDivElement>) => void;
  onCardClick: (id: string) => void;
  onCardDragStart: (id: string) => void;
  onCardDragEnd: () => void;
}) {
  const tone = statusTone(column.status);
  const accentText =
    tone === "active" ? "text-[var(--color-accent)]" :
    tone === "warn" ? "text-[var(--color-warn)]" :
    tone === "accent" ? "text-[var(--color-accent)]" :
    "text-[var(--color-fg-2)]";
  return (
    <div
      data-column-status={column.status}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      className={classNames(
        "hairline bg-[var(--color-surface)] flex flex-col",
        // ``snap-start`` aligns each column to the scroll container's
        // left edge when it snaps. ``min-h-[60vh]`` gives every
        // column the same vertical real-estate even when empty so
        // the board looks balanced.
        "snap-start min-h-[60vh] max-h-[70vh]",
        // Drop-target accent: when the user is dragging a card AND
        // hovering over THIS column, lift the border + background so
        // the drop site is unambiguous. The base ``draggingId`` state
        // adds a softer border to every column so the user knows
        // dropping is in flight.
        isDropTarget
          ? "border-[var(--color-accent)] bg-[var(--color-accent)]/[0.04]"
          : draggingId
          ? "border-[var(--color-border-strong)]"
          : "",
      )}
    >
      <header
        className={classNames(
          "px-3 py-2.5 flex items-baseline justify-between gap-2",
          "border-b border-[var(--color-border)] sticky top-0",
          "bg-[var(--color-surface)] z-10",
        )}
      >
        <span className={classNames(
          "font-data text-[11px] uppercase tracking-widest",
          accentText,
        )}>
          <span aria-hidden className="mr-1">{column.glyph}</span>
          {column.title}
        </span>
        <span className="font-data text-[10.5px] text-[var(--color-fg-dim)] tabular-nums">
          {tasks.length}
        </span>
      </header>
      <div className="overflow-y-auto p-2 space-y-2 flex-1">
        {tasks.length === 0 ? (
          <div className={classNames(
            "px-2 py-6 text-center font-data text-[10px] uppercase",
            "tracking-widest text-[var(--color-fg-dim)]",
            // Slightly lifted hint when the user is dragging — gives
            // the empty column more visual presence as a drop target.
            isDropTarget ? "text-[var(--color-accent)]" : "",
          )}>
            {isDropTarget ? "drop here" : "empty"}
          </div>
        ) : (
          tasks.map((t) => (
            <TaskCard
              key={t.id}
              task={t}
              dragging={draggingId === t.id}
              onClick={() => onCardClick(t.id)}
              onDragStart={() => onCardDragStart(t.id)}
              onDragEnd={onCardDragEnd}
            />
          ))
        )}
      </div>
    </div>
  );
}

function TaskCard({
  task,
  dragging,
  onClick,
  onDragStart,
  onDragEnd,
}: {
  task: KanbanTask;
  dragging: boolean;
  onClick: () => void;
  onDragStart: () => void;
  onDragEnd: () => void;
}) {
  return (
    <div
      draggable
      onDragStart={(e) => {
        e.dataTransfer.setData("text/plain", task.id);
        e.dataTransfer.effectAllowed = "move";
        onDragStart();
      }}
      onDragEnd={onDragEnd}
      onClick={onClick}
      className={classNames(
        "hairline bg-[var(--color-base)] cursor-pointer select-none",
        // Bumped padding + min-height so the card is comfortably
        // tappable on touch (≥44px target — Apple HIG / Material).
        "px-3 py-2.5 space-y-1.5 min-h-[64px]",
        "hover:border-[var(--color-border-strong)] active:bg-[var(--color-surface-2)] transition-colors",
        dragging ? "opacity-40" : "",
      )}
    >
      <div className="flex items-baseline gap-2">
        <span aria-hidden className="font-data text-[var(--color-fg-dim)] text-[10px] mt-px">
          {statusGlyph(task.status)}
        </span>
        <span className="font-data text-[12.5px] text-[var(--color-fg)] line-clamp-2 leading-snug min-w-0 flex-1">
          {task.title}
        </span>
      </div>
      <div className="flex items-center gap-1.5 flex-wrap">
        {task.lane && (
          <span className="font-data text-[9.5px] uppercase tracking-widest text-[var(--color-fg-dim)] hairline px-1 py-[1px]">
            {task.lane}
          </span>
        )}
        {task.priority > 0 && (
          <span className="font-data text-[9.5px] uppercase tracking-widest text-[var(--color-accent)] hairline border-[var(--color-accent)]/40 px-1 py-[1px]">
            p{task.priority}
          </span>
        )}
        {task.consecutive_failures > 0 && (
          <span className="font-data text-[9.5px] uppercase tracking-widest text-[var(--color-warn)] hairline border-[var(--color-warn)]/40 px-1 py-[1px]">
            ✕{task.consecutive_failures}
          </span>
        )}
        <span className="ml-auto font-data text-[9.5px] text-[var(--color-fg-dim)]">
          {task.id}
        </span>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Goal-pad sidebar (read-only projection of /goal)
// ──────────────────────────────────────────────────────────────────

function GoalPad({ goals }: { goals: GoalsState | null }) {
  return (
    <Section title="Goal pad" trailing={
      <a
        href="#goals"
        className="font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)]"
      >
        full →
      </a>
    }>
      {!goals?.active ? (
        <Card>
          <div className="px-4 py-4">
            <EmptyState
              glyph="·"
              title="No active goal."
              hint={
                <>
                  Set one with{" "}
                  <code className="font-data text-[var(--color-fg)]">
                    /goal &lt;text&gt;
                  </code>{" "}
                  on Telegram. Goals run parallel to kanban — different
                  state machine, same observation surface.
                </>
              }
            />
          </div>
        </Card>
      ) : (
        <GoalCard record={goals.active} />
      )}
      {goals?.history && goals.history.length > 0 && (
        <details className="mt-2 group">
          <summary className="cursor-pointer font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)]">
            recent ({goals.history.length})
          </summary>
          <div className="space-y-1 mt-2">
            {goals.history.slice(0, 5).map((g, i) => (
              <div
                key={`${g.session_uuid}-${i}`}
                className="hairline bg-[var(--color-surface)] px-2 py-1.5"
              >
                <div className="flex items-baseline gap-2">
                  <span className="font-data text-[10px] text-[var(--color-fg-dim)] uppercase tracking-widest">
                    {g.status}
                  </span>
                  <span className="font-data text-[10px] text-[var(--color-fg-dim)] tabular-nums">
                    {g.turns_used}/{g.max_turns}
                  </span>
                </div>
                <div className="font-data text-[11px] text-[var(--color-fg-2)] line-clamp-2 mt-0.5">
                  {g.goal}
                </div>
              </div>
            ))}
          </div>
        </details>
      )}
    </Section>
  );
}

function GoalCard({ record }: { record: GoalRecord }) {
  return (
    <Card>
      <div className="px-4 py-3 space-y-2">
        <div className="flex items-baseline gap-2">
          <Badge tone="active" glyph="⊙">
            {record.status}
          </Badge>
          <span className="font-data text-[10px] tabular-nums text-[var(--color-fg-2)]">
            {record.turns_used}/{record.max_turns}
          </span>
        </div>
        <div className="font-data text-[12px] text-[var(--color-fg)] leading-snug">
          {record.goal}
        </div>
        <div className="space-y-0.5 pt-1 border-t border-[var(--color-border)]">
          {record.last_verdict && (
            <div className="font-data text-[10px] text-[var(--color-fg-dim)]">
              <span className="uppercase tracking-widest">verdict</span>{" "}
              {record.last_verdict}
            </div>
          )}
          {record.last_reason && (
            <div className="font-data text-[10px] text-[var(--color-fg-dim)] line-clamp-2">
              <span className="uppercase tracking-widest">reason</span>{" "}
              {record.last_reason}
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────
// Task detail modal
// ──────────────────────────────────────────────────────────────────

function TaskDetailModal({
  token,
  taskId,
  lanes,
  onClose,
  onMutate,
  onAuthFail,
}: {
  token: string;
  taskId: string;
  lanes: KanbanLane[];
  onClose: () => void;
  onMutate: () => Promise<void>;
  onAuthFail: () => void;
}) {
  const [detail, setDetail] = useState<KanbanTaskDetailResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [comment, setComment] = useState("");
  const [blockReason, setBlockReason] = useState("");
  const [showBlockForm, setShowBlockForm] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  const refresh = useCallback(async () => {
    try {
      const d = await api.kanbanTask(token, taskId);
      setDetail(d);
      setError(null);
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }, [token, taskId, onAuthFail]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // Esc to close, click-outside to close.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const runAction = useCallback(
    async (fn: () => Promise<unknown>) => {
      setBusy(true);
      try {
        await fn();
        await refresh();
        await onMutate();
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setBusy(false);
      }
    },
    [refresh, onMutate, onAuthFail],
  );

  return (
    <div
      className="fixed inset-0 z-40 bg-[var(--color-base)]/80 backdrop-blur-sm flex items-start justify-center pt-12 px-4 pb-12 overflow-y-auto"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        className="hairline bg-[var(--color-surface)] max-w-2xl w-full"
        role="dialog"
        aria-modal="true"
      >
        {/* Header bar */}
        <div className="px-5 py-3 border-b border-[var(--color-border)] flex items-baseline gap-3">
          <span className="font-data text-[10px] text-[var(--color-fg-dim)] uppercase tracking-widest">
            task {taskId}
          </span>
          <button
            onClick={onClose}
            className="ml-auto font-data text-[14px] text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]"
            aria-label="close"
          >
            ✕
          </button>
        </div>

        {error && (
          <div className="hairline border-x-0 border-t-0 px-5 py-2 text-[12px] text-[var(--color-error)] font-data">
            {error}
          </div>
        )}

        {!detail ? (
          <div className="p-6 font-data text-[12px] text-[var(--color-fg-dim)] uppercase tracking-widest">
            loading…
          </div>
        ) : (
          <div className="px-5 py-4 space-y-4">
            <div className="flex items-baseline gap-3 flex-wrap">
              <Badge
                tone={statusTone(detail.task.status)}
                glyph={statusGlyph(detail.task.status)}
              >
                {detail.task.status.replace("_", " ")}
              </Badge>
              {detail.task.lane && (
                <span className="font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-2)] hairline px-1.5 py-[1px]">
                  {detail.task.lane}
                </span>
              )}
              {detail.task.priority > 0 && (
                <span className="font-data text-[10px] uppercase tracking-widest text-[var(--color-accent)]">
                  priority {detail.task.priority}
                </span>
              )}
              {detail.task.consecutive_failures > 0 && (
                <span className="font-data text-[10px] uppercase tracking-widest text-[var(--color-warn)]">
                  {detail.task.consecutive_failures} consecutive failures
                </span>
              )}
            </div>

            <h3 className="font-sans text-[15px] text-[var(--color-fg)] leading-snug">
              {detail.task.title}
            </h3>

            {detail.task.body && (
              <p className="font-data text-[12px] text-[var(--color-fg-2)] whitespace-pre-wrap leading-relaxed">
                {detail.task.body}
              </p>
            )}

            {/* Metadata grid */}
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5 pt-2 border-t border-[var(--color-border)]">
              <KV label="created" value={relativeTime(timestampToIso(detail.task.created_at))} />
              {detail.task.started_at && (
                <KV label="started" value={relativeTime(timestampToIso(detail.task.started_at))} />
              )}
              {detail.task.completed_at && (
                <KV label="completed" value={relativeTime(timestampToIso(detail.task.completed_at))} />
              )}
              <KV label="created by" value={detail.task.created_by ?? "—"} />
              {detail.parents.length > 0 && (
                <KV label="parents" value={detail.parents.join(", ")} />
              )}
              {detail.children.length > 0 && (
                <KV label="children" value={detail.children.join(", ")} />
              )}
              {detail.task.last_failure_error && (
                <KV
                  label="last error"
                  value={detail.task.last_failure_error}
                  span2
                />
              )}
            </div>

            {/* Move-to-column + lane reassign. Both are select-driven
                so the touch UX (no drag-drop on phones) is direct:
                pick a column, status flips. The select submits on
                change — no confirm step needed (the dispatcher
                catches any post-flip surprises). */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 pt-2 border-t border-[var(--color-border)]">
              <label className="flex items-center gap-2 min-w-0">
                <span className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] shrink-0">
                  column:
                </span>
                <select
                  value={detail.task.status}
                  onChange={(e) => {
                    const next = e.target.value;
                    if (next === detail.task.status) return;
                    runAction(async () => {
                      const resp = await fetch(
                        `/api/v1/kanban/tasks/${encodeURIComponent(taskId)}/status`,
                        {
                          method: "POST",
                          headers: {
                            "Content-Type": "application/json",
                            Authorization: `Bearer ${token}`,
                          },
                          body: JSON.stringify({ status: next }),
                        },
                      );
                      if (!resp.ok) {
                        throw new ApiError(
                          resp.status,
                          (await resp.json().catch(() => ({}))).detail
                            ?.error ?? resp.statusText,
                        );
                      }
                      return resp.json();
                    });
                  }}
                  disabled={busy}
                  className="flex-1 min-w-0 font-data text-[11.5px] bg-[var(--color-base)] hairline px-2 py-1.5 text-[var(--color-fg)] focus:outline-none focus:border-[var(--color-border-strong)]"
                >
                  {[
                    "triage", "todo", "ready", "in_progress",
                    "blocked", "done", "archived",
                  ].map((s) => (
                    <option key={s} value={s}>
                      {s.replace("_", " ")}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2 min-w-0">
                <span className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] shrink-0">
                  lane:
                </span>
                <select
                  value={detail.task.lane ?? ""}
                  onChange={(e) =>
                    runAction(() =>
                      api.kanbanAssign(
                        token, taskId,
                        e.target.value ? e.target.value : null,
                      ),
                    )
                  }
                  disabled={busy}
                  className="flex-1 min-w-0 font-data text-[11.5px] bg-[var(--color-base)] hairline px-2 py-1.5 text-[var(--color-fg)] focus:outline-none focus:border-[var(--color-border-strong)]"
                >
                  <option value="">(none)</option>
                  {lanes.map((lane) => (
                    <option key={lane.name} value={lane.name}>
                      {lane.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-2 flex-wrap pt-3 border-t border-[var(--color-border)]">
              {detail.task.status !== "done" && (
                <Button
                  variant="primary"
                  size="sm"
                  loading={busy}
                  onClick={() =>
                    runAction(() => api.kanbanComplete(token, taskId))
                  }
                >
                  ✔ Complete
                </Button>
              )}
              {detail.task.status === "blocked" ? (
                <Button
                  variant="ghost"
                  size="sm"
                  loading={busy}
                  onClick={() =>
                    runAction(() => api.kanbanUnblock(token, taskId))
                  }
                >
                  ↺ Unblock
                </Button>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  loading={busy}
                  onClick={() => setShowBlockForm((v) => !v)}
                >
                  ⏸ Block
                </Button>
              )}
              <Button
                variant="danger"
                size="sm"
                loading={busy}
                onClick={() =>
                  runAction(() => api.kanbanArchive(token, taskId))
                }
              >
                Archive
              </Button>
            </div>

            {showBlockForm && (
              <form
                className="space-y-2 pt-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!blockReason.trim()) return;
                  runAction(() =>
                    api.kanbanBlock(token, taskId, blockReason.trim()),
                  ).then(() => {
                    setBlockReason("");
                    setShowBlockForm(false);
                  });
                }}
              >
                <input
                  autoFocus
                  value={blockReason}
                  onChange={(e) => setBlockReason(e.target.value)}
                  placeholder="reason for blocking…"
                  className="w-full bg-[var(--color-base)] hairline px-2 py-1.5 font-data text-[12px] text-[var(--color-fg)] focus:outline-none focus:border-[var(--color-border-strong)]"
                />
                <div className="flex items-center gap-2">
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={!blockReason.trim() || busy}
                  >
                    Block
                  </Button>
                  <button
                    type="button"
                    onClick={() => {
                      setBlockReason("");
                      setShowBlockForm(false);
                    }}
                    className="font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)]"
                  >
                    cancel
                  </button>
                </div>
              </form>
            )}

            {/* Comments */}
            <div className="space-y-2 pt-3 border-t border-[var(--color-border)]">
              <div className="uppercase-tight text-[10px] text-[var(--color-fg-dim)]">
                comments ({detail.comments.length})
              </div>
              {detail.comments.length === 0 && (
                <div className="font-data text-[10.5px] text-[var(--color-fg-dim)]">
                  no comments yet
                </div>
              )}
              <div className="space-y-1.5">
                {detail.comments.map((c) => (
                  <CommentRow key={c.id} comment={c} />
                ))}
              </div>
              <form
                className="flex items-center gap-2 pt-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!comment.trim()) return;
                  runAction(() =>
                    api.kanbanComment(token, taskId, comment.trim()),
                  ).then(() => setComment(""));
                }}
              >
                <input
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  placeholder="add a comment…"
                  className="flex-1 bg-[var(--color-base)] hairline px-2 py-1.5 font-data text-[12px] text-[var(--color-fg)] focus:outline-none focus:border-[var(--color-border-strong)]"
                />
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={!comment.trim() || busy}
                >
                  Post
                </Button>
              </form>
            </div>

            {/* Run history */}
            {detail.runs.length > 0 && (
              <div className="space-y-2 pt-3 border-t border-[var(--color-border)]">
                <div className="uppercase-tight text-[10px] text-[var(--color-fg-dim)]">
                  runs ({detail.runs.length})
                </div>
                <div className="space-y-1.5">
                  {detail.runs.slice(0, 5).map((r) => (
                    <RunRow key={r.id} run={r} />
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function CommentRow({ comment }: { comment: KanbanComment }) {
  return (
    <div className="hairline bg-[var(--color-base)] px-2.5 py-1.5">
      <div className="flex items-baseline gap-2">
        <span className="font-data text-[10px] uppercase tracking-widest text-[var(--color-fg-dim)]">
          {comment.author}
        </span>
        <span className="font-data text-[10px] text-[var(--color-fg-dim)]">
          {relativeTime(timestampToIso(comment.created_at))}
        </span>
      </div>
      <div className="font-data text-[12px] text-[var(--color-fg-2)] whitespace-pre-wrap mt-1">
        {comment.body}
      </div>
    </div>
  );
}

function RunRow({ run }: { run: KanbanRun }) {
  const outcomeTone =
    run.outcome === "completed" ? "accent" :
    run.outcome === "blocked" ? "warn" :
    run.outcome === "spawn_failed" || run.outcome === "failed" || run.outcome === "timed_out" || run.outcome === "crashed" ? "error" :
    "subtle";
  return (
    <div className="hairline bg-[var(--color-base)] px-2.5 py-1.5">
      <div className="flex items-baseline gap-2 flex-wrap">
        <Badge tone={outcomeTone}>{run.outcome ?? "in progress"}</Badge>
        <span className="font-data text-[10px] text-[var(--color-fg-dim)]">
          run #{run.id} · {relativeTime(timestampToIso(run.started_at))}
          {run.ended_at &&
            <> → {relativeTime(timestampToIso(run.ended_at))}</>}
        </span>
      </div>
      {run.summary && (
        <div className="font-data text-[11px] text-[var(--color-fg-2)] whitespace-pre-wrap mt-1 line-clamp-3">
          {run.summary}
        </div>
      )}
      {run.error && (
        <div className="font-data text-[11px] text-[var(--color-error)] whitespace-pre-wrap mt-1 line-clamp-2">
          {run.error}
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────

function timestampToIso(seconds: number): string {
  return new Date(seconds * 1000).toISOString();
}

function KV({
  label, value, span2,
}: { label: string; value: string; span2?: boolean }) {
  return (
    <div className={classNames(
      "flex items-baseline gap-2 min-w-0",
      span2 && "col-span-2",
    )}>
      <span className="uppercase-tight text-[10px] text-[var(--color-fg-dim)] shrink-0">
        {label}
      </span>
      <span
        className="font-data text-[11.5px] text-[var(--color-fg-2)] truncate"
        title={value}
      >
        {value}
      </span>
    </div>
  );
}

function KanbanSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-12 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-14 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="grid grid-cols-6 gap-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <div
            key={i}
            className="h-64 hairline bg-[var(--color-surface)] animate-pulse-slow"
          />
        ))}
      </div>
    </div>
  );
}
