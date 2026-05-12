// Thin HTTP client over /api/v1. All calls require the bearer token;
// 401 responses bubble up so the UI can drop back to the no-token state.

import type {
  ApproveCollisionPayload,
  ApproveOkResponse,
  ApproveSensitivePayload,
  BrowserState,
  AttachmentRef,
  ChatHistoryState,
  ChatReply,
  ChatSessionsState,
  VoiceInfo,
  VoiceReply,
  VoiceSettings,
  VoiceSettingsResponse,
  VoiceSettingsUpdate,
  CuratorRunDetail,
  CuratorState,
  GoalRecord,
  GoalsState,
  LearningJudgeRequest,
  LearningJudgeResult,
  LearningState,
  MemoryState,
  ModelBrainResponse,
  ModelDiscoveryRefreshResponse,
  ModelResetResponse,
  ModelSetResponse,
  ModelsState,
  RelationshipsCandidatesState,
  RelationshipsLiveState,
  ScheduleRecord,
  SchedulesState,
  SkillBody,
  SkillsState,
  StatusState,
  TailscaleStatus,
} from "./types";

const API_BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export class TokenInvalidError extends ApiError {
  constructor() {
    super(401, "token rejected");
  }
}

interface FetchOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  // AbortSignal lets callers cancel an in-flight request when the
  // component unmounts or the user navigates away. The fetch
  // implementation already supports this natively — we just plumb
  // it through. ``AbortError`` propagates back so callers can
  // distinguish "user cancelled" from "network failed".
  signal?: AbortSignal;
}

async function call<T>(
  token: string,
  path: string,
  opts: FetchOptions = {},
): Promise<T> {
  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    signal: opts.signal,
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

  const resp = await fetch(`${API_BASE}${path}`, init);
  if (resp.status === 401) {
    throw new TokenInvalidError();
  }
  if (!resp.ok) {
    let detail = "";
    try {
      const j = await resp.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j);
    } catch {
      detail = await resp.text().catch(() => "");
    }
    throw new ApiError(resp.status, detail || resp.statusText);
  }
  return resp.json() as Promise<T>;
}

/** Wire-stable error categories from the SSE ``error`` event.
 *  Backend producer: ``core.handler._ERR_CODE_*`` constants.
 *  When adding a new code, also extend ``mapErrorCode`` in
 *  ChatPage so the user sees a specific recovery UX rather than
 *  the generic "Something went wrong" fallback.
 *
 *  - ``brain_error``     transient — retry button
 *  - ``brain_timeout``   long turn — retry won't help, suggest a
 *                         shorter prompt or different model
 *  - ``session_lost``    auto-recovers; UI shows a soft note
 *  - ``cancelled``       Stop button — silent
 *  - ``rejected``        auth gate; UI flips to auth-fail
 *  - ``unknown``         generic — at least admit something broke
 */
export type ErrorCode =
  | "brain_error"
  | "brain_timeout"
  | "session_lost"
  | "cancelled"
  | "rejected"
  | "unknown";

const _ERROR_CODES: ReadonlySet<ErrorCode> = new Set<ErrorCode>([
  "brain_error", "brain_timeout", "session_lost",
  "cancelled", "rejected", "unknown",
]);

function isErrorCode(value: unknown): value is ErrorCode {
  return typeof value === "string" && _ERROR_CODES.has(value as ErrorCode);
}

