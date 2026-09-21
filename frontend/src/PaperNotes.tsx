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
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const sequence = useRef(0);
  const path = `/api/paper/${encodeURIComponent(paperId)}/reading/annotations${
    documentId ? `?documentId=${encodeURIComponent(documentId)}&limit=200` : "?limit=200"
  }`;
  const refresh = useCallback(async () => {
    const id = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const value = await api<{ annotations: Annotation[] }>(path);
      if (id === sequence.current) setItems(value.annotations || []);
    } catch (e) {
      if (id === sequence.current) setError(String((e as Error).message || e));
    } finally {
      if (id === sequence.current) setLoading(false);
    }
  }, [path]);
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
    loading,
    busy,
    error,
    refresh,
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

export function NoteEditor({
  paperId,
  note,
  onNote,
  onInsertExcerpt,
  onResolveConflict,
  onError,
}: {
  paperId: string;
  note: NotePayload | null;
  onNote: (note: NotePayload) => void;
  onInsertExcerpt?: () => void;
  onResolveConflict?: (conflictId: string, choice: "current" | "draft") => Promise<void>;
  onError?: (message: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const [status, setStatus] = useState<"idle" | "dirty" | "saving" | "saved" | "failed">("idle");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(false);
  const revision = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saved = useRef("");

  useEffect(() => {
    if (!note) return;
    // A different note (or a server-side change) replaces the editor content,
    // but never while an unsaved local edit is pending.
    if (status === "dirty" || status === "saving") return;
    setDraft(note.markdown);
    saved.current = note.markdown;
    revision.current = note.revision;
    // A saved revision (not the `exists` flag alone) is what may be called saved.
    setStatus(note.revision ? "saved" : "idle");
  }, [note?.revision, note?.paperId]);

  const save = useCallback(
    async (markdown: string) => {
      setStatus("saving");
      setMessage("");
      try {
        const value = await api<{ note: NotePayload }>(
          `/api/paper/${encodeURIComponent(paperId)}/reading/note`,
          "PUT",
          { markdown, revision: revision.current },
        );
        revision.current = value.note.revision;
        saved.current = markdown;
        setStatus(markdown === value.note.markdown ? "saved" : "dirty");
        onNote({ ...value.note, entries: note?.entries || [], conflicts: [] });
      } catch (e) {
        const text = String((e as Error).message || e);
        setStatus("failed");
        // A conflict keeps the local text and asks the user which side to keep.
        setMessage(
          /conflict/i.test(text)
            ? "另一个标签或设备也编辑了这篇笔记：你的内容仍保留在编辑区，请选择保留哪一份。"
            : "保存失败，内容仍保留在编辑区；恢复后会自动重试。",
        );
        onError?.(text);
      }
    },
    [paperId, note?.entries, onNote, onError],
  );

  useEffect(() => {
    if (status !== "dirty") return;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => void save(draft), 1200);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [draft, status, save]);

  const conflict = note?.conflicts?.[0];
  const statusLabel =
    status === "saving"
      ? "保存中…"
      : status === "saved"
        ? "已保存"
        : status === "failed"
          ? "保存失败，内容仍在编辑区"
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
      {message && <p className="notice error">{message}</p>}
      {conflict && (
        <div className="notice">
          <p>检测到并发编辑（草稿保存在服务端）。保留哪一份？</p>
          <div className="button-row">
            <button
              type="button"
              onClick={() => void onResolveConflict?.(conflict.id, "current")}
            >
              保留服务器版本
            </button>
            <button
              type="button"
              onClick={() => void onResolveConflict?.(conflict.id, "draft")}
            >
              保留我的草稿
            </button>
          </div>
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
          onChange={(event) => {
            setDraft(event.target.value);
            setStatus(event.target.value === saved.current ? "saved" : "dirty");
          }}
          onBlur={() => {
            if (status === "dirty") void save(draft);
          }}
        />
      )}
      {onInsertExcerpt && (
        <div className="note-editor-actions">
          <button type="button" onClick={onInsertExcerpt}>
            插入最新摘录
          </button>
        </div>
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
  note,
  onOpen,
  onRefresh,
  onNote,
  onDelete,
  onRestore,
  onError,
}: {
  annotations: Annotation[];
  loading: boolean;
  error: string;
  note: NotePayload | null;
  onOpen: (annotation: Annotation) => void;
  onRefresh: () => void;
  onNote: (note: NotePayload) => void;
  onDelete: (annotation: Annotation) => void;
  onRestore: (annotation: Annotation) => void;
  onError: (message: string) => void;
}) {
  const [filter, setFilter] = useState<"all" | "pdf" | "structure" | "note">("all");
  const [query, setQuery] = useState("");
  const [lastDeleted, setLastDeleted] = useState<Annotation | null>(null);
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
                  {annotation.anchor?.mode === "structure"
                    ? "结构内容"
                    : annotation.context?.documentKind === "babeldoc_dual" ||
                        annotation.context?.documentKind === "babeldoc_mono"
                      ? "版式译文"
                      : "原文"}{" "}
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
                  setLastDeleted(annotation);
                  onDelete(annotation);
                }}
              >
                删除
              </button>
            </div>
          </li>
        ))}
      </ul>
      {lastDeleted && (
        <div className="notice">
          已删除一条批注。
          <button
            type="button"
            onClick={() => {
              onRestore(lastDeleted);
              setLastDeleted(null);
            }}
          >
            撤销
          </button>
        </div>
      )}
      <NoteEditor paperId={note?.paperId || ""} note={note} onNote={onNote} onError={onError} />
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
        // Verify the stored excerpt against the current text before drawing;
        // after a re-parse or retranslation the offsets may no longer match.
        const current = normalise(range.toString());
        const expected = normalise(annotation.excerpt);
        if (!current || !expected || !current.startsWith(expected.slice(0, Math.max(4, Math.min(expected.length, 24))))) {
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
