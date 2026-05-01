import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type { MemoryBlock, MemoryState } from "../lib/types";
import { relativeTime } from "../lib/format";
import { BudgetBar } from "../components/BudgetBar";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface MemoryPageProps {
  token: string;
  onAuthFail: () => void;
}

export function MemoryPage({ token, onAuthFail }: MemoryPageProps) {
  const [state, setState] = useState<MemoryState | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .memory(token)
      .then((data) => {
        if (!cancelled) setState(data);
      })
      .catch((exc: unknown) => {
        if (cancelled) return;
        if (exc instanceof ApiError && exc.status === 401) {
          onAuthFail();
          return;
        }
        setError(exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      cancelled = true;
    };
  }, [token, onAuthFail]);

  if (error) {
    return <ErrorRow message={error} />;
  }
  if (!state) {
    return <SkeletonGrid />;
  }

  return (
    <div className="grid gap-8 lg:grid-cols-2">
      <BlockColumn
        block={state.memory}
        title="Memory"
        subtitle="Your personal notes — what you've learned about the work"
      />
      <BlockColumn
        block={state.user}
        title="User profile"
        subtitle="Who the user is — preferences, role, working style"
      />
    </div>
  );
}

function BlockColumn({
  block,
  title,
  subtitle,
}: {
  block: MemoryBlock;
  title: string;
  subtitle: string;
}) {
  return (
    <Section
      title={title}
      trailing={`last modified ${relativeTime(block.mtime)}`}
    >
      <p className="text-xs text-[var(--color-fg-dim)] -mt-1.5">{subtitle}</p>
      <BudgetBar
        label="Budget"
        current={block.current}
        limit={block.limit}
        percent={block.percent}
      />
      {block.entries.length === 0 ? (
        <EmptyState
          glyph="§"
          title="No entries yet"
          hint={
            <>
              Vexis writes to this file when something is worth remembering
              across conversations. Edit on disk via{" "}
              <code className="font-data text-[var(--color-fg-2)]">
                vexis-mem
              </code>{" "}
              or let Vexis manage it himself.
            </>
          }
        />
      ) : (
        <ul className="space-y-3">
          {block.entries.map((entry, idx) => (
            <Card key={idx} delay={Math.min(idx, 6) * 40}>
              <div className="flex items-start gap-3 px-4 py-3">
                <span
                  aria-hidden
                  className="font-data text-[var(--color-accent)] text-sm leading-tight pt-0.5"
                >
                  §
                </span>
                <p className="font-data text-[12.5px] leading-relaxed text-[var(--color-fg)] whitespace-pre-wrap break-words flex-1">
                  {entry}
                </p>
              </div>
            </Card>
          ))}
        </ul>
      )}
      <p className="font-data text-[10px] text-[var(--color-fg-dim)] truncate">
        {block.path}
      </p>
    </Section>
  );
}

function SkeletonGrid() {
  return (
    <div className="grid gap-8 lg:grid-cols-2">
      {[0, 1].map((i) => (
        <div key={i} className="space-y-3">
          <div className="h-4 w-24 bg-[var(--color-border)] animate-pulse-slow" />
          <div className="h-2 w-full bg-[var(--color-border)] animate-pulse-slow" />
          <div className="h-20 hairline bg-[var(--color-surface)] animate-pulse-slow" />
          <div className="h-16 hairline bg-[var(--color-surface)] animate-pulse-slow" />
        </div>
      ))}
    </div>
  );
}

function ErrorRow({ message }: { message: string }) {
  return (
    <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
      Could not load memory: {message}
    </div>
  );
}
