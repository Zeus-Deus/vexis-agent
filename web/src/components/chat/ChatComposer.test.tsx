// Tests for the ChatComposer's Send→Stop swap.
//
// The streaming-state contract drives whether the rightmost button
// is "Send" (clickable when there's a draft) or "Stop" (always
// clickable, fires onStop). Pinning that swap so a future refactor
// can't remove the Stop affordance without breaking a test.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ChatComposer } from "./ChatComposer";

afterEach(() => {
  vi.restoreAllMocks();
});

beforeEach(() => {
  // ChatComposer's useEffect resizes the textarea via scrollHeight,
  // which jsdom always reports as 0 — that's fine, but stub the
  // setter so the test doesn't print warnings about unsupported
  // pixel-string parsing.
});

function defaultProps(overrides: Partial<React.ComponentProps<typeof ChatComposer>> = {}) {
  return {
    token: "tok",
    pending: false,
    streaming: false,
    onStop: vi.fn(),
    onSend: vi.fn(),
    sttAvailable: false,
    onVoiceCapture: vi.fn(),
    onVoiceError: vi.fn(),
    callModeAvailable: false,
    onOpenCallMode: vi.fn(),
    attachmentQueue: [],
    setAttachmentQueue: vi.fn(),
    onAttachmentError: vi.fn(),
    draftKey: null,
    lastUserMessage: null,
    ...overrides,
  };
}

describe("ChatComposer Send/Stop swap", () => {
  it("renders Send button (not Stop) by default", () => {
    render(<ChatComposer {...defaultProps()} />);
    expect(screen.getByTestId("composer-send")).toBeTruthy();
    expect(screen.queryByTestId("composer-stop")).toBeNull();
  });

  it("renders Stop button (not Send) while streaming", () => {
    render(<ChatComposer {...defaultProps({ streaming: true })} />);
    expect(screen.getByTestId("composer-stop")).toBeTruthy();
    expect(screen.queryByTestId("composer-send")).toBeNull();
  });

  it("invokes onStop when the Stop button is clicked", () => {
    const onStop = vi.fn();
    render(<ChatComposer {...defaultProps({ streaming: true, onStop })} />);
    fireEvent.click(screen.getByTestId("composer-stop"));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("does NOT invoke onSend when streaming (Stop is what's rendered)", () => {
    const onSend = vi.fn();
    const onStop = vi.fn();
    render(
      <ChatComposer
        {...defaultProps({ streaming: true, onSend, onStop })}
      />,
    );
    // The Stop button is what's there — clicking it must not
    // accidentally fire the send path. Pinning the wiring.
    fireEvent.click(screen.getByTestId("composer-stop"));
    expect(onStop).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it("Stop button is clickable even when there's no draft", () => {
    // The Send button is gated on draft length; Stop must NOT be
    // gated the same way — the user might want to stop generation
    // without ever typing again.
    const onStop = vi.fn();
    render(<ChatComposer {...defaultProps({ streaming: true, onStop })} />);
    const stop = screen.getByTestId("composer-stop") as HTMLButtonElement;
    expect(stop.disabled).toBe(false);
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
  });
});


describe("ChatComposer keyboard shortcuts (Phase D)", () => {
  it("↑ in empty composer recalls the last user message", () => {
    render(
      <ChatComposer
        {...defaultProps({ lastUserMessage: "previously asked about X" })}
      />,
    );
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.keyDown(ta, { key: "ArrowUp" });
    expect(ta.value).toBe("previously asked about X");
  });

  it("↑ does NOT recall when the composer already has text", () => {
    render(
      <ChatComposer
        {...defaultProps({ lastUserMessage: "previously asked" })}
      />,
    );
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "current draft" } });
    // Place cursor in the middle of the draft so the recall guard
    // (cursor at 0,0) doesn't trigger.
    ta.setSelectionRange(5, 5);
    fireEvent.keyDown(ta, { key: "ArrowUp" });
    expect(ta.value).toBe("current draft");
  });

  it("Cmd+Enter submits even with text", () => {
    const onSend = vi.fn();
    render(<ChatComposer {...defaultProps({ onSend })} />);
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "test" } });
    fireEvent.keyDown(ta, { key: "Enter", metaKey: true });
    expect(onSend).toHaveBeenCalledWith("test", []);
  });

  it("Esc blurs the composer", () => {
    render(<ChatComposer {...defaultProps()} />);
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    ta.focus();
    expect(document.activeElement).toBe(ta);
    fireEvent.keyDown(ta, { key: "Escape" });
    expect(document.activeElement).not.toBe(ta);
  });
});


describe("ChatComposer draft persistence (Phase D)", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("loads the draft from localStorage on mount when draftKey is set", () => {
    localStorage.setItem("vexis-draft:work", "half-typed message");
    render(
      <ChatComposer {...defaultProps({ draftKey: "vexis-draft:work" })} />,
    );
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    expect(ta.value).toBe("half-typed message");
  });

  it("persists every keystroke to localStorage", () => {
    render(
      <ChatComposer {...defaultProps({ draftKey: "vexis-draft:work" })} />,
    );
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "drafting…" } });
    expect(localStorage.getItem("vexis-draft:work")).toBe("drafting…");
  });

  it("clears localStorage when the draft becomes empty", () => {
    localStorage.setItem("vexis-draft:work", "old");
    render(
      <ChatComposer {...defaultProps({ draftKey: "vexis-draft:work" })} />,
    );
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    fireEvent.change(ta, { target: { value: "" } });
    expect(localStorage.getItem("vexis-draft:work")).toBeNull();
  });

  it("switches drafts when draftKey changes (session switch)", () => {
    localStorage.setItem("vexis-draft:work", "work draft");
    localStorage.setItem("vexis-draft:side", "side draft");
    const { rerender } = render(
      <ChatComposer {...defaultProps({ draftKey: "vexis-draft:work" })} />,
    );
    let ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    expect(ta.value).toBe("work draft");
    rerender(
      <ChatComposer {...defaultProps({ draftKey: "vexis-draft:side" })} />,
    );
    ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    expect(ta.value).toBe("side draft");
  });

  it("draftKey=null disables persistence (and starts empty)", () => {
    localStorage.setItem("vexis-draft:work", "should not be loaded");
    render(<ChatComposer {...defaultProps({ draftKey: null })} />);
    const ta = screen.getByPlaceholderText(/Type a message/) as HTMLTextAreaElement;
    expect(ta.value).toBe("");
  });
});
