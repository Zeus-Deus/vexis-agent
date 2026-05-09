import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { ChatSession, QueuedAttachment, VoiceInfo } from "../lib/types";
import { ChatSidebar } from "../components/chat/ChatSidebar";
import {
  ChatMessages,
  type ChatMessage,
} from "../components/chat/ChatMessages";
import { ChatComposer } from "../components/chat/ChatComposer";
import type { AttachmentPickerHandle } from "../components/chat/AttachmentPicker";

// Lazy-load voice call mode + its dependencies (vad-web,
// onnxruntime-web, the Silero ONNX model wasm). These add ~900 KB
// to the bundle when bundled into the main chunk; deferring them
// behind an ``import()`` boundary means users who never tap the
// 📞 button never download them. Suspense fallback shows nothing
// because we only mount the component while ``callOpen`` is true.
const VoiceCallMode = lazy(() =>
  import("../components/chat/VoiceCallMode").then((m) => ({
    default: m.VoiceCallMode,
  })),
);

interface ChatPageProps {
  token: string;
  onAuthFail: () => void;
  // Hide the dashboard's outer chrome (top bar, tab strip, footer)
  // when this page renders standalone at /talk. The component itself
  // doesn't draw chrome — App.tsx wraps it conditionally — but we
  // accept the flag so future surfaces (e.g. a "popout" button) can
  // toggle compact-only behaviour from inside the page.
  fullscreen?: boolean;
}

// Phase 1 shape: in-memory messages, one buffer per session name.
// Switching a session swaps the visible buffer; the previous one is
// kept around so coming back doesn't wipe what you saw last (until a
// page reload, which is expected — the brain transcripts on disk are
// the durable record). Phase 1.5 will add a /chat/history endpoint
// that lazily backfills the buffer from JSONL/SQLite on first switch.
type MessageBuffers = Record<string, ChatMessage[]>;