export const api = {
  memory: (token: string) => call<MemoryState>(token, "/memory"),
  skills: (token: string) => call<SkillsState>(token, "/skills"),
  skillBody: (token: string, name: string) =>
    call<SkillBody>(token, `/skills/${encodeURIComponent(name)}`),
  pinSkill: (token: string, name: string) =>
    call<{ ok: boolean; pinned: boolean; changed: boolean }>(
      token,
      `/skills/${encodeURIComponent(name)}/pin`,
      { method: "POST" },
    ),
  unpinSkill: (token: string, name: string) =>
    call<{ ok: boolean; pinned: boolean; changed: boolean }>(
      token,
      `/skills/${encodeURIComponent(name)}/unpin`,
      { method: "POST" },
    ),
  restoreSkill: (token: string, name: string) =>
    call<{ ok: boolean; message: string }>(
      token,
      `/skills/${encodeURIComponent(name)}/restore`,
      { method: "POST" },
    ),
  // ── Skill CRUD (workspace skills only — bundled / installed are
  //    refused upstream by core/skills.py with a clear error). ──
  createSkill: (
    token: string,
    body: {
      name: string;
      content: string;
      category?: string;
      protect?: boolean;
    },
  ) =>
    call<{
      ok: boolean;
      name: string;
      category: string;
      pinned: boolean;
      message: string;
    }>(token, "/skills", { method: "POST", body }),
  editSkill: (
    token: string,
    name: string,
    body: { content: string; force_unpin?: boolean },
  ) =>
    call<{ ok: boolean; name: string; pinned: boolean; message: string }>(
      token,
      `/skills/${encodeURIComponent(name)}`,
      { method: "PUT", body },
    ),
  deleteSkill: (token: string, name: string) =>
    call<{ ok: boolean; name: string; message: string }>(
      token,
      `/skills/${encodeURIComponent(name)}`,
      { method: "DELETE" },
    ),
  curator: (token: string) => call<CuratorState>(token, "/curator"),
  curatorRun: (token: string, folder: string) =>
    call<CuratorRunDetail>(
      token,
      `/curator/runs/${encodeURIComponent(folder)}`,
    ),
  forceCuratorRun: (token: string) =>
    call<{
      ok: boolean;
      folder: string;
      phase1: { archived: number; marked_stale: number; reactivated: number };
      phase2: { ran: boolean; archived_names: string[]; created_names: string[] };
    }>(token, "/curator/run", { method: "POST" }),
  status: (token: string) => call<StatusState>(token, "/status"),
  browser: (token: string) => call<BrowserState>(token, "/browser"),
  browserOpenBlank: (token: string) =>
    call<{ ok: boolean; url?: string; error?: string; hint?: string }>(
      token,
      "/browser/open-blank",
      { method: "POST" },
    ),
  browserRecycle: (token: string) =>
    call<{ ok: boolean; was_running: boolean }>(token, "/browser/recycle", {
      method: "POST",
    }),
  learning: (token: string) => call<LearningState>(token, "/learning"),
  learningCoherenceAudit: (token: string, body: LearningJudgeRequest) =>
    call<LearningJudgeResult>(token, "/learning/coherence-audit", {
      method: "POST",
      body,
    }),
  // v3c Day 4b: relationships dashboard surface.
  relationshipsLive: (token: string) =>
    call<RelationshipsLiveState>(token, "/relationships/live"),
  relationshipsCandidates: (token: string, opts?: { includeRejected?: boolean }) =>
    call<RelationshipsCandidatesState>(
      token,
      opts?.includeRejected
        ? "/relationships/candidates?include_rejected=true"
        : "/relationships/candidates",
    ),
  relationshipsApprove: (
    token: string,
    slug: string,
    body: { fact_ids?: string[] | null; qualifier?: string | null },
  ) =>
    callWithErrorBody<ApproveOkResponse>(
      token,
      `/relationships/candidates/${encodeURIComponent(slug)}/approve`,
      { method: "POST", body },
    ),
  relationshipsReject: (
    token: string,
    slug: string,
    body: { fact_ids?: string[] | null } = {},
  ) =>
    call<{ ok: boolean; slug: string; reply_text: string }>(
      token,
      `/relationships/candidates/${encodeURIComponent(slug)}/reject`,
      { method: "POST", body },
    ),
  relationshipsEdit: (
    token: string,
    slug: string,
    body: { fact_id: string; new_text: string },
  ) =>
    call<{ ok: boolean; slug: string; old_fact_id: string; new_fact_id: string }>(
      token,
      `/relationships/candidates/${encodeURIComponent(slug)}/edit`,
      { method: "POST", body },
    ),
  tailscale: (token: string) => call<TailscaleStatus>(token, "/tailscale/status"),
  goals: (token: string) => call<GoalsState>(token, "/goals"),
  models: (token: string) => call<ModelsState>(token, "/models"),
  setModel: (
    token: string,
    body: { subsystem: string; value: string },
  ) =>
    call<ModelSetResponse>(token, "/models/set", { method: "POST", body }),
  resetModel: (token: string, body: { subsystem?: string } = {}) =>
    call<ModelResetResponse>(token, "/models/reset", { method: "POST", body }),
  setBrain: (token: string, body: { kind: string }) =>
    call<ModelBrainResponse>(token, "/models/brain", { method: "POST", body }),
  refreshModelDiscovery: (token: string) =>
    call<ModelDiscoveryRefreshResponse>(
      token, "/models/discovery/refresh", { method: "POST" },
    ),
  pauseGoal: (token: string) =>
    call<GoalRecord>(token, "/goals/pause", { method: "POST" }),
  resumeGoal: (token: string) =>
    call<GoalRecord>(token, "/goals/resume", { method: "POST" }),
  clearGoal: (token: string) =>
    call<GoalRecord>(token, "/goals/clear", { method: "POST" }),
  // ----- /schedule -----
  schedules: (token: string) => call<SchedulesState>(token, "/schedules"),
  pauseSchedule: (token: string, id: string) =>
    call<ScheduleRecord>(
      token,
      `/schedules/${encodeURIComponent(id)}/pause`,
      { method: "POST" },
    ),
  resumeSchedule: (token: string, id: string) =>
    call<ScheduleRecord>(
      token,
      `/schedules/${encodeURIComponent(id)}/resume`,
      { method: "POST" },
    ),
  clearSchedule: (token: string, id: string) =>
    call<ScheduleRecord>(
      token,
      `/schedules/${encodeURIComponent(id)}/clear`,
      { method: "POST" },
    ),
  relationshipsResolveQualifier: (
    token: string,
    slug: string,
    body: { existing_qualifier: string },
  ) =>
    call<{ ok: boolean; old_slug: string; new_slug: string; qualifier: string }>(
      token,
      `/relationships/candidates/${encodeURIComponent(slug)}/resolve_qualifier`,
      { method: "POST", body },
    ),
  // ----- chat -----
  // The reply field carries either the brain's response (chat.send) or
  // a control message ("Switched to demo", "Conversation cleared.").
  // The UI renders both as a normal assistant bubble.
  chatSend: (token: string, text: string, signal?: AbortSignal) =>
    call<ChatReply>(token, "/chat/send", {
      method: "POST",
      body: { text },
      signal,
    }),
  /**
   * Streaming variant — POSTs to /chat/stream, parses the SSE
   * response with manual ReadableStream + TextDecoder. EventSource
   * is GET-only so we can't use it; the fetch + ReadableStream
   * combo is the standard browser pattern for SSE-over-POST.
   *
   * Calls back per chunk as text arrives, then once with the full
   * concatenated reply when the brain emits its ``done`` event.
   * On error: invokes ``onError`` and stops. AbortController on
   * the caller side cancels the read mid-stream cleanly.
   */
  chatSendStream: async (
    token: string,
    payload: {
      text: string;
      attachments?: AttachmentRef[];
      model?: string;
      reasoning_level?: string;
    },
    handlers: {
      onChunk: (text: string) => void;
      onDone: (full: string) => void;
      // ``message`` is the user-facing string (may be empty for
      // silent codes like ``cancelled``). ``code`` discriminates
      // error categories so the UI can pick a specific recovery
      // affordance — retry button on transient brain errors,
      // auth-fail flow on ``rejected``, silent dismiss on
      // ``cancelled``. New codes default to "unknown" on the
      // wire; tests / older callers tolerate that.
      onError: (
        message: string,
        opts?: { code?: ErrorCode },
      ) => void;
      // Tool-use status updates streamed inline with text deltas.
      // Optional — surface omitted by callers that don't render
      // tool status (e.g. tests, voice path). When present the
      // callback fires per tool_use event the brain emits during
      // the turn, in the order they fire.
      onTool?: (event: { name: string; target: string | null }) => void;
      signal?: AbortSignal;
    },
  ): Promise<void> => {
    let resp: Response;
    try {
      resp = await fetch("/api/v1/chat/stream", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
        signal: handlers.signal,
      });
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      handlers.onError(e instanceof Error ? e.message : String(e));
      return;
    }
    if (resp.status === 401) {
      throw new TokenInvalidError();
    }
    if (!resp.ok || !resp.body) {
      let detail = resp.statusText;
      try { detail = (await resp.json()).detail ?? detail; } catch {}
      handlers.onError(detail);
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    // SSE frames are separated by ``\n\n``. We accumulate raw bytes
    // in ``buffer`` until we see a frame terminator, parse the
    // ``data: ...`` line, and feed the JSON payload to the handlers.
    // Partial frames at the read boundary stay in ``buffer`` until
    // the next read fills them in.
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sepIdx;
        while ((sepIdx = buffer.indexOf("\n\n")) !== -1) {
          const frame = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);
          // Each frame may contain multiple lines; we only care
          // about ``data: `` lines (per SSE spec there's also
          // ``event: ``, ``id: ``, ``retry: `` — we don't use them).
          for (const line of frame.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            const json = line.slice(6);
            try {
              const evt = JSON.parse(json);
              if (evt.type === "chunk" && typeof evt.text === "string") {
                handlers.onChunk(evt.text);
              } else if (evt.type === "tool" && typeof evt.name === "string") {
                // Tool-use status. ``target`` is null for tools
                // without a clear filename/command (Task, MCP).
                // We tolerate missing onTool — older clients /
                // tests just ignore the frame.
                handlers.onTool?.({
                  name: evt.name,
                  target: typeof evt.target === "string" ? evt.target : null,
                });
              } else if (evt.type === "done" && typeof evt.reply === "string") {
                handlers.onDone(evt.reply);
              } else if (evt.type === "error") {
                // ``code`` is wire-stable; defaults to "unknown" on
                // older servers / unrecognised values so the UI
                // always has something to dispatch on.
                const code = isErrorCode(evt.code) ? evt.code : "unknown";
                handlers.onError(
                  typeof evt.message === "string" ? evt.message : "",
                  { code },
                );
              }
            } catch {
              // Malformed frame — skip rather than crash the loop.
            }
          }
        }
      }
    } catch (e) {
      if (e instanceof DOMException && e.name === "AbortError") return;
      handlers.onError(e instanceof Error ? e.message : String(e));
    }
  },
  chatSessions: (token: string, signal?: AbortSignal) =>
    call<ChatSessionsState>(token, "/chat/sessions", { signal }),
  // History backfill. Lazy-loaded on first switch into a session
  // per page-load so the conversation pane shows prior turns
  // instead of a blank canvas.
  chatHistory: (
    token: string, name: string, opts: { limit?: number; signal?: AbortSignal } = {},
  ) =>
    call<ChatHistoryState>(
      token,
      `/chat/sessions/${encodeURIComponent(name)}/history?limit=${opts.limit ?? 50}`,
      { signal: opts.signal },
    ),
  chatNewSession: (token: string, name?: string) =>
    call<ChatReply>(token, "/chat/sessions/new", {
      method: "POST",
      body: name ? { name } : {},
    }),
  chatSwitchSession: (token: string, name: string) =>
    call<ChatReply>(token, "/chat/sessions/switch", {
      method: "POST",
      body: { name },
    }),
  chatRenameSession: (token: string, oldName: string, newName: string) =>
    call<ChatReply>(token, "/chat/sessions/rename", {
      method: "POST",
      body: { old: oldName, new: newName },
    }),
  chatDeleteSession: (token: string, name: string) =>
    call<ChatReply>(token, "/chat/sessions/delete", {
      method: "POST",
      body: { name },
    }),
  chatClear: (token: string) =>
    call<ChatReply>(token, "/chat/clear", { method: "POST" }),
  /**
   * Cancel any in-flight brain turn for the web chat. Used by the
   * Stop button and by session-switch / unmount cleanup so a long
   * stream doesn't keep burning tokens on a reply the user will
   * never see.
   *
   * Always best-effort: errors are swallowed because cancel-on-
   * unmount is a fire-and-forget signal — there's no UI to
   * surface a failure to. The local AbortController on the
   * streaming fetch already closes the SSE pipe regardless.
   */
  chatCancel: async (token: string): Promise<{ cancelled: boolean }> => {
    try {
      return await call<{ cancelled: boolean }>(
        token, "/chat/cancel", { method: "POST" },
      );
    } catch {
      return { cancelled: false };
    }
  },
  // ----- voice -----
  voiceInfo: (token: string, signal?: AbortSignal) =>
    call<VoiceInfo>(token, "/chat/voice/info", { signal }),
  // Voice settings (dashboard Voice tab — full config + model picker)
  voiceSettings: (token: string, signal?: AbortSignal) =>
    call<VoiceSettings>(token, "/voice", { signal }),
  voiceSettingsSet: (token: string, body: VoiceSettingsUpdate) =>
    call<VoiceSettingsResponse>(token, "/voice", { method: "POST", body }),
  // STT: multipart upload of an audio Blob → {transcript, reply}.
  // Bypasses ``call`` because that helper sets Content-Type to
  // application/json — the browser handles multipart boundary
  // generation only when fetch sees a FormData body and we DON'T
  // override Content-Type.
  chatVoice: async (
    token: string,
    audio: Blob,
    opts: { model?: string; reasoning_level?: string } = {},
  ): Promise<VoiceReply> => {
    const fd = new FormData();
    // Hint extension via the second arg so the server's tempfile
    // suffix-detection picks the right ffmpeg demuxer. Browsers
    // typically produce webm or ogg from MediaRecorder; we send
    // whatever the Blob's MIME suggests.
    const ext = audio.type.includes("ogg") ? "ogg" :
                audio.type.includes("webm") ? "webm" :
                audio.type.includes("wav") ? "wav" : "bin";
    fd.append("audio", audio, `voice.${ext}`);
    // Per-turn overrides (voice call mode). Omitted entirely when
    // unset so the server's ``Form(default=None)`` falls through to
    // brain defaults — preserves current behaviour for any caller
    // that doesn't pass these.
    if (opts.model) fd.append("model", opts.model);
    if (opts.reasoning_level) {
      fd.append("reasoning_level", opts.reasoning_level);
    }
    const resp = await fetch("/api/v1/chat/voice", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: fd,
    });
    if (resp.status === 401) throw new TokenInvalidError();
    if (!resp.ok) {
      let detail = resp.statusText;
      try { detail = (await resp.json()).detail ?? detail; } catch {}
      throw new ApiError(resp.status, detail);
    }
    return resp.json() as Promise<VoiceReply>;
  },
  // ----- attachments -----
  // Upload a single file. Caller passes the same File they got from
  // <input type=file> / drag-drop / paste — the Blob streams to the
  // server without buffering into memory (fetch + FormData uses the
  // File reference directly). Optional ``signal`` for cancellation
  // if the user removes the chip mid-upload or navigates away.
  // Optional ``onProgress`` reports bytes-uploaded; piped through an
  // XMLHttpRequest because fetch's Streams API for upload progress
  // isn't widely supported yet (Safari especially).
  chatAttach: (
    token: string,
    file: File,
    opts: {
      signal?: AbortSignal;
      onProgress?: (loaded: number, total: number) => void;
    } = {},
  ): Promise<AttachmentRef> => {
    return new Promise<AttachmentRef>((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/v1/chat/attach", true);
      xhr.setRequestHeader("Authorization", `Bearer ${token}`);
      xhr.upload.onprogress = (ev) => {
        if (ev.lengthComputable && opts.onProgress) {
          opts.onProgress(ev.loaded, ev.total);
        }
      };
      xhr.onload = () => {
        if (xhr.status === 401) { reject(new TokenInvalidError()); return; }
        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = xhr.statusText;
          try { detail = JSON.parse(xhr.responseText).detail ?? detail; } catch {}
          reject(new ApiError(xhr.status, detail));
          return;
        }
        try { resolve(JSON.parse(xhr.responseText) as AttachmentRef); }
        catch (e) { reject(e instanceof Error ? e : new Error(String(e))); }
      };
      xhr.onerror = () => reject(new ApiError(0, "network error"));
      xhr.onabort = () => reject(new DOMException("aborted", "AbortError"));
      if (opts.signal) {
        if (opts.signal.aborted) {
          xhr.abort();
          reject(new DOMException("aborted", "AbortError"));
          return;
        }
        opts.signal.addEventListener("abort", () => xhr.abort(), { once: true });
      }
      const fd = new FormData();
      fd.append("file", file, file.name);
      xhr.send(fd);
    });
  },
  // /chat/send variant that accepts attachments. Wraps the regular
  // chatSend helper so the JSON shape stays in one place.
  chatSendWithAttachments: (
    token: string,
    text: string,
    attachments: AttachmentRef[],
  ) =>
    call<ChatReply>(token, "/chat/send", {
      method: "POST",
      body: { text, attachments },
    }),
  // TTS: text → audio Blob. Returns a Blob the UI feeds straight
  // into <audio src=URL.createObjectURL(...)>. 204 = empty input,
  // surface as null so the caller skips playback cleanly.
  chatTts: async (token: string, text: string): Promise<Blob | null> => {
    const resp = await fetch("/api/v1/chat/tts", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ text }),
    });
    if (resp.status === 401) throw new TokenInvalidError();
    if (resp.status === 204) return null;
    if (!resp.ok) {
      let detail = resp.statusText;
      try { detail = (await resp.json()).detail ?? detail; } catch {}
      throw new ApiError(resp.status, detail);
    }
    return resp.blob();
  },

  // ── Kanban ──────────────────────────────────────────────────
  // Wire-compatible with /api/v1/kanban/* (core/web_kanban.py).
  // Lanes (defaults: research, implementation, review, ops, triage,
  // default) replace the upstream profiles per .plans/kanban-research.md.
  kanbanBoard: (
    token: string,
    opts?: { lane?: string; status?: string; archived?: boolean },
  ) => {
    const params = new URLSearchParams();
    if (opts?.lane) params.set("lane", opts.lane);
    if (opts?.status) params.set("status", opts.status);
    if (opts?.archived) params.set("archived", "true");
    const qs = params.toString();
    return call<import("./types").KanbanBoardResponse>(
      token, `/kanban/board${qs ? "?" + qs : ""}`,
    );
  },
  kanbanLanes: (token: string) =>
    call<import("./types").KanbanLanesResponse>(token, "/kanban/lanes"),
  kanbanTask: (token: string, id: string) =>
    call<import("./types").KanbanTaskDetailResponse>(
      token, `/kanban/tasks/${encodeURIComponent(id)}`,
    ),
  kanbanCreate: (
    token: string, body: import("./types").KanbanCreatePayload,
  ) =>
    call<import("./types").KanbanTask>(token, "/kanban/tasks", {
      method: "POST", body,
    }),
  kanbanSetStatus: (token: string, id: string, status: string) =>
    call<import("./types").KanbanTask>(
      token, `/kanban/tasks/${encodeURIComponent(id)}/status`,
      { method: "POST", body: { status } },
    ),
  kanbanComplete: (token: string, id: string, summary?: string) =>
    call<import("./types").KanbanTask>(
      token, `/kanban/tasks/${encodeURIComponent(id)}/complete`,
      { method: "POST", body: summary ? { summary } : {} },
    ),
  kanbanBlock: (token: string, id: string, reason: string) =>
    call<import("./types").KanbanTask>(
      token, `/kanban/tasks/${encodeURIComponent(id)}/block`,
      { method: "POST", body: { reason } },
    ),
  kanbanUnblock: (token: string, id: string) =>
    call<import("./types").KanbanTask>(
      token, `/kanban/tasks/${encodeURIComponent(id)}/unblock`,
      { method: "POST", body: {} },
    ),
  kanbanArchive: (token: string, id: string) =>
    call<{ task_id: string; archived: boolean }>(
      token, `/kanban/tasks/${encodeURIComponent(id)}/archive`,
      { method: "POST" },
    ),
  kanbanAssign: (token: string, id: string, lane: string | null) =>
    call<import("./types").KanbanTask>(
      token, `/kanban/tasks/${encodeURIComponent(id)}/assign`,
      { method: "POST", body: { lane } },
    ),
  kanbanComment: (token: string, id: string, body: string) =>
    call<import("./types").KanbanComment>(
      token, `/kanban/tasks/${encodeURIComponent(id)}/comment`,
      { method: "POST", body: { body } },
    ),
  kanbanLink: (token: string, parentId: string, childId: string) =>
    call<{ parent_id: string; child_id: string }>(
      token, "/kanban/links",
      { method: "POST", body: { parent_id: parentId, child_id: childId } },
    ),
  kanbanUnlink: (token: string, parentId: string, childId: string) =>
    call<{ parent_id: string; child_id: string }>(
      token, "/kanban/links/delete",
      { method: "POST", body: { parent_id: parentId, child_id: childId } },
    ),
};

