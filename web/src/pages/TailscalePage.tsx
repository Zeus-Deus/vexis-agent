import { useCallback, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import type {
  TailscaleFunnel,
  TailscaleNode,
  TailscalePeer,
  TailscaleServe,
  TailscaleStatus,
} from "../lib/types";
import { classNames, relativeTime } from "../lib/format";
import { Badge } from "../components/Badge";
import { Card, Section } from "../components/Card";
import { EmptyState } from "../components/EmptyState";

interface TailscalePageProps {
  token: string;
  onAuthFail: () => void;
}

// Serves don't change at second resolution, so polling at 5s would be
// wasteful — the dashboard's own data refresh cycle is the bottleneck,
// not tailscaled. 30s matches what a human would notice as "live."
const POLL_INTERVAL_MS = 30000;

export function TailscalePage({ token, onAuthFail }: TailscalePageProps) {
  const [state, setState] = useState<TailscaleStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [peersOpen, setPeersOpen] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await api.tailscale(token);
      setState(data);
      setError(null);
    } catch (exc: unknown) {
      if (exc instanceof ApiError && exc.status === 401) {
        onAuthFail();
        return;
      }
      setError(exc instanceof Error ? exc.message : String(exc));
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

  if (error) {
    return (
      <div className="hairline px-4 py-3 text-sm text-[var(--color-error)] bg-[var(--color-surface)]">
        Could not load Tailscale status: {error}
      </div>
    );
  }
  if (!state) {
    return <TailscaleSkeleton />;
  }

  return (
    <div className="space-y-8">
      {state.error && <ErrorBanner message={state.error} />}
      <NodePanel node={state.node} />
      <ServesPanel serves={state.serves} />
      <FunnelsPanel funnels={state.funnels} />
      <PeersPanel
        peers={state.peers}
        open={peersOpen}
        onToggle={() => setPeersOpen((v) => !v)}
      />
    </div>
  );
}

// ---- Error banner ------------------------------------------------

function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="hairline px-4 py-3 bg-[var(--color-surface)]">
      <p className="font-data text-[12px] text-[var(--color-error)]">
        <span className="uppercase-tight text-[10px] mr-2">tailscale</span>
        {message}
      </p>
      <p className="mt-1 font-data text-[10.5px] text-[var(--color-fg-dim)]">
        The page will retry automatically every {Math.round(POLL_INTERVAL_MS / 1000)}s.
      </p>
    </div>
  );
}

// ---- This-node panel --------------------------------------------

function NodePanel({ node }: { node: TailscaleNode | null }) {
  return (
    <Section title="This node">
      <Card>
        <div className="px-5 py-4">
          {node === null ? (
            <p className="font-data text-[11.5px] text-[var(--color-fg-dim)]">
              Tailscale identity unavailable.
            </p>
          ) : (
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-2">
              <KV label="hostname" value={node.hostname || "—"} mono />
              <KV label="ip" value={node.ip || "—"} mono />
              <div className="ml-auto">
                {node.online ? (
                  <Badge tone="active" glyph="●">
                    online
                  </Badge>
                ) : (
                  <Badge tone="stale" glyph="○">
                    offline
                  </Badge>
                )}
              </div>
            </div>
          )}
        </div>
      </Card>
    </Section>
  );
}

// ---- Serves panel -----------------------------------------------

function ServesPanel({ serves }: { serves: TailscaleServe[] }) {
  return (
    <Section
      title="Active serves"
      trailing={`${serves.length} mount${serves.length === 1 ? "" : "s"}`}
    >
      {serves.length === 0 ? (
        <EmptyState
          glyph="·"
          title="No active Tailscale serves."
          hint={
            <>
              Run{" "}
              <code className="font-data text-[var(--color-fg)]">
                tailscale serve --bg &lt;target&gt;
              </code>{" "}
              to expose a local service to your tailnet.
            </>
          }
        />
      ) : (
        <Card>
          <ul className="divide-y divide-[var(--color-border)]">
            {serves.map((s, i) => (
              <ServeRow key={`${s.port}${s.mount}-${i}`} serve={s} />
            ))}
          </ul>
        </Card>
      )}
    </Section>
  );
}

function ServeRow({ serve }: { serve: TailscaleServe }) {
  return (
    <li className="px-5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-data text-[12px]">
        <span className="text-[var(--color-fg-2)] tabular-nums">
          :{serve.port}
        </span>
        <span className="text-[var(--color-fg)]">{serve.mount}</span>
        <span className="text-[var(--color-fg-dim)]">→</span>
        <span className="text-[var(--color-fg)]">{serve.target}</span>
        <div className="ml-auto flex items-baseline gap-2">
          {serve.tls && (
            <Badge tone="active" glyph="◈">
              tls
            </Badge>
          )}
          {serve.funnel ? (
            <Badge tone="warn" glyph="!">
              funnel
            </Badge>
          ) : (
            <Badge tone="subtle">private</Badge>
          )}
        </div>
      </div>
    </li>
  );
}

