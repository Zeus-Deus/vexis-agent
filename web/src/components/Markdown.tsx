import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownProps {
  source: string;
  className?: string;
}

/** Wraps a fenced ``<pre>`` code block in a relatively-positioned
 *  shell with a Copy button overlay. The button reads
 *  ``pre.textContent`` at click time so we don't have to walk
 *  ReactMarkdown's nested children tree to flatten the source —
 *  the DOM already has the right text. ``aria-live`` on the label
 *  span announces the "Copied" flip for screen readers.
 *
 *  Why a separate component (not inline JSX in ``components.pre``):
 *  hooks. The copy state + revert timer need ``useRef`` /
 *  ``useState``, which can't sit inside a ReactMarkdown component
 *  override that's redefined per render — would leak timers. */
function CodeBlockWithCopy(
  { children, ...rest }: React.HTMLAttributes<HTMLPreElement>,
) {
  const preRef = useRef<HTMLPreElement | null>(null);
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    const text = preRef.current?.textContent ?? "";
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      // Browsers without clipboard API (rare) — fail silently.
      // Per-bubble copy button has the same posture; consistency
      // matters more than a one-off error path here.
    }
  };
  return (
    <div className="relative group not-prose">
      <pre ref={preRef} {...rest}>
        {children}
      </pre>
      <button
        type="button"
        onClick={onCopy}
        data-testid="codeblock-copy"
        aria-label={copied ? "Copied to clipboard" : "Copy code"}
        title={copied ? "Copied" : "Copy"}
        className={[
          // Tucked top-right, only visible on hover/focus on
          // desktop — mobile shows it always (no hover state).
          // ``opacity-0 group-hover:opacity-100`` handles the
          // hover; ``focus-within`` keeps it visible while the
          // button itself has focus (keyboard nav).
          "absolute top-1.5 right-1.5 z-10",
          "px-2 py-0.5 text-[10px] uppercase tracking-wider",
          "rounded border transition-all",
          "bg-[var(--color-surface)] border-[var(--color-border-strong)]",
          "text-[var(--color-fg-dim)] hover:text-[var(--color-fg)]",
          "hover:bg-[var(--color-base)]",
          "md:opacity-0 md:group-hover:opacity-100 md:focus:opacity-100",
          copied ? "text-[var(--color-accent)]" : "",
        ].join(" ")}
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

// Markdown renderer with a dialed-in dark style. Used for SKILL.md
// bodies and curator REPORT.md.
//
// We don't apply Tailwind's typography plugin — its prose styles are
// generic and don't match the rest of the dashboard. Instead the
// classes here speak directly to the elements ReactMarkdown emits.
export function Markdown({ source, className }: MarkdownProps) {
  return (
    <div
      className={[
        // ``min-w-0`` lets this markdown root shrink below its
        // intrinsic width when it's a flex/grid child (the chat
        // bubble). ``break-words`` is the wrap policy for long
        // text/URLs inside paragraphs.
        "text-sm leading-relaxed text-[var(--color-fg-2)] min-w-0 break-words",
        "[&>*]:mb-3 [&>*:last-child]:mb-0",
        "[&_h1]:uppercase-tight [&_h1]:text-xs [&_h1]:text-[var(--color-fg)] [&_h1]:mt-5 [&_h1]:mb-2",
        "[&_h2]:uppercase-tight [&_h2]:text-xs [&_h2]:text-[var(--color-fg)] [&_h2]:mt-4 [&_h2]:mb-2",
        "[&_h3]:uppercase-tight [&_h3]:text-[10px] [&_h3]:text-[var(--color-fg-2)] [&_h3]:mt-3 [&_h3]:mb-1",
        "[&_p]:mb-3",
        "[&_ul]:list-disc [&_ul]:ml-5 [&_ul]:mb-3 [&_ul]:space-y-1",
        "[&_ol]:list-decimal [&_ol]:ml-5 [&_ol]:mb-3 [&_ol]:space-y-1",
        "[&_li]:text-[var(--color-fg-2)]",
        "[&_strong]:text-[var(--color-fg)] [&_strong]:font-semibold",
        "[&_em]:text-[var(--color-fg)] [&_em]:italic",
        "[&_a]:text-[var(--color-accent)] [&_a]:underline-offset-2 hover:[&_a]:underline [&_a]:break-all",
        // ``break-all`` on inline code: long flags / paths / shell
        // commands that would otherwise force the bubble wider than
        // the screen now wrap mid-token. Block code (``<pre>``) keeps
        // its no-wrap + horizontal-scroll behaviour because line-
        // breaking destroys command-line meaning.
        "[&_code]:font-data [&_code]:text-[12.5px] [&_code]:px-1 [&_code]:py-[1px] [&_code]:bg-[var(--color-base)] [&_code]:border [&_code]:border-[var(--color-border)] [&_code]:text-[var(--color-fg)] [&_code]:break-all",
        // ``max-w-full`` + ``overflow-x-auto`` lets a wide block of
        // code scroll horizontally inside the bubble without pushing
        // the bubble wider than its container.
        "[&_pre]:font-data [&_pre]:text-[12.5px] [&_pre]:p-3 [&_pre]:bg-[var(--color-base)] [&_pre]:border [&_pre]:border-[var(--color-border)] [&_pre]:overflow-x-auto [&_pre]:max-w-full",
        "[&_pre_code]:bg-transparent [&_pre_code]:border-0 [&_pre_code]:p-0 [&_pre_code]:break-normal [&_pre_code]:whitespace-pre",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-[var(--color-accent)] [&_blockquote]:pl-4 [&_blockquote]:text-[var(--color-fg-2)] [&_blockquote]:italic",
        "[&_hr]:border-t [&_hr]:border-[var(--color-border)] [&_hr]:my-4",
        // Table wrapper would be ideal but ReactMarkdown emits raw
        // ``<table>`` — the parent bubble's ``min-w-0`` lets us at
        // least allow horizontal scroll when needed.
        "[&_table]:font-data [&_table]:text-[12px] [&_table]:w-full [&_table]:border-collapse",
        "[&_th]:text-left [&_th]:uppercase-tight [&_th]:text-[10px] [&_th]:text-[var(--color-fg-dim)] [&_th]:py-1.5 [&_th]:border-b [&_th]:border-[var(--color-border)]",
        "[&_td]:py-1.5 [&_td]:border-b [&_td]:border-[var(--color-border)]/50",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // Override fenced code blocks (``<pre>``) to add the copy
          // affordance. Inline code (``<code>`` not inside ``<pre>``)
          // stays untouched — copy buttons on inline code would be
          // visual noise.
          pre: CodeBlockWithCopy,
        }}
      >
        {source}
      </ReactMarkdown>
    </div>
  );
}
