// Tests for the per-bubble copy button.
//
// Covers the user-visible contract: tap → clipboard.writeText with
// the raw content, button label flips to "Copied" for ~1.5s, then
// reverts. System (error) bubbles don't render a copy button.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, act, fireEvent } from "@testing-library/react";
import { ChatMessages, type ChatMessage } from "./ChatMessages";

beforeEach(() => {
  // jsdom doesn't ship Element.prototype.scrollIntoView; ChatMessages
  // calls it in a useEffect to keep the conversation pinned to the
  // newest message. Stub once per test so the render doesn't crash.
  Element.prototype.scrollIntoView = vi.fn();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function userMsg(content: string): ChatMessage {
  return { role: "user", content, ts: 1700000000000 };
}

function assistantMsg(content: string): ChatMessage {
  return { role: "assistant", content, ts: 1700000001000 };
}

function systemMsg(content: string): ChatMessage {
  return { role: "system", content, ts: 1700000002000 };
}

function mountClipboardSpy(): ReturnType<typeof vi.fn> {
  // jsdom has no clipboard; install a mock via Object.defineProperty
  // (assignment doesn't work — navigator.clipboard is read-only).
  const writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
    writable: true,
  });
  return writeText;
}


describe("ChatMessages — copy button", () => {
  it("renders a copy button next to user and assistant bubbles", () => {
    render(
      <ChatMessages
        messages={[userMsg("hello"), assistantMsg("hi back")]}
        pending={false}
      />,
    );
    // Two bubbles → two copy buttons (initial state, before any click).
    const buttons = screen.getAllByRole("button", { name: /copy message/i });
    expect(buttons).toHaveLength(2);
  });

  it("does NOT render a copy button on system bubbles", () => {
    render(
      <ChatMessages
        messages={[systemMsg("⚠️ network error")]}
        pending={false}
      />,
    );
    expect(
      screen.queryByRole("button", { name: /copy message/i }),
    ).toBeNull();
  });

  it("writes the bubble's raw content to the clipboard on click", async () => {
    const writeText = mountClipboardSpy();
    render(
      <ChatMessages
        messages={[assistantMsg("the quick brown fox")]}
        pending={false}
      />,
    );
    const btn = screen.getByRole("button", { name: /copy message/i });
    fireEvent.click(btn);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    expect(writeText).toHaveBeenCalledWith("the quick brown fox");
  });

  it("flips to 'Copied' immediately after click and reverts after 1.5s", async () => {
    mountClipboardSpy();
    vi.useFakeTimers();
    render(
      <ChatMessages
        messages={[assistantMsg("ping")]}
        pending={false}
      />,
    );
    const btn = screen.getByRole("button", { name: /copy message/i });
    fireEvent.click(btn);
    // After awaiting microtasks the writeText resolves and state flips.
    await act(async () => {
      await Promise.resolve();
    });
    expect(btn.textContent).toContain("Copied");
    expect(btn.getAttribute("aria-label")).toBe("Copied");
    // Advance the timer past COPIED_FEEDBACK_MS (1500ms).
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });
    expect(btn.textContent).toContain("Copy");
    expect(btn.getAttribute("aria-label")).toBe("Copy message");
  });

  it("survives clipboard.writeText rejection without crashing", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(async () => { throw new Error("denied"); }) },
      configurable: true,
      writable: true,
    });
    render(
      <ChatMessages
        messages={[assistantMsg("anything")]}
        pending={false}
      />,
    );
    const btn = screen.getByRole("button", { name: /copy message/i });
    // Must not throw.
    fireEvent.click(btn);
    await act(async () => {
      await Promise.resolve();
    });
    // State stays in "Copy" since the write failed silently.
    expect(btn.textContent).toContain("Copy");
    expect(btn.textContent).not.toContain("Copied");
  });

  it("copies the user's plain text (not styled markdown) verbatim", async () => {
    const writeText = mountClipboardSpy();
    const raw =
      "List my files:\n```\nls -lah\n```\nthen pipe to less.";
    render(
      <ChatMessages messages={[userMsg(raw)]} pending={false} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /copy message/i }));
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith(raw);
    });
  });
});
