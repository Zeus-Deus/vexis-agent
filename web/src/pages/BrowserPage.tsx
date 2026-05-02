import { useCallback, useEffect, useState, type ReactNode } from "react";
import { api, ApiError, browserScreenshotUrl } from "../lib/api";
import type {
  BrowserConfigSnapshot,
  BrowserNavigationEntry,
  BrowserProfileInfo,
  BrowserScreenshotEntry,
  BrowserSessionInfo,
  BrowserState,
} from "../lib/types";
import { formatBytes, relativeTime, uptime } from "../lib/format";
import { Badge } from "../components/Badge";
import { Button } from "../components/Button";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface BrowserPageProps {
  token: string;
  onAuthFail: () => void;
}

const POLL_INTERVAL_MS = 5000;

export function BrowserPage({ token, onAuthFail }: BrowserPageProps) {
  const [state, setState] = useState<BrowserState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<{
    open: boolean;
    recycle: boolean;
  }>({ open: false, recycle: false });
  const [actionMessage, setActionMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const data = await api.browser(token);
      setState(data);
      setError(null);
      return data;
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return null;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
      return null;
    }
  }, [token, onAuthFail]);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;

    async function loop() {
      if (cancelled) return;
      await refresh();
      if (!cancelled) {
        timer = window.setTimeout(loop, POLL_INTERVAL_MS);
      }
    }
    loop();
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refresh]);

  const handleOpenBlank = useCallback(async () => {
    setPending((p) => ({ ...p, open: true }));
    setActionMessage(null);
    try {
      const result = await api.browserOpenBlank(token);
      if (result.ok) {
        setActionMessage("Opened about:blank — drive the headed window manually.");
      } else {
        setActionMessage(
          `Open failed: ${result.error ?? "unknown error"}${
            result.hint ? ` — ${result.hint}` : ""
          }`,
        );
      }
      await refresh();
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) onAuthFail();
      else setActionMessage("Open failed.");
    } finally {
      setPending((p) => ({ ...p, open: false }));
    }
  }, [token, refresh, onAuthFail]);

  const handleRecycle = useCallback(async () => {
    if (
      !window.confirm(
        "Recycle the browser session? Any unsaved page state will be lost. " +
          "Cookies and localStorage stay on disk.",
      )
    ) {
      return;
    }
    setPending((p) => ({ ...p, recycle: true }));
    setActionMessage(null);
    try {
      const result = await api.browserRecycle(token);
      setActionMessage(
        result.was_running
          ? "Session recycled."
          : "No session was running — nothing to recycle.",
      );
      await refresh();
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 401) onAuthFail();
      else setActionMessage("Recycle failed.");
    } finally {
      setPending((p) => ({ ...p, recycle: false }));
    }
  }, [token, refresh, onAuthFail]);

  if (error && !state) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load browser state: {error}
      </div>
    );
  }
  if (!state) {
    return <BrowserSkeleton />;
  }

  return (
    <div className="space-y-8">
      <SessionHeader
        session={state.session}
        pending={pending}
        actionMessage={actionMessage}
        onOpenBlank={handleOpenBlank}
        onRecycle={handleRecycle}
      />
      <div className="grid gap-8 lg:grid-cols-2">
        <RecentNavigations entries={state.recent_navigations} />
        <ProfileCard profile={state.profile} />
      </div>
      <ConfigCard config={state.config} />
      <RecentScreenshots
        token={token}
        entries={state.recent_screenshots}
      />
    </div>
  );
}

