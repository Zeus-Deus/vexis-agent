import type { ButtonHTMLAttributes, ReactNode } from "react";
import { classNames } from "../lib/format";

type Variant = "primary" | "ghost" | "danger";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  children: ReactNode;
}

// Three variants. The accent fill is reserved for the *one* primary
// action on a page (force run, switch session). Ghost is the default
// for everything else, and danger is amber-bordered (no rainbow red —
// state is communicated through borders + body verb).
export function Button({
  variant = "ghost",
  size = "md",
  loading = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const isDisabled = disabled || loading;
  return (
    <button
      type="button"
      disabled={isDisabled}
      className={classNames(
        "inline-flex items-center justify-center gap-2 select-none",
        "uppercase-tight font-medium",
        "transition-colors transition-shadow duration-150",
        "focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--color-base)]",
        size === "sm"
          ? "px-2.5 py-1 text-[10px]"
          : "px-3.5 py-1.5 text-[11px]",
        variant === "primary" &&
          "bg-[var(--color-accent)] text-[var(--color-accent-fg)] hover:bg-[var(--color-accent-2)] disabled:bg-[var(--color-accent-2)] disabled:text-[var(--color-fg-dim)]",
        variant === "ghost" &&
          "hairline text-[var(--color-fg-2)] hover:text-[var(--color-fg)] hover:border-[var(--color-border-strong)] disabled:text-[var(--color-fg-dim)] disabled:hover:border-[var(--color-border)]",
        variant === "danger" &&
          "border border-[var(--color-error)]/40 text-[var(--color-error)] hover:bg-[var(--color-error)]/8 hover:border-[var(--color-error)]/60",
        "rounded-[2px]",
        className,
      )}
      {...rest}
    >
      {loading && <span className="font-data text-[10px]">…</span>}
      {children}
    </button>
  );
}
