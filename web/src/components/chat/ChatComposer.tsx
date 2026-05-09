import { useCallback, useEffect, useRef, useState } from "react";
import type { Ref } from "react";
import { MicButton } from "./MicButton";
import {
  AttachmentChips,
  AttachmentPicker,
  type AttachmentPickerHandle,
} from "./AttachmentPicker";
import type { QueuedAttachment } from "../../lib/types";

interface ChatComposerProps {
  // Auth token forwarded to AttachmentPicker for the upload route.
  // Same token everywhere; we don't fork it per component.
  token: string;
  // True while a previous message is still being processed.
  // Send is blocked but the textarea stays editable so the user
  // can queue their next message during the wait.
  pending: boolean;
  // True while an SSE stream is open and assistant tokens are
  // still arriving. Drives the Send→Stop button swap. Distinct
  // from ``pending`` because the buffered (non-streaming) voice
  // round-trip flips ``pending`` without ever flipping
  // ``streaming`` — only the SSE chat path has a stream to stop.
  streaming: boolean;
  // Called when the user clicks the Stop button. Parent aborts
  // the in-flight fetch and POSTs /chat/cancel so the brain
  // subprocess actually quits (not just the SSE pipe).
  onStop: () => void;
  // Called with the user's text + the queued attachments. Parent
  // is responsible for clearing the queue after a successful send
  // (passes a fresh empty array via ``attachments`` next render).
  onSend: (text: string, attachments: QueuedAttachment[]) => void;
  // When true, render the mic button. Wired by ChatPage off the
  // /chat/voice/info probe — hidden when STT isn't available so
  // tapping it can't 503.
  sttAvailable: boolean;
  // Called when the user releases the mic; parent uploads the blob
  // to /chat/voice and injects the resulting transcript+reply.
  onVoiceCapture: (audio: Blob) => void;
  // Surfaced as an inline error string above the composer (parent
  // owns the rendering — composer just signals).
  onVoiceError: (message: string) => void;
  // Voice-call mode entry. ChatPage owns the modal state; we just
  // render the entry button. Hidden when STT *and* TTS aren't both
  // available — call mode without one or the other isn't useful.
  callModeAvailable: boolean;
  onOpenCallMode: () => void;
  // Attachment queue is parent-owned so drag-drop and paste handlers
  // on the conversation pane can also append into it.
  attachmentQueue: QueuedAttachment[];
  setAttachmentQueue: (next: QueuedAttachment[]) => void;
  onAttachmentError: (message: string) => void;
  // Ref forwarded to the underlying AttachmentPicker so the page's
  // drag-drop / paste handlers can route into the same upload flow
  // as the paperclip button. ``null`` is allowed because tests +
  // legacy call sites that don't need drag-drop just omit it.
  attachmentPickerRef?: Ref<AttachmentPickerHandle>;
  // (Phase D) Persistence key for the composer draft. When set,
  // every keystroke is written to ``localStorage[draftKey]`` and
  // the initial draft loads from there on mount. Lets a tab close
  // / accidental reload not eat a half-typed long message.
  // Pass ``null`` (or omit) on transient surfaces (voice modal,
  // tests) where draft persistence would be more confusing than
  // helpful.
  draftKey?: string | null;
  // (Phase D) Last user message in the active session. ↑ in the
  // empty composer recalls this into the textarea for editing
  // before resending — same muscle-memory shortcut as a terminal
  // shell, ChatGPT, Claude.ai. Undefined / null = no recall
  // (e.g. fresh session).
  lastUserMessage?: string | null;
}

// Soft cap on the textarea height so a giant paste doesn't push the
// composer up to the top of the viewport. Server-side cap is 32 KiB
// (web_server.py:_CHAT_TEXT_MAX_BYTES) — far above this height limit.
const MAX_HEIGHT_PX = 240;

