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
        {/*
          ``overflow-x-auto`` lets the tab row scroll horizontally on
          narrow viewports. We pin ``overflow-y`` to ``hidden`` because
          the CSS spec promotes a ``visible`` cross-axis to ``auto``
          whenever the other axis is non-visible — and a 1px rounding
          delta between scrollHeight and clientHeight is enough for
          wheel and touch gestures to scroll the tab strip vertically,
          which on mobile reads as the tabs bouncing in and out of view.
          ``overscroll-contain`` keeps a horizontal swipe on the strip
          from chaining up to the page once the strip hits its edge.
        */}
        <ul className="flex flex-1 items-stretch overflow-x-auto overflow-y-hidden overscroll-contain">
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
