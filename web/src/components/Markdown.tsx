import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

interface MarkdownProps {
  source: string;
  className?: string;
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
        "text-sm leading-relaxed text-[var(--color-fg-2)]",
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
        "[&_a]:text-[var(--color-accent)] [&_a]:underline-offset-2 hover:[&_a]:underline",
        "[&_code]:font-data [&_code]:text-[12.5px] [&_code]:px-1 [&_code]:py-[1px] [&_code]:bg-[var(--color-base)] [&_code]:border [&_code]:border-[var(--color-border)] [&_code]:text-[var(--color-fg)]",
        "[&_pre]:font-data [&_pre]:text-[12.5px] [&_pre]:p-3 [&_pre]:bg-[var(--color-base)] [&_pre]:border [&_pre]:border-[var(--color-border)] [&_pre]:overflow-x-auto",
        "[&_pre_code]:bg-transparent [&_pre_code]:border-0 [&_pre_code]:p-0",
        "[&_blockquote]:border-l-2 [&_blockquote]:border-[var(--color-accent)] [&_blockquote]:pl-4 [&_blockquote]:text-[var(--color-fg-2)] [&_blockquote]:italic",
        "[&_hr]:border-t [&_hr]:border-[var(--color-border)] [&_hr]:my-4",
        "[&_table]:font-data [&_table]:text-[12px] [&_table]:w-full [&_table]:border-collapse",
        "[&_th]:text-left [&_th]:uppercase-tight [&_th]:text-[10px] [&_th]:text-[var(--color-fg-dim)] [&_th]:py-1.5 [&_th]:border-b [&_th]:border-[var(--color-border)]",
        "[&_td]:py-1.5 [&_td]:border-b [&_td]:border-[var(--color-border)]/50",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
    </div>
  );
}
