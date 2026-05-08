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

// ---- /goal dashboard tab ----------------------------------------

export type GoalStatus = "active" | "paused" | "done" | "cleared";

export interface GoalRecord {
  session_uuid: string;
  goal: string;
  status: GoalStatus;
  turns_used: number;
  max_turns: number;
  created_at: string | null;
  last_turn_at: string | null;
  last_verdict: "done" | "continue" | "skipped" | null;
  last_reason: string | null;
  paused_reason: string | null;
}

export interface GoalsState {
  // The current session's goal record when status ∈ {active, paused}.
  // Done and cleared records appear in `history` instead so the
  // active panel only renders things the user can act on.
  active: GoalRecord | null;
  // Most recent 20 non-active records (paused / done / cleared)
  // sorted by last_turn_at desc.
  history: GoalRecord[];
}

// ── Model UX (Day 3 read-only; Day 4 adds edit affordances) ──

export type ValidationSeverity = "error" | "warning" | "info";

export interface ModelValidationFinding {
  severity: ValidationSeverity;
  // null for whole-config findings (brain.kind validity etc.).
  subsystem: string | null;
  problem: string;
  suggested_fix: string;
}

export interface ModelSubsystemRow {
  // One of the keys in DEFAULT_SUBSYSTEM_TIERS — curator, goal_judge, etc.
  name: string;
  // Raw value from config (legacy or new schema), or null if defaulted.
  configured: string | null;
  // What the resolution layer sees (abstract tier or raw alias).
  // null when no model is configured (caller falls back to brain default).
  resolved_tier: string | null;
  // Native model id the brain CLI receives. null = no --model flag passed.
  resolved_model_id: string | null;
  // Per-row validator findings (rule 4/5/6/7 hits for this subsystem).
  findings: ModelValidationFinding[];
}

export interface ModelTierOverride {
  // User-set value via models.tiers.<brain>.<tier>, or null.
  configured: string | null;
  // DEFAULT_TIER_MAP_<brain>[<tier>], or null when the brain has
  // no built-in default for this tier.
  default: string | null;
}

export interface ModelsState {
  // The brain currently active (read-once at daemon startup).
  brain_kind: string;
  // 8 rows — one per known subsystem in DEFAULT_SUBSYSTEM_TIERS.
  subsystems: ModelSubsystemRow[];
  // Per-tier overrides for the active brain. Day 4 adds an editor.
  tier_overrides: Record<string, ModelTierOverride>;
  // VALID_BRAIN_KINDS — populated for Day 4's brain switcher.
  brain_inventory: string[];
  // Whole-config findings (subsystem=null) — brain.kind validity,
  // unknown legacy keys, etc.
  global_findings: ModelValidationFinding[];
  // ── Day 4 additions ────────────────────────────────────────
  // Per-brain available model lists, sourced from
  // core.model_discovery (5-min in-process cache). Retained for
  // backwards compatibility — Day 2 of model picker UX migrated
  // the dropdown to ``available_models_by_provider`` below, but
  // any consumer that doesn't care about provider grouping (e.g.
  // membership checks) can keep reading the flat list.
  available_models: Record<string, string[]>;
  // Day 1 of model picker UX — provider-grouped sibling of
  // available_models. Shape: {brain_kind: {provider: [model_ids]}}.
  // Within-provider order is lexicographic; provider order is
  // anthropic-first (vexis is anthropic-centric) then alphabetical.
  // Empty for brains without discovery (BrainNull) and for opencode
  // when the binary isn't installed. Drives the dashboard's
  // <optgroup>-grouped dropdown (Day 2).
  available_models_by_provider: Record<string, Record<string, string[]>>;
  // True iff ~/.vexis/config.yaml currently has YAML comments.
  // Self-managing across daemon restarts (after the first
  // mutation comments are gone, so this stays false until the
  // user manually re-comments). Drives the dashboard's
  // comment-preservation confirm modal.
  has_comments: boolean;
  // model_ux.enabled gate; UI surfaces a disabled banner if false.
  model_ux_enabled: boolean;
}

