import { useCallback, useEffect, useRef, useState } from "react";
import { Markdown } from "../Markdown";
import type { QueuedAttachment } from "../../lib/types";

export interface ChatMessage {
  // ``user`` is what you typed; ``assistant`` is the brain's reply or
  // a control message ("Switched to demo", "Conversation cleared.").
  // ``system`` is reserved for our own UI notices (errors, empty
  // states) so they read distinctly from the conversation.
  role: "user" | "assistant" | "system";
  content: string;
  // Local wall-clock when the message was appended. Not load-bearing
  // for ordering (the array order is) — only used for the timestamp
  // tooltip on hover.
  ts: number;
  // Attachments included with the user's message. Image MIME types
  // render inline thumbnails using the client-side ``previewUrl`` blob;
  // others render as a chip with the filename and size. Attachments
  // are forensic-only after send (the brain has already received the
  // server paths in the message body).
  attachments?: QueuedAttachment[];
}

interface ChatMessagesProps {
  messages: ChatMessage[];
  // True while an outbound request is in flight. We render a
  // "thinking…" bubble so the user knows their message landed and
  // the wait is real, not a frozen UI. Phase 1 ships buffered
  // replies; this placeholder is what stands in for streaming
  // until we add it.
  pending: boolean;
  // Edit the LAST user message — drops everything after it and
  // re-sends. ``undefined`` disables the edit affordance entirely
  // (e.g. while a reply is streaming). Append-only: we don't
  // rewind the brain's session JSONL, the brain just sees a new
  // turn. Simpler than ChatGPT's branch model but works for the
  // user-visible UI.
  onEditLastUser?: (
    newText: string,
    attachments: QueuedAttachment[],
  ) => void;
  // Re-run the last user → assistant turn. Drops the assistant
  // bubble, then re-sends the last user message's content +
  // attachments. Same append-only contract as edit.
  onRegenerateLastAssistant?: () => void;
}