function SessionHeader({
  session,
  pending,
  actionMessage,
  onOpenBlank,
  onRecycle,
}: {
  session: BrowserSessionInfo;
  pending: { open: boolean; recycle: boolean };
  actionMessage: string | null;
  onOpenBlank: () => void;
  onRecycle: () => void;
}) {
  const running = session.state === "running";
  const sinceStart =
    running && session.started_at
      ? Math.max(
          0,
          (Date.now() - new Date(session.started_at).getTime()) / 1000,
        )
      : null;
  return (
    <Card>
      <div className="px-5 py-4 space-y-3">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {running ? (
            <Badge tone="active" glyph="●">
              running
            </Badge>
          ) : (
            <Badge tone="subtle" glyph="○">
              not started
            </Badge>
          )}
          <Badge tone={session.headless ? "subtle" : "neutral"}>
            {session.headless ? "headless" : "headed"}
          </Badge>
          <Badge
            tone={session.attach_mode === "cdp-attach" ? "accent" : "subtle"}
          >
            {session.attach_mode}
          </Badge>
          {sinceStart !== null && (
            <span className="font-data text-[12.5px] text-[var(--color-fg-2)]">
              <span className="text-[var(--color-fg-dim)] uppercase-tight text-[10px] mr-2">
                up
              </span>
              {uptime(sinceStart)}
            </span>
          )}
          {running && session.last_activity_at && (
            <span className="font-data text-[11px] text-[var(--color-fg-dim)]">
              last action {relativeTime(session.last_activity_at)}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            <Button
              variant={running ? "ghost" : "primary"}
              loading={pending.open}
              onClick={onOpenBlank}
            >
              {running ? "Open about:blank" : "Open browser"}
            </Button>
            {running && (
              <Button
                variant="danger"
                loading={pending.recycle}
                onClick={onRecycle}
              >
                Recycle session
              </Button>
            )}
          </div>
        </div>
        {running ? (
          <CurrentPageRow
            url={session.current_url}
            title={session.current_title}
          />
        ) : (
          <p className="font-data text-[12px] text-[var(--color-fg-dim)]">
            No browser session yet. The first navigate launches Chromium —
            either via the brain (Vexis on Telegram) or via the action
            above for a manual login.
          </p>
        )}
        {actionMessage && (
          <p className="font-data text-[12px] text-[var(--color-accent)]">
            {actionMessage}
          </p>
        )}
      </div>
    </Card>
  );
}

function CurrentPageRow({
  url,
  title,
}: {
  url: string | null;
  title: string | null;
}) {
  return (
    <div className="flex items-baseline gap-3 min-w-0">
      <span
        aria-hidden
        className="font-data text-[var(--color-accent)] leading-none translate-y-[1px]"
      >
        ▍
      </span>
      <div className="min-w-0 flex-1">
        <p
          className="font-data text-[12.5px] text-[var(--color-fg)] truncate"
          title={url ?? undefined}
        >
          {url ?? "(unknown URL)"}
        </p>
        {title && (
          <p className="text-xs text-[var(--color-fg-dim)] truncate">
            {title}
          </p>
        )}
      </div>
    </div>
  );
}

function RecentNavigations({
  entries,
}: {
  entries: BrowserNavigationEntry[];
}) {
  return (
    <Section
      title="Recent navigations"
      trailing={`${entries.length} of 10`}
    >
      {entries.length === 0 ? (
        <EmptyState
          glyph="◐"
          title="No navigations yet"
          hint="Vexis hasn't opened anything in this daemon process yet — the ring buffer fills as he browses."
        />
      ) : (
        <ul className="space-y-2">
          {entries.map((entry, idx) => (
            <Card key={`${entry.at}-${idx}`} delay={Math.min(idx, 6) * 30}>
              <div className="px-4 py-2.5 flex items-baseline gap-3">
                <span
                  aria-hidden
                  className="font-data text-[var(--color-accent)] leading-none"
                >
                  §
                </span>
                <span
                  className="font-data text-[12.5px] text-[var(--color-fg)] truncate flex-1 min-w-0"
                  title={entry.url}
                >
                  {entry.url}
                </span>
                <span className="font-data text-[11px] text-[var(--color-fg-dim)]">
                  {relativeTime(entry.at)}
                </span>
              </div>
            </Card>
          ))}
        </ul>
      )}
    </Section>
  );
}

function ProfileCard({ profile }: { profile: BrowserProfileInfo }) {
  const sizeLine =
    profile.size_bytes === null ? "—" : formatBytes(profile.size_bytes);
  const cookieLine =
    profile.cookie_count === null
      ? "—"
      : profile.cookie_count.toLocaleString();
  return (
    <Section title="Profile">
      <Card>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-5 gap-y-2 px-5 py-4 font-data text-[12.5px]">
          <Term>Path</Term>
          <Definition>
            <span className="break-all">{profile.path}</span>
            {!profile.exists && (
              <Badge tone="subtle" className="ml-2">
                not yet on disk
              </Badge>
            )}
          </Definition>
          <Term>Size</Term>
          <Definition>
            <span className="text-[var(--color-fg)]">{sizeLine}</span>
            {profile.size_as_of && (
              <span className="ml-2 text-[var(--color-fg-dim)] text-[11px]">
                as of {relativeTime(profile.size_as_of)}
              </span>
            )}
          </Definition>
          <Term>Cookies</Term>
          <Definition>
            <span className="text-[var(--color-fg)]">{cookieLine}</span>
            {profile.cookie_count === null && (
              <span className="ml-2 text-[var(--color-fg-dim)] text-[11px]">
                cookie db unreadable
              </span>
            )}
          </Definition>
        </dl>
      </Card>
    </Section>
  );
}

function ConfigCard({ config }: { config: BrowserConfigSnapshot }) {
  const rows: [string, string][] = [
    ["profiles_dir", config.profiles_dir],
    ["default_profile", config.default_profile],
    ["headless", String(config.headless)],
    ["inactivity_timeout", `${config.inactivity_timeout_seconds}s`],
    ["action_timeout", `${config.action_timeout_seconds}s`],
    ["chromium_path", config.chromium_path ?? "(default)"],
    ["cdp_url", config.cdp_url ?? "(none)"],
    ["screenshot_include_base64", String(config.screenshot_include_base64)],
  ];
  return (
    <Section title="Config snapshot">
      <Card>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-5 gap-y-2 px-5 py-4 font-data text-[12.5px]">
          {rows.map(([k, v]) => (
            <ConfigRow key={k} label={k} value={v} />
          ))}
        </dl>
        <p className="px-5 pb-3 font-data text-[10.5px] text-[var(--color-fg-dim)]">
          edit ~/.vexis/config.yaml [browser] and restart the daemon to
          change.
        </p>
      </Card>
    </Section>
  );
}

function ConfigRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <Term>{label}</Term>
      <Definition>
        <span className="text-[var(--color-fg)] break-all">{value}</span>
      </Definition>
    </>
  );
}

