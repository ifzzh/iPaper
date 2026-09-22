// Persistent highlights, annotations and the per-paper note.
//
// Everything here is owner-scoped on the server; this module only shapes the
// requests, keeps an honest save state and renders markers for the current
// document revision. No model call is made anywhere in this file.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Markdown, Status } from "./ui";

export type AnnotationAnchor =
  | { mode: "pdf"; page: number; rects: Array<{ x: number; y: number; w: number; h: number; page: number }> }
  | { mode: "structure"; blockId: string; field: string; start: number; end: number; translationRevision?: number }
  | { mode: "page"; page: number; rects: [] };

export type Annotation = {
  id: string;
  paperId: string;
  documentId: string;
  resultId: string | null;
  kind: "highlight" | "note" | "page_note";
  color: AnnotationColor;
  excerpt: string;
  comment: string;
  anchor: AnnotationAnchor;
  context: Record<string, unknown>;
  revision: string;
  deleted: boolean;
  stale: boolean;
  canNavigate: boolean;
  contentKind?: string;
  contentLabel?: string;
  notice: string;
  createdAt: string;
  updatedAt: string;
};

export type AnnotationColor = "violet" | "blue" | "pink" | "amber";

export const ANNOTATION_COLORS: Array<{ value: AnnotationColor; label: string }> = [
  { value: "violet", label: "淡紫" },
  { value: "blue", label: "灰蓝" },
  { value: "pink", label: "雾粉" },
  { value: "amber", label: "暖黄" },
];

export type NotePayload = {
  paperId: string;
  markdown: string;
  revision: string | null;
  exists: boolean;
  updatedAt: string | null;
  entries: Array<{ id: string; kind: "excerpt" | "answer"; content: Record<string, unknown>; createdAt: string }>;
  conflicts: Array<{ id: string; markdown: string; baseRevision: string; currentRevision: string; createdAt: string }>;
  duplicate?: boolean;
};

// Rotation-aware normalisation: rects are stored in the page's unrotated frame
// so zoom, rotation, fit-width and window resizes all keep them correct.
function rotateRect(
  rect: { x: number; y: number; w: number; h: number },
  rotation: number,
  inverse = false,
): { x: number; y: number; w: number; h: number } {
  const step = ((rotation % 360) + 360) % 360;
  const value = inverse ? (360 - step) % 360 : step;
  if (value === 90) {
    return { x: 1 - rect.y - rect.h, y: rect.x, w: rect.h, h: rect.w };
  }
  if (value === 180) {
    return { x: 1 - rect.x - rect.w, y: 1 - rect.y - rect.h, w: rect.w, h: rect.h };
  }
  if (value === 270) {
    return { x: rect.y, y: 1 - rect.x - rect.w, w: rect.h, h: rect.w };
  }
  return rect;
}

export function selectionRects(
  range: Range,
  rotation: number,
): Array<{ x: number; y: number; w: number; h: number; page: number }> {
  // Each visual line keeps its own rectangle, resolved to the page it actually
  // sits on, so multi-line, cross-page and two-column selections never cover the
  // gaps or the neighbouring column.
  const hosts = Array.from(document.querySelectorAll<HTMLElement>(".page-host[data-page]"));
  if (!hosts.length) return [];
  const boxes = hosts.map((host) => ({ host, box: host.getBoundingClientRect() }));
  const rects: Array<{ x: number; y: number; w: number; h: number; page: number }> = [];
  for (const client of Array.from(range.getClientRects())) {
    if (client.width < 1 || client.height < 1) continue;
    const cx = client.left + client.width / 2;
    const cy = client.top + client.height / 2;
    const match = boxes.find(
      ({ box }) => cx >= box.left && cx <= box.right && cy >= box.top && cy <= box.bottom,
    );
    if (!match) continue;
    const surface = (match.host.querySelector(".pdf-page") as HTMLElement) || match.host;
    const box = surface.getBoundingClientRect();
    if (!box.width || !box.height) continue;
    const page = Number(match.host.getAttribute("data-page"));
    if (!page) continue;
    const normalised = {
      x: Math.min(Math.max((client.left - box.left) / box.width, 0), 1),
      y: Math.min(Math.max((client.top - box.top) / box.height, 0), 1),
      w: Math.min(Math.max(client.width / box.width, 0), 1),
      h: Math.min(Math.max(client.height / box.height, 0), 1),
    };
    rects.push({ ...rotateRect(normalised, rotation, true), page });
  }
  return rects.slice(0, 200);
}

export function selectionContext(range: Range): { before: string; after: string } {
  const container = range.startContainer;
  const text = container.textContent || "";
  const start = range.startOffset;
  const end = container === range.endContainer ? range.endOffset : start;
  return {
    before: text.slice(Math.max(0, start - 60), start),
    after: text.slice(end, end + 60),
  };
}