export function ChatComposer({
  token,
  pending,
  streaming,
  onStop,
  onSend,
  sttAvailable,
  onVoiceCapture,
  onVoiceError,
  callModeAvailable,
  onOpenCallMode,
  attachmentQueue,
  setAttachmentQueue,
  onAttachmentError,
  attachmentPickerRef,
  draftKey,
  lastUserMessage,
}: ChatComposerProps) {
  // Hydrate the draft from localStorage when ``draftKey`` is set.
  // The lazy initializer keeps the localStorage read out of the
  // hot render path. Falsy keys (undefined, null, "") fall back
  // to the empty draft — e.g. when ``activeName`` is "" before
  // the sessions list arrives.
  const [draft, setDraft] = useState<string>(() => {
    if (!draftKey) return "";
    try {
      return localStorage.getItem(draftKey) ?? "";
    } catch {
      return "";
    }
  });
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  // Switch the draft buffer when ``draftKey`` changes (session
  // switch). Also fires on first mount if draftKey arrives later
  // than the initial render — the initializer above runs only
  // once. We deliberately read fresh every time so a switch back
  // to a session shows whatever draft was most recently typed.
  useEffect(() => {
    if (!draftKey) {
      setDraft("");
      return;
    }
    try {
      setDraft(localStorage.getItem(draftKey) ?? "");
    } catch {
      setDraft("");
    }
  }, [draftKey]);

  // Persist on every keystroke. Cheap (single string write) and
  // stays inside the render cycle so a tab close mid-edit catches
  // the latest. We don't debounce — localStorage writes are
  // synchronous and fast enough for a single key per stroke.
  useEffect(() => {
    if (!draftKey) return;
    try {
      if (draft) localStorage.setItem(draftKey, draft);
      else localStorage.removeItem(draftKey);
    } catch {
      // Quota exceeded / private mode — silently drop. Drafts
      // are nice-to-have, not essential.
    }
  }, [draft, draftKey]);

  const removeAttachment = useCallback(
    (path: string) => {
      const target = attachmentQueue.find((a) => a.path === path);
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl);
      setAttachmentQueue(attachmentQueue.filter((a) => a.path !== path));
    },
    [attachmentQueue, setAttachmentQueue],
  );

  // Auto-grow: reset to ``auto`` so scrollHeight reports the natural
  // size, then clamp to MAX_HEIGHT_PX. Past the cap the textarea
  // becomes scrollable inside the bubble.
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = "auto";
    const next = Math.min(el.scrollHeight, MAX_HEIGHT_PX);
    el.style.height = `${next}px`;
  }, [draft]);

  const submit = useCallback(() => {
    const trimmed = draft.trim();
    // Allow sending with no text if attachments are queued — useful
    // for "look at this image" without a caption. Common on phones.
    if ((!trimmed && attachmentQueue.length === 0) || pending) return;
    // Don't send while attachments are still uploading — the
    // placeholder paths (__pending__/...) wouldn't resolve on the
    // server side. The picker shows progress so users wait naturally.
    if (attachmentQueue.some((a) => a.path.startsWith("__pending__/"))) return;
    onSend(trimmed, attachmentQueue);
    setDraft("");
    // We DO NOT revoke previewUrls here — the user bubble in the
    // parent's messages buffer holds onto them so the image stays
    // visible after send. They get freed when the messages buffer
    // is replaced (session switch) or the page reloads, which is
    // acceptable for the typical-session memory budget. Premature
    // revocation here would race the bubble's <img> fetch and the
    // image would render as broken alt text.
  }, [draft, pending, onSend, attachmentQueue]);

  // Keybindings (in priority order):
  //   - Enter (no shift)               → submit
  //   - Cmd/Ctrl+Enter                  → submit (alt for users who
  //                                       prefer Shift+Enter as the
  //                                       newline default and want
  //                                       a chord they can't trip)
  //   - ↑ in an empty composer          → recall the last user msg
  //                                       so editing-and-resending a
  //                                       previous prompt doesn't
  //                                       require copy-paste
  //   - Esc                             → blur the composer (kills
  //                                       the IME / mobile keyboard,
  //                                       lets the user scroll the
  //                                       conversation cleanly)
  // Mobile keyboards still get a newline button via the IME's
  // Shift+Enter equivalent (most send a return key event without
  // shift, which we deliberately treat as send — long-form edits
  // happen on desktop).
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      // ↑ recall — only when the textarea is empty AND the cursor
      // is at the very start (defensive: a user on a multi-line
      // draft pressing ↑ to navigate the textarea shouldn't trip
      // the recall). Cursor-at-start covers both "empty draft"
      // and "draft starts at 0,0" for the same code path.
      if (
        e.key === "ArrowUp"
        && !e.shiftKey
        && !e.metaKey
        && !e.ctrlKey
        && !e.altKey
        && !e.nativeEvent.isComposing
        && lastUserMessage
        && (draft === "" || (
          e.currentTarget.selectionStart === 0
          && e.currentTarget.selectionEnd === 0
        ))
      ) {
        e.preventDefault();
        setDraft(lastUserMessage);
        // Move the cursor to the end of the recalled text so the
        // user can start editing immediately. Defer one tick so
        // the new value is committed first.
        setTimeout(() => {
          const el = textareaRef.current;
          if (el) el.setSelectionRange(el.value.length, el.value.length);
        }, 0);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        e.currentTarget.blur();
        return;
      }
      if (
        e.key === "Enter"
        && !e.shiftKey
        && !e.nativeEvent.isComposing
      ) {
        e.preventDefault();
        submit();
        return;
      }
      // Cmd/Ctrl+Enter is the safety-gated submit. We deliberately
      // accept shift here so a user who's holding shift to insert
      // newlines can still use the chord without fighting their
      // own modifier.
      if (
        e.key === "Enter"
        && (e.metaKey || e.ctrlKey)
        && !e.nativeEvent.isComposing
      ) {
        e.preventDefault();
        submit();
      }
    },
    [submit, lastUserMessage, draft],
  );

  return (
    <div
      className={[
        "border-t border-[var(--color-border)] bg-[var(--color-base)]",
        "px-4 sm:px-6 py-3",
        // iOS home-indicator and Android nav-bar safe-area. The
        // viewport-fit=cover meta in index.html lets these env()
        // values resolve to non-zero on hardware that needs them;
        // on desktop / Android-with-buttons they collapse to 0.
        "pb-[max(env(safe-area-inset-bottom),0.75rem)]",
      ].join(" ")}
    >
      <div className="max-w-3xl mx-auto">
        <AttachmentChips queue={attachmentQueue} onRemove={removeAttachment} />
        <div
          className={[
            "flex items-end rounded-lg border bg-[var(--color-surface)]",
            // Tighter padding + inter-button gap on mobile so the
            // 4-button + textarea cluster fits a 375px viewport
            // without crushing the textarea down to ~50px wide.
            "gap-1 px-2 py-2 sm:gap-2 sm:px-3",
            "border-[var(--color-border)] focus-within:border-[var(--color-accent-2)]",
            "transition-colors",
          ].join(" ")}
        >
          <AttachmentPicker
            ref={attachmentPickerRef}
            token={token}
            disabled={pending}
            queue={attachmentQueue}
            onChange={setAttachmentQueue}
            onError={onAttachmentError}
          />
          <textarea
            ref={textareaRef}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={onKeyDown}
            rows={1}
            placeholder={pending ? "Waiting for reply…" : "Type a message…"}
            className={[
              "flex-1 resize-none bg-transparent outline-none",
              "text-sm leading-relaxed text-[var(--color-fg)]",
              "placeholder:text-[var(--color-fg-dim)]",
              "min-h-[1.5rem]",
            ].join(" ")}
            style={{ maxHeight: MAX_HEIGHT_PX }}
          />
          {sttAvailable && (
            <MicButton
              disabled={pending}
              onRecordingComplete={onVoiceCapture}
              onError={onVoiceError}
            />
          )}
          {callModeAvailable && (
            <button
              type="button"
              onClick={onOpenCallMode}
              disabled={pending}
              aria-label="Start voice call"
              title="Voice call — hands-free conversation"
              className={[
                "shrink-0 rounded-md flex items-center justify-center",
                // 44x44 square on mobile, tighter pill on desktop —
                // matches the rest of the composer button row.
                "w-11 h-11 md:w-auto md:h-auto md:px-2.5 md:py-1.5",
                "transition-colors select-none",
                "border border-[var(--color-accent-2)]",
                "text-[var(--color-accent)] hover:text-[var(--color-fg)]",
                "hover:border-[var(--color-accent)] hover:bg-[var(--color-accent)]/10",
                "disabled:opacity-40 disabled:cursor-not-allowed",
              ].join(" ")}
            >
              <span aria-hidden className="text-base leading-none">📞</span>
            </button>
          )}
          {streaming ? (
            // Stop button — replaces Send while assistant tokens
            // are streaming in. Distinct red-ish accent so it
            // reads as a destructive action; the square glyph is
            // the de-facto "stop" pictogram (matches ChatGPT,
            // Claude.ai, Cursor). data-testid keeps the
            // ChatComposer test surface stable across Send/Stop
            // swaps without coupling tests to localised aria
            // labels.
            <button
              type="button"
              onClick={onStop}
              data-testid="composer-stop"
              aria-label="Stop generating"
              title="Stop generating"
              className={[
                "shrink-0 rounded-md font-semibold transition-colors",
                "w-11 h-11 flex items-center justify-center text-base",
                "md:w-auto md:h-auto md:px-3 md:py-1.5 md:text-xs md:uppercase md:tracking-wider",
                "bg-[var(--color-error)] text-[var(--color-base)]",
                "hover:opacity-90",
              ].join(" ")}
            >
              <span aria-hidden className="md:hidden">■</span>
              <span className="hidden md:inline">Stop</span>
            </button>
          ) : (
            <button
              type="button"
              onClick={submit}
              data-testid="composer-send"
              disabled={!draft.trim() || pending}
              className={[
                "shrink-0 rounded-md font-semibold transition-colors",
                // Mobile: square 44x44 icon button (touch-target
                // floor), arrow glyph instead of "SEND" text — the
                // 50px the word would have eaten lets the textarea
                // breathe. Desktop: keep the labeled pill.
                "w-11 h-11 flex items-center justify-center text-base",
                "md:w-auto md:h-auto md:px-3 md:py-1.5 md:text-xs md:uppercase md:tracking-wider",
                "bg-[var(--color-accent)] text-[var(--color-accent-fg)]",
                "hover:bg-[var(--color-accent-2)] hover:text-[var(--color-fg)]",
                "disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-[var(--color-accent)]",
              ].join(" ")}
              aria-label="Send message"
            >
              {/* Up-arrow glyph on mobile (sends "up" into the
                  conversation), "Send" text on desktop. md:hidden /
                  hidden md:inline is the canonical Tailwind toggle. */}
              <span aria-hidden className="md:hidden">↑</span>
              <span className="hidden md:inline">Send</span>
            </button>
          )}
        </div>
        {/* Keyboard hint is desktop-only — mobile keyboards don't
            expose Shift+Enter and the line just steals 16px of
            vertical space above the keyboard. */}
        <div className="hidden md:block mt-1.5 px-1 text-[10px] text-[var(--color-fg-dim)]">
          Enter to send, Shift+Enter for newline.
        </div>
      </div>
    </div>
  );
}
