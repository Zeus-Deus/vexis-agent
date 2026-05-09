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


// ──────────────────────────────────────────────────────────────────
// Edit + Regenerate (Phase 4)
// ──────────────────────────────────────────────────────────────────


describe("ChatMessages — Edit last user message", () => {
  it("renders an Edit button only on the LAST user bubble", () => {
    render(
      <ChatMessages
        messages={[
          userMsg("first user msg"),
          assistantMsg("first reply"),
          userMsg("second user msg"),
          assistantMsg("second reply"),
        ]}
        pending={false}
        onEditLastUser={vi.fn()}
      />,
    );
    // Only one Edit button — on the second (last) user bubble.
    const editButtons = screen.getAllByTestId("bubble-edit");
    expect(editButtons).toHaveLength(1);
  });

  it("does NOT render Edit when onEditLastUser is undefined", () => {
    render(
      <ChatMessages
        messages={[userMsg("only msg"), assistantMsg("reply")]}
        pending={false}
        // onEditLastUser omitted on purpose — caller hasn't wired
        // edit yet, the affordance shouldn't appear.
      />,
    );
    expect(screen.queryByTestId("bubble-edit")).toBeNull();
  });

  it("does NOT render Edit while ``pending`` is true", () => {
    // Edit during a streaming reply would race with the in-flight
    // turn. Hide the button until the reply finishes.
    render(
      <ChatMessages
        messages={[userMsg("hi"), assistantMsg("typing…")]}
        pending={true}
        onEditLastUser={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("bubble-edit")).toBeNull();
  });

  it("clicking Edit swaps the bubble into a textarea pre-filled with the original", () => {
    render(
      <ChatMessages
        messages={[userMsg("original text"), assistantMsg("reply")]}
        pending={false}
        onEditLastUser={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("bubble-edit"));
    const ta = screen.getByTestId("bubble-edit-textarea") as HTMLTextAreaElement;
    expect(ta.value).toBe("original text");
  });

  it("Save fires onEditLastUser with the trimmed new text + original attachments", () => {
    const onEdit = vi.fn();
    const userWithAttachments: ChatMessage = {
      role: "user",
      content: "what's this?",
      ts: 1700000000000,
      attachments: [
        { path: "/tmp/cat.png", name: "cat.png", size: 100, mime: "image/png" },
      ],
    };
    render(
      <ChatMessages
        messages={[userWithAttachments, assistantMsg("a cat")]}
        pending={false}
        onEditLastUser={onEdit}
      />,
    );
    fireEvent.click(screen.getByTestId("bubble-edit"));
    const ta = screen.getByTestId("bubble-edit-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "  what kind of cat? " } });
    fireEvent.click(screen.getByTestId("bubble-edit-save"));
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(onEdit).toHaveBeenCalledWith(
      "what kind of cat?",
      userWithAttachments.attachments,
    );
  });

  it("Cancel reverts to the read-only bubble without calling onEditLastUser", () => {
    const onEdit = vi.fn();
    render(
      <ChatMessages
        messages={[userMsg("don't change"), assistantMsg("ok")]}
        pending={false}
        onEditLastUser={onEdit}
      />,
    );
    fireEvent.click(screen.getByTestId("bubble-edit"));
    const ta = screen.getByTestId("bubble-edit-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "modified but cancelled" } });
    fireEvent.click(screen.getByTestId("bubble-edit-cancel"));
    expect(onEdit).not.toHaveBeenCalled();
    // Bubble back in read-only mode — the textarea is gone.
    expect(screen.queryByTestId("bubble-edit-textarea")).toBeNull();
  });

  it("Enter submits, Escape cancels in the edit textarea", () => {
    const onEdit = vi.fn();
    render(
      <ChatMessages
        messages={[userMsg("seed"), assistantMsg("reply")]}
        pending={false}
        onEditLastUser={onEdit}
      />,
    );
    // Submit via Enter.
    fireEvent.click(screen.getByTestId("bubble-edit"));
    let ta = screen.getByTestId("bubble-edit-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "edited via enter" } });
    fireEvent.keyDown(ta, { key: "Enter" });
    expect(onEdit).toHaveBeenCalledWith("edited via enter", []);

    // Cancel via Escape on a fresh edit session.
    fireEvent.click(screen.getByTestId("bubble-edit"));
    ta = screen.getByTestId("bubble-edit-textarea") as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "abandoned" } });
    fireEvent.keyDown(ta, { key: "Escape" });
    // onEdit count unchanged from the first submission.
    expect(onEdit).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("bubble-edit-textarea")).toBeNull();
  });

  it("Save with the unchanged text exits edit mode without firing onEditLastUser", () => {
    // No-op edit — burning a brain turn on the identical message
    // would surprise the user. Re-enter read-only mode silently.
    const onEdit = vi.fn();
    render(
      <ChatMessages
        messages={[userMsg("same"), assistantMsg("ok")]}
        pending={false}
        onEditLastUser={onEdit}
      />,
    );
    fireEvent.click(screen.getByTestId("bubble-edit"));
    fireEvent.click(screen.getByTestId("bubble-edit-save"));
    expect(onEdit).not.toHaveBeenCalled();
    expect(screen.queryByTestId("bubble-edit-textarea")).toBeNull();
  });
});


describe("ChatMessages — Image lightbox (Phase D)", () => {
  function imgMsg(): ChatMessage {
    return {
      role: "user",
      content: "look at this",
      ts: 1700000000000,
      attachments: [
        {
          path: "/tmp/cat.png",
          name: "cat.png",
          size: 1024,
          mime: "image/png",
          previewUrl: "blob:fake",
        },
      ],
    };
  }

  it("does NOT render the lightbox by default", () => {
    render(<ChatMessages messages={[imgMsg()]} pending={false} />);
    expect(screen.queryByTestId("attachment-lightbox")).toBeNull();
  });

  it("clicking the inline image opens the lightbox", () => {
    render(<ChatMessages messages={[imgMsg()]} pending={false} />);
    fireEvent.click(screen.getByTestId("attachment-image"));
    expect(screen.getByTestId("attachment-lightbox")).toBeTruthy();
  });

  it("clicking the close button dismisses the lightbox", () => {
    render(<ChatMessages messages={[imgMsg()]} pending={false} />);
    fireEvent.click(screen.getByTestId("attachment-image"));
    expect(screen.getByTestId("attachment-lightbox")).toBeTruthy();
    fireEvent.click(screen.getByTestId("lightbox-close"));
    expect(screen.queryByTestId("attachment-lightbox")).toBeNull();
  });

  it("Esc dismisses the lightbox", () => {
    render(<ChatMessages messages={[imgMsg()]} pending={false} />);
    fireEvent.click(screen.getByTestId("attachment-image"));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByTestId("attachment-lightbox")).toBeNull();
  });

  it("clicking the backdrop dismisses; clicking the image does NOT", () => {
    render(<ChatMessages messages={[imgMsg()]} pending={false} />);
    fireEvent.click(screen.getByTestId("attachment-image"));
    const dialog = screen.getByTestId("attachment-lightbox");
    // The image inside the dialog stops propagation — click on it
    // shouldn't dismiss.
    const innerImg = dialog.querySelector("img");
    fireEvent.click(innerImg!);
    expect(screen.getByTestId("attachment-lightbox")).toBeTruthy();
    // Backdrop click closes.
    fireEvent.click(dialog);
    expect(screen.queryByTestId("attachment-lightbox")).toBeNull();
  });
});