// ApproveError surfaces the typed 409 / 422 / 4xx body from the
// approve endpoint so the panel can render the modal flow.
export class ApproveError extends ApiError {
  payload: ApproveCollisionPayload | ApproveSensitivePayload | { error: string; slug?: string; reply_text?: string; detail?: string };
  constructor(
    status: number,
    payload: ApproveCollisionPayload | ApproveSensitivePayload | { error: string; slug?: string; reply_text?: string; detail?: string },
  ) {
    super(status, typeof payload === "object" && payload && "reply_text" in payload && payload.reply_text ? payload.reply_text : (payload.error ?? "approve failed"));
    this.payload = payload;
  }
}

async function callWithErrorBody<T>(
  token: string,
  path: string,
  opts: FetchOptions = {},
): Promise<T> {
  const init: RequestInit = {
    method: opts.method ?? "GET",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const resp = await fetch(`${API_BASE}${path}`, init);
  if (resp.status === 401) {
    throw new TokenInvalidError();
  }
  if (!resp.ok) {
    let body: any = null;
    try {
      body = await resp.json();
    } catch {
      body = { error: "unknown", reply_text: await resp.text().catch(() => "") };
    }
    throw new ApproveError(resp.status, body);
  }
  return resp.json() as Promise<T>;
}

// Build a screenshot URL with the bearer carried as a query parameter.
// The auth dependency on the dashboard accepts ?token= as a fallback,
// which lets <img src="..."> and click-to-open-fullsize anchors work
// without rewriting how the rest of the dashboard handles auth.
export function browserScreenshotUrl(token: string, filename: string): string {
  return `/api/v1/browser/screenshot/${encodeURIComponent(
    filename,
  )}?token=${encodeURIComponent(token)}`;
}
