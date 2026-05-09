import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { ChatSession, QueuedAttachment, VoiceInfo } from "../lib/types";
import { ChatSidebar } from "../components/chat/ChatSidebar";
import {
  ChatMessages,
  type ChatMessage,
} from "../components/chat/ChatMessages";
import { ChatComposer } from "../components/chat/ChatComposer";

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
  // preference survives reloads. Default: TTS plays when available.
  const [ttsMuted, setTtsMuted] = useState<boolean>(() => {
    try { return localStorage.getItem("vexis-tts-muted") === "1"; }
    catch { return false; }
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

  // Voice call mode — opens a full-screen overlay with VAD-driven
  // hands-free conversation. The component owns its own mic +
  // playback; we just track open/closed and forward each turn back
  // here so the conversation buffer reflects what was said.
  const [callOpen, setCallOpen] = useState(false);

  const activeName = sessions?.find((s) => s.is_active)?.name ?? "";
  const messages = activeName ? buffers[activeName] ?? [] : [];

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
      const ts = Date.now();
      // Snapshot the queue so the user bubble can render the same
      // attachments even after the queue is cleared post-send.
      const userMsg: ChatMessage = {
        role: "user",
        content: text,
        ts,
        attachments: attachments.length > 0 ? attachments : undefined,
      };
      setMessages(activeName, (prev) => [...prev, userMsg]);
      setPendingSend(true);
      // Clear the queue immediately (optimistic) so a fast user can
      // start composing the next message. Don't revoke preview URLs
      // here — the user bubble still needs them for inline display.
      // They'll be revoked when the bubble unmounts (page refresh /
      // session switch wipes the in-memory buffer).
      setAttachmentQueue([]);
      try {
        const { reply } =
          attachments.length > 0
            ? await api.chatSendWithAttachments(
                token,
                text,
                // Strip the client-only previewUrl before sending —
                // server doesn't know what to do with it.
                attachments.map(({ previewUrl: _p, ...ref }) => ref),
              )
            : await api.chatSend(token, text);
        setMessages(activeName, (prev) => [
          ...prev,
          { role: "assistant", content: reply, ts: Date.now() },
        ]);
        // Fire-and-forget TTS — don't block the UI on synthesis.
        // No-op when voice is disabled or muted.
        void speakReply(reply);
      } catch (exc: unknown) {
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
      }
    },
    [activeName, token, onAuthFail, setMessages, speakReply],
  );

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
      <div className="flex-1 flex flex-col min-w-0">
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
        <ChatMessages messages={messages} pending={pendingSend} />
        <ChatComposer
          token={token}
          pending={pendingSend}
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
        />
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