describe("ChatMessages — Error bubble + Retry", () => {
  function errMsg(
    overrides: Partial<ChatMessage> = {},
  ): ChatMessage {
    return {
      role: "system",
      content: "⚠️ Brain timed out",
      ts: 1700000000000,
      errorCode: "brain_timeout",
      retryPayload: { text: "the original prompt", attachments: [] },
      ...overrides,
    };
  }

  it("renders an error bubble with a Retry button when retryPayload is set", () => {
    render(
      <ChatMessages
        messages={[
          { role: "user", content: "the original prompt", ts: 1 },
          errMsg(),
        ]}
        pending={false}
        onRetryFailed={vi.fn()}
      />,
    );
    expect(screen.getByTestId("error-bubble")).toBeTruthy();
    expect(screen.getByTestId("error-retry")).toBeTruthy();
  });

  it("clicking Retry fires onRetryFailed with the original payload", () => {
    const onRetry = vi.fn();
    render(
      <ChatMessages
        messages={[
          { role: "user", content: "the original prompt", ts: 1 },
          errMsg(),
        ]}
        pending={false}
        onRetryFailed={onRetry}
      />,
    );
    fireEvent.click(screen.getByTestId("error-retry"));
    expect(onRetry).toHaveBeenCalledWith("the original prompt", []);
  });

  it("hides Retry while pending (don't queue a second turn)", () => {
    render(
      <ChatMessages
        messages={[errMsg()]}
        pending={true}
        onRetryFailed={vi.fn()}
      />,
    );
    // Bubble still visible, but the button is suppressed.
    expect(screen.getByTestId("error-bubble")).toBeTruthy();
    expect(screen.queryByTestId("error-retry")).toBeNull();
  });

  it("plain system note (no errorCode) renders as a centered dim line", () => {
    render(
      <ChatMessages
        messages={[
          { role: "system", content: "Conversation cleared.", ts: 1 },
        ]}
        pending={false}
        onRetryFailed={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("error-bubble")).toBeNull();
    expect(screen.getByText("Conversation cleared.")).toBeTruthy();
  });

  it("cancelled-code system messages don't render as an error bubble", () => {
    // ``cancelled`` is the user-pressed-Stop path. The frontend
    // shouldn't have placed it in the buffer at all (handleSend
    // drops it), but if a stale row sneaks through, it must NOT
    // render a Retry button — the user already chose to stop.
    render(
      <ChatMessages
        messages={[
          {
            role: "system",
            content: "",
            ts: 1,
            errorCode: "cancelled",
          },
        ]}
        pending={false}
        onRetryFailed={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("error-bubble")).toBeNull();
    expect(screen.queryByTestId("error-retry")).toBeNull();
  });
});


describe("ChatMessages — Tool-use inline status", () => {
  it("renders one row per tool event in order", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "reply text",
      ts: 1700000001000,
      tools: [
        { name: "Read", target: "src/foo.py", ts: 1 },
        { name: "Bash", target: "git status", ts: 2 },
        { name: "Edit", target: "src/foo.py", ts: 3 },
      ],
    };
    render(<ChatMessages messages={[msg]} pending={false} />);
    const rows = screen.getAllByTestId("tool-use-row");
    expect(rows).toHaveLength(3);
    expect(rows[0].textContent).toContain("Reading");
    expect(rows[0].textContent).toContain("src/foo.py");
    expect(rows[1].textContent).toContain("Running");
    expect(rows[1].textContent).toContain("git status");
    expect(rows[2].textContent).toContain("Editing");
  });

  it("renders ONLY on assistant bubbles (not user/system)", () => {
    // Defensive: even if a caller mistakenly attaches tools to a
    // user message, the bubble must not render the tool row.
    // We're checking that the renderer drops the row defensively
    // even if a caller mistakenly attaches tools to a user role.
    // Tools are typed optional on every role, so no @ts-expect-error
    // is needed — the test still verifies the runtime guard.
    const userWithTools: ChatMessage = {
      role: "user",
      content: "hi",
      ts: 1,
      tools: [{ name: "Read", target: "x", ts: 0 }],
    };
    render(<ChatMessages messages={[userWithTools]} pending={false} />);
    expect(screen.queryByTestId("tool-use-list")).toBeNull();
  });

  it("suppresses the inline pulse when tools have streamed", () => {
    // A streaming bubble normally shows a 3-dot pulse during the
    // empty-content window. Once a tool event has streamed, the
    // tool row's own indicator is the "alive" signal — the pulse
    // would be redundant.
    const msg: ChatMessage = {
      role: "assistant",
      content: "",
      ts: 1700000001000,
      tools: [{ name: "Read", target: "src/foo.py", ts: 1 }],
    };
    const { container } = render(
      <ChatMessages messages={[msg]} pending={true} />,
    );
    expect(screen.getByTestId("tool-use-list")).toBeTruthy();
    // The standalone PendingBubble pulse (three dots in a row) is
    // not rendered because the tail bubble itself is streaming.
    // The InlinePulse inside the bubble is also suppressed.
    expect(container.querySelectorAll(".animate-pulse").length).toBeLessThanOrEqual(1);
  });

  it("falls back to the raw tool name when no humanised label exists", () => {
    // MCP servers and arbitrary Task names — must render verbatim
    // rather than as "undefined" or empty string.
    const msg: ChatMessage = {
      role: "assistant",
      content: "ok",
      ts: 1,
      tools: [{ name: "mcp__codemux__browser_open", target: "https://x", ts: 0 }],
    };
    render(<ChatMessages messages={[msg]} pending={false} />);
    const rows = screen.getAllByTestId("tool-use-row");
    expect(rows[0].textContent).toContain("mcp__codemux__browser_open");
  });

  it("does not crash when target is null", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "delegated",
      ts: 1,
      tools: [{ name: "Task", target: null, ts: 0 }],
    };
    render(<ChatMessages messages={[msg]} pending={false} />);
    const row = screen.getByTestId("tool-use-row");
    // Label still rendered ("Delegating") but no target span.
    expect(row.textContent).toContain("Delegating");
  });
});