export function ChatMessages({
  messages,
  pending,
  onEditLastUser,
  onRegenerateLastAssistant,
}: ChatMessagesProps) {
  const endRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to bottom on new messages or pending-state flip.
  // ``smooth`` for incremental adds; the initial render uses
  // ``auto`` so a session-switch lands at the bottom without a
  // scroll animation across the whole list.
  useEffect(() => {
    if (endRef.current) {
      endRef.current.scrollIntoView({
        behavior: messages.length <= 1 ? "auto" : "smooth",
      });
    }
  }, [messages.length, pending]);

  if (messages.length === 0 && !pending) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="text-center max-w-sm">
          <div className="text-[var(--color-fg-dim)] text-xs uppercase tracking-wider mb-2">
            New conversation
          </div>
          <div className="text-[var(--color-fg-2)] text-sm">
            Type a message below to start. Sessions persist — switch from
            the sidebar to pick up where you left off.
          </div>
        </div>
      </div>
    );
  }

  // Suppress the bottom pulsing indicator when the last message is
  // already a streaming-in-progress assistant bubble — that bubble
  // shows its own inline pulse via the empty-content branch in
  // Bubble. Two indicators stacked would be visual noise.
  const tail = messages[messages.length - 1];
  const tailIsStreaming =
    tail?.role === "assistant" && tail.content === "";

  // Find the last user / last non-empty assistant indices once,
  // so each Bubble can decide whether to render Edit / Regenerate
  // without re-scanning the array. We deliberately skip empty
  // assistant content (mid-stream placeholder) so Regenerate
  // doesn't render on a half-formed bubble.
  let lastUserIdx = -1;
  let lastAssistantIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (lastUserIdx === -1 && messages[i].role === "user") lastUserIdx = i;
    if (
      lastAssistantIdx === -1
      && messages[i].role === "assistant"
      && messages[i].content.length > 0
    ) {
      lastAssistantIdx = i;
    }
    if (lastUserIdx !== -1 && lastAssistantIdx !== -1) break;
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-6">
      <div className="max-w-3xl mx-auto flex flex-col gap-5">
        {messages.map((m, i) => (
          <Bubble
            key={i}
            message={m}
            isLastUser={i === lastUserIdx}
            isLastAssistant={i === lastAssistantIdx}
            onEdit={
              i === lastUserIdx && onEditLastUser && !pending
                ? onEditLastUser
                : undefined
            }
            onRegenerate={
              i === lastAssistantIdx
              && onRegenerateLastAssistant
              && !pending
                ? onRegenerateLastAssistant
                : undefined
            }
          />
        ))}
        {pending && !tailIsStreaming && <PendingBubble />}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function Bubble({
  message,
  isLastUser,
  isLastAssistant,
  onEdit,
  onRegenerate,
}: {
  message: ChatMessage;
  isLastUser: boolean;
  isLastAssistant: boolean;
  onEdit?: (
    newText: string,
    attachments: QueuedAttachment[],
  ) => void;
  onRegenerate?: () => void;
}) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  // Edit-in-place state for the last user bubble. Lives on the
  // Bubble (not lifted to ChatMessages) because only one bubble at
  // a time can be in edit mode and the local state never needs to
  // be observed elsewhere.
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(message.content);

  if (isSystem) {
    return (
      <div className="text-center text-xs text-[var(--color-fg-dim)] py-1">
        {message.content}
      </div>
    );
  }

  return (
    <div
      className={[
        "flex gap-3 group/bubble",
        isUser ? "justify-end" : "justify-start",
      ].join(" ")}
    >
      {/* Column inside the row holds the bubble itself plus the
          action row below. Aligning items to the bubble's edge
          (right for user, left for assistant) keeps the action row
          flush with the bubble's nearest screen edge. ``max-width``
          lives on the column so the action row doesn't extend
          wider than the bubble. */}
      <div
        className={[
          "flex flex-col gap-1 max-w-[90%] sm:max-w-[75%] min-w-0",
          isUser ? "items-end" : "items-start",
        ].join(" ")}
      >
        <div
          className={[
            // Mobile: 90% width inherited from the column; desktop:
            // 75%. ``min-w-0`` lets markdown children shrink
            // properly (long URLs / code flags would otherwise push
            // the bubble wider than its max-w).
            "min-w-0 rounded-lg px-4 py-3",
            // While editing, the user bubble swaps to the surface
            // colour so the textarea reads against a neutral
            // background (the accent fill would clash with the
            // composer aesthetic).
            isUser && !editing
              ? "bg-[var(--color-accent)] text-[var(--color-accent-fg)]"
              : "bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-fg)]",
            editing ? "w-[min(36rem,90vw)]" : "",
          ].join(" ")}
          title={editing ? undefined : new Date(message.ts).toLocaleString()}
        >
          {editing ? (
            <EditBubble
              draft={draft}
              onChange={setDraft}
              onSubmit={() => {
                const trimmed = draft.trim();
                if (!trimmed) return;
                if (trimmed === message.content) {
                  // No-op edit — just exit edit mode rather than
                  // burning a brain turn on the identical message.
                  setEditing(false);
                  return;
                }
                setEditing(false);
                onEdit?.(trimmed, message.attachments ?? []);
              }}
              onCancel={() => {
                setDraft(message.content);
                setEditing(false);
              }}
            />
          ) : (
            <>
              {/* Attachments above the text — matches Claude.ai /
                  ChatGPT and lets a long caption flow naturally
                  below the previews. */}
              {message.attachments && message.attachments.length > 0 && (
                <BubbleAttachments attachments={message.attachments} isUser={isUser} />
              )}
              {message.content ? (
                isUser ? (
                  <div className="whitespace-pre-wrap text-sm leading-relaxed">
                    {message.content}
                  </div>
                ) : (
                  <Markdown source={message.content} />
                )
              ) : (
                // Empty-content state on assistant bubbles:
                // streaming in progress but no tokens have landed
                // yet. Render the pulse inline so the user sees the
                // bubble is alive and a reply is on the way. User
                // bubbles never have empty content (the composer
                // blocks empty sends).
                !isUser && <InlinePulse />
              )}
            </>
          )}
        </div>
        {/* Action row — copy + edit (last user) / regenerate (last
            assistant). Hidden on desktop until the bubble is
            hovered (less visual noise during reading); always
            visible on touch since :hover is unreliable. */}
        {message.content && !editing && (
          <div
            className={[
              "flex items-center gap-1 px-1",
              "transition-opacity",
              "opacity-100 md:opacity-0 md:group-hover/bubble:opacity-100",
              "md:focus-within:opacity-100",
            ].join(" ")}
          >
            <CopyButton text={message.content} />
            {isLastUser && onEdit && (
              <ActionButton
                testId="bubble-edit"
                label="Edit"
                glyph="✎"
                onClick={() => {
                  setDraft(message.content);
                  setEditing(true);
                }}
              />
            )}
            {isLastAssistant && onRegenerate && (
              <ActionButton
                testId="bubble-regenerate"
                label="Regenerate"
                glyph="↻"
                onClick={onRegenerate}
              />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Compact action button used for Edit / Regenerate. Same visual
 *  treatment as CopyButton so the action row reads as a single
 *  toolbar even with three buttons. */
function ActionButton({
  testId,
  label,
  glyph,
  onClick,
}: {
  testId: string;
  label: string;
  glyph: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      aria-label={label}
      title={label}
      className={[
        "flex items-center gap-1 px-1.5 py-0.5 rounded",
        "text-[10px] uppercase tracking-wider",
        "transition-colors",
        "text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)]",
        "hover:bg-[var(--color-surface)]",
      ].join(" ")}
    >
      <span aria-hidden className="text-[11px] leading-none">{glyph}</span>
      <span>{label}</span>
    </button>
  );
}

/** Inline editor that replaces the user bubble's text content
 *  while the user revises their last message. ``Enter`` submits,
 *  ``Shift+Enter`` is a newline, ``Escape`` cancels — same key
 *  contract as the composer for muscle-memory consistency. */
function EditBubble({
  draft,
  onChange,
  onSubmit,
  onCancel,
}: {
  draft: string;
  onChange: (next: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  // Focus + place cursor at end on first render so the user can
  // start editing immediately. Re-running on draft changes would
  // fight with their cursor position; the empty deps array keeps
  // this to one-shot.
  useEffect(() => {
    const el = taRef.current;
    if (!el) return;
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
    // Auto-grow once on mount.
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div className="flex flex-col gap-2">
      <textarea
        ref={taRef}
        value={draft}
        data-testid="bubble-edit-textarea"
        onChange={(e) => {
          onChange(e.target.value);
          // Auto-grow as the draft changes.
          const el = e.currentTarget;
          el.style.height = "auto";
          el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") {
            e.preventDefault();
            onCancel();
            return;
          }
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            onSubmit();
          }
        }}
        rows={1}
        className={[
          "w-full resize-none bg-transparent outline-none",
          "text-sm leading-relaxed text-[var(--color-fg)]",
        ].join(" ")}
      />
      <div className="flex items-center justify-end gap-2">
        <button
          type="button"
          data-testid="bubble-edit-cancel"
          onClick={onCancel}
          className={[
            "px-2.5 py-1 rounded text-[11px] uppercase tracking-wider",
            "text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]",
            "hover:bg-[var(--color-base)] transition-colors",
          ].join(" ")}
        >
          Cancel
        </button>
        <button
          type="button"
          data-testid="bubble-edit-save"
          onClick={onSubmit}
          disabled={!draft.trim()}
          className={[
            "px-2.5 py-1 rounded text-[11px] uppercase tracking-wider",
            "bg-[var(--color-accent)] text-[var(--color-accent-fg)]",
            "hover:bg-[var(--color-accent-2)] hover:text-[var(--color-fg)]",
            "disabled:opacity-40 disabled:cursor-not-allowed",
            "transition-colors",
          ].join(" ")}
        >
          Save & resend
        </button>
      </div>
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  // ``copied`` flips true for ``COPIED_FEEDBACK_MS`` after a
  // successful write — drives the icon swap + label change. Stored
  // in state (not a ref) because it triggers a re-render.
  const [copied, setCopied] = useState(false);
  const timerRef = useRef<number | null>(null);

  // Cleanup the timer on unmount so a fast tap → unmount doesn't
  // call setState on a torn-down component.
  useEffect(() => {
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
    };
  }, []);

  const onCopy = useCallback(async () => {
    try {
      // navigator.clipboard requires HTTPS or localhost; the
      // dashboard always serves over HTTPS via Tailscale or
      // localhost for dev so this is safe to assume.
      await navigator.clipboard.writeText(text);
      setCopied(true);
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current);
      }
      timerRef.current = window.setTimeout(() => {
        setCopied(false);
        timerRef.current = null;
      }, COPIED_FEEDBACK_MS);
    } catch {
      // Clipboard write rejected (permission, http context,
      // browser block). Silent — there's no clean fallback that
      // works inside an event handler.
    }
  }, [text]);

  return (
    <button
      type="button"
      onClick={onCopy}
      aria-label={copied ? "Copied" : "Copy message"}
      title={copied ? "Copied" : "Copy"}
      className={[
        "flex items-center gap-1 px-1.5 py-0.5 rounded",
        "text-[10px] uppercase tracking-wider",
        "transition-colors",
        copied
          ? "text-[var(--color-accent)]"
          : "text-[var(--color-fg-dim)] hover:text-[var(--color-fg-2)]",
        "hover:bg-[var(--color-surface)]",
      ].join(" ")}
    >
      <span aria-hidden className="text-[11px] leading-none">
        {copied ? "✓" : "⎘"}
      </span>
      <span>{copied ? "Copied" : "Copy"}</span>
    </button>
  );
}

// How long the copy button stays in the "Copied" state before
// reverting to "Copy". 1.5s is the sweet spot — long enough to
// feel like clear feedback, short enough that a fast user can
// copy something else immediately after.
const COPIED_FEEDBACK_MS = 1500;

function BubbleAttachments({
  attachments,
  isUser,
}: {
  attachments: QueuedAttachment[];
  isUser: boolean;
}) {
  return (
    <div
      className={[
        "flex flex-wrap gap-2",
        attachments.length > 0 ? "mb-2" : "",
      ].join(" ")}
    >
      {attachments.map((a) => {
        const isImage = a.mime.startsWith("image/");
        if (isImage && a.previewUrl) {
          return (
            <img
              key={a.path}
              src={a.previewUrl}
              alt={a.name}
              className="max-w-full sm:max-w-xs max-h-64 rounded object-contain"
              loading="lazy"
            />
          );
        }
        return (
          <div
            key={a.path}
            className={[
              "flex items-center gap-2 px-2 py-1.5 rounded text-xs",
              isUser
                ? "bg-[var(--color-accent-2)] text-[var(--color-fg)]"
                : "bg-[var(--color-base)] border border-[var(--color-border)] text-[var(--color-fg-2)]",
            ].join(" ")}
          >
            <span aria-hidden>📄</span>
            <span className="truncate">{a.name}</span>
          </div>
        );
      })}
    </div>
  );
}

function InlinePulse() {
  // Three-dot pulse rendered inside an empty assistant bubble while
  // waiting for the first streamed chunk. Same visual language as
  // the standalone PendingBubble below — using the bubble itself as
  // the container avoids the layout shift that would happen when
  // the standalone-pending bubble is replaced by the streaming
  // bubble.
  return (
    <div className="flex gap-1.5 items-center text-[var(--color-fg-dim)] py-0.5">
      <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
      <span
        className="w-1.5 h-1.5 rounded-full bg-current animate-pulse"
        style={{ animationDelay: "0.15s" }}
      />
      <span
        className="w-1.5 h-1.5 rounded-full bg-current animate-pulse"
        style={{ animationDelay: "0.3s" }}
      />
    </div>
  );
}

function PendingBubble() {
  return (
    <div className="flex justify-start">
      <div className="rounded-lg px-4 py-3 bg-[var(--color-surface)] border border-[var(--color-border)]">
        <div className="flex gap-1.5 items-center text-[var(--color-fg-dim)]">
          <span className="w-1.5 h-1.5 rounded-full bg-current animate-pulse" />
          <span
            className="w-1.5 h-1.5 rounded-full bg-current animate-pulse"
            style={{ animationDelay: "0.15s" }}
          />
          <span
            className="w-1.5 h-1.5 rounded-full bg-current animate-pulse"
            style={{ animationDelay: "0.3s" }}
          />
        </div>
      </div>
    </div>
  );
}
