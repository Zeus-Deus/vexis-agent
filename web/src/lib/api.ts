// Thin HTTP client over /api/v1. All calls require the bearer token;
// 401 responses bubble up so the UI can drop back to the no-token state.

import type {
  ApproveCollisionPayload,
  ApproveOkResponse,
  ApproveSensitivePayload,
  BrowserState,
  CuratorRunDetail,
  CuratorState,
  LearningJudgeRequest,
  LearningJudgeResult,
  LearningState,
  MemoryState,
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
