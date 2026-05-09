// Tests for the sidebar's grouping + preview rendering. The pure
// classifier (``classifySession``) is tested in isolation so the
// date-bucket logic is easy to debug; the SessionGroup render is
// covered as a single integration test that the right sections
// appear when sessions span multiple recency buckets.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import {
  ChatSidebar,
  classifySession,
  groupSessions,
} from "./ChatSidebar";
import type { ChatSession } from "../../lib/types";

function s(
  name: string,
  createdAt: string,
  overrides: Partial<ChatSession> = {},
): ChatSession {
  return {
    name,
    is_active: false,
    created_at: createdAt,
    ...overrides,
  };
}

describe("classifySession", () => {
  // Anchor "now" at a fixed local-time so the tests don't drift.
  // 2026-05-09 14:00 local — gives us a 14-hour Today window and
  // a clean Yesterday boundary at 2026-05-08 00:00.
  const NOW = new Date(2026, 4, 9, 14, 0, 0);

  it("buckets sessions created today as Today", () => {
    const morning = new Date(2026, 4, 9, 8, 30, 0).toISOString();
    expect(classifySession(morning, NOW)).toBe("Today");
    const justNow = new Date(2026, 4, 9, 13, 59, 0).toISOString();
    expect(classifySession(justNow, NOW)).toBe("Today");
  });

  it("buckets the previous calendar day as Yesterday", () => {
    const yesterdayMorning = new Date(2026, 4, 8, 9, 0, 0).toISOString();
    expect(classifySession(yesterdayMorning, NOW)).toBe("Yesterday");
    const yesterdayLateNight = new Date(2026, 4, 8, 23, 59, 0).toISOString();
    expect(classifySession(yesterdayLateNight, NOW)).toBe("Yesterday");
  });

  it("respects the day boundary even at 23:59 vs 00:01", () => {
    // 1 minute on either side of midnight — easy place to flip
    // a bucket if we used hours-since-now math instead of day
    // boundaries.
    const lastMinuteOfYesterday = new Date(2026, 4, 8, 23, 59, 30).toISOString();
    const firstMinuteOfToday = new Date(2026, 4, 9, 0, 0, 30).toISOString();
    expect(classifySession(lastMinuteOfYesterday, NOW)).toBe("Yesterday");
    expect(classifySession(firstMinuteOfToday, NOW)).toBe("Today");
  });

  it("buckets 2-7 days ago as Previous 7 days", () => {
    const threeDaysAgo = new Date(2026, 4, 6, 12, 0, 0).toISOString();
    const sixDaysAgo = new Date(2026, 4, 3, 12, 0, 0).toISOString();
    expect(classifySession(threeDaysAgo, NOW)).toBe("Previous 7 days");
    expect(classifySession(sixDaysAgo, NOW)).toBe("Previous 7 days");
  });

  it("buckets >7 days ago as Older", () => {
    const eightDaysAgo = new Date(2026, 4, 1, 12, 0, 0).toISOString();
    const monthAgo = new Date(2026, 3, 9, 12, 0, 0).toISOString();
    expect(classifySession(eightDaysAgo, NOW)).toBe("Older");
    expect(classifySession(monthAgo, NOW)).toBe("Older");
  });

  it("malformed timestamps default to Older (don't crash)", () => {
    expect(classifySession("not-a-date", NOW)).toBe("Older");
    expect(classifySession("", NOW)).toBe("Older");
  });
});

describe("groupSessions", () => {
  const NOW = new Date(2026, 4, 9, 14, 0, 0);

  it("returns groups in declaration order (Today first)", () => {
    const sessions = [
      s("a", new Date(2026, 4, 1, 12, 0, 0).toISOString()), // Older
      s("b", new Date(2026, 4, 9, 10, 0, 0).toISOString()), // Today
      s("c", new Date(2026, 4, 8, 9, 0, 0).toISOString()),  // Yesterday
      s("d", new Date(2026, 4, 5, 9, 0, 0).toISOString()),  // Previous 7
    ];
    const groups = groupSessions(sessions, NOW);
    expect(groups.map((g) => g.label)).toEqual([
      "Today", "Yesterday", "Previous 7 days", "Older",
    ]);
  });

  it("omits empty buckets entirely", () => {
    // A user with only 'Today' sessions sees one section, not four
    // (three of them empty).
    const sessions = [
      s("a", new Date(2026, 4, 9, 10, 0, 0).toISOString()),
      s("b", new Date(2026, 4, 9, 11, 0, 0).toISOString()),
    ];
    const groups = groupSessions(sessions, NOW);
    expect(groups).toHaveLength(1);
    expect(groups[0].label).toBe("Today");
    expect(groups[0].items).toHaveLength(2);
  });

  it("preserves input order within each group", () => {
    // Caller (ChatPage) sorts newest-first; we shouldn't reshuffle.
    const sessions = [
      s("first", new Date(2026, 4, 9, 12, 0, 0).toISOString()),
      s("second", new Date(2026, 4, 9, 10, 0, 0).toISOString()),
    ];
    const groups = groupSessions(sessions, NOW);
    expect(groups[0].items.map((x) => x.name)).toEqual(["first", "second"]);
  });
});


describe("ChatSidebar — sections render with previews", () => {
  const NOW = new Date(2026, 4, 9, 14, 0, 0);
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("renders one section per non-empty bucket and shows previews", () => {
    const sessions: ChatSession[] = [
      s("today-a", new Date(2026, 4, 9, 10, 0, 0).toISOString(), {
        preview: "asked about quantum mechanics",
      }),
      s("yesterday-b", new Date(2026, 4, 8, 10, 0, 0).toISOString(), {
        preview: "help me debug the CSS layout",
      }),
      s("week-c", new Date(2026, 4, 5, 10, 0, 0).toISOString()),
    ];
    render(
      <ChatSidebar
        sessions={sessions}
        pendingName={null}
        open
        onClose={vi.fn()}
        onAfterAction={vi.fn()}
        onNew={vi.fn()}
        onSwitch={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    // Three sections, in order.
    expect(screen.getByTestId("group-Today")).toBeTruthy();
    expect(screen.getByTestId("group-Yesterday")).toBeTruthy();
    expect(screen.getByTestId("group-Previous 7 days")).toBeTruthy();
    expect(screen.queryByTestId("group-Older")).toBeNull();
    // Previews rendered for sessions that have one; absent for the
    // session without a preview.
    const previews = screen.getAllByTestId("session-preview");
    expect(previews).toHaveLength(2);
    expect(previews[0].textContent).toContain("quantum mechanics");
    expect(previews[1].textContent).toContain("debug the CSS");
  });

  it("renders no preview line when ``preview`` is null/undefined", () => {
    const sessions: ChatSession[] = [
      s("a", NOW.toISOString(), { preview: null }),
      s("b", NOW.toISOString()), // preview field absent
    ];
    render(
      <ChatSidebar
        sessions={sessions}
        pendingName={null}
        open
        onClose={vi.fn()}
        onAfterAction={vi.fn()}
        onNew={vi.fn()}
        onSwitch={vi.fn()}
        onRename={vi.fn()}
        onDelete={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("session-preview")).toBeNull();
  });
});

