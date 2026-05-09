import { useEffect, useRef } from "react";
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
}

export function ChatMessages({ messages, pending }: ChatMessagesProps) {
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

  return (
    <div className="flex-1 overflow-y-auto px-4 sm:px-6 py-6">
      <div className="max-w-3xl mx-auto flex flex-col gap-5">
        {messages.map((m, i) => (
          <Bubble key={i} message={m} />
        ))}
        {pending && <PendingBubble />}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function Bubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";

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
        "flex gap-3",
        isUser ? "justify-end" : "justify-start",
      ].join(" ")}
    >
      <div
        className={[
          // Mobile: 90% to give long messages more reading width on
          // narrow screens. Desktop: tighter so bubbles don't span
          // edge-to-edge. ``min-w-0`` lets markdown children shrink
          // properly (some content like long URLs / code flags
          // would otherwise push the bubble wider than max-w).
          "max-w-[90%] sm:max-w-[75%] min-w-0 rounded-lg px-4 py-3",
          isUser
            ? "bg-[var(--color-accent)] text-[var(--color-accent-fg)]"
            : "bg-[var(--color-surface)] border border-[var(--color-border)] text-[var(--color-fg)]",
        ].join(" ")}
        title={new Date(message.ts).toLocaleString()}
      >
        {/* Attachments above the text — matches Claude.ai / ChatGPT
            and lets a long caption flow naturally below the previews. */}
        {message.attachments && message.attachments.length > 0 && (
          <BubbleAttachments attachments={message.attachments} isUser={isUser} />
        )}
        {message.content && (
          isUser ? (
            <div className="whitespace-pre-wrap text-sm leading-relaxed">
              {message.content}
            </div>
          ) : (
            <Markdown source={message.content} />
          )
        )}
      </div>
    </div>
  );
}

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
