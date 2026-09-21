import {
  useEffect,
  useRef,
  useState,
  type ReactNode,
  type RefObject,
} from "react";
import type { PDFDocumentProxy } from "pdfjs-dist/legacy/build/pdf.mjs";
import {
  Search,
  BookmarkPlus,
  X,
  ChevronLeft,
  ChevronRight,
  Languages,
} from "lucide-react";
import { api, Status, Modal } from "./ui";
import { errorText } from "./api";
import { ANNOTATION_COLORS, type AnnotationColor } from "./PaperNotes";
import { PdfSearch, type PageSearch } from "./pdfSearch";
import type { Match } from "./readingSearch";
import {
  languageLabels,
  processingNames,
  type ProcessingResult,
} from "./Processing";
import type { Excerpt } from "./Chat";
import "./reading-tools.css";
export type Identity = {
  id: string;
  sha256: string;
  pageCount: number;
  kind: string;
};
export type Bookmark = {
  id: string;
  name: string;
  documentId: string;
  documentKind: string;
  resultId: string | null;
  revision: string;
  location: any;
  canNavigate: boolean;
  notice: string;
  stale: boolean;
};
export type Heading = {
  blockId: string;
  title: string;
  level: number | null;
  page?: number;
  regions: { page: number; rect: number[] }[];
  precision: string;
};
export function useReadingShortcut(
  root: RefObject<HTMLElement | null>,
  open: () => void,
  enabled = true,
) {
  const action = useRef(open);
  action.current = open;
  useEffect(() => {
    if (!enabled) return;
    const key = (e: KeyboardEvent) => {
      if (
        (e.ctrlKey || e.metaKey) &&
        e.key.toLowerCase() === "f" &&
        root.current?.getClientRects().length &&
        !(e.target as HTMLElement)?.closest(
          "input,textarea,[contenteditable=true]",
        )
      ) {
        e.preventDefault();
        action.current();
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [root, enabled]);
}
export function useDocumentIdentity(
  paperId: string,
  document: string,
  documentId?: string,
  ready = true,
) {
  const [identity, setIdentity] = useState<Identity | null>(null),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  useEffect(() => {
    setIdentity(null);
    setError("");
    if (!ready) return;
    const c = new AbortController();
    void api(
      `/api/paper/${encodeURIComponent(paperId)}/reading-document`,
      "POST",
      { document, documentId },
      c.signal,
    )
      .then((r) => {
        if (!c.signal.aborted) setIdentity(r.document);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      });
    return () => c.abort();
  }, [paperId, document, documentId, ready, retry]);
  return { identity, error, retry: () => setRetry((v) => v + 1) };
}
function WindowList<T>({
  items,
  render,
  compact = false,
}: {
  items: T[];
  render: (item: T, index: number) => ReactNode;
  compact?: boolean;
}) {
  const [top, setTop] = useState(0),
    ref = useRef<HTMLDivElement>(null);
  const height = compact ? 34 : 72,
    first = Math.max(0, Math.floor(top / height) - 2),
    last = Math.min(items.length, first + 12);
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = 0;
    setTop(0);
  }, [items.length === 0]);
  return (
    <div
      className={compact ? "tool-window compact" : "tool-window"}
      ref={ref}
      onScroll={(e) => setTop(e.currentTarget.scrollTop)}
    >
      <Space height={first * height} />
      {items.slice(first, last).map((item, index) => (
        <div className="tool-window-row" key={first + index}>
          {render(item, first + index)}
        </div>
      ))}
      <Space height={(items.length - last) * height} />
    </div>
  );
}
function Space({ height }: { height: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.style.height = height + "px";
  }, [height]);
  return <div aria-hidden ref={ref} />;
}
export function Bookmarks({
  paperId,
  identity,
  resultId,
  current,
  onJump,
}: {
  paperId: string;
  identity: Identity | null;
  resultId?: string;
  current: () => any;
  onJump: (b: Bookmark) => void;
}) {
  const [items, setItems] = useState<Bookmark[]>([]),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [name, setName] = useState(""),
    [editing, setEditing] = useState<Bookmark | null>(null),
    [creating, setCreating] = useState(false);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    const c = new AbortController();
    void api(`/api/paper/${paperId}/bookmarks`, "GET", undefined, c.signal)
      .then((r) => setItems(r.bookmarks))
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      });
    return () => {
      alive.current = false;
      c.abort();
    };
  }, [paperId]);
  async function change(action: "save" | "delete", item?: Bookmark) {
    setBusy(true);
    setError("");
    try {
      const target = item || editing;
      if (action === "delete" && target) {
        await api(`/api/paper/${paperId}/bookmarks/${target.id}`, "DELETE", {
          revision: target.revision,
        });
        if (alive.current) setItems((v) => v.filter((b) => b.id !== target.id));
      } else {
        const value = target
          ? await api(`/api/paper/${paperId}/bookmarks/${target.id}`, "PUT", {
              name,
              revision: target.revision,
            })
          : await api(`/api/paper/${paperId}/bookmarks`, "POST", {
              name,
              documentId: identity?.id,
              resultId,
              location: current(),
            });
        if (alive.current) {
          setItems((v) => [
            ...v.filter((b) => b.id !== value.bookmark.id),
            value.bookmark,
          ]);
          setCreating(false);
          setEditing(null);
        }
      }
    } catch (e) {
      if (alive.current) setError(errorText(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  return (
    <div className="bookmarks">
      <button
        disabled={!identity || busy}
        onClick={() => {
          setName(current()?.page ? `第 ${current().page} 页` : "阅读位置");
          setCreating(true);
        }}
      >
        <BookmarkPlus size={15} />
        保存当前位置
      </button>
      <Status error={error} />
      {!items.length && (
        <p className="muted">尚未保存书签。书签与自动阅读位置分别保存。</p>
      )}
      {items.map((item) => (
        <div className="bookmark-row" key={item.id}>
          <button
            disabled={!item.canNavigate || busy}
            title={item.notice}
            onClick={() => onJump(item)}
          >
            {item.name}
            <small>
              {item.resultId
                ? "结构内容"
                : item.documentKind === "original"
                  ? "原始 PDF"
                  : "版式译文"}
              {item.location.page ? ` · 第 ${item.location.page} 页` : ""}
            </small>
          </button>
          <div>
            <button
              disabled={busy}
              onClick={() => {
                setEditing(item);
                setName(item.name);
              }}
            >
              重命名
            </button>
            <button disabled={busy} onClick={() => void change("delete", item)}>
              删除
            </button>
          </div>
          {item.notice && <p className="muted">{item.notice}</p>}
        </div>
      ))}
      {(creating || editing) && (
        <Modal
          title={editing ? "重命名书签" : "保存书签"}
          onClose={() => {
            if (!busy) {
              setCreating(false);
              setEditing(null);
            }
          }}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void change("save");
            }}
          >
            <label>
              书签名称
              <input
                autoFocus
                maxLength={120}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <p className="muted">
              保存当前文档或结构版本，不改变自动恢复位置。
            </p>
            <Status error={error} />
            <button className="primary" disabled={busy || !name.trim()}>
              保存
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}
export function PdfNavigation({
  doc,
  paperId,
  identity,
  identityError,
  onIdentityRetry,
  current,
  onJump,
  onBookmark,
  structureResult,
  thumbnail,
  tab,
  setTab,
  onClose,
  onMatch,
}: {
  doc: PDFDocumentProxy | null;
  paperId: string;
  identity: Identity | null;
  identityError: string;
  onIdentityRetry: () => void;
  current: () => any;
  onJump: (page: number, offset?: number) => void;
  onBookmark: (b: Bookmark) => void;
  structureResult?: ProcessingResult;
  thumbnail: ReactNode;
  tab: string;
  setTab: (v: string) => void;
  onClose: () => void;
  onMatch: (page: number, match: Match | null) => void;
}) {
  const [query, setQuery] = useState(""),
    [sensitive, setSensitive] = useState(false),
    [pages, setPages] = useState<PageSearch[]>([]),
    [done, setDone] = useState(false),
    [error, setError] = useState(""),
    [active, setActive] = useState(-1),
    [excerpt, setExcerpt] = useState(""),
    [headings, setHeadings] = useState<
      { title: string; page: number; offset: number; depth: number }[]
    >([]),
    [outlineBusy, setOutlineBusy] = useState(false);
  const engine = useRef<PdfSearch | null>(null),
    sequence = useRef(0),
    input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (tab === "search") input.current?.focus();
  }, [tab]);
  useEffect(() => {
    engine.current?.destroy();
    engine.current = null;
    setPages([]);
    setDone(false);
    setError("");
    setActive(-1);
    setExcerpt("");
    onMatch(0, null);
    sequence.current++;
    if (!doc || !query.trim() || tab !== "search") return;
    const search = new PdfSearch(doc, query, sensitive);
    engine.current = search;
    const timer = setTimeout(
      () =>
        void search.scan(setPages).then(() => {
          if (engine.current === search) setDone(true);
        }),
      180,
    );
    return () => {
      clearTimeout(timer);
      search.destroy();
      if (engine.current === search) engine.current = null;
    };
  }, [doc, query, sensitive, tab]);
  useEffect(() => {
    if (!doc) return;
    let alive = true;
    const c = new AbortController();
    setHeadings([]);
    setOutlineBusy(true);
    setError("");
    void (async () => {
      const native = await doc.getOutline();
      const items: {
        title: string;
        page: number;
        offset: number;
        depth: number;
      }[] = [];
      async function visit(entries: any[], depth: number) {
        if (depth > 16) return;
        for (const entry of entries) {
          if (!alive || items.length >= 5000) return;
          let dest = entry.dest;
          if (typeof dest === "string") {
            try {
              dest = await doc!.getDestination(dest);
            } catch {
              dest = null;
            }
          }
          if (Array.isArray(dest)) {
            try {
              const page =
                typeof dest[0] === "number"
                  ? dest[0] + 1
                  : (await doc!.getPageIndex(dest[0])) + 1;
              if (page >= 1 && page <= doc!.numPages) {
                const p = await doc!.getPage(page),
                  vp = p.getViewport({ scale: 1 });
                const y =
                  dest[1]?.name === "XYZ"
                    ? dest[3]
                    : dest[1]?.name === "FitH"
                      ? dest[2]
                      : null;
                const offset =
                  typeof y === "number"
                    ? Math.max(
                        0,
                        Math.min(
                          1,
                          vp.convertToViewportPoint(p.view[0], y)[1] /
                            vp.height,
                        ),
                      )
                    : 0;
                items.push({ title: entry.title, page, offset, depth });
              }
            } catch {
              /* Invalid or external targets are never executed. */
            }
          }
          if (entry.items) await visit(entry.items, depth + 1);
        }
      }
      if (native?.length) await visit(native, 0);
      if (
        !items.length &&
        identity?.kind === "original" &&
        structureResult?.documentId === identity.id &&
        !structureResult.stale
      ) {
        let after: number | null = -1;
        while (after !== null && alive && items.length < 5000) {
          const r: any = await api(
            `/api/results/${structureResult.id}/navigation?after=${after}`,
            "GET",
            undefined,
            c.signal,
          );
          for (const h of r.items)
            if (h.page)
              items.push({
                title: h.title,
                page: h.page,
                offset: 0,
                depth: Math.min(h.level || 0, 6),
              });
          after = r.nextCursor;
        }
      }
      if (alive) setHeadings(items);
    })()
      .catch((e) => {
        if (alive) setError(errorText(e));
      })
      .finally(() => {
        if (alive) setOutlineBusy(false);
      });
    return () => {
      alive = false;
      c.abort();
    };
  }, [doc, identity?.id, structureResult?.id]);
  const total = pages.reduce((n, p) => n + p.count, 0),
    failures = pages.filter((p) => p.error).length;
  async function choose(index: number) {
    if (!engine.current || !total) return;
    index = (index + total) % total;
    const serial = ++sequence.current,
      search = engine.current;
    setActive(index);
    let start = 0;
    for (const row of pages) {
      if (index < start + row.count) {
        try {
          const r = await search.matches(row.page, index - start, 1);
          if (serial !== sequence.current || search !== engine.current) return;
          const match = r.matches[0];
          setExcerpt(
            match ? match.before + "【" + match.text + "】" + match.after : "",
          );
          onMatch(row.page, match || null);
          onJump(row.page);
        } catch (e) {
          if (serial === sequence.current) setError(errorText(e));
        }
        return;
      }
      start += row.count;
    }
  }
  const hitPages = pages.filter((p) => p.count);
  let before = 0;
  const hitItems = hitPages.map((p) => {
    const item = { ...p, start: before };
    before += p.count;
    return item;
  });
  return (
    <aside className="reading-navigation" aria-label="阅读导航">
      <div className="navigation-tabs">
        {[
          ["outline", "目录"],
          ["thumbnails", "缩略图"],
          ["bookmarks", "书签"],
        ].map(([key, name]) => (
          <button
            key={key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {name}
          </button>
        ))}
        <button aria-label="关闭阅读导航" onClick={onClose}>
          <X size={15} />
        </button>
      </div>
      <button
        className={
          tab === "search" ? "active nav-search-button" : "nav-search-button"
        }
        onClick={() => setTab("search")}
      >
        <Search size={15} />
        搜索全文
      </button>
      {tab === "thumbnails" && thumbnail}
      {tab === "outline" && (
        <>
          <Status loading={outlineBusy} error={error} />
          {!outlineBusy && !headings.length && (
            <p className="muted">
              没有可用目录。可以使用缩略图或书签，不会自动生成目录。
            </p>
          )}
          <WindowList
            compact
            items={headings}
            render={(h) => (
              <button title={h.title} onClick={() => onJump(h.page, h.offset)}>
                <span>
                  {"· ".repeat(h.depth)}
                  {h.title}
                </span>
                <small>第 {h.page} 页</small>
              </button>
            )}
          />
        </>
      )}
      {tab === "bookmarks" && (
        <>
          <Status error={identityError} retry={onIdentityRetry} />
          <Bookmarks
            paperId={paperId}
            identity={identity}
            current={current}
            onJump={onBookmark}
          />
        </>
      )}
      {tab === "search" && (
        <div className="reading-search">
          <input
            ref={input}
            aria-label="搜索 PDF 全文"
            placeholder="搜索当前 PDF"
            value={query}
            maxLength={256}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void choose(active + (e.shiftKey ? -1 : 1));
              }
              if (e.key === "Escape") {
                setQuery("");
                setTab("outline");
              }
            }}
          />
          <label>
            <input
              type="checkbox"
              checked={sensitive}
              onChange={(e) => setSensitive(e.target.checked)}
            />
            区分英文大小写
          </label>
          <div className="search-count" role="status">
            {query &&
              `${done ? "扫描结束" : "正在搜索"}：${pages.length} / ${doc?.numPages || 0} 页 · ${total} 处匹配`}
            {failures > 0 && <p>{failures} 页文字无法提取，搜索范围不完整。</p>}
            {done &&
              pages.length === doc?.numPages &&
              !pages.some((p) => p.characters) &&
              !failures && (
                <p>此 PDF 没有可搜索文字，可能是扫描件。未自动 OCR。</p>
              )}
            {done &&
              pages.length === doc?.numPages &&
              total === 0 &&
              pages.some((p) => p.characters) &&
              !failures && <p>全文没有匹配。</p>}
          </div>
          <div className="search-controls">
            <button
              aria-label="上一个匹配"
              disabled={!total}
              onClick={() => void choose(active - 1)}
            >
              <ChevronLeft size={16} />
            </button>
            <span>
              {active >= 0 ? active + 1 : 0} / {total}
            </span>
            <button
              aria-label="下一个匹配"
              disabled={!total}
              onClick={() => void choose(active + 1)}
            >
              <ChevronRight size={16} />
            </button>
            {!done && pages.length > 0 && (
              <button
                onClick={() => {
                  engine.current?.destroy();
                  engine.current = null;
                  setDone(true);
                  setError("搜索已停止，以上为已扫描范围。");
                }}
              >
                停止搜索
              </button>
            )}
          </div>
          <Status error={error} />
          {excerpt && <p className="search-excerpt">{excerpt}</p>}
          <WindowList
            items={hitItems}
            render={(p) => (
              <button onClick={() => void choose(p.start)}>
                <span>第 {p.page} 页</span>
                <small>{p.count} 处匹配 · 点击定位第一处</small>
              </button>
            )}
          />
        </div>
      )}
    </aside>
  );
}
export type SelectionInput = {
  text: string;
  documentId: string;
  resultId?: string;
  sourceId?: string;
  page?: number;
  document: "original" | "translated";
  title: string;
};
export function SelectionPopup({
  paperId,
  selection,
  onClose,
  onAsk,
  onSaveAnnotation,
}: {
  paperId: string;
  selection: SelectionInput;
  onClose: () => void;
  onAsk: (excerpt: Excerpt) => void;
  /** Save a persistent highlight/annotation for this selection (optional). */
  onSaveAnnotation?: (payload: {
    kind: "highlight" | "note";
    color: AnnotationColor;
    comment: string;
    excerpt: string;
    anchor: unknown;
    context: unknown;
  }) => Promise<boolean>;
}) {
  const [language, setLanguage] = useState("zh-CN"),
    [preview, setPreview] = useState<any>(null),
    [job, setJob] = useState<any>(null),
    [translation, setTranslation] = useState<any>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [color, setColor] = useState<AnnotationColor>("violet"),
    [comment, setComment] = useState(""),
    [annotationState, setAnnotationState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const request = useRef<AbortController | null>(null),
    alive = useRef(true);
  const payload = {
    text: selection.text,
    documentId: selection.documentId,
    resultId: selection.resultId,
    sourceId: selection.sourceId,
    targetLanguage: language,
  };
  useEffect(() => {
    alive.current = true;
    setPreview(null);
    setJob(null);
    setTranslation(null);
    setError("");
    const c = new AbortController();
    request.current = c;
    void api(
      `/api/paper/${paperId}/selection-translation/preview`,
      "POST",
      payload,
      c.signal,
    )
      .then((r) => {
        if (!c.signal.aborted) {
          setPreview(r);
          setTranslation(r.cached);
        }
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      });
    return () => {
      alive.current = false;
      c.abort();
      request.current?.abort();
    };
  }, [paperId, selection, language]);
  useEffect(() => {
    if (!job || !["queued", "running", "cancelling"].includes(job.status))
      return;
    let timer: ReturnType<typeof setTimeout>;
    const c = new AbortController();
    const poll = async () => {
      try {
        const r = await api(
          `/api/processing/jobs/${job.id}`,
          "GET",
          undefined,
          c.signal,
        );
        if (c.signal.aborted) return;
        if (r.job.status === "completed") {
          const result = await api(
            `/api/selection-translations/${r.job.resultId}`,
            "GET",
            undefined,
            c.signal,
          );
          if (!c.signal.aborted) {
            setTranslation(result.translation);
            setJob(r.job);
          }
        } else if (["queued", "running", "cancelling"].includes(r.job.status)) {
          setJob(r.job);
          timer = setTimeout(() => void poll(), 1000);
        } else {
          setJob(r.job);
          setError(
            r.job.error ? errorText({ code: r.job.error }) : "任务已停止。",
          );
        }
      } catch (e) {
        if (!c.signal.aborted) setError(errorText(e));
      }
    };
    timer = setTimeout(() => void poll(), 600);
    return () => {
      c.abort();
      clearTimeout(timer);
    };
  }, [job?.id, job?.status]);
  async function translate() {
    setBusy(true);
    setError("");
    const c = new AbortController();
    request.current = c;
    try {
      const r = await api(
        `/api/paper/${paperId}/selection-translation`,
        "POST",
        {
          ...payload,
          retryJobId:
            job && ["failed", "cancelled", "interrupted"].includes(job.status)
              ? job.id
              : undefined,
        },
        c.signal,
      );
      if (c.signal.aborted) return;
      setJob(r.job || null);
      setTranslation(r.translation || null);
    } catch (e) {
      if (!c.signal.aborted) setError(errorText(e));
    } finally {
      if (alive.current && !c.signal.aborted) setBusy(false);
    }
  }
  const active =
    job && ["queued", "running", "cancelling"].includes(job.status);
  return (
    <Modal
      title={onSaveAnnotation ? "划词与批注" : "划词翻译"}
      onClose={onClose}
      className="selection-translation-dialog"
    >
      <label>
        目标语言
        <select
          value={language}
          disabled={busy || active}
          onChange={(e) => setLanguage(e.target.value)}
        >
          {Object.entries(languageLabels).map(([key, name]) => (
            <option key={key} value={key}>
              {name}
            </option>
          ))}
        </select>
      </label>
      <p className="muted">
        {preview?.model || "读取结构化翻译配置…"} ·{" "}
        {preview?.sourcePrecision === "unverified"
          ? "临时选区，未核实精确来源"
          : "保留已核实来源"}
      </p>
      <h3>原文</h3>
      <p className="selection-original">{selection.text}</p>
      {onSaveAnnotation && (
        <section className="selection-annotation">
          <div className="selection-annotation-row">
            <span>高亮颜色</span>
            {ANNOTATION_COLORS.map((item) => (
              <button
                key={item.value}
                type="button"
                className={
                  "annotation-swatch color-" + item.value + (color === item.value ? " selected" : "")
                }
                aria-label={`使用${item.label}高亮`}
                aria-pressed={color === item.value}
                onClick={() => setColor(item.value)}
              />
            ))}
          </div>
          <label>
            批注（可留空只保存高亮）
            <input
              value={comment}
              maxLength={2000}
              placeholder="写下你的理解…"
              onChange={(e) => setComment(e.target.value)}
            />
          </label>
          <div className="dialog-actions">
            <button
              className="primary"
              disabled={annotationState === "saving" || !(selection as any).anchor}
              onClick={async () => {
                setAnnotationState("saving");
                const ok = await onSaveAnnotation({
                  kind: comment.trim() ? "note" : "highlight",
                  color,
                  comment: comment.trim(),
                  excerpt: selection.text,
                  anchor: (selection as any).anchor,
                  context: (selection as any).context || {},
                });
                setAnnotationState(ok ? "saved" : "failed");
              }}
            >
              {annotationState === "saving"
                ? "正在保存…"
                : annotationState === "saved"
                  ? "已保存批注"
                  : "保存高亮/批注"}
            </button>
          </div>
          {annotationState === "failed" && (
            <p className="notice error">保存失败：内容仍在输入框中，可重试。</p>
          )}
          {!(selection as any).anchor && (
            <p className="muted">这段文字没有可用的位置信息，只能作为页级记录保存。</p>
          )}
        </section>
      )}
      {translation && (
        <>
          <h3>译文{preview?.cached ? " · 已有缓存" : ""}</h3>
          <p className="selection-result">{translation.translation}</p>
          <button
            onClick={() =>
              void navigator.clipboard
                .writeText(translation.translation)
                .catch(() => setError("复制失败，请手动选择译文。"))
            }
          >
            复制译文
          </button>
        </>
      )}
      <Status error={error} />
      {job && <p role="status">{processingNames[job.status] || job.status}</p>}
      <div className="dialog-actions">
        <button
          className="primary"
          disabled={!preview || busy || active || !!translation}
          onClick={() => void translate()}
        >
          <Languages size={16} />
          {busy ? "正在提交…" : job ? "明确重试" : "翻译"}
        </button>
        <button
          onClick={() =>
            onAsk({
              text: selection.text,
              page: selection.page,
              document: selection.document,
              title: selection.title,
              sourceId: selection.sourceId,
            })
          }
        >
          带入问答
        </button>
        {active && (
          <button
            onClick={() =>
              void api(`/api/processing/jobs/${job.id}/cancel`, "POST", {})
                .then(() => {
                  if (alive.current) setJob({ ...job, status: "cancelling" });
                })
                .catch((e) => setError(errorText(e)))
            }
          >
            取消任务
          </button>
        )}
      </div>
      <p className="muted">
        关闭仅停止展示；取消任务停止后续处理，不能确认供应商已取消或未计费。译文不会覆盖整块翻译。
      </p>
    </Modal>
  );
}
export type StructureHit = {
  blockId: string;
  order: number;
  field: string;
  display: "original" | "translated";
  start: number;
  end: number;
  before: string;
  match: string;
  after: string;
  page?: number;
};
export function StructureNavigation({
  paperId,
  result,
  current,
  onJump,
  onBookmark,
  onClose,
  tab,
  setTab,
}: {
  paperId: string;
  result: ProcessingResult;
  current: () => any;
  onJump: (
    blockId: string,
    offset?: number,
    display?: string,
    hit?: StructureHit,
  ) => void;
  onBookmark: (b: Bookmark) => void;
  onClose: () => void;
  tab: string;
  setTab: (v: string) => void;
}) {
  const [headings, setHeadings] = useState<Heading[]>([]),
    [query, setQuery] = useState(""),
    [scope, setScope] = useState("original"),
    [sensitive, setSensitive] = useState(false),
    [matches, setMatches] = useState<StructureHit[]>([]),
    [progress, setProgress] = useState<any>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [active, setActive] = useState(-1),
    [totalMatches, setTotalMatches] = useState(0),
    [batchIndex, setBatchIndex] = useState(0),
    [batchCount, setBatchCount] = useState(0);
  const batches = useRef<
    { cursor: string | null; start: number; count: number }[]
  >([]);
  const choice = useRef(0);
  const input = useRef<HTMLInputElement>(null),
    search = useRef<AbortController | null>(null);
  useEffect(() => {
    if (tab === "search") input.current?.focus();
  }, [tab]);
  useEffect(() => {
    const c = new AbortController();
    void (async () => {
      let after: number | null = -1;
      const items: Heading[] = [];
      while (after !== null) {
        const r: any = await api(
          `/api/results/${result.id}/navigation?after=${after}`,
          "GET",
          undefined,
          c.signal,
        );
        items.push(...r.items);
        after = r.nextCursor;
      }
      if (!c.signal.aborted) setHeadings(items);
    })().catch((e) => {
      if (!c.signal.aborted) setError(errorText(e));
    });
    return () => c.abort();
  }, [result.id]);
  useEffect(() => {
    search.current?.abort();
    const c = new AbortController();
    search.current = c;
    setMatches([]);
    batches.current = [];
    choice.current++;
    setTotalMatches(0);
    setBatchIndex(0);
    setBatchCount(0);
    setProgress(null);
    setActive(-1);
    setError("");
    setBusy(false);
    if (!query.trim() || tab !== "search") return;
    const timer = setTimeout(() => {
      setBusy(true);
      void (async () => {
        let cursor: string | null = null;
        do {
          const r: any = await api(
            `/api/results/${result.id}/search`,
            "POST",
            { query, scope, caseSensitive: sensitive, cursor },
            c.signal,
          );
          if (c.signal.aborted) return;
          if (r.matches.length) {
            const previous = batches.current.at(-1);
            const start = previous ? previous.start + previous.count : 0;
            batches.current.push({ cursor, start, count: r.matches.length });
            if (batches.current.length === 1) setMatches(r.matches);
            setTotalMatches(start + r.matches.length);
            setBatchCount(batches.current.length);
          }
          setProgress(r);
          cursor = r.cursor;
          if(cursor && !c.signal.aborted) await new Promise<void>((resolve) => {
            const finish = () => { clearTimeout(timer); c.signal.removeEventListener("abort",finish); resolve(); };
            const timer = setTimeout(finish,650);
            c.signal.addEventListener("abort",finish,{once:true});
          });
        } while (cursor && !c.signal.aborted);
      })()
        .catch((e) => {
          if (!c.signal.aborted) setError(errorText(e));
        })
        .finally(() => {
          if (!c.signal.aborted) setBusy(false);
        });
    }, 180);
    return () => {
      c.abort();
      clearTimeout(timer);
    };
  }, [query, scope, sensitive, result.id, tab]);
  async function showBatch(index: number, matchIndex?: number) {
    const selected = batches.current[index];
    if (!selected) return;
    const token = ++choice.current;
    try {
      const response: any = await api(
        `/api/results/${result.id}/search`,
        "POST",
        { query, scope, caseSensitive: sensitive, cursor: selected.cursor },
        search.current?.signal,
      );
      if (token !== choice.current || search.current?.signal.aborted) return;
      setMatches(response.matches);
      setBatchIndex(index);
      if (matchIndex !== undefined) {
        const hit = response.matches[matchIndex];
        if (hit) {
          setActive(selected.start + matchIndex);
          onJump(hit.blockId, 0, hit.display, hit);
        }
      }
    } catch (e) {
      if (token === choice.current && !search.current?.signal.aborted)
        setError(errorText(e));
    }
  }
  function choose(index: number) {
    if (!totalMatches) return;
    index = (index + totalMatches) % totalMatches;
    const batch = batches.current.findIndex(
      (b) => index >= b.start && index < b.start + b.count,
    );
    const local = index - batches.current[batch].start;
    if (batch !== batchIndex) {
      void showBatch(batch, local);
      return;
    }
    setActive(index);
    const hit = matches[local];
    if (hit) onJump(hit.blockId, 0, hit.display, hit);
  }
  return (
    <aside className="reading-navigation" aria-label="结构阅读导航">
      <div className="navigation-tabs">
        {[
          ["outline", "目录"],
          ["bookmarks", "书签"],
          ["search", "搜索"],
        ].map(([key, name]) => (
          <button
            key={key}
            className={tab === key ? "active" : ""}
            onClick={() => setTab(key)}
          >
            {name}
          </button>
        ))}
        <button aria-label="关闭阅读导航" onClick={onClose}>
          <X size={15} />
        </button>
      </div>
      <Status error={error} />
      {tab === "outline" && (
        <>
          {!headings.length && (
            <p className="muted">没有可用结构标题，不会自动生成目录。</p>
          )}
          <WindowList
            compact
            items={headings}
            render={(h) => (
              <button title={h.title} onClick={() => onJump(h.blockId)}>
                <span>{h.title}</span>
                <small>{h.page ? `第 ${h.page} 页` : "结构内容"}</small>
              </button>
            )}
          />
        </>
      )}
      {tab === "bookmarks" && (
        <Bookmarks
          paperId={paperId}
          identity={{
            id: result.documentId,
            sha256: result.sourceHash,
            pageCount: result.pageCount,
            kind: "original",
          }}
          resultId={result.id}
          current={current}
          onJump={(b) => {
            if (b.resultId === result.id)
              onJump(b.location.blockId, b.location.offset, b.location.display);
            else onBookmark(b);
          }}
        />
      )}
      {tab === "search" && (
        <div className="reading-search">
          <input
            ref={input}
            value={query}
            maxLength={256}
            aria-label="搜索结构全文"
            placeholder="搜索当前结构结果"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                choose(active + (e.shiftKey ? -1 : 1));
              }
              if (e.key === "Escape") {
                setQuery("");
                setTab("outline");
              }
            }}
          />
          <select
            aria-label="结构搜索范围"
            value={scope}
            onChange={(e) => setScope(e.target.value)}
          >
            <option value="original">原文</option>
            <option
              value="translated"
              disabled={result.kind !== "structured_translation"}
            >
              译文
            </option>
            <option
              value="both"
              disabled={result.kind !== "structured_translation"}
            >
              原文与译文
            </option>
          </select>
          <label>
            <input
              type="checkbox"
              checked={sensitive}
              onChange={(e) => setSensitive(e.target.checked)}
            />
            区分英文大小写
          </label>
          <p className="search-count" role="status">
            {progress
              ? `${busy ? "正在搜索" : "已扫描"} ${progress.scannedBlocks} / ${progress.totalBlocks} 块，${totalMatches} 处匹配`
              : "输入关键词搜索全部结构内容。"}
            {progress && scope !== "original" && (
              <span>
                ；已有译文 {progress.translatedBlocks} / {progress.totalBlocks}{" "}
                块
              </span>
            )}
            {progress?.complete && !matches.length && "。所选范围没有匹配。"}
          </p>
          <div className="search-controls">
            <button
              aria-label="上一个匹配"
              disabled={!matches.length}
              onClick={() => choose(active - 1)}
            >
              <ChevronLeft size={16} />
            </button>
            <span>
              {active + 1} / {totalMatches}
            </span>
            <button
              aria-label="下一个匹配"
              disabled={!matches.length}
              onClick={() => choose(active + 1)}
            >
              <ChevronRight size={16} />
            </button>
            {busy && (
              <button
                onClick={() => {
                  search.current?.abort();
                  setBusy(false);
                  setError("搜索已停止，以上为已扫描范围。");
                }}
              >
                停止搜索
              </button>
            )}
          </div>
          {batchCount > 1 && (
            <div className="search-controls">
              <button
                disabled={batchIndex === 0}
                onClick={() => void showBatch(batchIndex - 1)}
              >
                前一批
              </button>
              <span>
                结果 {batchIndex + 1} / {batchCount} 批
              </span>
              <button
                disabled={batchIndex + 1 === batchCount}
                onClick={() => void showBatch(batchIndex + 1)}
              >
                后一批
              </button>
            </div>
          )}
          <WindowList
            items={matches}
            render={(m, index) => (
              <button
                onClick={() =>
                  choose((batches.current[batchIndex]?.start || 0) + index)
                }
                title={m.before + m.match + m.after}
              >
                <span>
                  {m.before}
                  <mark>{m.match}</mark>
                  {m.after}
                </span>
                <small>
                  {m.display === "original" ? "原文" : "译文"} ·{" "}
                  {m.page ? `第 ${m.page} 页` : "结构内容"}
                </small>
              </button>
            )}
          />
        </div>
      )}
    </aside>
  );
}
