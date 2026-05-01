import { clampPercent, formatNumber } from "../lib/format";

interface BudgetBarProps {
  label: string;
  current: number;
  limit: number;
  percent: number;
}

// A thin horizontal bar showing memory budget usage. Mirrors the
// ``[N% — chars/cap]`` line the brain sees in its system prompt so the
// dashboard reads as the same artefact, just rendered for human eyes.
//
// The fill switches to amber when usage exceeds 70% as a soft warning
// — beyond that point the user might want to consolidate entries.
export function BudgetBar({ label, current, limit, percent }: BudgetBarProps) {
  const p = clampPercent(percent);
  const tone = p >= 85 ? "warn" : p >= 70 ? "accent" : "neutral";
  return (
    <div>
      <div className="flex items-baseline justify-between font-data text-[11px]">
        <span className="text-[var(--color-fg-2)]">{label}</span>
        <span className="text-[var(--color-fg-dim)]">
          <span className="text-[var(--color-fg)]">{p}%</span>{" "}
          <span aria-hidden>·</span> {formatNumber(current)}/
          {formatNumber(limit)} chars
        </span>
      </div>
      <div
        className="mt-1.5 h-1 bg-[var(--color-border)]/60 overflow-hidden"
        role="progressbar"
        aria-valuenow={p}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label={`${label} usage`}
      >
        <div
          className="h-full transition-[width] duration-700 ease-out"
          style={{
            width: `${Math.max(p, 1)}%`,
            backgroundColor:
              tone === "warn"
                ? "var(--color-warn)"
                : tone === "accent"
                  ? "var(--color-accent)"
                  : "var(--color-fg-dim)",
          }}
        />
      </div>
    </div>
  );
}