function Term({ children }: { children: ReactNode }) {
  return (
    <dt className="text-[var(--color-fg-dim)] uppercase-tight text-[10px] self-baseline">
      {children}
    </dt>
  );
}

function Definition({ children }: { children: ReactNode }) {
  return (
    <dd className="text-[var(--color-fg-2)] flex items-baseline flex-wrap gap-x-2">
      {children}
    </dd>
  );
}

function RecentScreenshots({
  token,
  entries,
}: {
  token: string;
  entries: BrowserScreenshotEntry[];
}) {
  return (
    <Section
      title="Recent screenshots"
      trailing={`${entries.length} shown`}
    >
      {entries.length === 0 ? (
        <EmptyState
          glyph="◐"
          title="No screenshots yet"
          hint="vexis-browse screenshot writes PNGs to ~/vexis-workspace/browser/screenshots/."
        />
      ) : (
        <Card>
          <ul className="flex flex-wrap gap-3 px-4 py-4">
            {entries.map((entry, idx) => (
              <ScreenshotTile
                key={entry.filename}
                token={token}
                entry={entry}
                delay={Math.min(idx, 5) * 30}
              />
            ))}
          </ul>
        </Card>
      )}
    </Section>
  );
}

function ScreenshotTile({
  token,
  entry,
  delay,
}: {
  token: string;
  entry: BrowserScreenshotEntry;
  delay: number;
}) {
  const url = browserScreenshotUrl(token, entry.filename);
  const time = entry.mtime
    ? new Date(entry.mtime).toLocaleTimeString(undefined, {
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";
  return (
    <li
      className="anim-rise flex flex-col gap-1.5"
      style={{ animationDelay: `${delay}ms` }}
    >
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="block w-[140px] aspect-[16/10] hairline bg-[var(--color-base)] overflow-hidden hover:border-[var(--color-accent)]/60 transition-colors"
        title={`${entry.filename} — open fullsize`}
      >
        <img
          src={url}
          alt={entry.filename}
          loading="lazy"
          className="w-full h-full object-cover"
        />
      </a>
      <div className="flex items-baseline justify-between font-data text-[10.5px] text-[var(--color-fg-dim)]">
        <span>{time}</span>
        <span>{formatBytes(entry.size_bytes)}</span>
      </div>
    </li>
  );
}

function BrowserSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-20 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <div
            key={i}
            className="h-40 hairline bg-[var(--color-surface)] animate-pulse-slow"
          />
        ))}
      </div>
      <div className="h-48 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-44 hairline bg-[var(--color-surface)] animate-pulse-slow" />
    </div>
  );
}
