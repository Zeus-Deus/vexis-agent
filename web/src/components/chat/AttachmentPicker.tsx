import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState,
} from "react";
import { api, ApiError } from "../../lib/api";
import type { QueuedAttachment } from "../../lib/types";

/** Handle exposed by ``AttachmentPicker`` via ``ref``. Lets the
 *  parent (ChatPage) trigger an upload from drag-drop / paste
 *  without re-implementing the optimistic-chip + progress flow. */
export interface AttachmentPickerHandle {
  /** Upload a list of File objects sequentially — same flow the
   *  paperclip button uses. Returns when all uploads finish or
   *  fail; errors are surfaced through the ``onError`` callback
   *  rather than thrown so a single bad file doesn't cancel the
   *  rest of the batch. */
  uploadFiles: (files: File[]) => Promise<void>;
}

interface AttachmentPickerProps {
  token: string;
  // Drives the paperclip's disabled state — same contract as
  // MicButton: don't queue more during an in-flight send.
  disabled: boolean;
  // Current queue. Owned by the composer (parent) so the queue
  // survives the picker's own re-renders and ChatPage can read
  // it on send.
  queue: QueuedAttachment[];
  onChange: (queue: QueuedAttachment[]) => void;
  // Inline-error path for upload failures (mime not in allowlist,
  // size cap, network). Composer renders a small banner.
  onError: (message: string) => void;
}

// MIME accept list mirrors the server default in
// core/yaml_config.py:_DEFAULT_ATTACHMENT_MIMES. Server-side validation
// is the authority — this just nudges the OS picker to filter sensibly.
const ACCEPT = [
  "image/png",
  "image/jpeg",
  "image/webp",
  "image/gif",
  "application/pdf",
  "text/plain",
  "text/markdown",
  "text/csv",
  "application/json",
].join(",");

// Track upload state per file so the chip can show progress without
// the parent owning per-file state. Lives only inside the picker.
interface UploadState {
  loaded: number;
  total: number;
  abort: AbortController;
}

export const AttachmentPicker = forwardRef<
  AttachmentPickerHandle,
  AttachmentPickerProps
