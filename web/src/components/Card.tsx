import type { CSSProperties, ElementType, ReactNode } from "react";
import { classNames } from "../lib/format";

interface CardProps {
  children: ReactNode;
  className?: string;
  as?: ElementType;
  delay?: number;
}

// Surface primitive. Hairline border, near-flat surface, no rounded
// corners by default — radii are reserved for buttons and badges where
// they earn their keep. The optional `delay` (ms) drives the staggered
// page-load reveal so a list of cards rises in sequence.
export function Card({
  children,
  className,
  as: Tag = "div",
  delay,
}: CardProps) {
  const style: CSSProperties | undefined =
    delay !== undefined ? { animationDelay: `${delay}ms` } : undefined;
  return (
    <Tag
      className={classNames(
        "hairline bg-[var(--color-surface)] anim-rise",
        className,
      )}
      style={style}
    >
      {children}
    </Tag>
  );
}

interface SectionProps {
  title: string;
  trailing?: ReactNode;
  children: ReactNode;
  className?: string;
}

// Section wraps a labeled block. The label is uppercase letter-spaced
// IBM Plex — chrome typography. Body is whatever the children render.
export function Section({ title, trailing, children, className }: SectionProps) {
  return (
    <section className={classNames("space-y-3", className)}>
      <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 className="uppercase-tight text-xs text-[var(--color-fg-2)]">
          {title}
        </h2>
        {trailing && (
          <div className="text-xs text-[var(--color-fg-dim)] font-data">
            {trailing}
          </div>
        )}
      </header>
      {children}
    </section>
  );
}