export function ChatPage({ token, onAuthFail, fullscreen }: ChatPageProps) {
  const [sessions, setSessions] = useState<ChatSession[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [buffers, setBuffers] = useState<MessageBuffers>({});
  // ``pendingName`` identifies the session affected by an in-flight
  // sidebar action; ``pendingSend`` is true while the chat turn is
  // round-tripping. Two flags so a slow-network sidebar action
  // doesn't lock the composer.
  const [pendingName, setPendingName] = useState<string | null>(null);
  const [pendingSend, setPendingSend] = useState(false);
  // Mobile drawer state. Above the md breakpoint the sidebar is
  // always-on regardless of this flag (CSS overrides). On phones the
  // drawer starts closed so the conversation has the full viewport
  // and the hamburger is the explicit "I want to switch" gesture.
  const [sidebarOpen, setSidebarOpen] = useState(false);

  // Voice capability — fetched once on mount via /chat/voice/info.
  // Null until the probe resolves; while null we conservatively hide
  // voice affordances so the UI doesn't flash mic-then-no-mic.
  const [voiceInfo, setVoiceInfo] = useState<VoiceInfo | null>(null);
  // User-toggleable TTS mute. Persisted to localStorage so the
  // preference survives reloads. **Muted by default** — opt-in
  // for spoken replies because:
  //   - first-time mobile users via Tailscale may be in a public
  //     space; surprise audio is jarring
  //   - autoplay policies on iOS Safari can drop the first reply
  //     audio anyway, so it's better to surface the toggle and let
  //     the user explicitly enable
  //   - voice call mode is a separate explicit opt-in (the 📞
  //     button) and plays its own TTS inside the modal regardless
  //     of this flag — that's where the "I want to hear replies"
  //     intent lives most naturally
  // Storage key writes "0" on opt-in, "1" on mute. Anything else
  // (unset / corrupted / first-load) coerces to muted.
  const [ttsMuted, setTtsMuted] = useState<boolean>(() => {
    try {
      const v = localStorage.getItem("vexis-tts-muted");
      // Only an explicit "0" means the user opted in.
      return v !== "0";
    } catch { return true; }
  });
  // Tracks the in-flight TTS audio element so a new reply can stop
  // the previous playback (avoids overlapping audio when replies
  // come back-to-back).
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null);

  // Pending attachments queue. Owned here (not in ChatComposer)
  // because the conversation pane's drag-drop / paste handlers
  // need to push into the same queue without going through the
  // composer. Clears after a successful send.
  const [attachmentQueue, setAttachmentQueue] = useState<QueuedAttachment[]>([]);

  // Active stream's AbortController + the session it belongs to.
  // ``activeStreamRef`` is the source of truth that the Stop
  // button + session-switch / unmount cleanup all read from. Tied
  // to a session name so a fast switch-and-resume doesn't
  // accidentally cancel a stream that just-started for the new
  // session.
  const activeStreamRef = useRef<{
    controller: AbortController;
    sessionName: string;
  } | null>(null);
  // ``streaming`` mirrors the ref into React state so the Stop
  // button can render conditionally. The ref alone wouldn't
  // trigger re-renders.
  const [streaming, setStreaming] = useState(false);

  // Forwarded to AttachmentPicker so drag-drop / paste handlers
  // on the conversation pane can route uploads through the same
  // optimistic-chip + progress flow as the paperclip button.
  const attachmentPickerRef = useRef<AttachmentPickerHandle | null>(null);
  // Visual feedback while a drag is hovering the conversation
  // pane. Phones don't fire drag events so this is desktop-only;
  // we just gate the highlight on the boolean.
  const [dragHover, setDragHover] = useState(false);

  // Voice call mode — opens a full-screen overlay with VAD-driven
  // hands-free conversation. The component owns its own mic +
  // playback; we just track open/closed and forward each turn back
  // here so the conversation buffer reflects what was said.
  const [callOpen, setCallOpen] = useState(false);

  const activeName = sessions?.find((s) => s.is_active)?.name ?? "";
  const messages = activeName ? buffers[activeName] ?? [] : [];
  // Track which session names have had their history fetched
  // already (per page-load). Once loaded, switching away and back
  // reuses the in-memory buffer rather than re-fetching — avoids
  // hammering the brain's transcript reader on every tab switch.
  const historyLoadedRef = useRef<Set<string>>(new Set());

  // Server returns sessions in their on-disk insertion order (oldest
  // first). The chat UX wants newest first — that's where the active
  // session almost always lives, and it matches the "stack of recent
  // conversations" mental model from ChatGPT/Claude.ai. Sort by
  // created_at descending; ties (auto-named within the same minute)
  // fall back to alphabetical so the order stays deterministic.
  //
  // Memoised so the 50+-row sidebar doesn't get a fresh array
  // reference on every keystroke in the composer (which would force
  // every SessionRow to re-render via the ChatSidebar prop chain).
  const sortedSessions = useMemo(() => {
    return (sessions ?? []).slice().sort((a, b) => {
      const cmp = b.created_at.localeCompare(a.created_at);
      return cmp !== 0 ? cmp : a.name.localeCompare(b.name);
    });
  }, [sessions]);

  const setMessages = useCallback(
    (name: string, next: ChatMessage[] | ((prev: ChatMessage[]) => ChatMessage[])) => {
      setBuffers((prev) => {
        const current = prev[name] ?? [];
        const resolved = typeof next === "function" ? next(current) : next;
        return { ...prev, [name]: resolved };
      });
    },
    [],
  );

  /** Abort the active stream + tell the daemon to kill the brain
   *  subprocess. Safe to call when nothing's in flight. */
  const cancelStream = useCallback(
    async (reason: "user-stop" | "switch" | "unmount" = "user-stop") => {
      const active = activeStreamRef.current;
      if (!active) return;
      activeStreamRef.current = null;
      setStreaming(false);
      try { active.controller.abort(); } catch {}
      // Server-side cancel — best-effort. Without this the brain
      // subprocess keeps running until it finishes naturally,
      // burning tokens on a reply nobody will see. ``chatCancel``
      // swallows its own errors so this won't throw.
      void api.chatCancel(token);
      // ``unmount`` skips state mutations because the component
      // is going away anyway and React would warn about state-
      // updates-on-unmounted.
      if (reason === "unmount") return;
      // For switch / user-stop, leave whatever already-streamed
      // content is in the bubble. Drop the bubble entirely if it
      // never received any chunks (would render as empty).
      setMessages(active.sessionName, (prev) => {
        const last = prev[prev.length - 1];
        if (last?.role === "assistant" && last.content === "") {
          return prev.slice(0, -1);
        }
        return prev;
      });
    },
    [token, setMessages],
  );

  const refreshSessions = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const data = await api.chatSessions(token, signal);
        setSessions(data.sessions);
        setError(null);
      } catch (exc: unknown) {
        // Aborted requests are expected during unmount/refresh
        // races; swallow them silently.
        if (exc instanceof DOMException && exc.name === "AbortError") return;
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      }
    },
    [token, onAuthFail],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    refreshSessions(ctrl.signal);
    // Cancel the in-flight fetch on unmount so a navigation-during-load
    // doesn't leave an orphan fetch holding component state.
    return () => ctrl.abort();
  }, [refreshSessions]);

  // Lazy-load history when the active session changes (or when the
  // sessions list first arrives with an active session pinned).
  // Skipped when:
  //   - already loaded once this page-load (Set lookup)
  //   - the buffer already has messages (user is mid-conversation —
  //     don't stomp what they just typed)
  // Cancels via AbortController on session change / unmount so a
  // slow brain transcript read doesn't land into the wrong session.
  useEffect(() => {
    if (!activeName) return;
    if (historyLoadedRef.current.has(activeName)) return;
    if ((buffers[activeName]?.length ?? 0) > 0) {
      // Mark as loaded so we don't refetch on the next render.
      historyLoadedRef.current.add(activeName);
      return;
    }
    const ctrl = new AbortController();
    (async () => {
      try {
        const data = await api.chatHistory(token, activeName, {
          limit: 50, signal: ctrl.signal,
        });
        // Convert to the in-memory ChatMessage shape — same fields
        // as our send-time messages, just sourced from disk.
        const fetched: ChatMessage[] = data.messages.map((m) => ({
          role: m.role as ChatMessage["role"],
          content: m.content,
          ts: m.ts,
        }));
        setMessages(activeName, fetched);
        historyLoadedRef.current.add(activeName);
      } catch (exc) {
        if (exc instanceof DOMException && exc.name === "AbortError") return;
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        // Non-401 errors: log silently. History backfill is a
        // nice-to-have; failing to load shouldn't block sending.
        // Mark loaded so we don't retry on every render.
        historyLoadedRef.current.add(activeName);
      }
    })();
    return () => ctrl.abort();
  }, [activeName, token, buffers, onAuthFail, setMessages]);

  // Refresh sessions when the tab becomes visible again — phones
  // suspend background tabs aggressively, and a backgrounded chat
  // can be minutes-stale by the time the user comes back. Cheap
  // refetch keeps the sidebar honest. We don't refresh voiceInfo
  // because it doesn't change at runtime (provider switches require
  // a daemon restart anyway).
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        // Don't await — fire-and-forget. The component is mounted
        // (the listener wouldn't fire otherwise) so a stale-state
        // setSessions is fine.
        void refreshSessions();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [refreshSessions]);

  // Cancel any in-flight stream when:
  //   1. The active session changes (user switched mid-stream
  //      — the abandoned bubble shouldn't keep growing in a
  //      hidden buffer, AND we shouldn't keep paying brain
  //      tokens for a reply the user navigated away from).
  //   2. The component unmounts (page reload, route change,
  //      auth-fail re-mount).
  // The ref tracks ``sessionName`` so we only cancel when the
  // ACTIVE name diverges from the streaming-bubble's name.
  useEffect(() => {
    const active = activeStreamRef.current;
    if (active && active.sessionName !== activeName) {
      void cancelStream("switch");
    }
  }, [activeName, cancelStream]);
  useEffect(() => {
    return () => {
      // Unmount: same path. Voids all state updates since the
      // component is going away anyway.
      const active = activeStreamRef.current;
      if (active) {
        try { active.controller.abort(); } catch {}
        void api.chatCancel(token);
        activeStreamRef.current = null;
      }
    };
  }, [token]);

  // Voice-capability probe. One-shot on mount — the result drives
  // whether the mic button renders and whether TTS playback fires.
  // 401 still bubbles through the same auth-fail path. Any other
  // error means the dashboard is up but voice misbehaved; we
  // silently default to no voice rather than showing a scary error
  // bar over a feature the user might not even care about.
  useEffect(() => {
    const ctrl = new AbortController();
    (async () => {
      try {
        const info = await api.voiceInfo(token, ctrl.signal);
        setVoiceInfo(info);
      } catch (exc) {
        if (exc instanceof DOMException && exc.name === "AbortError") return;
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        // Treat any other error as "voice disabled" — better
        // posture than spamming an error to the user.
        setVoiceInfo({
          enabled: false,
          stt: { provider: "null", available: false },
          tts: { provider: "null", available: false },
          call_mode: { model: "", reasoning_level: "" },
        });
      }
    })();
    return () => ctrl.abort();
  }, [token, onAuthFail]);

  // TTS playback helper. Bypasses entirely when voice is disabled,
  // TTS provider is null, the user has muted, or the assistant
  // string is empty. Stops any prior audio so back-to-back replies
  // don't overlap. Errors swallow silently (TTS is non-essential
  // — no point fronting a 500 to the user).
  const speakReply = useCallback(
    async (text: string) => {
      if (!voiceInfo?.tts.available || ttsMuted || !text.trim()) return;
      try {
        const blob = await api.chatTts(token, text);
        if (!blob) return;
        // Stop any prior reply still playing.
        if (ttsAudioRef.current) {
          ttsAudioRef.current.pause();
          ttsAudioRef.current.src = "";
          ttsAudioRef.current = null;
        }
        const url = URL.createObjectURL(blob);
        const audio = new Audio(url);
        ttsAudioRef.current = audio;
        // Revoke the object URL once playback ends to free memory.
        audio.addEventListener("ended", () => {
          URL.revokeObjectURL(url);
          if (ttsAudioRef.current === audio) ttsAudioRef.current = null;
        });
        await audio.play().catch(() => {
          // Browsers block autoplay until the user has interacted
          // with the page. The first send is interaction enough on
          // both Chrome and Safari, so this should only fail in
          // edge cases — safe to swallow.
        });
      } catch {
        // Network failure / 503 / 500 — voice is non-essential.
      }
    },
    [token, voiceInfo, ttsMuted],
  );

  // Persist mute preference and stop any in-flight audio when toggled on.
  const toggleTtsMute = useCallback(() => {
    setTtsMuted((prev) => {
      const next = !prev;
      try { localStorage.setItem("vexis-tts-muted", next ? "1" : "0"); }
      catch {}
      if (next && ttsAudioRef.current) {
        ttsAudioRef.current.pause();
        ttsAudioRef.current = null;
      }
      return next;
    });
  }, []);

  const handleSend = useCallback(
    async (text: string, attachments: QueuedAttachment[]) => {
      if (!activeName) return;
      const userTs = Date.now();
      const assistantTs = userTs + 1;
      // Snapshot the queue so the user bubble can render the same
      // attachments even after the queue is cleared post-send.
      const userMsg: ChatMessage = {
        role: "user",
        content: text,
        ts: userTs,
        attachments: attachments.length > 0 ? attachments : undefined,
      };
      // Append the user bubble + an empty assistant bubble that we'll
      // grow chunk-by-chunk as the SSE stream arrives. ``assistantTs``
      // is the unique id we use to find-and-update that bubble during
      // streaming. Sentinel ``--streaming--`` marker on content lets
      // the renderer suppress empty-bubble flicker if the brain takes
      // a beat before its first chunk lands.
      setMessages(activeName, (prev) => [
        ...prev,
        userMsg,
        { role: "assistant", content: "", ts: assistantTs },
      ]);
      setPendingSend(true);
      setAttachmentQueue([]);

      // Track this stream's AbortController so the Stop button +
      // session-switch / unmount cleanup can kill it. Cleared in
      // the ``finally`` below regardless of how the stream ends —
      // success, error, or user-stop — so a subsequent send always
      // starts from a known empty state.
      const controller = new AbortController();
      activeStreamRef.current = {
        controller,
        sessionName: activeName,
      };
      setStreaming(true);

      try {
        await api.chatSendStream(
          token,
          {
            text,
            attachments:
              attachments.length > 0
                ? attachments.map(({ previewUrl: _p, ...ref }) => ref)
                : undefined,
          },
          {
            signal: controller.signal,
            onChunk: (chunk) => {
              // Find the assistant bubble we placed and append the
              // chunk to its content. ``assistantTs`` stays unique
              // because we mint it from Date.now()+1 and React re-
              // render between sends serialises the increments.
              setMessages(activeName, (prev) =>
                prev.map((m) =>
                  m.ts === assistantTs && m.role === "assistant"
                    ? { ...m, content: m.content + chunk }
                    : m,
                ),
              );
            },
            onDone: (full) => {
              // Replace the streamed content with the canonical
              // ``done`` payload — should be byte-equal to the
              // concatenated chunks but the server sends a clean
              // copy so the UI doesn't accumulate any stream-parse
              // discrepancies.
              setMessages(activeName, (prev) =>
                prev.map((m) =>
                  m.ts === assistantTs && m.role === "assistant"
                    ? { ...m, content: full }
                    : m,
                ),
              );
              // Fire-and-forget TTS on the final reply. Same
              // contract as the buffered path — runs only when
              // voice.tts is enabled and not muted.
              void speakReply(full);
            },
            onError: (msg) => {
              // Remove the empty assistant bubble (or leave its
              // partial content if anything streamed) and append
              // a system note describing the failure. Empty msg
              // means cancel — drop the placeholder silently.
              setMessages(activeName, (prev) => {
                const out = prev.filter((m) => {
                  if (m.ts !== assistantTs) return true;
                  // Keep the bubble if it has any streamed content;
                  // drop it if it was empty (no tokens before the
                  // error fired).
                  return m.content.length > 0;
                });
                if (msg) {
                  out.push({
                    role: "system",
                    content: `⚠️ ${msg}`,
                    ts: Date.now(),
                  });
                }
                return out;
              });
            },
          },
        );
      } catch (exc: unknown) {
        // AbortError is raised by fetch when our AbortController
        // fires — that's the user-stop / switch / unmount path,
        // which has already been handled by ``cancelStream``. Drop
        // the throw silently so we don't paint a scary "⚠️ The
        // operation was aborted" system row over a deliberate
        // cancel.
        if (exc instanceof DOMException && exc.name === "AbortError") {
          return;
        }
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        const detail = exc instanceof Error ? exc.message : String(exc);
        setMessages(activeName, (prev) => [
          ...prev,
          { role: "system", content: `⚠️ ${detail}`, ts: Date.now() },
        ]);
      } finally {
        setPendingSend(false);
        // Only clear the activeStreamRef if it still points at THIS
        // controller — a fast cancel-then-resend could have already
        // overwritten it with a new entry by the time this finally
        // runs (cancelStream nulls the ref synchronously).
        if (activeStreamRef.current?.controller === controller) {
          activeStreamRef.current = null;
        }
        setStreaming(false);
      }
    },
    [activeName, token, onAuthFail, setMessages, speakReply],
  );

  /** Edit-and-resend the last user message. Drops everything from
   *  the last user bubble onward (including the assistant's reply),
   *  then routes the new text through the same streaming send path.
   *  Append-only — we don't rewind the brain's session JSONL, the
   *  brain just sees a new user turn after this. Simpler than
   *  ChatGPT's branch model but works for the user-visible UI. */
  const handleEditLastUser = useCallback(
    (newText: string, attachments: QueuedAttachment[]) => {
      if (!activeName) return;
      // Truncate the visible buffer to BEFORE the last user message
      // — we'll re-send via handleSend which appends a fresh user
      // bubble with the new text. Walk from the end so we drop the
      // most recent user (and everything that came after).
      setMessages(activeName, (prev) => {
        for (let i = prev.length - 1; i >= 0; i--) {
          if (prev[i].role === "user") {
            return prev.slice(0, i);
          }
        }
        return prev;
      });
      // Re-send with the edited text. ``handleSend`` is async but
      // we don't need to await — it owns its own pending/streaming
      // state.
      void handleSend(newText, attachments);
    },
    [activeName, setMessages, handleSend],
  );

  /** Re-run the last turn. Drops the assistant bubble, then
   *  re-sends the previous user message verbatim. Same append-only
   *  contract as edit. */
  const handleRegenerateLastAssistant = useCallback(() => {
    if (!activeName) return;
    const buf = buffers[activeName] ?? [];
    // Find the last assistant index AND the user message that
    // preceded it. If either is missing, no-op (regenerate
    // shouldn't run on a blank conversation).
    let lastAssistantIdx = -1;
    for (let i = buf.length - 1; i >= 0; i--) {
      if (buf[i].role === "assistant" && buf[i].content.length > 0) {
        lastAssistantIdx = i;
        break;
      }
    }
    if (lastAssistantIdx === -1) return;
    let lastUserIdx = -1;
    for (let i = lastAssistantIdx - 1; i >= 0; i--) {
      if (buf[i].role === "user") {
        lastUserIdx = i;
        break;
      }
    }
    if (lastUserIdx === -1) return;
    const userMsg = buf[lastUserIdx];
    // Truncate to BEFORE the last user bubble — handleSend will
    // re-append a fresh user bubble with the same text. We drop the
    // old user bubble too rather than just the assistant so the
    // conversation reads cleanly (one user/assistant pair, not a
    // duplicated user followed by a new assistant).
    setMessages(activeName, (prev) => prev.slice(0, lastUserIdx));
    void handleSend(userMsg.content, userMsg.attachments ?? []);
  }, [activeName, buffers, setMessages, handleSend]);

  // Voice capture: STT + brain turn in one round-trip. The server
  // returns both the transcript and the brain's reply, which we
  // append as separate bubbles so the conversation reads the same
  // as a typed exchange.
  const handleVoiceCapture = useCallback(
    async (audio: Blob) => {
      if (!activeName) return;
      setPendingSend(true);
      try {
        const { transcript, reply } = await api.chatVoice(token, audio);
        setMessages(activeName, (prev) => [
          ...prev,
          { role: "user", content: transcript, ts: Date.now() },
          { role: "assistant", content: reply, ts: Date.now() },
        ]);
        void speakReply(reply);
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        const detail = exc instanceof Error ? exc.message : String(exc);
        // 422 with empty-transcription is the most common failure
        // (silence, mic too far, speaking too quietly). Show as a
        // system row so the user knows to try again.
        setMessages(activeName, (prev) => [
          ...prev,
          { role: "system", content: `⚠️ ${detail}`, ts: Date.now() },
        ]);
      } finally {
        setPendingSend(false);
      }
    },
    [activeName, token, onAuthFail, setMessages, speakReply],
  );

  const handleVoiceError = useCallback((message: string) => {
    setError(message);
  }, []);

  // Voice-call-mode each-turn callback. Both transcript and reply
  // can be null when the turn errored out (empty transcription, for
  // instance) — in that case we don't append anything; call mode
  // shows the error state inside the modal.
  const handleCallTurn = useCallback(
    (transcript: string | null, reply: string | null) => {
      if (!activeName) return;
      const ts = Date.now();
      const next: ChatMessage[] = [];
      if (transcript) {
        next.push({ role: "user", content: transcript, ts });
      }
      if (reply) {
        next.push({ role: "assistant", content: reply, ts: ts + 1 });
      }
      if (next.length > 0) {
        setMessages(activeName, (prev) => [...prev, ...next]);
      }
    },
    [activeName, setMessages],
  );

  const handleNew = useCallback(async () => {
    setPendingName("__new__");
    try {
      await api.chatNewSession(token);
      await refreshSessions();
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setPendingName(null);
    }
  }, [token, onAuthFail, refreshSessions]);

  const handleSwitch = useCallback(
    async (name: string) => {
      setPendingName(name);
      try {
        await api.chatSwitchSession(token, name);
        await refreshSessions();
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setPendingName(null);
      }
    },
    [token, onAuthFail, refreshSessions],
  );

  const handleRename = useCallback(
    async (oldName: string, newName: string) => {
      setPendingName(oldName);
      try {
        await api.chatRenameSession(token, oldName, newName);
        // Migrate the in-memory message buffer so the rename is
        // transparent to the user — same conversation, new label.
        setBuffers((prev) => {
          if (!(oldName in prev)) return prev;
          const next = { ...prev };
          next[newName] = next[oldName];
          delete next[oldName];
          return next;
        });
        await refreshSessions();
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setPendingName(null);
      }
    },
    [token, onAuthFail, refreshSessions],
  );

  /** Filter a list of File objects to ones the server will accept.
   *  Mirrors the picker's ACCEPT list — keeping a copy here so a
   *  drag of an unsupported type (e.g. a video) surfaces a clean
   *  "not supported" toast instead of a 415 from the upload route.
   *  Server-side validation in
   *  ``core/web_server.py:_attach_route`` is still the authority. */
  const filterAcceptable = useCallback(
    (files: File[]): { ok: File[]; rejected: string[] } => {
      const accept = new Set([
        "image/png", "image/jpeg", "image/webp", "image/gif",
        "application/pdf", "text/plain", "text/markdown", "text/csv",
        "application/json",
      ]);
      const ok: File[] = [];
      const rejected: string[] = [];
      for (const f of files) {
        // Some pasted images come through with an empty type when
        // the OS pasteboard didn't tag them — accept anything that
        // claims to be an image.
        if (accept.has(f.type) || f.type.startsWith("image/")) {
          ok.push(f);
        } else {
          rejected.push(f.name || `(${f.type || "unknown"})`);
        }
      }
      return { ok, rejected };
    },
    [],
  );

  /** Hand a batch of files to the AttachmentPicker's exposed
   *  uploadFiles handle. Used by both drag-drop and paste. */
  const uploadFiles = useCallback(
    async (files: File[]) => {
      if (files.length === 0) return;
      const { ok, rejected } = filterAcceptable(files);
      if (rejected.length > 0) {
        setError(
          `Skipped ${rejected.length} unsupported file${rejected.length > 1 ? "s" : ""}: ${rejected.join(", ")}`,
        );
      }
      if (ok.length === 0) return;
      const handle = attachmentPickerRef.current;
      if (!handle) return;
      await handle.uploadFiles(ok);
    },
    [filterAcceptable],
  );

  // Drag-and-drop on the conversation pane. We attach to the entire
  // chat container (sidebar excluded) so the user has a generous
  // hit area — anywhere over the messages list or composer wrapper
  // counts as "drop here". Browser default behaviour on a drop is
  // to navigate to the file URL; preventDefault on every event in
  // the chain stops that.
  const onDragEnter = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      // Only react when files are being dragged — text drags
      // (e.g. dragging a selection) shouldn't trigger the upload UI.
      if (!Array.from(e.dataTransfer.types).includes("Files")) return;
      e.preventDefault();
      e.stopPropagation();
      setDragHover(true);
    },
    [],
  );
  const onDragOver = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      if (!Array.from(e.dataTransfer.types).includes("Files")) return;
      e.preventDefault();
      e.stopPropagation();
      // dropEffect "copy" gives the browser the right cursor.
      e.dataTransfer.dropEffect = "copy";
    },
    [],
  );
  const onDragLeave = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      // Only flip hover off when we leave the chat container — not
      // every child boundary the cursor crosses.
      if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
      setDragHover(false);
    },
    [],
  );
  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      const files = Array.from(e.dataTransfer.files);
      if (files.length === 0) return;
      e.preventDefault();
      e.stopPropagation();
      setDragHover(false);
      void uploadFiles(files);
    },
    [uploadFiles],
  );

  // Paste handler — trapped on the document so it works regardless
  // of which composer-internal element has focus. Most common case:
  // the user takes a screenshot (Cmd+Shift+4 on macOS, PrtSc on
  // Linux) and pastes into the chat. We pull only image-typed
  // entries from clipboard.items; ignoring text/* lets a paste of
  // mixed clipboard content (image + html) still work as expected.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const items = e.clipboardData?.items;
      if (!items) return;
      const files: File[] = [];
      for (const item of Array.from(items)) {
        if (item.kind === "file") {
          const f = item.getAsFile();
          if (f) files.push(f);
        }
      }
      if (files.length === 0) return;
      // Prevent the default paste which would otherwise embed the
      // image into the textarea as base64 (some browsers do this).
      e.preventDefault();
      void uploadFiles(files);
    };
    document.addEventListener("paste", onPaste);
    return () => document.removeEventListener("paste", onPaste);
  }, [uploadFiles]);

  const handleDelete = useCallback(
    async (name: string) => {
      setPendingName(name);
      try {
        await api.chatDeleteSession(token, name);
        setBuffers((prev) => {
          if (!(name in prev)) return prev;
          const next = { ...prev };
          delete next[name];
          return next;
        });
        await refreshSessions();
      } catch (exc: unknown) {
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      } finally {
        setPendingName(null);
      }
    },
    [token, onAuthFail, refreshSessions],
  );

  // Loading shell — render the layout structure so the sidebar and
  // composer don't pop in after the first /chat/sessions response.
  // sessions === null is the pre-load state; an empty array is "no
  // sessions yet" and falls through to the empty-state in the sidebar.
  return (
    <div
      className={[
        "flex bg-[var(--color-base)]",
        // /talk renders chrome-less and needs to fill the viewport.
        // Inside the dashboard the parent <main> already provides
        // padding and a max-width so we want to fill *its* box —
        // hence the calc on the dashboard side, dvh on /talk.
        fullscreen ? "h-dvh" : "h-[calc(100dvh-220px)] min-h-[500px] rounded-lg overflow-hidden border border-[var(--color-border)]",
      ].join(" ")}
    >
      <ChatSidebar
        sessions={sortedSessions}
        pendingName={pendingName}
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        onAfterAction={() => setSidebarOpen(false)}
        onNew={handleNew}
        onSwitch={handleSwitch}
        onRename={handleRename}
        onDelete={handleDelete}
      />
      <div
        className="flex-1 flex flex-col min-w-0 relative"
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {/* Mobile-only header: hamburger to open the drawer + the
            active session name as the visual orienting cue. Above md
            the sidebar is always visible so this row is hidden. */}
        <div
          className={[
            "md:hidden flex items-center gap-3 px-3 py-2.5 border-b",
            "border-[var(--color-border)] bg-[var(--color-surface)]",
          ].join(" ")}
        >
          <button
            type="button"
            onClick={() => setSidebarOpen(true)}
            aria-label="Open sessions"
            className={[
              "shrink-0 w-9 h-9 flex items-center justify-center rounded-md",
              "text-[var(--color-fg)] hover:bg-[var(--color-base)]",
              "border border-[var(--color-border-strong)]",
              "transition-colors",
            ].join(" ")}
          >
            {/* Three-line hamburger (U+2630). Drawn with the existing
                font rather than an SVG to keep the bundle thin. The
                `≡` glyph (U+2261) reads as math notation in some
                fonts; ☰ is the dedicated trigram and renders crisply
                everywhere we tested. */}
            <span aria-hidden className="text-lg leading-none">☰</span>
          </button>
          <div className="flex-1 min-w-0">
            <div className="text-[10px] uppercase tracking-wider text-[var(--color-fg-dim)]">
              Session
            </div>
            <div className="text-sm font-semibold truncate text-[var(--color-fg)]">
              {activeName || "—"}
            </div>
          </div>
          {voiceInfo?.tts.available && (
            <button
              type="button"
              onClick={toggleTtsMute}
              aria-label={
                ttsMuted ? "Unmute spoken replies" : "Mute spoken replies"
              }
              title={
                ttsMuted
                  ? "Spoken replies muted (you can still send voice; replies are text-only). Tap to enable speech."
                  : "Spoken replies on. Tap to mute."
              }
              className={[
                "shrink-0 w-9 h-9 flex items-center justify-center rounded-md",
                "border border-[var(--color-border-strong)] transition-colors",
                ttsMuted
                  ? "text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]"
                  : "text-[var(--color-accent)] hover:text-[var(--color-fg)]",
                "hover:bg-[var(--color-base)]",
              ].join(" ")}
            >
              {/* 🔊 / 🔇 — speaker glyphs read clearly at small sizes. */}
              <span aria-hidden className="text-base leading-none">
                {ttsMuted ? "🔇" : "🔊"}
              </span>
            </button>
          )}
        </div>
        {/* Desktop TTS toggle — same control, surfaced as a tiny
            corner button so it's reachable without the mobile
            header. Hidden when TTS is unavailable. Label is
            "spoken replies" rather than "voice" because muting
            doesn't affect voice INPUT — only whether the assistant
            reads its replies aloud. */}
        {voiceInfo?.tts.available && (
          <div className="hidden md:flex justify-end px-4 sm:px-6 pt-2">
            <button
              type="button"
              onClick={toggleTtsMute}
              aria-label={
                ttsMuted ? "Unmute spoken replies" : "Mute spoken replies"
              }
              title={
                ttsMuted
                  ? "Spoken replies muted (voice input still works). Click to enable speech."
                  : "Spoken replies on. Click to mute."
              }
              className={[
                "text-xs flex items-center gap-1 px-2 py-1 rounded",
                "transition-colors",
                ttsMuted
                  ? "text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]"
                  : "text-[var(--color-accent)] hover:text-[var(--color-fg)]",
                "hover:bg-[var(--color-surface)]",
              ].join(" ")}
            >
              <span aria-hidden>{ttsMuted ? "🔇" : "🔊"}</span>
              <span>{ttsMuted ? "Replies muted" : "Spoken replies"}</span>
            </button>
          </div>
        )}
        {error && (
          <div className="px-4 sm:px-6 py-2 text-xs text-[var(--color-error)] border-b border-[var(--color-border)] bg-[var(--color-surface)]">
            {error}
          </div>
        )}
        <ChatMessages
          messages={messages}
          pending={pendingSend}
          onEditLastUser={handleEditLastUser}
          onRegenerateLastAssistant={handleRegenerateLastAssistant}
        />
        <ChatComposer
          token={token}
          pending={pendingSend}
          streaming={streaming}
          onStop={() => void cancelStream("user-stop")}
          onSend={handleSend}
          sttAvailable={voiceInfo?.stt.available ?? false}
          onVoiceCapture={handleVoiceCapture}
          onVoiceError={handleVoiceError}
          callModeAvailable={
            // Hands-free call mode needs both halves wired. Either
            // missing → button doesn't render so the user can't
            // open a half-functional modal.
            (voiceInfo?.stt.available ?? false) &&
            (voiceInfo?.tts.available ?? false)
          }
          onOpenCallMode={() => setCallOpen(true)}
          attachmentQueue={attachmentQueue}
          setAttachmentQueue={setAttachmentQueue}
          onAttachmentError={(m) => setError(m)}
          attachmentPickerRef={attachmentPickerRef}
        />
        {/* Drag-hover overlay — full-pane translucent state with a
            "drop image here" affordance. Pointer-events:none so the
            drop event still reaches the underlying container.
            Hidden on mobile (no drag events fire from a tap). */}
        {dragHover && (
          <div
            aria-hidden
            className={[
              "hidden md:flex absolute inset-0 z-20 pointer-events-none",
              "items-center justify-center",
              "bg-[var(--color-accent)]/10 border-2 border-dashed",
              "border-[var(--color-accent)]",
              "text-[var(--color-accent)] text-sm font-semibold tracking-wide",
            ].join(" ")}
          >
            Drop to attach
          </div>
        )}
      </div>
      {callOpen && (
        <Suspense fallback={null}>
          <VoiceCallMode
            token={token}
            open={callOpen}
            onTurn={handleCallTurn}
            onClose={() => setCallOpen(false)}
            // Sourced from voice.call_mode.{model,reasoning_level} in
            // config (via the /chat/voice/info probe). Empty string =
            // brain default; any other value is the override the call
            // modal applies for every turn while open.
            modelOverride={voiceInfo?.call_mode.model ?? ""}
            reasoningOverride={voiceInfo?.call_mode.reasoning_level ?? ""}
          />
        </Suspense>
      )}
    </div>
  );
}

