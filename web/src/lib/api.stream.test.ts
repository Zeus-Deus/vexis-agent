// Tests for the SSE-over-fetch consumer used by chat streaming.
// We mock ``fetch`` to return a synthetic ReadableStream of SSE
// frames split across realistic byte boundaries (mid-frame splits,
// multiple events per chunk), and verify the consumer re-assembles
// them into the right onChunk/onDone/onError calls.

import { describe, expect, it, vi, afterEach } from "vitest";
import { api } from "./api";

afterEach(() => {
  vi.restoreAllMocks();
});

function readableFromChunks(chunks: string[]): ReadableStream<Uint8Array> {
  // Encodes each string chunk as a separate ``enqueue`` so the
  // consumer's frame-reassembly logic gets exercised across read
  // boundaries.
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const c of chunks) {
        controller.enqueue(encoder.encode(c));
      }
      controller.close();
    },
  });
}

function mockFetch(chunks: string[], status = 200) {
  return vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(readableFromChunks(chunks), {
      status,
      headers: { "Content-Type": "text/event-stream" },
    }) as Response,
  );
}


describe("api.chatSendStream — SSE consumer", () => {
  it("invokes onChunk for each frame and onDone for the final reply", async () => {
    mockFetch([
      // Three chunks, each in its own SSE frame.
      `data: {"type":"chunk","text":"hel"}\n\n`,
      `data: {"type":"chunk","text":"lo "}\n\n`,
      `data: {"type":"chunk","text":"world"}\n\n`,
      `data: {"type":"done","reply":"hello world"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError },
    );
    expect(onChunk).toHaveBeenCalledTimes(3);
    expect(onChunk).toHaveBeenNthCalledWith(1, "hel");
    expect(onChunk).toHaveBeenNthCalledWith(2, "lo ");
    expect(onChunk).toHaveBeenNthCalledWith(3, "world");
    expect(onDone).toHaveBeenCalledWith("hello world");
    expect(onError).not.toHaveBeenCalled();
  });

  it("reassembles frames split across byte boundaries", async () => {
    // ``fetch`` reads might land mid-frame. Verify the buffered
    // parser re-assembles correctly.
    mockFetch([
      `data: {"type":"chu`,                    // partial frame start
      `nk","text":"first"}\n\ndata: {"type":"chunk","text":"sec`,  // straddles frame boundary
      `ond"}\n\ndata: {"type":"done","reply":"firstsecond"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError },
    );
    expect(onChunk).toHaveBeenCalledWith("first");
    expect(onChunk).toHaveBeenCalledWith("second");
    expect(onDone).toHaveBeenCalledWith("firstsecond");
  });

  it("invokes onError when the server emits an error frame", async () => {
    mockFetch([
      `data: {"type":"chunk","text":"partial"}\n\n`,
      `data: {"type":"error","code":"brain_error","message":"brain crashed"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError },
    );
    expect(onChunk).toHaveBeenCalledWith("partial");
    // Phase C: onError now receives a second arg with the error
    // ``code``. Specific code → UI dispatches to retry / auth /
    // silent-cancel paths.
    expect(onError).toHaveBeenCalledWith("brain crashed", { code: "brain_error" });
    expect(onDone).not.toHaveBeenCalled();
  });

  it("error frame without a code falls back to 'unknown'", async () => {
    // Older daemons / future codes we haven't taught the client
    // about must still fire onError with a sane code so the UI
    // can render the generic recovery affordance.
    mockFetch([
      `data: {"type":"error","message":"something off"}\n\n`,
    ]);
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk: vi.fn(), onDone: vi.fn(), onError },
    );
    expect(onError).toHaveBeenCalledWith("something off", { code: "unknown" });
  });

  it("error frame with each well-known code roundtrips", async () => {
    // Pin every wire-stable code so a backend rename surfaces in
    // a test rather than silently flipping the UX to "unknown".
    const codes = [
      "brain_error", "brain_timeout", "session_lost",
      "cancelled", "rejected", "unknown",
    ];
    for (const code of codes) {
      mockFetch([
        `data: {"type":"error","code":"${code}","message":"x"}\n\n`,
      ]);
      const onError = vi.fn();
      await api.chatSendStream(
        "tok", { text: "hi" },
        { onChunk: vi.fn(), onDone: vi.fn(), onError },
      );
      expect(onError).toHaveBeenCalledWith("x", { code });
    }
  });

  it("throws TokenInvalidError on 401", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response("", { status: 401 }),
    );
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await expect(
      api.chatSendStream(
        "bad", { text: "hi" },
        { onChunk, onDone, onError },
      ),
    ).rejects.toThrow();
    expect(onChunk).not.toHaveBeenCalled();
    expect(onDone).not.toHaveBeenCalled();
  });

  it("invokes onError on non-401 non-OK responses", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "bad request" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "" },
      { onChunk, onDone, onError },
    );
    // Pre-stream HTTP errors don't have a wire code — onError is
    // called with just the message (no opts arg). The body is
    // raised before the SSE parser even runs.
    expect(onError).toHaveBeenCalledWith("bad request");
    // Defensive: still no code in the args list (proves the
    // pre-stream path is distinct from the in-stream path).
    expect(onError.mock.calls[0]).toHaveLength(1);
  });

  it("survives malformed frames without crashing", async () => {
    mockFetch([
      `data: not-json-at-all\n\n`,
      `data: {"type":"chunk","text":"good"}\n\n`,
      `data: {"type":"done","reply":"good"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError },
    );
    // Bad frame skipped; good frames still processed.
    expect(onChunk).toHaveBeenCalledWith("good");
    expect(onDone).toHaveBeenCalledWith("good");
  });

  it("invokes onTool for tool-use frames with name + target", async () => {
    mockFetch([
      `data: {"type":"tool","name":"Read","target":"src/foo.py"}\n\n`,
      `data: {"type":"chunk","text":"reading…"}\n\n`,
      `data: {"type":"tool","name":"Bash","target":"git status"}\n\n`,
      `data: {"type":"done","reply":"reading…"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    const onTool = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError, onTool },
    );
    expect(onTool).toHaveBeenCalledTimes(2);
    expect(onTool).toHaveBeenNthCalledWith(1, {
      name: "Read", target: "src/foo.py",
    });
    expect(onTool).toHaveBeenNthCalledWith(2, {
      name: "Bash", target: "git status",
    });
    expect(onChunk).toHaveBeenCalledWith("reading…");
    expect(onDone).toHaveBeenCalledWith("reading…");
  });

  it("tool frames with null target normalise to null (not undefined)", async () => {
    // Backend sends ``"target": null`` for tools without a clear
    // file/command (Task, MCP servers). Pin that we surface null,
    // not undefined or empty string — UI uses the falsy check.
    mockFetch([
      `data: {"type":"tool","name":"Task","target":null}\n\n`,
      `data: {"type":"done","reply":""}\n\n`,
    ]);
    const onTool = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk: vi.fn(), onDone: vi.fn(), onError: vi.fn(), onTool },
    );
    expect(onTool).toHaveBeenCalledWith({ name: "Task", target: null });
  });

  it("works without onTool (handler optional)", async () => {
    // Older callers / tests / voice path don't pass onTool. The
    // SSE consumer must skip tool frames silently rather than
    // crashing.
    mockFetch([
      `data: {"type":"tool","name":"Read","target":"f"}\n\n`,
      `data: {"type":"chunk","text":"x"}\n\n`,
      `data: {"type":"done","reply":"x"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError },
    );
    expect(onChunk).toHaveBeenCalledWith("x");
    expect(onDone).toHaveBeenCalledWith("x");
    expect(onError).not.toHaveBeenCalled();
  });

  it("ignores frames without 'data: ' prefix (event:/id:/retry:)", async () => {
    mockFetch([
      // Per SSE spec, ``event:`` lines exist but we don't use them.
      // Make sure they don't break parsing.
      `event: ping\ndata: {"type":"chunk","text":"x"}\n\n`,
      `: heartbeat comment\n\n`,
      `data: {"type":"done","reply":"x"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError },
    );
    expect(onChunk).toHaveBeenCalledWith("x");
    expect(onDone).toHaveBeenCalledWith("x");
  });
});
