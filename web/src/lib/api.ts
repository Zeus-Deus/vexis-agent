// Thin HTTP client over /api/v1. All calls require the bearer token;
// 401 responses bubble up so the UI can drop back to the no-token state.

import type {
  ApproveCollisionPayload,
  ApproveOkResponse,
  ApproveSensitivePayload,
  BrowserState,
  AttachmentRef,
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
  method?: "GET" | "POST";
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
  chatSessions: (token: string, signal?: AbortSignal) =>
    call<ChatSessionsState>(token, "/chat/sessions", { signal }),
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