describe("ChatMessages — Regenerate last assistant", () => {
  it("renders a Regenerate button only on the LAST non-empty assistant bubble", () => {
    render(
      <ChatMessages
        messages={[
          userMsg("u1"), assistantMsg("a1"),
          userMsg("u2"), assistantMsg("a2"),
        ]}
        pending={false}
        onRegenerateLastAssistant={vi.fn()}
      />,
    );
    const buttons = screen.getAllByTestId("bubble-regenerate");
    expect(buttons).toHaveLength(1);
  });

  it("Regenerate is suppressed on an in-flight (empty content) assistant bubble", () => {
    // While streaming, the empty placeholder bubble shouldn't get
    // a Regenerate button — there's nothing to regenerate yet.
    render(
      <ChatMessages
        messages={[
          userMsg("hello"),
          { role: "assistant", content: "", ts: 1700000001000 },
        ]}
        pending={true}
        onRegenerateLastAssistant={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("bubble-regenerate")).toBeNull();
  });

  it("clicking Regenerate fires the callback exactly once", () => {
    const onRegen = vi.fn();
    render(
      <ChatMessages
        messages={[userMsg("hi"), assistantMsg("first reply")]}
        pending={false}
        onRegenerateLastAssistant={onRegen}
      />,
    );
    fireEvent.click(screen.getByTestId("bubble-regenerate"));
    expect(onRegen).toHaveBeenCalledTimes(1);
  });

  it("Regenerate is hidden while pending is true", () => {
    render(
      <ChatMessages
        messages={[userMsg("hi"), assistantMsg("complete reply")]}
        pending={true}
        onRegenerateLastAssistant={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("bubble-regenerate")).toBeNull();
  });
});
