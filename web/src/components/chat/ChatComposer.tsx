import { useCallback, useEffect, useRef, useState } from "react";
import { MicButton } from "./MicButton";
import {
  AttachmentChips,
  AttachmentPicker,
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
}

// Soft cap on the textarea height so a giant paste doesn't push the
// composer up to the top of the viewport. Server-side cap is 32 KiB
// (web_server.py:_CHAT_TEXT_MAX_BYTES) — far above this height limit.
const MAX_HEIGHT_PX = 240;

export function ChatComposer({
  token,
  pending,
  onSend,
  sttAvailable,
  onVoiceCapture,
  onVoiceError,
  callModeAvailable,
  onOpenCallMode,
  attachmentQueue,
  setAttachmentQueue,
  onAttachmentError,
}: ChatComposerProps) {
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

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

  // Enter sends, Shift+Enter newline. Matches Telegram, ChatGPT,
  // Claude.ai. Mobile keyboards still get a newline button via
  // the IME's Shift+Enter equivalent (most send a return key event
  // without shift, which we deliberately treat as send — long-form
  // edits happen on desktop).
  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        submit();
      }
    },
    [submit],
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
            "flex items-end gap-2 rounded-lg border bg-[var(--color-surface)] px-3 py-2",
            "border-[var(--color-border)] focus-within:border-[var(--color-accent-2)]",
            "transition-colors",
          ].join(" ")}
        >
          <AttachmentPicker
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
                "px-3 py-2.5 md:px-2.5 md:py-1.5",
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
          <button
            type="button"
            onClick={submit}
            disabled={!draft.trim() || pending}
            className={[
              "shrink-0 rounded-md text-xs uppercase tracking-wider",
              // Generous touch target on mobile (~44px tall), tighter
              // on desktop where a cursor doesn't need the padding.
              "px-4 py-2.5 md:px-3 md:py-1.5",
              "font-semibold transition-colors",
              "bg-[var(--color-accent)] text-[var(--color-accent-fg)]",
              "hover:bg-[var(--color-accent-2)] hover:text-[var(--color-fg)]",
              "disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-[var(--color-accent)]",
            ].join(" ")}
            aria-label="Send message"
          >
            Send
          </button>
        </div>
        <div className="mt-1.5 px-1 text-[10px] text-[var(--color-fg-dim)]">
          Enter to send, Shift+Enter for newline.
        </div>
      </div>
    </div>
  );
}