export function useAnnotations(paperId: string, documentId?: string | null) {
  const [items, setItems] = useState<Annotation[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const sequence = useRef(0);

  const query = useCallback(
    (cursor?: string | null) => {
      const params = new URLSearchParams({ limit: "100" });
      if (documentId) params.set("documentId", documentId);
      if (cursor) params.set("cursor", cursor);
      return `/api/paper/${encodeURIComponent(paperId)}/reading/annotations?${params.toString()}`;
    },
    [paperId, documentId],
  );

  // Reading order inside the paper: document, then original before translated,
  // then block order / page, so versions never interleave.
  const ordered = (values: Annotation[]) =>
    [...values].sort((a, b) => {
      const left = (a as any).orderKey || [];
      const right = (b as any).orderKey || [];
      for (let i = 0; i < Math.max(left.length, right.length); i += 1) {
        const x = left[i] ?? "";
        const y = right[i] ?? "";
        if (x === y) continue;
        return x < y ? -1 : 1;
      }
      return 0;
    });

  const refresh = useCallback(async () => {
    const id = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const value = await api<{ annotations: Annotation[]; nextCursor: string | null; total: number }>(
        query(null),
      );
      if (id !== sequence.current) return;
      setItems(ordered(value.annotations || []));
      setNextCursor(value.nextCursor ?? null);
      setTotal(value.total ?? (value.annotations || []).length);
    } catch (e) {
      if (id === sequence.current) setError(String((e as Error).message || e));
    } finally {
      if (id === sequence.current) setLoading(false);
    }
  }, [query]);

  const loadMore = useCallback(async () => {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const value = await api<{ annotations: Annotation[]; nextCursor: string | null; total: number }>(
        query(nextCursor),
      );
      setItems((current) => ordered([...current, ...(value.annotations || [])]));
      setNextCursor(value.nextCursor ?? null);
      setTotal(value.total ?? 0);
    } catch (e) {
      setError(String((e as Error).message || e));
    } finally {
      setLoadingMore(false);
    }
  }, [nextCursor, loadingMore, query]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function mutate<T>(operation: () => Promise<T>): Promise<T | null> {
    setBusy(true);
    setError("");
    try {
      const value = await operation();
      await refresh();
      return value;
    } catch (e) {
      setError(String((e as Error).message || e));
      return null;
    } finally {
      setBusy(false);
    }
  }

  return {
    items,
    total,
    hasMore: Boolean(nextCursor),
    loading,
    loadingMore,
    busy,
    error,
    refresh,
    loadMore,
    create: (body: Record<string, unknown>) =>
      mutate(() => api(`/api/paper/${encodeURIComponent(paperId)}/reading/annotations`, "POST", body)),
    update: (id: string, body: Record<string, unknown>) =>
      mutate(() => api(`/api/paper/${encodeURIComponent(paperId)}/reading/annotations/${id}`, "PUT", body)),
    remove: (id: string, revision: string) =>
      mutate(() => api(`/api/paper/${encodeURIComponent(paperId)}/reading/annotations/${id}`, "DELETE", { revision })),
    restore: (id: string, revision: string) =>
      mutate(() => api(`/api/paper/${encodeURIComponent(paperId)}/reading/annotations/${id}`, "PUT", { revision, restore: true })),
  };
}

export function HighlightOverlay({
  annotations,
  page,
  rotation,
  onOpen,
}: {
  annotations: Annotation[];
  page: number;
  rotation: number;
  onOpen?: (annotation: Annotation) => void;
}) {
  const marks = useMemo(() => {
    const output: Array<{ annotation: Annotation; style: Record<string, string> }> = [];
    for (const annotation of annotations) {
      if (annotation.deleted || annotation.anchor?.mode !== "pdf") continue;
      for (const rect of annotation.anchor.rects) {
        if (rect.page && rect.page !== page) continue;
        const shown = rotateRect(rect, rotation);
        output.push({
          annotation,
          style: {
            left: `${shown.x * 100}%`,
            top: `${shown.y * 100}%`,
            width: `${shown.w * 100}%`,
            height: `${shown.h * 100}%`,
          },
        });
      }
    }
    return output;
  }, [annotations, page, rotation]);
  if (!marks.length) return null;
  return (
    <div className="reader-highlight-layer" aria-hidden={false}>
      {marks.map(({ annotation, style }, index) => (
        <button
          key={annotation.id + ":" + index}
          type="button"
          className={"reader-highlight color-" + annotation.color + (annotation.stale ? " stale" : "")}
          style={style}
          title={annotation.comment || annotation.excerpt}
          aria-label={`高亮：${annotation.excerpt.slice(0, 24)}`}
          onClick={(event) => {
            event.stopPropagation();
            onOpen?.(annotation);
          }}
        />
      ))}
    </div>
  );
}