>(function AttachmentPicker({
  token,
  disabled,
  queue,
  onChange,
  onError,
}, ref) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Map keyed by client-generated id (we mint one per File) so the
  // chip can correlate progress events to its row even before the
  // server returns a path.
  const [uploads, setUploads] = useState<Record<string, UploadState>>({});

  // Cleanup: abort any in-flight uploads on unmount so a tab switch
  // mid-upload doesn't leak the XHR or hold a strong ref to the File.
  useEffect(() => {
    return () => {
      Object.values(uploads).forEach((s) => s.abort.abort());
    };
    // We deliberately don't depend on `uploads` — this cleanup only
    // matters for the unmount case. New uploads tear down their own
    // abort controllers when they finish.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const uploadOne = useCallback(
    async (file: File) => {
      // Guard: skip empty files (some browsers report 0-byte drops).
      if (file.size === 0) {
        onError(`"${file.name}" is empty.`);
        return;
      }
      const id = `${file.name}-${file.size}-${file.lastModified}-${Math.random().toString(36).slice(2, 8)}`;
      const abort = new AbortController();
      // Local preview URL for images — gives instant chip thumbnail
      // without round-tripping to the server. Revoked on remove or
      // unmount (see removeOne / useEffect cleanup).
      const previewUrl = file.type.startsWith("image/")
        ? URL.createObjectURL(file)
        : undefined;

      // Optimistic chip — appears immediately so the user sees the
      // upload starting. Server-side path/size/mime get backfilled
      // when the upload completes.
      const placeholder: QueuedAttachment = {
        path: `__pending__/${id}`,
        name: file.name,
        size: file.size,
        mime: file.type || "application/octet-stream",
        previewUrl,
      };
      onChange([...queue, placeholder]);
      setUploads((prev) => ({
        ...prev,
        [id]: { loaded: 0, total: file.size, abort },
      }));

      try {
        const ref = await api.chatAttach(token, file, {
          signal: abort.signal,
          onProgress: (loaded, total) => {
            setUploads((prev) => ({
              ...prev,
              [id]: { loaded, total, abort },
            }));
          },
        });
        // Replace the placeholder with the server-confirmed ref,
        // preserving the local preview URL so the chip image
        // doesn't flicker when the server response lands.
        onChange(
          queue.concat([{ ...ref, previewUrl }]),
        );
      } catch (exc) {
        // Roll back the optimistic chip.
        onChange(queue);
        if (previewUrl) URL.revokeObjectURL(previewUrl);
        if (exc instanceof DOMException && exc.name === "AbortError") return;
        const detail = exc instanceof ApiError ? exc.message : String(exc);
        onError(`Couldn't upload "${file.name}": ${detail}`);
      } finally {
        setUploads((prev) => {
          const next = { ...prev };
          delete next[id];
          return next;
        });
      }
    },
    [queue, token, onChange, onError],
  );

  const onPick = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      // Sequential uploads keep the progress UX simple and avoid
      // hammering the server with parallel multipart streams. For a
      // typical "drop two screenshots" case this is plenty fast.
      for (const file of Array.from(files)) {
        // eslint-disable-next-line no-await-in-loop
        await uploadOne(file);
      }
    },
    [uploadOne],
  );

  // Expose ``uploadFiles`` so ChatPage's drag-drop / paste handlers
  // can route into the same optimistic-chip + progress flow as the
  // paperclip-picked path. Recreated on every uploadOne re-bind so
  // the closure inside captures the latest queue (uploadOne already
  // depends on ``queue``).
  useImperativeHandle(
    ref,
    () => ({
      uploadFiles: async (files: File[]) => {
        for (const file of files) {
          // eslint-disable-next-line no-await-in-loop
          await uploadOne(file);
        }
      },
    }),
    [uploadOne],
  );

  // Active uploads — surfaced as a small "uploading X" indicator
  // alongside the paperclip. We don't render per-chip progress in
  // this minimal phase; can layer that in if the use case needs it.
  const inflight = Object.values(uploads).length;
  const totalLoaded = Object.values(uploads).reduce((s, u) => s + u.loaded, 0);
  const totalSize = Object.values(uploads).reduce((s, u) => s + u.total, 0);
  const pct = totalSize > 0 ? Math.round((totalLoaded / totalSize) * 100) : 0;

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        className="hidden"
        onChange={(e) => {
          onPick(e.target.files);
          // Reset so picking the same file twice in a row still fires.
          if (inputRef.current) inputRef.current.value = "";
        }}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled}
        aria-label="Attach files"
        title={inflight > 0 ? `Uploading ${inflight} (${pct}%)` : "Attach files"}
        className={[
          "shrink-0 rounded-md flex items-center justify-center",
          // 44x44 square on mobile, tighter pill on desktop —
          // matches MicButton and Send for a consistent
          // composer-button row height.
          "w-11 h-11 md:w-auto md:h-auto md:px-2.5 md:py-1.5",
          "transition-colors select-none",
          "border border-[var(--color-border-strong)]",
          "text-[var(--color-fg-2)] hover:text-[var(--color-fg)]",
          "hover:border-[var(--color-accent)] hover:bg-[var(--color-base)]",
          "disabled:opacity-40 disabled:cursor-not-allowed",
        ].join(" ")}
      >
        {inflight > 0 ? (
          <span className="text-xs font-mono tabular-nums">{pct}%</span>
        ) : (
          <span aria-hidden className="text-base leading-none">📎</span>
        )}
      </button>
    </>
  );
});

/**
 * Render the queued-attachment chips above the textarea. Kept as a
 * sibling component so the picker button and the chip strip can sit
 * in different places in the composer layout.
 */
export function AttachmentChips({
  queue,
  onRemove,
}: {
  queue: QueuedAttachment[];
  onRemove: (path: string) => void;
}) {
  if (queue.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-2 mb-2">
      {queue.map((a) => (
        <AttachmentChip key={a.path} attachment={a} onRemove={onRemove} />
      ))}
    </div>
  );
}

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: QueuedAttachment;
  onRemove: (path: string) => void;
}) {
  const isImage = attachment.mime.startsWith("image/");
  const isPending = attachment.path.startsWith("__pending__/");
  return (
    <div
      className={[
        "flex items-center gap-2 px-2 py-1.5 rounded-md",
        "border border-[var(--color-border)] bg-[var(--color-surface)]",
        "max-w-full",
        isPending ? "opacity-70" : "",
      ].join(" ")}
    >
      {isImage && attachment.previewUrl ? (
        <img
          src={attachment.previewUrl}
          alt=""
          className="w-8 h-8 rounded object-cover shrink-0"
        />
      ) : (
        <span aria-hidden className="text-base shrink-0">
          {isImage ? "🖼" : "📄"}
        </span>
      )}
      <div className="min-w-0">
        <div className="text-xs text-[var(--color-fg)] truncate">
          {attachment.name}
        </div>
        <div className="text-[10px] text-[var(--color-fg-dim)] tabular-nums">
          {formatBytes(attachment.size)}
          {isPending ? " · uploading…" : ""}
        </div>
      </div>
      <button
        type="button"
        onClick={() => onRemove(attachment.path)}
        aria-label={`Remove ${attachment.name}`}
        className={[
          "shrink-0 w-6 h-6 flex items-center justify-center rounded",
          "text-[var(--color-fg-dim)] hover:text-[var(--color-error)]",
          "hover:bg-[var(--color-base)] transition-colors text-xs",
        ].join(" ")}
      >
        ✕
      </button>
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
