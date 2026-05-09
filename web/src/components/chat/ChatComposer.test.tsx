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