// --- the main note ---------------------------------------------------------

// --- unsaved note drafts: session-scoped, never keyed outside owner+paper ----

export type NoteDraft = {
  markdown: string;
  baseRevision: string | null;
  failed: boolean;
  at: number;
};

const noteDrafts = new Map<string, NoteDraft>();

// The signed-in owner, tracked by the shell. Drafts never outlive an account
// change, and nothing is written to persistent storage.
let currentOwner = "";

export function setNoteOwner(ownerId: string) {
  if (currentOwner && currentOwner !== ownerId) clearNoteDrafts(currentOwner);
  currentOwner = ownerId || "";
}

export function noteOwner() {
  return currentOwner;
}

export function noteDraftKey(ownerId: string, paperId: string) {
  return `${ownerId || "anonymous"}::${paperId}`;
}

/** Privacy cleanup: called on logout/session loss so the next account sees nothing. */
export function clearNoteDrafts(ownerId?: string) {
  if (!ownerId) {
    noteDrafts.clear();
    return;
  }
  for (const key of Array.from(noteDrafts.keys())) {
    if (key.startsWith(`${ownerId}::`)) noteDrafts.delete(key);
  }
}

export function hasUnsavedDraft(ownerId: string, paperId: string) {
  const draft = noteDrafts.get(noteDraftKey(ownerId, paperId));
  return Boolean(draft && draft.markdown.trim());
}

export function NoteConflictBanner({
  note,
  onResolve,
  onOpen,
}: {
  note: NotePayload | null;
  onResolve: (conflictId: string, choice: "current" | "draft") => Promise<void> | void;
  onOpen?: () => void;
}) {
  const [show, setShow] = useState(false);
  const pending = (note?.conflicts || []).filter(
    (item) => !String(item.currentRevision || "").startsWith("resolved:"),
  );
  if (!pending.length) return null;
  const conflict = pending[0];
  return (
    <div className="note-conflict-banner" role="alert">
      <p>
        这篇笔记在另一个标签或设备也被编辑过。请选择要保留的版本（两边都会留副本，可随时找回）。
      </p>
      {show && (
        <div className="note-conflict-compare">
          <div>
            <strong>服务器当前版本</strong>
            <pre>{note?.markdown || "（空）"}</pre>
          </div>
          <div>
            <strong>未合并的草稿</strong>
            <pre>{conflict.markdown}</pre>
          </div>
        </div>
      )}
      <div className="button-row">
        <button type="button" onClick={() => setShow((v) => !v)}>
          {show ? "收起对比" : "对比两边"}
        </button>
        <button type="button" onClick={() => void onResolve(conflict.id, "current")}>
          保留服务器版本
        </button>
        <button type="button" onClick={() => void onResolve(conflict.id, "draft")}>
          保留我的草稿
        </button>
        {onOpen && (
          <button type="button" onClick={onOpen}>
            打开笔记
          </button>
        )}
      </div>
    </div>
  );
}

