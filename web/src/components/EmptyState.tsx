import type { ReactNode } from "react";

interface EmptyStateProps {
  glyph?: string;
  title: string;
  hint?: ReactNode;
}

// Used wherever a list comes back empty. Small, dim, and explicit about
// why nothing is here so the user doesn't think the dashboard is broken.
export function EmptyState({ glyph = "·", title, hint }: EmptyStateProps) {
  return (
    <div className="hairline px-6 py-10 text-center bg-[var(--color-surface)]/50">
      <div
        aria-hidden
        className="text-[var(--color-fg-dim)] font-data text-2xl mb-2"
      >
        {glyph}
      </div>
      <p className="text-sm text-[var(--color-fg-2)]">{title}</p>
      {hint && (
        <p className="mt-2 text-xs text-[var(--color-fg-dim)] max-w-sm mx-auto leading-relaxed">
          {hint}
        </p>
      )}
    </div>
  );
}