// ── Mutation response shapes ───────────────────────────────────

export interface ModelSetResponse {
  ok: true;
  subsystem: string;
  value: string;
  resolved_tier: string | null;
  resolved_model_id: string | null;
  // Path of the .bak file when comment-preservation backup
  // fired; null when skipped (no comments in current config).
  backup_path: string | null;
}

export interface ModelResetResponse {
  ok: true;
  // "all subsystems" or the subsystem name reset.
  scope: string;
  backup_path: string | null;
}

export interface ModelBrainResponse {
  ok: true;
  kind: string;
  // Always true for brain.kind changes — read-once at startup.
  restart_required: boolean;
  // Preview-mode validator findings against the proposed brain.
  // Surfaced informationally; the write proceeded regardless.
  warnings: ModelValidationFinding[];
  backup_path: string | null;
}

export interface ModelDiscoveryRefreshResponse {
  ok: true;
  available_models: Record<string, string[]>;
}

// ----- chat -----

export interface ChatSession {
  name: string;
  is_active: boolean;
  // ISO-8601 UTC. Format with Intl.DateTimeFormat at render time.
  created_at: string;
}

export interface ChatSessionsState {
  sessions: ChatSession[];
}

export interface ChatReply {
  // The brain's response (or a handler-emitted control reply for
  // session ops, e.g. "Switched to <name>" / "⚠️ Couldn't resume…").
  // Already trimmed; markdown is safe to render.
  reply: string;
}

// ----- voice -----

export interface VoiceCapability {
  provider: string;        // "voxtype" | "piper" | "null" | future
  available: boolean;      // false when provider is "null"
  mime_type?: string;      // TTS only — what audio/* the bytes are
}

export interface VoiceInfo {
  enabled: boolean;        // mirrors voice.enabled in ~/.vexis/config.yaml
  stt: VoiceCapability;
  tts: VoiceCapability;
}

export interface VoiceReply {
  // STT round-trip result. Both fields are present together — the
  // server returns the transcript so the UI can render it as a
  // user bubble, AND the brain's reply so the UI doesn't have to
  // chain a second /chat/send call.
  transcript: string;
  reply: string;
}

// ----- attachments -----

export interface AttachmentRef {
  // Server-side path under <workspace>/uploads/<session>/. The
  // brain reads files from this path directly.
  path: string;
  // Sanitized filename (extension preserved). Used for display
  // and for re-sending to the server in /chat/send body.
  name: string;
  // Bytes written to disk. Useful for showing "1.2 MB" next to
  // the chip without round-tripping the actual file.
  size: number;
  // Server-validated mime — same as what the upload sent, but
  // verified against the allowlist server-side.
  mime: string;
}

// Used by the composer to render queued attachments before send.
// Adds a client-only ``previewUrl`` (blob: URL) so we can show the
// thumbnail without re-fetching from the server.
export interface QueuedAttachment extends AttachmentRef {
  previewUrl?: string;
}

// ----- voice settings (dashboard tab) -----

export interface PiperVoice {
  path: string;
  name: string;
  language: string;
  size: number;
  has_config: boolean;
}

export interface VoiceSettings {
  enabled: boolean;
  stt: {
    provider: string;
    available_providers: string[];
  };
  tts: {
    provider: string;
    available_providers: string[];
    voice_model_path: string | null;
    binary: string | null;
  };
  available_voices: PiperVoice[];
}

export interface VoiceSettingsUpdate {
  enabled?: boolean;
  stt?: { provider?: string };
  tts?: {
    provider?: string;
    voice_model_path?: string | null;
    binary?: string | null;
  };
}

export interface VoiceSettingsResponse extends VoiceSettings {
  ok: true;
  backup_path: string | null;
}
