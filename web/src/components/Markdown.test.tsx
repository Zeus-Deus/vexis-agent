// Tests for the code-block copy button overlay.
//
// Pins the user-visible contract: each fenced ``<pre>`` gets a
// dedicated Copy button; clicking it writes the block's text to
// the clipboard; the label flips to "Copied" briefly. Inline code
// does NOT render a copy button (would be visual noise).

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { Markdown } from "./Markdown";

let writeText: ReturnType<typeof vi.fn>;

beforeEach(() => {
  writeText = vi.fn(async () => undefined);
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("Markdown — code-block copy button", () => {
  it("renders a Copy button for each fenced code block", () => {
    const source = [
      "Some prose.",
      "",
      "```python",
      "print('hello')",
      "```",
      "",
      "More prose.",
      "",
      "```bash",
      "ls -la",
      "```",
    ].join("\n");
    render(<Markdown source={source} />);
    const buttons = screen.getAllByTestId("codeblock-copy");
    expect(buttons).toHaveLength(2);
  });

  it("does NOT render a copy button for inline code", () => {
    // Single backticks → inline ``<code>``, NOT inside ``<pre>``,
    // so no copy button. (We don't want a button per inline code
    // span — would clutter every paragraph.)
    render(<Markdown source="Use `git status` to see changes." />);
    expect(screen.queryByTestId("codeblock-copy")).toBeNull();
  });

  it("writes the code block text to the clipboard on click", async () => {
    const source = "```python\nprint('hello world')\n```";
    render(<Markdown source={source} />);
    const button = screen.getByTestId("codeblock-copy");
    fireEvent.click(button);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    // ReactMarkdown injects a trailing newline into the pre's
    // textContent; we check ``includes`` rather than equality so
    // a future ReactMarkdown version that drops/keeps the newline
    // doesn't flip the test.
    const arg = writeText.mock.calls[0][0] as string;
    expect(arg).toContain("print('hello world')");
  });

  it("flips the label to 'Copied' for ~1.5s after click, then reverts", async () => {
    vi.useFakeTimers();
    const source = "```\nfoo\n```";
    render(<Markdown source={source} />);
    const button = screen.getByTestId("codeblock-copy");
    expect(button.textContent).toBe("Copy");
    // Fire the click and let the await navigator.clipboard.writeText
    // microtask resolve.
    await act(async () => {
      fireEvent.click(button);
    });
    expect(button.textContent).toBe("Copied");
    // Advance past the 1500ms revert timer.
    await act(async () => {
      vi.advanceTimersByTime(1600);
    });
    expect(button.textContent).toBe("Copy");
  });

  it("each code block's button copies its OWN content (not the first one)", async () => {
    const source = [
      "```",
      "first-block",
      "```",
      "",
      "```",
      "second-block",
      "```",
    ].join("\n");
    render(<Markdown source={source} />);
    const buttons = screen.getAllByTestId("codeblock-copy");
    fireEvent.click(buttons[1]);
    await waitFor(() => {
      expect(writeText).toHaveBeenCalledTimes(1);
    });
    const arg = writeText.mock.calls[0][0] as string;
    expect(arg).toContain("second-block");
    expect(arg).not.toContain("first-block");
  });

  it("swallows clipboard errors silently (no thrown exception)", async () => {
    Object.defineProperty(navigator, "clipboard", {
      value: { writeText: vi.fn(async () => { throw new Error("denied"); }) },
      writable: true,
      configurable: true,
    });
    const source = "```\nfoo\n```";
    render(<Markdown source={source} />);
    const button = screen.getByTestId("codeblock-copy");
    // Should not throw.
    await act(async () => {
      fireEvent.click(button);
    });
    // Label stays "Copy" — we don't flip to "Copied" if the write
    // failed, because the user might think it worked otherwise.
    expect(button.textContent).toBe("Copy");
  });
});
