// Tests for the AttachmentPicker's imperative handle — the seam
// drag-drop and paste handlers in ChatPage use to route files into
// the same optimistic-chip + progress flow as the paperclip button.
//
// We don't test the picker's button-click path here (that's been
// stable since the picker shipped); the imperative handle is the
// new surface and the one most likely to drift.

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRef } from "react";
import { render, act } from "@testing-library/react";
import {
  AttachmentPicker,
  type AttachmentPickerHandle,
} from "./AttachmentPicker";
import * as apiModule from "../../lib/api";

const { api } = apiModule;

beforeEach(() => {
  // jsdom doesn't ship URL.createObjectURL — preview-URL minting in
  // the picker calls it for image files.
  Object.defineProperty(URL, "createObjectURL", {
    value: vi.fn(() => "blob:fake-url"),
    writable: true,
    configurable: true,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    value: vi.fn(),
    writable: true,
    configurable: true,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function makeFile(name: string, type: string, content = "x"): File {
  return new File([content], name, { type });
}

describe("AttachmentPicker imperative handle", () => {
  it("exposes uploadFiles via the forwarded ref", () => {
    const ref = createRef<AttachmentPickerHandle>();
    render(
      <AttachmentPicker
        ref={ref}
        token="tok"
        disabled={false}
        queue={[]}
        onChange={vi.fn()}
        onError={vi.fn()}
      />,
    );
    expect(ref.current).not.toBeNull();
    expect(typeof ref.current?.uploadFiles).toBe("function");
  });

  it("uploadFiles routes each File through api.chatAttach", async () => {
    const chatAttach = vi
      .spyOn(api, "chatAttach")
      .mockImplementation(async (_token, file) => ({
        path: `/tmp/${file.name}`,
        name: file.name,
        size: file.size,
        mime: file.type,
      }));
    const ref = createRef<AttachmentPickerHandle>();
    const onChange = vi.fn();
    const onError = vi.fn();
    let queue: import("../../lib/types").QueuedAttachment[] = [];
    const updateQueue = (next: typeof queue) => {
      queue = next;
      onChange(next);
    };

    const { rerender } = render(
      <AttachmentPicker
        ref={ref}
        token="tok"
        disabled={false}
        queue={queue}
        onChange={updateQueue}
        onError={onError}
      />,
    );

    const f1 = makeFile("a.png", "image/png");
    const f2 = makeFile("b.png", "image/png");
    await act(async () => {
      await ref.current!.uploadFiles([f1, f2]);
      // Re-render so the second uploadOne sees the latest queue
      // — the picker's uploadOne closure captures `queue` from
      // props, so callers iterating multiple files in the same
      // tick should re-render between iterations. The picker
      // does this internally via its own setState. We trigger
      // a manual rerender to mimic the React commit.
      rerender(
        <AttachmentPicker
          ref={ref}
          token="tok"
          disabled={false}
          queue={queue}
          onChange={updateQueue}
          onError={onError}
        />,
      );
    });
    expect(chatAttach).toHaveBeenCalledTimes(2);
    expect(chatAttach.mock.calls[0][1]).toBe(f1);
    expect(chatAttach.mock.calls[1][1]).toBe(f2);
  });

  it("uploadFiles surfaces upload errors via onError without throwing", async () => {
    vi.spyOn(api, "chatAttach").mockImplementation(async () => {
      throw new apiModule.ApiError(415, "forbidden mime");
    });
    const ref = createRef<AttachmentPickerHandle>();
    const onError = vi.fn();
    render(
      <AttachmentPicker
        ref={ref}
        token="tok"
        disabled={false}
        queue={[]}
        onChange={vi.fn()}
        onError={onError}
      />,
    );
    await act(async () => {
      await ref.current!.uploadFiles([makeFile("bad.exe", "application/x-msdownload")]);
    });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toContain("bad.exe");
  });

  it("skips empty (0-byte) files via onError", async () => {
    const ref = createRef<AttachmentPickerHandle>();
    const onError = vi.fn();
    const chatAttach = vi.spyOn(api, "chatAttach");
    render(
      <AttachmentPicker
        ref={ref}
        token="tok"
        disabled={false}
        queue={[]}
        onChange={vi.fn()}
        onError={onError}
      />,
    );
    await act(async () => {
      // Build a 0-byte File — File constructor accepts an empty
      // BlobPart array.
      const empty = new File([], "empty.png", { type: "image/png" });
      await ref.current!.uploadFiles([empty]);
    });
    expect(onError).toHaveBeenCalledTimes(1);
    expect(onError.mock.calls[0][0]).toContain("empty.png");
    expect(chatAttach).not.toHaveBeenCalled();
  });
});
