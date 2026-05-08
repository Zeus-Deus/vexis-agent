import type { ReactNode } from "react";
import { classNames } from "../lib/format";

export interface TabDef {
  id: string;
  label: string;
  glyph?: ReactNode;
}

interface TabsProps {
  tabs: TabDef[];
  active: string;
  onChange: (id: string) => void;
  trailing?: ReactNode;
}

export function Tabs({ tabs, active, onChange, trailing }: TabsProps) {
  // Outer <nav> keeps the bottom border edge-to-edge so the divider
  // reads as a full-width hairline; inner container centers the tab
  // buttons against the same 1400px column the top bar and main use.
  return (
    <nav className="border-b border-[var(--color-border)]">
      <div className="max-w-[1400px] w-full mx-auto px-3 sm:px-5 flex items-stretch">
        <ul className="flex flex-1 items-stretch overflow-x-auto">
          {tabs.map((tab) => {
            const isActive = tab.id === active;
            return (
              <li key={tab.id}>
                <button
                  onClick={() => onChange(tab.id)}
                  aria-current={isActive ? "page" : undefined}
                  className={classNames(
                    "uppercase-tight text-[11px] px-4 py-3 transition-colors",
                    "focus:outline-none focus-visible:bg-[var(--color-surface)]",
                    isActive
                      ? "text-[var(--color-fg)] border-b-2 border-[var(--color-accent)] -mb-px"
                      : "text-[var(--color-fg-dim)] border-b-2 border-transparent -mb-px hover:text-[var(--color-fg-2)]",
                  )}
                >
                  <span className="flex items-center gap-2">
                    {tab.glyph && (
                      <span className="font-data text-[var(--color-fg-dim)]">
                        {tab.glyph}
                      </span>
                    )}
                    {tab.label}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
        {trailing && (
          <div className="flex items-center pl-4 text-xs text-[var(--color-fg-dim)] font-data">
            {trailing}
          </div>
        )}
      </div>
    </nav>
  );
}
