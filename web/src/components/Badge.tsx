import type { ReactNode } from "react";
import { classNames } from "../lib/format";

type Tone =
  | "neutral"
  | "active"
  | "stale"
  | "archived"
  | "warn"
  | "error"
  | "accent"
  | "subtle";

interface BadgeProps {
  tone?: Tone;
  children: ReactNode;
  className?: string;
  glyph?: ReactNode;
}

// Compact status pill. Glyph (●, ○, ▲, ■) reads as the primary visual
// signal so the eye picks state from the first character; the label
// is supporting context. Mono throughout for crisp alignment in tables.
export function Badge({ tone = "neutral", children, className, glyph }: BadgeProps) {
  return (
    <span
      className={classNames(
        "inline-flex items-center gap-1.5 px-1.5 py-[2px] font-data text-[10px] tracking-wider uppercase",
        "border rounded-[2px]",
        tone === "neutral" &&
          "border-[var(--color-border)] text-[var(--color-fg-2)]",
        tone === "active" &&
          "border-[var(--color-accent)]/40 text-[var(--color-accent)]",
        tone === "stale" &&
          "border-[var(--color-state-stale)]/40 text-[var(--color-state-stale)]",
        tone === "archived" &&
          "border-[var(--color-state-archived)]/40 text-[var(--color-state-archived)]",
        tone === "warn" &&
          "border-[var(--color-warn)]/40 text-[var(--color-warn)]",
        tone === "error" &&
          "border-[var(--color-error)]/40 text-[var(--color-error)]",
        tone === "accent" &&
          "border-[var(--color-accent)]/40 text-[var(--color-accent)] bg-[var(--color-accent)]/[0.06]",
        tone === "subtle" &&
          "border-[var(--color-border)] text-[var(--color-fg-dim)]",
        className,
      )}
    >
      {glyph && <span aria-hidden>{glyph}</span>}
      <span>{children}</span>
    </span>
  );
}