// ---- Funnels panel ----------------------------------------------

function FunnelsPanel({ funnels }: { funnels: TailscaleFunnel[] }) {
  return (
    <Section
      title="Active funnels"
      trailing={`${funnels.length} mount${funnels.length === 1 ? "" : "s"}`}
    >
      {funnels.length === 0 ? (
        <EmptyState
          glyph="·"
          title="No active funnels."
          hint="Funnels expose services to the public internet. Use sparingly."
        />
      ) : (
        <Card>
          <ul className="divide-y divide-[var(--color-border)]">
            {funnels.map((f, i) => (
              <FunnelRow key={`${f.port}${f.mount}-${i}`} funnel={f} />
            ))}
          </ul>
        </Card>
      )}
    </Section>
  );
}

function FunnelRow({ funnel }: { funnel: TailscaleFunnel }) {
  return (
    <li className="px-5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-data text-[12px]">
        <span className="text-[var(--color-fg-2)] tabular-nums">
          :{funnel.port}
        </span>
        <span className="text-[var(--color-fg)]">{funnel.mount}</span>
        <span className="text-[var(--color-fg-dim)]">→</span>
        <span className="text-[var(--color-fg)]">{funnel.target}</span>
        <div className="ml-auto flex items-baseline gap-2">
          {funnel.tls && (
            <Badge tone="active" glyph="◈">
              tls
            </Badge>
          )}
          <Badge tone="warn" glyph="!">
            public
          </Badge>
        </div>
      </div>
    </li>
  );
}

// ---- Peers panel ------------------------------------------------

function PeersPanel({
  peers,
  open,
  onToggle,
}: {
  peers: TailscalePeer[];
  open: boolean;
  onToggle: () => void;
}) {
  const onlineCount = peers.filter((p) => p.online).length;
  return (
    <Section
      title="Peer devices"
      trailing={
        peers.length === 0
          ? "no peers"
          : `${onlineCount} online · ${peers.length} total`
      }
    >
      <Card>
        <button
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          className={classNames(
            "w-full text-left px-5 py-3 flex items-center justify-between",
            "font-data text-[11.5px] text-[var(--color-fg-2)]",
            "hover:bg-[var(--color-surface)] focus:outline-none",
            "focus-visible:bg-[var(--color-surface)]",
          )}
        >
          <span>
            {open ? "Hide" : "Show"} peer list ({peers.length})
          </span>
          <span
            aria-hidden
            className={classNames(
              "font-data text-[var(--color-fg-dim)] transition-transform",
              open && "rotate-90",
            )}
          >
            ▸
          </span>
        </button>
        {open && (
          <div className="border-t border-[var(--color-border)]">
            {peers.length === 0 ? (
              <p className="px-5 py-4 font-data text-[11px] text-[var(--color-fg-dim)]">
                Solo tailnet — no other devices visible.
              </p>
            ) : (
              <ul className="divide-y divide-[var(--color-border)]">
                {peers.map((p, i) => (
                  <PeerRow key={`${p.hostname}-${i}`} peer={p} />
                ))}
              </ul>
            )}
          </div>
        )}
      </Card>
    </Section>
  );
}

function PeerRow({ peer }: { peer: TailscalePeer }) {
  return (
    <li className="px-5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 font-data text-[11.5px]">
        <span
          aria-hidden
          className={classNames(
            "w-3 inline-block",
            peer.online
              ? "text-[var(--color-accent)]"
              : "text-[var(--color-fg-dim)]",
          )}
          title={peer.online ? "online" : "offline"}
        >
          {peer.online ? "●" : "○"}
        </span>
        <span className="text-[var(--color-fg)]">{peer.hostname || "—"}</span>
        <span className="text-[var(--color-fg-dim)] tabular-nums">
          {peer.ip || "—"}
        </span>
        <span className="text-[var(--color-fg-2)]">{peer.os || "—"}</span>
        <span className="ml-auto text-[var(--color-fg-dim)] text-[10.5px]">
          {peer.online ? "now" : `last seen ${relativeTime(peer.last_seen)}`}
        </span>
      </div>
    </li>
  );
}

// ---- helpers ----------------------------------------------------

function KV({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="uppercase-tight text-[10px] text-[var(--color-fg-dim)]">
        {label}
      </span>
      <span
        className={classNames(
          mono ? "font-data" : "",
          "text-[12.5px] text-[var(--color-fg)]",
        )}
      >
        {value}
      </span>
    </div>
  );
}

function TailscaleSkeleton() {
  return (
    <div className="space-y-6">
      <div className="h-20 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-32 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-32 hairline bg-[var(--color-surface)] animate-pulse-slow" />
      <div className="h-16 hairline bg-[var(--color-surface)] animate-pulse-slow" />
    </div>
  );
}
