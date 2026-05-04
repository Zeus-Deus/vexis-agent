import { useCallback, useEffect, useState } from "react";
import { bootstrapToken, clearToken } from "./lib/auth";
import { Tabs, type TabDef } from "./components/Tabs";
import { MemoryPage } from "./pages/MemoryPage";
import { SkillsPage } from "./pages/SkillsPage";
import { CuratorPage } from "./pages/CuratorPage";
import { StatusPage } from "./pages/StatusPage";
import { BrowserPage } from "./pages/BrowserPage";
import { LearningPage } from "./pages/LearningPage";
import { TailscalePage } from "./pages/TailscalePage";

type TabId =
  | "memory"
  | "skills"
  | "curator"
  | "status"
  | "browser"
  | "learning"
  | "tailscale";

const TABS: TabDef[] = [
  { id: "memory", label: "Memory", glyph: "§" },
  { id: "skills", label: "Skills", glyph: "◇" },
  { id: "curator", label: "Curator", glyph: "◆" },
  { id: "status", label: "Status", glyph: "●" },
  { id: "browser", label: "Browser", glyph: "◐" },
  { id: "learning", label: "Learning", glyph: "▲" },
  { id: "tailscale", label: "Tailscale", glyph: "◈" },
];

const HASH_TO_TAB: Record<string, TabId> = {
  "#memory": "memory",
  "#skills": "skills",
  "#curator": "curator",
  "#status": "status",
  "#browser": "browser",
  "#learning": "learning",
  "#tailscale": "tailscale",
};

function readTabFromHash(): TabId {
  const fromHash = HASH_TO_TAB[window.location.hash];
  return fromHash ?? "memory";
}

export function App() {
  const [token, setToken] = useState<string | null>(() => bootstrapToken());
  const [tab, setTab] = useState<TabId>(() => readTabFromHash());

  const handleTabChange = useCallback((id: string) => {
    setTab(id as TabId);
    window.history.replaceState({}, "", `#${id}`);
  }, []);

  const handleAuthFail = useCallback(() => {
    clearToken();
    setToken(null);
  }, []);

  useEffect(() => {
    const onHash = () => setTab(readTabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  if (!token) {
    return <NoTokenScreen />;
  }

  return (
    <div className="min-h-screen flex flex-col">
      <TopBar />
      <Tabs
        tabs={TABS}
        active={tab}
        onChange={handleTabChange}
        trailing={<HostLine />}
      />
      <main className="flex-1 max-w-[1400px] w-full mx-auto px-5 py-8">
        {tab === "memory" && (
          <MemoryPage token={token} onAuthFail={handleAuthFail} />
        )}
        {tab === "skills" && (
          <SkillsPage token={token} onAuthFail={handleAuthFail} />
        )}
        {tab === "curator" && (
          <CuratorPage token={token} onAuthFail={handleAuthFail} />
        )}
        {tab === "status" && (
          <StatusPage token={token} onAuthFail={handleAuthFail} />
        )}
        {tab === "browser" && (
          <BrowserPage token={token} onAuthFail={handleAuthFail} />
        )}
        {tab === "learning" && (
          <LearningPage token={token} onAuthFail={handleAuthFail} />
        )}
        {tab === "tailscale" && (
          <TailscalePage token={token} onAuthFail={handleAuthFail} />
        )}
      </main>
      <Footer />
    </div>
  );
}

function TopBar() {
  return (
    <header className="border-b border-[var(--color-border)]">
      <div className="max-w-[1400px] w-full mx-auto px-5 py-4 flex items-baseline gap-4">
        <Logotype />
        <div className="ml-auto font-data text-[10.5px] text-[var(--color-fg-dim)] uppercase tracking-widest">
          internal · tailnet only
        </div>
      </div>
    </header>
  );
}

function Logotype() {
  return (
    <div className="flex items-baseline gap-2.5">
      <span
        aria-hidden
        className="font-data text-[var(--color-accent)] text-lg leading-none"
      >
        ▍
      </span>
      <span className="font-data text-[15px] tracking-tight text-[var(--color-fg)]">
        vexis<span className="text-[var(--color-fg-dim)]">-agent</span>
      </span>
      <span className="font-sans uppercase-tight text-[10px] text-[var(--color-fg-dim)] -translate-y-[1px]">
        / dashboard
      </span>
    </div>
  );
}

function HostLine() {
  return (
    <span className="hidden md:inline">
      {window.location.host || "localhost"}
    </span>
  );
}

function Footer() {
  return (
    <footer className="border-t border-[var(--color-border)] mt-auto">
      <div className="max-w-[1400px] w-full mx-auto px-5 py-3 flex items-baseline gap-4 font-data text-[10px] text-[var(--color-fg-dim)] uppercase tracking-widest">
        <span>read-only · with six actions</span>
        <span className="ml-auto">
          mem · skills · curator · status · browser · learning · tailscale
        </span>
      </div>
    </footer>
  );
}

function NoTokenScreen() {
  return (
    <div className="min-h-screen grid place-items-center px-6">
      <div className="hairline bg-[var(--color-surface)] max-w-md w-full p-8">
        <div className="font-data text-[var(--color-accent)] text-2xl mb-4">
          §
        </div>
        <h1 className="font-sans uppercase-tight text-xs text-[var(--color-fg-dim)] mb-3">
          Token required
        </h1>
        <p className="text-sm text-[var(--color-fg-2)] leading-relaxed mb-4">
          The dashboard rejects unauthenticated requests. Send Vexis the
          command{" "}
          <code className="font-data text-[var(--color-fg)] bg-[var(--color-base)] hairline px-1 py-[1px]">
            /dashboard
          </code>{" "}
          on Telegram and follow the URL it returns.
        </p>
        <p className="text-xs text-[var(--color-fg-dim)] leading-relaxed">
          Tokens rotate on every daemon restart. If your tab was open across a
          restart, request a fresh URL.
        </p>
      </div>
    </div>
  );
}
