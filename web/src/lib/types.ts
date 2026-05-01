// Shape of API responses. Mirrors the FastAPI route bodies in
// core/web_server.py — keep both in lock-step when adding fields.

export interface MemoryBlock {
  entries: string[];
  current: number;
  limit: number;
  percent: number;
  mtime: string | null;
  path: string;
}

export interface MemoryState {
  memory: MemoryBlock;
  user: MemoryBlock;
}

export interface ActiveSkill {
  name: string;
  description: string;
  category: string;
  state: "active" | "stale" | "archived";
  view_count: number;
  use_count: number;
  patch_count: number;
  last_used_at: string | null;
  created_at: string | null;
  pinned: boolean;
  path: string;
}

export interface ArchivedSkill {
  name: string;
  archived_at: string | null;
  description: string;
}

export interface SkillsState {
  active: ActiveSkill[];
  archived: ArchivedSkill[];
}

export interface SkillBody {
  name: string;
  description: string;
  category: string;
  body: string;
  path: string;
  frontmatter: Record<string, unknown>;
}

export interface CuratorRunSummary {
  folder: string;
  started_at: string | null;
  finished_at: string | null;
  phase1: {
    checked: number;
    marked_stale: number;
    reactivated: number;
    archived: number;
  };
  phase2_ran: boolean;
  phase2_archived: string[];
  phase2_created: string[];
  phase2_error: string | null;
}

export interface CuratorState {
  enabled: boolean;
  paused: boolean;
  running: boolean;
  last_run_at: string | null;
  last_run_summary: string | null;
  next_eligible_at: string | null;
  interval_hours: number;
  stale_after_days: number;
  archive_after_days: number;
  archived_count: number;
  runs: CuratorRunSummary[];
}

export interface CuratorRunDetail {
  folder: string;
  report_md: string;
  run_json: Record<string, unknown> | null;
}

export interface SessionInfo {
  name: string;
  uuid: string;
  initialized: boolean;
  created_at: string;
  is_active: boolean;
}

export interface ForegroundChat {
  chat_id: number;
  drain_active: boolean;
  queue_depth: number;
  slot_reserved: boolean;
  slot_pid: number | null;
  cancelled: boolean;
}

export interface BackgroundTaskSummary {
  name: string;
  chat_id: number;
  status: "pending" | "running" | "finished" | "cancelled" | "failed";
  spawned_at: string;
  finished_at: string | null;
  exit_code: number | null;
  pid: number | null;
  log_path: string;
}

export interface LogLine {
  ts: string;
  level: string;
  logger: string;
  message: string;
}

export interface StatusState {
  started_at: string;
  uptime_seconds: number;
  session_count: number;
  active_session: string;
  sessions: SessionInfo[];
  foreground_chats: ForegroundChat[];
  background_tasks: BackgroundTaskSummary[];
  log_lines: LogLine[];
}
