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
      `data: {"type":"error","message":"brain crashed"}\n\n`,
    ]);
    const onChunk = vi.fn();
    const onDone = vi.fn();
    const onError = vi.fn();
    await api.chatSendStream(
      "tok", { text: "hi" },
      { onChunk, onDone, onError },
    );
    expect(onChunk).toHaveBeenCalledWith("partial");
    expect(onError).toHaveBeenCalledWith("brain crashed");
    expect(onDone).not.toHaveBeenCalled();
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
    expect(onError).toHaveBeenCalledWith("bad request");
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