export function NoteEditor({
  paperId,
  ownerId = noteOwner(),
  note,
  onNote,
  onReloadNote,
  onInsertExcerpt,
  entries,
  onOpenSource,
  onResolveConflict,
  onError,
}: {
  paperId: string;
  ownerId: string;
  note: NotePayload | null;
  onNote: (note: NotePayload) => void;
  onReloadNote?: () => Promise<NotePayload | null>;
  onInsertExcerpt?: () => Promise<void> | void;
  entries?: NotePayload["entries"];
  onOpenSource?: (source: { sourceId: string; label: string }) => void;
  onResolveConflict?: (conflictId: string, choice: "current" | "draft") => Promise<void>;
  onError?: (message: string) => void;
}) {
  const key = noteDraftKey(ownerId, paperId);
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<"idle" | "dirty" | "saving" | "saved" | "failed" | "conflict">(
    "idle",
  );
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(false);
  const revision = useRef<string | null>(null);
  const savedText = useRef("");
  const editVersion = useRef(0);
  const desired = useRef<string | null>(null);
  const running = useRef(false);
  const epoch = useRef(0);
  const retries = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);
  const draftRef = useRef("");

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // New paper/owner: never let an old response or the old text leak across.
  useEffect(() => {
    epoch.current += 1;
    desired.current = null;
    retries.current = 0;
    if (timer.current) clearTimeout(timer.current);
  }, [key]);

  // Adopt the server copy only when nothing unsaved is waiting locally — or when
  // the server now holds exactly the local text (a resolved conflict).
  useEffect(() => {
    if (!note) return;
    if (draftRef.current !== savedText.current && draftRef.current !== note.markdown) return;
    if (draftRef.current === note.markdown) {
      noteDrafts.delete(key);
      setMessage("");
    }
    revision.current = note.revision;
    savedText.current = note.markdown;
    draftRef.current = note.markdown;
    setDraft(note.markdown);
    setStatus(note.revision ? "saved" : "idle");
  }, [note?.revision, note?.paperId, key]);

  // Recover a draft that outlived the panel (same session, same owner+paper).
  useEffect(() => {
    const stored = noteDrafts.get(key);
    if (!stored) return;
    if (stored.markdown === (note?.markdown ?? "")) {
      noteDrafts.delete(key);
      return;
    }
    revision.current = stored.baseRevision;
    draftRef.current = stored.markdown;
    setDraft(stored.markdown);
    setStatus(stored.failed ? "failed" : "dirty");
    setMessage(
      stored.failed
        ? "上次保存失败的草稿已恢复，可以直接重试保存。"
        : "这个会话里还有未保存的草稿，已恢复。",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const record = useCallback(
    (markdown: string, failed: boolean) => {
      noteDrafts.set(key, { markdown, baseRevision: revision.current, failed, at: Date.now() });
    },
    [key],
  );

  /** Serialised save pump: one PUT at a time, newest text wins. */
  const pump = useCallback(async () => {
    if (running.current) return;
    running.current = true;
    const myEpoch = epoch.current;
    try {
      while (desired.current !== null && mounted.current && epoch.current === myEpoch) {
        const text = desired.current;
        const version = editVersion.current;
        desired.current = null;
        setStatus("saving");
        setMessage("");
        try {
          const value = await api<{ note: NotePayload }>(
            `/api/paper/${encodeURIComponent(paperId)}/reading/note`,
            "PUT",
            { markdown: text, revision: revision.current },
          );
          if (epoch.current !== myEpoch || !mounted.current) return;
          revision.current = value.note.revision;
          retries.current = 0;
          noteDrafts.delete(key);
          if (editVersion.current === version) {
            savedText.current = text;
            setStatus("saved");
          } else {
            // Newer typing exists: stay honest and keep saving.
            setStatus("dirty");
          }
          onNote({ ...value.note, entries: entries || [], conflicts: [] });
        } catch (e) {
          if (epoch.current !== myEpoch || !mounted.current) return;
          const text2 = String((e as Error).message || e);
          if (/conflict/i.test(text2)) {
            record(text, false);
            setMessage("另一个标签或设备也编辑了这篇笔记，正在读取当前版本…");
            // Load the current note *before* offering the decision, so the
            // revision the buttons send is the one the user is shown.
            await onReloadNote?.();
            if (epoch.current !== myEpoch || !mounted.current) return;
            setStatus("conflict");
            setMessage("另一个标签或设备也编辑了这篇笔记，请选择要保留的版本。");
            return;
          }
          // Unknown outcome (timeout after the server committed): look before
          // retrying so a retry cannot append or overwrite blindly.
          try {
            const current = await api<{ note: NotePayload }>(
              `/api/paper/${encodeURIComponent(paperId)}/reading/note`,
            );
            if (epoch.current === myEpoch && current.note.markdown === text) {
              revision.current = current.note.revision;
              savedText.current = text;
              noteDrafts.delete(key);
              setStatus("saved");
              onNote({ ...current.note, entries: entries || [], conflicts: [] });
              continue;
            }
          } catch {
            /* stay in the failed state; the draft is kept either way */
          }
          record(text, true);
          setStatus("failed");
          setMessage(
            retries.current < 3
              ? "保存失败，内容仍保留在编辑区；将自动重试（最多 3 次），也可以立即重试。"
              : "保存失败，内容仍保留在编辑区；请手动重试。",
          );
          onError?.(text2);
          if (retries.current < 3) {
            retries.current += 1;
            const delay = [3000, 8000, 20000][retries.current - 1] || 20000;
            if (timer.current) clearTimeout(timer.current);
            timer.current = setTimeout(() => {
              desired.current = draftRef.current;
              void pump();
            }, delay);
          }
          return;
        }
      }
    } finally {
      running.current = false;
      // Text typed while the last response was in flight still gets saved.
      if (desired.current !== null && mounted.current && epoch.current === myEpoch) void pump();
    }
  }, [paperId, key, entries, onNote, onReloadNote, onError, record]);

  const queue = useCallback(
    (text: string, delayMs = 1200) => {
      editVersion.current += 1;
      // The editor is controlled: the local value must be updated here, or the
      // next render would snap the textarea back to the last rendered draft.
      setDraft(text);
      draftRef.current = text;
      record(text, false);
      setStatus(text === savedText.current ? "saved" : "dirty");
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => {
        desired.current = draftRef.current;
        void pump();
      }, delayMs);
    },
    [pump, record],
  );

  /** Look at the server before retrying: a lost response is not a failure. */
  const reconcile = useCallback(
    async (text: string): Promise<"saved" | "conflict" | "retry"> => {
      try {
        const current = await api<{ note: NotePayload }>(
          `/api/paper/${encodeURIComponent(paperId)}/reading/note`,
        );
        if (current.note.markdown === text) {
          revision.current = current.note.revision;
          savedText.current = text;
          noteDrafts.delete(key);
          setStatus("saved");
          setMessage("");
          onNote({ ...current.note, entries: entries || [], conflicts: [] });
          return "saved";
        }
        if (current.note.revision !== revision.current) {
          // The note moved on elsewhere: show the real conflict instead of
          // overwriting text this editor never saw.
          revision.current = current.note.revision;
          record(text, false);
          setStatus("conflict");
          setMessage("这篇笔记已被其他页面修改，请选择要保留的版本。");
          await onReloadNote?.();
          return "conflict";
        }
        return "retry";
      } catch {
        return "retry";
      }
    },
    [paperId, key, entries, onNote, onReloadNote, record],
  );

  const retryNow = useCallback(async () => {
    retries.current = 0;
    if (timer.current) clearTimeout(timer.current);
    const outcome = await reconcile(draftRef.current);
    if (outcome !== "retry") return;
    desired.current = draftRef.current;
    record(draftRef.current, false);
    void pump();
  }, [pump, record, reconcile]);

  // Leaving with unsaved text must be a deliberate choice, never a silent loss.
  useEffect(() => {
    const handler = (event: BeforeUnloadEvent) => {
      if (draftRef.current !== savedText.current) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, []);

  const conflict = (note?.conflicts || []).find(
    (item) => !String(item.currentRevision || "").startsWith("resolved:"),
  );
  const statusLabel =
    status === "saving"
      ? "保存中…"
      : status === "saved"
        ? "已保存"
        : status === "failed"
          ? "保存失败，内容仍在编辑区"
          : status === "conflict"
            ? "有并发编辑，待选择"
            : status === "dirty"
              ? "未保存的修改"
              : "尚无笔记";

  return (
    <div className="note-editor">
      <div className="note-editor-bar">
        <span className={"note-status " + status} role="status">
          {statusLabel}
        </span>
        <button type="button" onClick={() => setPreview((v) => !v)}>
          {preview ? "编辑" : "预览"}
        </button>
        <a href={`/api/paper/${encodeURIComponent(paperId)}/reading/note/export.md`} download>
          导出 Markdown
        </a>
      </div>
      {message && <p className={"notice" + (status === "failed" ? " error" : "")}>{message}</p>}
      {status === "conflict" && conflict && (
        <div className="notice">
          <p>保留哪一份？（两边都会留副本，可随时找回）</p>
          <div className="button-row">
            <button
              type="button"
              onClick={() => void onResolveConflict?.(conflict.id, "current")}
            >
              保留服务器版本
            </button>
            <button type="button" onClick={() => void onResolveConflict?.(conflict.id, "draft")}>
              保留我的草稿
            </button>
          </div>
        </div>
      )}
      {status === "failed" && (
        <div className="button-row">
          <button type="button" onClick={() => void retryNow()}>
            重试保存
          </button>
          <button
            type="button"
            onClick={() => {
              noteDrafts.delete(key);
              revision.current = note?.revision ?? null;
              savedText.current = note?.markdown ?? "";
              draftRef.current = savedText.current;
              setDraft(savedText.current);
              setStatus(note?.revision ? "saved" : "idle");
              setMessage("");
            }}
          >
            放弃草稿，回到已保存版本
          </button>
        </div>
      )}
      {preview ? (
        <div className="note-preview">
          <Markdown text={draft || "_（还没有内容）_"} />
        </div>
      ) : (
        <textarea
          className="note-textarea"
          aria-label="论文笔记"
          value={draft}
          placeholder={"写下你的理解…\n\n可以用 Markdown：标题、列表、公式。"}
          onChange={(event) => queue(event.target.value)}
          onBlur={() => {
            if (draftRef.current !== savedText.current) {
              if (timer.current) clearTimeout(timer.current);
              desired.current = draftRef.current;
              void pump();
            }
          }}
        />
      )}
      {onInsertExcerpt && (
        <div className="note-editor-actions">
          <button type="button" onClick={() => void onInsertExcerpt()}>
            插入最新摘录
          </button>
        </div>
      )}
      {!!entries?.length && (
        <details className="note-sources">
          <summary>笔记来源（{entries.length}）</summary>
          <ul>
            {entries.map((entry) => (
              <li key={entry.id}>
                <span>
                  {entry.kind === "excerpt" ? "摘录" : "已保存的 AI 回答"} ·{" "}
                  {String((entry.content as any)?.label || "")}
                </span>
                {(entry.content as any)?.sources?.map((source: any) => (
                  <button
                    key={source.sourceId}
                    type="button"
                    onClick={() => onOpenSource?.({ sourceId: source.sourceId, label: source.label })}
                  >
                    回访来源 {source.label}
                    {source.where ? `（${source.where}）` : ""}
                  </button>
                ))}
                {(entry.content as any)?.anchor && (
                  <span className="muted">
                    {String((entry.content as any).label || "来源")}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      <p className="muted">
        笔记按论文保存，随主题或关键词变化不迁移；导出只包含这篇论文的内容。
      </p>
    </div>
  );
}

export function AnnotationsPanel({
  annotations,
  loading,
  error,
  onOpen,
  onRefresh,
  onDelete,
  onRestore,
  onEdit,
  onOpenNote,
  onLoadMore,
  hasMore,
}: {
  annotations: Annotation[];
  loading: boolean;
  error: string;
  onOpen: (annotation: Annotation) => void;
  onRefresh: () => void;
  onDelete: (annotation: Annotation) => Promise<Annotation | null>;
  onRestore: (annotation: Annotation) => Promise<Annotation | null>;
  onEdit: (annotation: Annotation, values: { comment?: string; color?: AnnotationColor }) => Promise<boolean>;
  onOpenNote?: () => void;
  onLoadMore?: () => void;
  hasMore?: boolean;
}) {
  const [filter, setFilter] = useState<"all" | "pdf" | "structure" | "note">("all");
  const [query, setQuery] = useState("");
  // Undo keeps the *server-returned* deleted record so its revision is valid.
  const [lastDeleted, setLastDeleted] = useState<Annotation | null>(null);
  const [undoProblem, setUndoProblem] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<{ comment: string; color: AnnotationColor }>({
    comment: "",
    color: "violet",
  });
  const [editState, setEditState] = useState<"idle" | "saving" | "failed" | "conflict">("idle");
  const visible = annotations.filter((item) => {
    if (filter === "note" && item.kind === "highlight" && !item.comment) return false;
    if (filter === "pdf" && item.anchor?.mode !== "pdf") return false;
    if (filter === "structure" && item.anchor?.mode !== "structure") return false;
    if (query && !`${item.excerpt} ${item.comment}`.toLowerCase().includes(query.toLowerCase()))
      return false;
    return true;
  });

  return (
    <div className="annotations-panel">
      <div className="annotations-toolbar">
        <label>
          <span className="visually-hidden">筛选批注</span>
          <select value={filter} onChange={(event) => setFilter(event.target.value as typeof filter)}>
            <option value="all">全部</option>
            <option value="pdf">原文 / 版式译文</option>
            <option value="structure">结构内容</option>
            <option value="note">仅批注</option>
          </select>
        </label>
        <label>
          <span className="visually-hidden">搜索批注</span>
          <input
            value={query}
            placeholder="搜索摘录或批注…"
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <button type="button" onClick={onRefresh}>
          刷新
        </button>
      </div>
      <Status error={error} loading={loading && !annotations.length} />
      {!visible.length && !loading && (
        <p className="muted">还没有批注。选中文字后可以保存高亮或写批注。</p>
      )}
      <ul className="annotation-list">
        {visible.map((annotation) => (
          <li key={annotation.id} className={annotation.stale ? "stale" : ""}>
            <button type="button" className="annotation-open" onClick={() => onOpen(annotation)}>
              <span className={"annotation-dot color-" + annotation.color} aria-hidden />
              <span className="annotation-main">
                <span className="annotation-excerpt">{annotation.excerpt || "（页级记录）"}</span>
                <span className="annotation-meta">
                  {annotation.contentLabel ||
                    (annotation.anchor?.mode === "structure" ? "结构内容" : "原文")}{" "}
                  ·{" "}
                  {annotation.anchor?.mode === "structure"
                    ? `块 ${annotation.anchor.blockId}`
                    : `第 ${(annotation.anchor as any)?.page} 页`}
                  {annotation.updatedAt ? ` · ${annotation.updatedAt.slice(0, 10)}` : ""}
                </span>
                {annotation.comment && (
                  <span className="annotation-comment">{annotation.comment}</span>
                )}
                {annotation.notice && <span className="annotation-notice">{annotation.notice}</span>}
              </span>
            </button>
            <div className="annotation-actions">
              <button
                type="button"
                onClick={() => {
                  setEditing(editing === annotation.id ? null : annotation.id);
                  setEditDraft({ comment: annotation.comment, color: annotation.color });
                  setEditState("idle");
                }}
              >
                {editing === annotation.id ? "收起" : "编辑"}
              </button>
              <button
                type="button"
                onClick={async () => {
                  const removed = await onDelete(annotation);
                  if (removed) {
                    // The server's new revision is what makes undo valid.
                    setLastDeleted(removed);
                    setUndoProblem("");
                  } else {
                    setUndoProblem("删除失败，记录仍在列表中，可以重试。");
                  }
                }}
              >
                删除
              </button>
            </div>
            {editing === annotation.id && (
              <div className="annotation-editor">
                <label>
                  批注
                  <textarea
                    aria-label={`编辑批注 ${annotation.excerpt.slice(0, 12)}`}
                    value={editDraft.comment}
                    maxLength={2000}
                    onChange={(e) => setEditDraft((v) => ({ ...v, comment: e.target.value }))}
                  />
                </label>
                <div className="annotation-edit-colors">
                  <span>颜色</span>
                  {ANNOTATION_COLORS.map((item) => (
                    <button
                      key={item.value}
                      type="button"
                      aria-label={`改为${item.label}`}
                      aria-pressed={editDraft.color === item.value}
                      className={
                        "annotation-swatch color-" + item.value + (editDraft.color === item.value ? " selected" : "")
                      }
                      onClick={() => setEditDraft((v) => ({ ...v, color: item.value }))}
                    />
                  ))}
                </div>
                <div className="button-row">
                  <button
                    type="button"
                    disabled={editState === "saving"}
                    onClick={async () => {
                      setEditState("saving");
                      const ok = await onEdit(annotation, editDraft);
                      setEditState(ok ? "idle" : "failed");
                      if (ok) setEditing(null);
                    }}
                  >
                    {editState === "saving" ? "正在保存…" : "保存修改"}
                  </button>
                  <button type="button" onClick={() => setEditing(null)}>
                    取消
                  </button>
                </div>
                {editState === "failed" && (
                  <p className="notice error">
                    保存失败（可能已被其他页面改动）。已保留你的输入，可重试或刷新后再改。
                  </p>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
      {lastDeleted && (
        <div className="notice">
          已删除一条批注。
          <button
            type="button"
            onClick={async () => {
              const restored = await onRestore(lastDeleted);
              if (restored) {
                setLastDeleted(null);
                setUndoProblem("");
              } else {
                setUndoProblem("撤销失败（修订已变化）。可再次尝试或重新创建批注。");
              }
            }}
          >
            撤销
          </button>
          {undoProblem && <p className="notice error">{undoProblem}</p>}
        </div>
      )}
      {hasMore && onLoadMore && (
        <button type="button" onClick={onLoadMore}>
          加载更多批注
        </button>
      )}
      <p className="muted">
        笔记正文在「笔记」页签中编辑；这里管理高亮与批注。上限为每篇 2,000 条。
      </p>
      {onOpenNote && (
        <button type="button" onClick={onOpenNote}>
          打开笔记
        </button>
      )}
    </div>
  );
}

// --- structured content marks ---------------------------------------------

function textRangeAt(element: HTMLElement, start: number, end: number): Range | null {
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  let offset = 0;
  let startNode: Text | null = null;
  let endNode: Text | null = null;
  let startOffset = 0;
  let endOffset = 0;
  let node = walker.nextNode() as Text | null;
  while (node) {
    const length = node.textContent?.length || 0;
    if (startNode === null && offset + length >= start) {
      startNode = node;
      startOffset = Math.max(0, start - offset);
    }
    if (offset + length >= end) {
      endNode = node;
      endOffset = Math.max(0, end - offset);
      break;
    }
    offset += length;
    node = walker.nextNode() as Text | null;
  }
  if (!startNode || !endNode) return null;
  try {
    const range = document.createRange();
    range.setStart(startNode, Math.min(startOffset, startNode.textContent?.length || 0));
    range.setEnd(endNode, Math.min(endOffset, endNode.textContent?.length || 0));
    return range;
  } catch {
    return null;
  }
}

function normalise(value: string) {
  return value.replace(/\s+/g, " ").trim();
}

export function StructuredMarks({
  host,
  blockId,
  translated,
  annotations,
  onUnverified,
}: {
  host: React.RefObject<HTMLElement | null>;
  blockId: string;
  translated: boolean;
  annotations: Annotation[];
  onUnverified?: (annotation: Annotation) => void;
}) {
  const [marks, setMarks] = useState<Array<{ annotation: Annotation; style: Record<string, string> }>>([]);

  useEffect(() => {
    const compute = () => {
      const section = host.current;
      if (!section) {
        setMarks([]);
        return;
      }
      const output: Array<{ annotation: Annotation; style: Record<string, string> }> = [];
      for (const annotation of annotations) {
        if (annotation.deleted || annotation.anchor?.mode !== "structure") continue;
        const anchor = annotation.anchor;
        if (anchor.blockId !== blockId) continue;
        // Original and translated sides are separate revisions: never mix them.
        const isTranslated = anchor.translationRevision != null;
        if (isTranslated !== translated) continue;
        const field = section.querySelector<HTMLElement>(`[data-field="${anchor.field}"]`);
        if (!field) continue;
        const range = textRangeAt(field, anchor.start, anchor.end);
        if (!range) {
          onUnverified?.(annotation);
          continue;
        }
        // The server already rejected stale sources; draw only when the stored
        // excerpt matches the current text in full (not just its opening).
        if (annotation.stale || annotation.canNavigate === false) {
          onUnverified?.(annotation);
          continue;
        }
        const current = normalise(range.toString());
        const expected = normalise(annotation.excerpt);
        if (!current || !expected || current !== expected) {
          onUnverified?.(annotation);
          continue;
        }
        const box = section.getBoundingClientRect();
        if (!box.width || !box.height) continue;
        for (const client of Array.from(range.getClientRects())) {
          if (client.width < 1 || client.height < 1) continue;
          output.push({
            annotation,
            style: {
              left: `${((client.left - box.left) / box.width) * 100}%`,
              top: `${((client.top - box.top) / box.height) * 100}%`,
              width: `${(client.width / box.width) * 100}%`,
              height: `${(client.height / box.height) * 100}%`,
            },
          });
        }
      }
      setMarks(output);
    };
    compute();
    const observer = new ResizeObserver(compute);
    if (host.current) observer.observe(host.current);
    window.addEventListener("resize", compute);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", compute);
    };
  }, [annotations, blockId, translated, host, onUnverified]);

  if (!marks.length) return null;
  return (
    <div className="reader-highlight-layer structured-marks-layer">
      {marks.map(({ annotation, style }, index) => (
        <span
          key={annotation.id + ":" + index}
          className={"reader-highlight color-" + annotation.color + (annotation.stale ? " stale" : "")}
          style={style}
          title={annotation.comment || annotation.excerpt}
        />
      ))}
    </div>
  );
}

/** A hand-written page-level record for scanned or unselectable content. */
export function PageNoteDialog({
  paperId,
  documentId,
  page,
  pageCount,
  onClose,
  onCreated,
}: {
  paperId: string;
  documentId: string;
  page: number;
  pageCount: number;
  onClose: () => void;
  onCreated: () => void;
}) {
  const [value, setValue] = useState(page);
  const [comment, setComment] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "failed">("idle");
  const [problem, setProblem] = useState("");
  return (
    <div className="page-note-dialog" role="dialog" aria-label="页级记录">
      <h3>页级记录</h3>
      <p className="muted">
        这一页没有可选的文字层（或你不想依赖它）。记录只绑定到页码，不会伪造精准选区，也不会触发 OCR。
      </p>
      <label>
        页码（1–{pageCount}）
        <input
          type="number"
          min={1}
          max={pageCount}
          value={value}
          onChange={(event) => setValue(Number(event.target.value))}
        />
      </label>
      <label>
        记录
        <textarea
          aria-label="页级记录内容"
          value={comment}
          maxLength={2000}
          placeholder="这一页的要点…"
          onChange={(event) => setComment(event.target.value)}
        />
      </label>
      {problem && <p className="notice error">{problem}</p>}
      <div className="button-row">
        <button
          type="button"
          disabled={state === "saving" || !comment.trim() || value < 1 || value > pageCount}
          onClick={async () => {
            setState("saving");
            setProblem("");
            try {
              await api(`/api/paper/${encodeURIComponent(paperId)}/reading/annotations`, "POST", {
                documentId,
                kind: "page_note",
                color: "blue",
                excerpt: "",
                comment: comment.trim(),
                anchor: { mode: "page", page: value },
                context: {},
              });
              onCreated();
              onClose();
            } catch (e) {
              setState("failed");
              setProblem(String((e as Error).message || e));
            }
          }}
        >
          {state === "saving" ? "正在保存…" : "保存页级记录"}
        </button>
        <button type="button" onClick={onClose}>
          取消
        </button>
      </div>
    </div>
  );
}
