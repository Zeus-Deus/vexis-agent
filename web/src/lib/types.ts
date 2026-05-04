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

export interface BrowserSessionInfo {
  state: "running" | "idle" | "not_started";
  current_url: string | null;
  current_title: string | null;
  started_at: string | null;
  last_activity_at: string | null;
  headless: boolean;
  attach_mode: "owned-chromium" | "cdp-attach";
}

export interface BrowserProfileInfo {
  path: string;
  exists: boolean;
  size_bytes: number | null;
  size_as_of: string | null;
  cookie_count: number | null;
}

export interface BrowserNavigationEntry {
  url: string;
  at: string;
}

export interface BrowserScreenshotEntry {
  filename: string;
  size_bytes: number;
  mtime: string;
}

export interface BrowserConfigSnapshot {
  profiles_dir: string;
  default_profile: string;
  headless: boolean;
  inactivity_timeout_seconds: number;
  action_timeout_seconds: number;
  chromium_path: string | null;
  cdp_url: string | null;
  screenshot_include_base64: boolean;
}

export interface BrowserState {
  session: BrowserSessionInfo;
  profile: BrowserProfileInfo;
  recent_navigations: BrowserNavigationEntry[];
  recent_screenshots: BrowserScreenshotEntry[];
  config: BrowserConfigSnapshot;
}

// ---- Learning tab (Step 15) ------------------------------------

export type CoherenceVerdict = "INCOHERENT" | "NEAR_MISS_REVIEW" | "COHERENT";

export interface LearningCuratorRow {
  name: "archive" | "learning" | "coherence";
  nested_under: string | null;
  enabled: boolean;
  paused: boolean;
  running: boolean;
  last_run_at: string | null;
  next_eligible_at: string | null;
  summary: string;
  interval_label: string;
}

export type LearningOutcomeKind =
  | "wrote"
  | "rejected"
  | "nothing-to-save"
  | "cooldown"
  | "error";

export interface LearningActivityRow {
  tick_folder: string;
  tick_at: string | null;
  session_uuid_prefix: string | null;
  outcome: LearningOutcomeKind;
  outcome_detail: string;
  lesson_preview: string | null;
  class: string | null;
  tier: string | null;
  source: string | null;
  coherence_verdict: CoherenceVerdict | null;
  coherence_reason: string | null;
  outcome_marker: string | null;
  entry_id: string | null;
}

export interface LearningShadowEntry {
  source: string;
  lesson: string;
  lesson_preview: string;
  class: string | null;
  tier: string | null;
  scope: string | null;
  evidence: string | null;
  coherence_verdict: CoherenceVerdict | null;
  coherence_reason: string | null;
  coherence_explanation: string | null;
  outcome_marker: string | null;
  source_session_prefix: string | null;
  entry_id: string;
}

export interface LearningDistribution {
  window_ticks: number;
  by_class: Record<string, number>;
  by_tier: Record<string, number>;
  a2_watch: boolean;
}

export interface LearningRates {
  window_ticks_scanned: number;
  dedup_skipped: number;
  coherence_flagged: number;
  coherence_near_miss: number;
  coherence_by_reason: Record<string, number>;
}

export interface LearningUserCandidate {
  claim_preview: string;
  distinct_sessions: number;
  threshold: number;
  first_seen: string;
  last_seen: string;
  days_until_expiry: number;
}

export interface LearningUserCandidates {
  pending: LearningUserCandidate[];
  promoted_count: number;
}

export interface LearningCuratorSkill {
  name: string;
  origin: string;
}

export interface LearningCuratorSkills {
  live: LearningCuratorSkill[];
  staged: LearningCuratorSkill[];
}

export interface LearningModels {
  brain?: string;
  learning_review?: string;
  coherence_judge?: string;
  migration_classifier?: string;
}

export interface LearningState {
  curators: LearningCuratorRow[];
  recent_activity: LearningActivityRow[];
  shadow_entries: LearningShadowEntry[];
  distribution: LearningDistribution;
  rates: LearningRates;
  user_candidates: LearningUserCandidates;
  coherence_pending_review: LearningShadowEntry[];
  curator_skills: LearningCuratorSkills;
  models: LearningModels;
  learning_disabled: boolean;
}

export interface LearningJudgeResult {
  verdict: CoherenceVerdict;
  reason: string | null;
  explanation: string | null;
  degraded: boolean;
  judged_at: string;
}

export interface LearningJudgeRequest {
  lesson: string;
  scope: string;
  evidence: string;
  class?: string | null;
  tier?: string | null;
  source?: string | null;
  entry_id?: string | null;
}

// v3c Day 4b — RELATIONSHIPS.md live + candidate queue.

export interface RelationshipFact {
  text: string;
  confirmed_date: string;
  source_session_short: string;
  superseded_by_date: string | null;
  superseded_by_session: string | null;
}

export interface RelationshipPerson {
  slug: string;
  display_name: string;
  relationship: string;
  qualifier: string | null;
  last_confirmed: string;
  source_session: string;
  facts: RelationshipFact[];
}

export interface RelationshipsLiveState {
  people: RelationshipPerson[];
}

export interface CandidateFactView {
  fact_id: string;
  text: string;
  occurrence_count: number;
  first_seen: string;
  last_seen: string;
  rejected_at: string | null;
}

export interface RelationshipCandidate {
  slug: string;
  display_name: string;
  qualifier: string | null;
  qualifier_candidates: string[];
  strongest_cue_seen: "weak" | "soft" | "strong";
  session_count: number;
  fact_count: number;
  eligible: boolean;
  first_seen: string;
  last_seen: string;
  approved_at: string | null;
  rejected_at: string | null;
  facts: CandidateFactView[];
}

export interface RelationshipsCandidatesState {
  candidates: RelationshipCandidate[];
}

export interface ApproveOkResponse {
  ok: true;
  slug: string;
  reply_text: string;
}

export interface ApproveCollisionPayload {
  error: "missing_existing_qualifier";
  slug: string;
  existing_slug: string;
  existing_facts: string[];
  existing_qualifier_candidates: string[];
  proposed_qualifier: string | null;
  reply_text: string;
}

export interface ApproveSensitivePayload {
  error: "blocked_by_sensitive_pattern";
  slug: string;
  reply_text: string;
  detail: string;
}

// ---- Tailscale visibility tab -----------------------------------

export interface TailscaleNode {
  hostname: string;
  ip: string;
  online: boolean;
}

export interface TailscaleServe {
  port: number;
  mount: string;
  target: string;
  tls: boolean;
  funnel: boolean;
}

export interface TailscaleFunnel {
  port: number;
  mount: string;
  target: string;
  tls: boolean;
}

export interface TailscalePeer {
  hostname: string;
  ip: string;
  online: boolean;
  last_seen: string | null;
  os: string;
}

export interface TailscaleStatus {
  node: TailscaleNode | null;
  serves: TailscaleServe[];
  funnels: TailscaleFunnel[];
  peers: TailscalePeer[];
  error: string | null;
}
