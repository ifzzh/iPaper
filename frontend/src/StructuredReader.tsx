import { MathFormula, AcademicText, sourceSelection } from "./MathFormula";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
  type ComponentProps,
  type ReactNode,
} from "react";
import {
  ArrowLeft,
  Languages,
  Quote,
  MapPin,
  MessageSquare,
  X,
  Layers,
  Search,
  PanelLeft,
} from "lucide-react";
import { PdfReader } from "./Reader";
import { Chat, type Excerpt } from "./Chat";
import { api, Status, useResource, timestampText, Modal } from "./ui";
import { errorText, ApiError } from "./api";
import {
  TranslationDialog,
  type ProcessingResult,
  type Source,
  processingNames,
  languageLabels,
} from "./Processing";
import "./structured.css";
import {
  StructureNavigation,
  SelectionPopup,
  useReadingShortcut,
  type Bookmark,
  type SelectionInput,
  type StructureHit,
} from "./ReadingTools";
import { MediaBoundary } from "./MediaViewer";
import {
  ANNOTATION_COLORS,
  StructuredMarks,
  AnnotationsPanel,
  NoteEditor,
  useAnnotations,
  type Annotation,
  type AnnotationColor,
  type NotePayload,
} from "./PaperNotes";

type Cell = { text: string; rowspan: number; colspan: number; header: boolean };
type Content = { text: string; caption: string; table: Cell[][] | null };
type Block = Content & {
  id: string;
  order: number;
  type: string;
  level: number | null;
  imageUrl: string | null;
  source: {
    page?: number;
    precision: string;
    regions: { page: number; rect: number[] }[];
  };
  translation: {
    status: string;
    error: string | null;
    revision: string | null;
    content?: Content;
  } | null;
};
type Display = "original" | "translated" | "bilingual";
type ReaderProps = ComponentProps<typeof PdfReader>;

export function Reader(props: ReaderProps) {
  const initial = props.preferences.readerResults?.[props.paper.id];
  const query = new URLSearchParams(location.search);
  const [mode, setMode] = useState<string>(() =>
    ["structure", "original", "translated"].includes(query.get("content") || "")
      ? query.get("content")!
      : query.has("document")
        ? props.translated
          ? "translated"
          : "original"
        : initial?.mode || (props.translated ? "translated" : "original"),
  );
  const [resultId, setResultId] = useState<string>(
    () =>
      (query.get("content") === "structure" ? query.get("result") : "") ||
      initial?.structureResultId ||
      (initial?.mode === "structure" ? initial?.resultId : "") ||
      "",
  );
  const [layoutId, setLayoutId] = useState<string>(
    () =>
      (query.get("content") === "translated" ? query.get("result") : "") ||
      initial?.layoutResultId ||
      (initial?.mode === "translated" ? initial?.resultId : "") ||
      "",
  );
  const [generate, setGenerate] = useState(false),
    [source, setSource] = useState<Source | null>(null),
    [sourceError, setSourceError] = useState(""),
    [returnTo, setReturnTo] = useState<string | null>(null);
  const [bookmarkTarget, setBookmarkTarget] = useState<Bookmark | null>(null);
  const [sourceExcerpt, setSourceExcerpt] = useState<any>(null);
  const results = useResource<{
    results: ProcessingResult[];
    registrationWarning?: string;
  }>(`/api/paper/${encodeURIComponent(props.paper.id)}/results`, {
    results: [],
  });
  const alive = useRef(true),
    navigation = useRef<AbortController | null>(null);
  useEffect(() => {
    if (props.sourceTarget) {
      setSource({
        ...props.sourceTarget,
        document: "original",
        canNavigate: true,
        stale: false,
      } as Source);
      setMode("original");
    }
  }, [props.sourceTarget?.id]);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      navigation.current?.abort();
    };
  }, []);
  const selected =
    results.data.results.find(
      (r) =>
        r.id === resultId &&
        ["structure", "structured_translation"].includes(r.kind),
    ) ||
    results.data.results.find((r) => r.kind === "structured_translation") ||
    results.data.results.find((r) => r.kind === "structure");
  useEffect(() => {
    if (mode === "structure" && selected && selected.id !== resultId)
      setResultId(selected.id);
  }, [mode, selected?.id]);
  useEffect(() => {
    props.onPreferences({
      readerResults: {
        ...props.preferences.readerResults,
        [props.paper.id]: {
          mode,
          resultId: mode === "translated" ? layoutId : resultId,
          structureResultId: resultId,
          layoutResultId: layoutId,
        },
      },
    });
    const url = new URL(location.href);
    url.searchParams.set("content", mode);
    const chosen =
      mode === "translated" ? layoutId : mode === "structure" ? resultId : "";
    if (chosen) url.searchParams.set("result", chosen);
    else url.searchParams.delete("result");
    history.replaceState(null, "", url);
  }, [mode, resultId, layoutId]);
  function change(value: string) {
    setSource(null);
    setMode(value);
    if (value !== "structure") props.onVersion(value === "translated");
  }
  async function navigateSource(id: string) {
    navigation.current?.abort();
    const c = new AbortController();
    navigation.current = c;
    setSourceError("");
    try {
      const r = await api<{ source: Source }>(
        `/api/sources/${encodeURIComponent(id)}`,
        "GET",
        undefined,
        c.signal,
      );
      if (c.signal.aborted) return;
      if (!r.source.canNavigate) {
        if (r.source.text) {
          setSourceExcerpt(r.source);
          return;
        }
        setSourceError(
          r.source.stale
            ? "原始文件已变化，保留的内容仍可读，当前来源无法定位。"
            : "此内容没有可靠的 PDF 页码。",
        );
        return;
      }
      setReturnTo(selected?.id || null);
      setSource(r.source);
      setMode(r.source.document === "translated" ? "translated" : "original");
      props.onVersion(r.source.document === "translated");
    } catch (e) {
      if (!c.signal.aborted) setSourceError(errorText(e));
    }
  }
  const layoutResults = results.data.results.filter((r) =>
    r.kind.startsWith("babeldoc_"),
  );
  const selectedLayout =
    layoutResults.find((r) => r.id === layoutId) || layoutResults[0];
  useEffect(() => {
    if (
      mode === "translated" &&
      selectedLayout &&
      selectedLayout.id !== layoutId
    )
      setLayoutId(selectedLayout.id);
  }, [mode, selectedLayout?.id]);
  function bookmarkNavigate(bookmark: Bookmark) {
    if (!bookmark.canNavigate) {
      setSourceError(bookmark.notice);
      return;
    }
    setSource(null);
    setBookmarkTarget(bookmark);
    if (bookmark.resultId) {
      setMode("structure");
      setResultId(bookmark.resultId);
    } else {
      setMode(bookmark.documentKind === "original" ? "original" : "translated");
      const layout = layoutResults.find(
        (r) => r.documentId === bookmark.documentId,
      );
      if (layout) setLayoutId(layout.id);
    }
  }
  const controls = (
    <>
      <select
        aria-label="阅读内容"
        value={mode}
        onChange={(e) => change(e.target.value)}
      >
        <option value="original">原始 PDF</option>
        {(props.paper.translated ||
          props.translated ||
          layoutResults.length > 0) && (
          <option value="translated">版式译文</option>
        )}
        <option value="structure">结构阅读</option>
      </select>
      {mode === "translated" && layoutResults.length > 0 && (
        <select
          aria-label="版式结果版本"
          value={selectedLayout?.id}
          onChange={(e) => setLayoutId(e.target.value)}
        >
          {layoutResults.map((r) => (
            <option key={r.id} value={r.id}>
              {r.kind === "babeldoc_mono" ? "纯译文 PDF" : "双语 PDF"} ·{" "}
              {r.provenance === "historical_config_unknown"
                ? "历史结果，配置未知"
                : timestampText(r.createdAt)}
            </option>
          ))}
        </select>
      )}
      <button onClick={() => setGenerate(true)}>
        <Languages size={16} />
        <span>生成翻译</span>
      </button>
      {returnTo && mode !== "structure" && (
        <button
          onClick={() => {
            setSource(null);
            setMode("structure");
            setResultId(returnTo);
            setReturnTo(null);
          }}
        >
          <ArrowLeft size={16} />
          返回结构内容
        </button>
      )}
    </>
  );
  return (
    <>
      {sourceExcerpt && (
        <Modal title="来源摘录" onClose={() => setSourceExcerpt(null)}>
          <p>
            {sourceExcerpt.stale
              ? "原始文件已变化，以下为保存的原文摘录。"
              : "此来源没有可靠的 PDF 页码或区域，保留原文供核对。"}
          </p>
          <p className="source-excerpt">{sourceExcerpt.text}</p>
        </Modal>
      )}
      {results.data.registrationWarning && (
        <p className="notice">
          版式译文版本信息暂时无法读取，已保留现有文件。
          <button onClick={results.refresh}>重试</button>
        </p>
      )}
      {sourceError && (
        <div className="notice error" role="alert">
          {sourceError}
          <button onClick={() => setSourceError("")}>关闭</button>
        </div>
      )}
      {mode === "structure" ? (
        <main className="structured-workspace">
          <div className="reader-toolbar">
            {props.toolbarLead}
            {controls}
            {selected && (
              <select
                aria-label="结构结果版本"
                value={selected.id}
                onChange={(e) => setResultId(e.target.value)}
              >
                {results.data.results
                  .filter((r) =>
                    ["structure", "structured_translation"].includes(r.kind),
                  )
                  .map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.kind === "structure"
                        ? "结构原文"
                        : `${languageLabels[r.targetLanguage] || r.targetLanguage} · ${r.model || "翻译"}`}{" "}
                      · {processingNames[r.status] || r.status} ·{" "}
                      {timestampText(r.createdAt)}
                      {r.stale ? " · 来源已变化" : ""}
                    </option>
                  ))}
              </select>
            )}
          </div>
          <Status
            error={results.error}
            loading={results.loading && !selected}
            retry={results.refresh}
          />
          {selected ? (
            <StructureContent
              key={selected.id}
              {...props}
              result={selected}
              onSource={navigateSource}
              onChanged={results.refresh}
              bookmarkTarget={
                bookmarkTarget?.resultId === selected.id
                  ? bookmarkTarget
                  : undefined
              }
              onBookmarkNavigate={bookmarkNavigate}
            />
          ) : (
            <div className="empty-state">
              <Layers size={40} />
              <h2>按论文结构阅读</h2>
              <p>
                当前尚无结构内容。MinerU
                先解析论文，再由配置的模型生成逐块译文；已有 PDF 译文会保留。
              </p>
              <button className="primary" onClick={() => setGenerate(true)}>
                生成结构内容
              </button>
            </div>
          )}
        </main>
      ) : (
        <PdfReader
          {...props}
          translated={mode === "translated"}
          toolbarContent={controls}
          sourceTarget={source || undefined}
          fileDocumentId={
            mode === "translated"
              ? source?.documentId || selectedLayout?.documentId
              : undefined
          }
          onSource={navigateSource}
          structureResult={results.data.results.find(
            (r) => r.kind === "structure" && !r.stale,
          )}
          translationResults={results.data.results.filter(
            (r) => r.kind === "structured_translation",
          )}
          cachedTranslationId={resultId}
          onCachedTranslation={setResultId}
          bookmarkTarget={
            bookmarkTarget && !bookmarkTarget.resultId
              ? bookmarkTarget
              : undefined
          }
          onBookmarkNavigate={bookmarkNavigate}
        />
      )}
      {generate && (
        <TranslationDialog
          paper={props.paper}
          initial={mode === "structure" ? "structure" : "babeldoc"}
          onClose={() => {
            setGenerate(false);
            results.refresh();
          }}
          onSubmitted={results.refresh}
        />
      )}
    </>
  );
}

function Sized({
  height,
  className,
  children,
}: {
  height: number;
  className?: string;
  children?: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (ref.current) ref.current.style.height = Math.max(0, height) + "px";
  }, [height]);
  return (
    <div ref={ref} className={className} aria-hidden={!children}>
      {children}
    </div>
  );
}
function MeasuredBlock({
  searchHit,
  block,
  onMeasure,
  children,
}: {
  searchHit?: StructureHit | null;
  block: Block;
  onMeasure: (id: string, height: number) => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const node = ref.current!;
    const obs = new ResizeObserver(() =>
      onMeasure(block.id, node.getBoundingClientRect().height),
    );
    obs.observe(node);
    return () => obs.disconnect();
  }, [block.id, onMeasure]);
  return (
    <article
      ref={ref}
      className={"structure-block" + (searchHit ? " search-target-block" : "")}
      data-block={block.id}
      data-order={block.order}
    >
      {searchHit && (
        <p className="search-excerpt" role="status">
          搜索匹配：{searchHit.before}
          <mark>{searchHit.match}</mark>
          {searchHit.after}
        </p>
      )}
      {children}
    </article>
  );
}
function BlockContent({
  content,
  block,
  label,
  translated,
  onSelection,
  onSource,
  annotations = [],
}: {
  content: Content;
  block: Block;
  label: string;
  translated: boolean;
  onSource?: () => void;
  onSelection: (
    block: Block,
    field: string,
    start: number,
    end: number,
    text: string,
    translated: boolean,
  ) => void;
  annotations?: Annotation[];
}) {
  const section = useRef<HTMLElement>(null);
  function select(e: React.SyntheticEvent<HTMLElement>) {
    const selection = window.getSelection();
    if (!selection?.rangeCount || selection.isCollapsed) return;
    const range = selection.getRangeAt(0),
      node = e.currentTarget;
    if (
      !node.contains(range.startContainer) ||
      !node.contains(range.endContainer)
    )
      return;
    const field = node.dataset.field || "text";
    let canonical = field === "caption" ? content.caption : content.text;
    if (field.startsWith("cell:")) {
      const [, r, c] = field.split(":");
      canonical = content.table?.[Number(r)]?.[Number(c)]?.text || "";
    }
    try {
      const selected = sourceSelection(node, range, canonical);
      if (selected.end - selected.start > 6000 || !selected.text.trim()) return;
      onSelection(
        block,
        field,
        selected.start,
        selected.end,
        selected.text,
        translated,
      );
    } catch {
      /* A selection spanning unrelated UI is not a valid source. */
    }
  }
  return (
    <MediaBoundary onSource={onSource}>
      <section
        className="block-language"
        data-language={translated ? "translated" : "original"}
        ref={section}
      >
        <span className="block-language-label">{label}</span>
        {block.imageUrl && !translated && (
          <img
            className="structure-image"
            src={block.imageUrl}
            alt={content.caption || "论文图片"}
            loading="lazy"
          />
        )}
        {content.table ? (
          <div className="structure-table-scroll">
            <table>
              <tbody>
                {content.table.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) =>
                      cell.header ? (
                        <th
                          key={c}
                          data-field={`cell:${r}:${c}`}
                          onMouseUp={select}
                          onTouchEnd={select}
                          rowSpan={cell.rowspan}
                          colSpan={cell.colspan}
                        >
                          <AcademicText text={cell.text} />
                        </th>
                      ) : (
                        <td
                          key={c}
                          data-field={`cell:${r}:${c}`}
                          onMouseUp={select}
                          onTouchEnd={select}
                          rowSpan={cell.rowspan}
                          colSpan={cell.colspan}
                        >
                          <AcademicText text={cell.text} />
                        </td>
                      ),
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          content.text && (
            <div
              className={
                block.level
                  ? "block-section-title"
                  : ["equation", "interline_equation", "formula"].includes(
                        block.type,
                      )
                    ? "structure-formula"
                    : "structure-paragraph"
              }
              data-field="text"
              onTouchEnd={
                ["equation", "interline_equation", "formula"].includes(
                  block.type,
                )
                  ? undefined
                  : select
              }
              onMouseUp={
                ["equation", "interline_equation", "formula"].includes(
                  block.type,
                )
                  ? undefined
                  : select
              }
            >
              {["equation", "interline_equation", "formula"].includes(
                block.type,
              ) ? (
                <MathFormula text={content.text} />
              ) : (
                <AcademicText text={content.text} />
              )}
            </div>
          )
        )}
        {content.caption && (
          <div
            className="structure-caption"
            data-field="caption"
            onMouseUp={select}
            onTouchEnd={select}
          >
            <AcademicText text={content.caption} />
          </div>
        )}
        {block.type === "table" && !content.table && (
          <p className="muted">此表格仅有图片，未识别出可翻译的单元格正文。</p>
        )}
        <StructuredMarks
          host={section}
          blockId={block.id}
          translated={translated}
          annotations={annotations}
        />
      </section>
    </MediaBoundary>
  );
}
function StructureContent(
  props: ReaderProps & {
    result: ProcessingResult;
    onSource: (id: string) => void;
    onChanged: () => void;
  },
) {
  const { result, paper } = props;
  const [navOpen, setNavOpen] = useState(false),
    [navTab, setNavTab] = useState(
      props.preferences.navigationPanel === "bookmarks"
        ? "bookmarks"
        : "outline",
    ),
    [searchHit, setSearchHit] = useState<StructureHit | null>(null),
    [translateSelection, setTranslateSelection] =
      useState<SelectionInput | null>(null);
  const jumpSequence = useRef(0);
  const root = useRef<HTMLDivElement>(null),
    alive = useRef(true),
    controller = useRef(new AbortController()),
    loadLock = useRef(false),
    restoring = useRef(true),
    lastPosition = useRef<any>(null);
  const [blocks, setBlocks] = useState<Block[]>([]),
    [next, setNext] = useState<number | null>(-1),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [display, setDisplay] = useState<Display>(
      result.kind === "structured_translation" ? "bilingual" : "original",
    ),
    [scroll, setScroll] = useState(0),
    [viewport, setViewport] = useState(800),
    [measureRevision, setMeasureRevision] = useState(0);
  const heights = useRef(new Map<string, number>()),
    restorePoint = useRef<{ blockId: string; offset: number } | null>(null),
    [excerpt, setExcerpt] = useState<Excerpt | null>(null),
    [selection, setSelection] = useState<any>(null),
    [panelTab, setPanelTab] = useState<"chat" | "annotations" | "note">("chat"),
    [note, setNote] = useState<NotePayload | null>(null),
    [noteError, setNoteError] = useState(""),
    [openedAnnotation, setOpenedAnnotation] = useState<Annotation | null>(null),
    [annotationState, setAnnotationState] = useState<"idle" | "saving" | "saved" | "failed">("idle"),
    [annotationColor, setAnnotationColor] = useState<AnnotationColor>("violet"),
    [annotationComment, setAnnotationComment] = useState(""),
    [chat, setChat] = useState(!matchMedia("(max-width:640px)").matches),
    [session, setSession] = useState(""),
    [retryBlock, setRetryBlock] = useState<string | null>(null);
  const sessionRef = useRef(session);
  sessionRef.current = session;
  const captureRef = useRef<() => void>(() => {});
  const after = useRef(-1),
    body = useRef<HTMLDivElement>(null),
    resizeCleanup = useRef<(() => void) | null>(null);
  const [comparison, setComparison] = useState<Source | null>(null);
  useReadingShortcut(body, () => {
    setNavOpen(true);
    setNavTab("search");
  });
  async function jumpBlock(
    id: string,
    offset = 0,
    mode?: string,
    hit?: StructureHit,
  ) {
    const sequence = ++jumpSequence.current;
    try {
      const b = await api(
        `/api/results/${result.id}/blocks/${encodeURIComponent(id)}`,
        "GET",
        undefined,
        controller.current.signal,
      );
      if (sequence !== jumpSequence.current || !alive.current) return;
      if (mode && ["original", "translated", "bilingual"].includes(mode)) {
        heights.current.clear();
        setDisplay(mode as Display);
      }
      setSearchHit(hit || null);
      restorePoint.current = { blockId: id, offset };
      const response = await api(
        `/api/results/${result.id}/blocks?after=${Math.max(-1, b.block.order - 1)}&limit=50`,
        "GET",
        undefined,
        controller.current.signal,
      );
      if (sequence !== jumpSequence.current || !alive.current) return;
      setBlocks(response.blocks);
      setNext(response.nextCursor);
      setScroll(0);
      if (matchMedia("(max-width:900px)").matches) setNavOpen(false);
    } catch (e) {
      if (alive.current) setError(errorText(e));
    }
  }
  useEffect(() => {
    if (props.bookmarkTarget?.resultId === result.id)
      void jumpBlock(
        props.bookmarkTarget.location.blockId,
        props.bookmarkTarget.location.offset,
        props.bookmarkTarget.location.display,
      );
  }, [props.bookmarkTarget?.id]);
  const selectionRef = useRef(selection);
  selectionRef.current = selection;
  async function translateSelected() {
    if (!selection) return;
    const snapshot = selection;
    try {
      const source = await sourceFor(
        snapshot.block,
        snapshot.field,
        snapshot.start,
        snapshot.end,
        snapshot.translated,
      );
      if (alive.current && selectionRef.current === snapshot)
        setTranslateSelection({
          text: source.text,
          sourceId: source.id,
          documentId: result.documentId,
          resultId: result.id,
          page: source.page,
          document: snapshot.translated ? "translated" : "original",
          title: paper.title,
        });
    } catch (e) {
      if (alive.current) setError(errorText(e));
    }
  }
  useEffect(() => () => resizeCleanup.current?.(), []);
  useLayoutEffect(() => {
    body.current?.style.setProperty(
      "--structure-chat-width",
      `${props.preferences.chatWidth || 380}px`,
    );
  }, [props.preferences.chatWidth]);
  function setWidth(width: number) {
    const value = Math.max(300, Math.min(640, width));
    body.current?.style.setProperty("--structure-chat-width", value + "px");
    return value;
  }
  function resize(e: React.PointerEvent) {
    e.preventDefault();
    const start = e.clientX,
      width =
        body.current?.querySelector(".structure-chat")?.getBoundingClientRect()
          .width || 380;
    let current = width;
    const move = (event: PointerEvent) => {
      current = setWidth(width + start - event.clientX);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      props.onPreferences({ chatWidth: Math.round(current) });
    };
    resizeCleanup.current = stop;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }
  useEffect(() => {
    const timer = setInterval(() => {
      if (
        blocks.length &&
        document.visibilityState === "visible" &&
        document.hasFocus() &&
        !(chat && matchMedia("(max-width:640px)").matches)
      ) {
        // One request per effective 30s tick. The server splits it at UTC+8
        // midnight, validates ownership and dedupes by tick_id; the legacy day
        // aggregate is updated server-side so the two stores cannot fork.
        const tickId =
          typeof crypto !== "undefined" && "randomUUID" in crypto
            ? crypto.randomUUID()
            : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
        void api(
          `/api/paper/${encodeURIComponent(paper.id)}/read-time`,
          "POST",
          { seconds: 30, tick_id: tickId, ended_at: Date.now() / 1000 },
        ).catch(() => {});
      }
    }, 30000);
    return () => clearInterval(timer);
  }, [paper.id, blocks.length, chat]);
  async function load(cursor: number, replace = false) {
    if (loadLock.current) return;
    loadLock.current = true;
    const sequence = jumpSequence.current;
    setLoading(true);
    try {
      const r = await api<{ blocks: Block[]; nextCursor: number | null }>(
        `/api/results/${result.id}/blocks?after=${cursor}&limit=50`,
        "GET",
        undefined,
        controller.current.signal,
      );
      if (!alive.current || sequence !== jumpSequence.current) return;
      setBlocks((previous) =>
        replace
          ? r.blocks
          : [
              ...previous,
              ...r.blocks.filter((b) => !previous.some((p) => p.id === b.id)),
            ],
      );
      setNext(r.nextCursor);
      after.current = r.nextCursor ?? -1;
    } catch (e) {
      if (!controller.current.signal.aborted) setError(errorText(e));
    } finally {
      loadLock.current = false;
      if (alive.current) setLoading(false);
    }
  }
  useEffect(() => {
    alive.current = true;
    controller.current = new AbortController();
    void (async () => {
      try {
        const r = await api(
          `/api/results/${result.id}/reading-position`,
          "GET",
          undefined,
          controller.current.signal,
        );
        if (!alive.current) return;
        let cursor = -1;
        if (r.position) {
          setDisplay(r.position.display);
          setSession(r.position.sessionId || "");
          restorePoint.current = r.position;
          const b = await api(
            `/api/results/${result.id}/blocks/${encodeURIComponent(r.position.blockId)}`,
            "GET",
            undefined,
            controller.current.signal,
          );
          cursor = Math.max(-1, b.block.order - 1);
        }
        await load(cursor, true);
      } catch (e) {
        if (!controller.current.signal.aborted) {
          setError(errorText(e));
          await load(-1, true);
        }
      } finally {
        restoring.current = false;
      }
    })();
    return () => {
      alive.current = false;
      controller.current.abort();
      if (lastPosition.current)
        void api(
          `/api/results/${result.id}/reading-position`,
          "PUT",
          lastPosition.current,
          undefined,
          true,
        ).catch(() => {});
    };
  }, [result.id]);
  useLayoutEffect(() => {
    const node = root.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setViewport(node.clientHeight));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const offsets = useMemo(() => {
    let y = 0;
    return blocks.map((block) => {
      const offset = y;
      y += heights.current.get(block.id) || 320;
      return offset;
    });
  }, [blocks, measureRevision]);
  const fullHeight = blocks.length
    ? offsets[blocks.length - 1] +
      (heights.current.get(blocks[blocks.length - 1].id) || 320)
    : 0;
  let first = 0,
    last = blocks.length;
  while (first < blocks.length - 1 && offsets[first + 1] < scroll - 900)
    first++;
  last = first;
  while (last < blocks.length && offsets[last] < scroll + viewport + 1000)
    last++;
  function measured(id: string, height: number) {
    const old = heights.current.get(id);
    if (old !== height) {
      heights.current.set(id, height);
      setMeasureRevision((v) => v + 1);
    }
  }
  useLayoutEffect(() => {
    if (restorePoint.current && blocks.length && root.current) {
      const index = blocks.findIndex(
        (b) => b.id === restorePoint.current!.blockId,
      );
      if (index >= 0) {
        root.current.scrollTop =
          offsets[index] +
          (heights.current.get(blocks[index].id) || 320) *
            restorePoint.current.offset;
        setScroll(root.current.scrollTop);
        if (heights.current.has(blocks[index].id)) restorePoint.current = null;
      }
    }
  }, [blocks, measureRevision]);
  useEffect(() => {
    if (!loading && next !== null && scroll + viewport + 1600 >= fullHeight)
      void load(next);
  }, [next, loading, scroll, viewport, fullHeight]);
  function capture() {
    if (restoring.current || !blocks.length || !root.current) return;
    const top = root.current.scrollTop;
    let index = 0;
    while (index < blocks.length - 1 && offsets[index + 1] <= top) index++;
    lastPosition.current = {
      blockId: blocks[index].id,
      offset: Math.max(
        0,
        Math.min(
          1,
          (top - offsets[index]) /
            (heights.current.get(blocks[index].id) || 320),
        ),
      ),
      display,
      sessionId: sessionRef.current || null,
    };
  }
  captureRef.current = capture;
  const changeSession = useCallback(
    (id: string) => {
      sessionRef.current = id;
      setSession(id);
      captureRef.current();
      if (lastPosition.current) {
        lastPosition.current = {
          ...lastPosition.current,
          sessionId: id || null,
        };
        void api(
          `/api/results/${result.id}/reading-position`,
          "PUT",
          lastPosition.current,
        ).catch(() => {
          if (alive.current) setError("会话选择保存失败，请稍后重试。");
        });
      }
    },
    [result.id],
  );
  useEffect(() => {
    const leave = () => {
      captureRef.current();
      if (lastPosition.current)
        void api(
          `/api/results/${result.id}/reading-position`,
          "PUT",
          lastPosition.current,
          undefined,
          true,
        ).catch(() => {});
    };
    window.addEventListener("pagehide", leave);
    return () => window.removeEventListener("pagehide", leave);
  }, [result.id]);
  useEffect(() => {
    capture();
    const timer = setTimeout(() => {
      if (lastPosition.current)
        void api(
          `/api/results/${result.id}/reading-position`,
          "PUT",
          lastPosition.current,
        ).catch(() => {
          if (alive.current) setError("结构阅读位置保存失败，请稍后重试。");
        });
    }, 700);
    return () => clearTimeout(timer);
  }, [scroll, display, session]);
  async function sourceFor(
    block: Block,
    field = "text",
    start = 0,
    end?: number,
    translation = false,
  ) {
    const content = translation ? block.translation?.content : block;
    const cell = field.match(/^cell:(\d+):(\d+)$/);
    const text = cell
      ? content?.table?.[Number(cell[1])]?.[Number(cell[2])]?.text || ""
      : (content as any)?.[field] || "";
    const response = await api<{ source: Source }>(
      `/api/paper/${encodeURIComponent(paper.id)}/sources`,
      "POST",
      {
        resultId: result.id,
        blockId: block.id,
        revisionId: translation ? block.translation?.revision : undefined,
        field,
        start,
        end: end ?? Math.min(text.length, 6000),
      },
      controller.current.signal,
    );
    return response.source;
  }
  async function navigate(block: Block) {
    try {
      capture();
      const source = await sourceFor(block);
      props.onSource(source.id);
    } catch (e) {
      if (alive.current) setError(errorText(e));
    }
  }
  async function compare(block: Block) {
    try {
      const source = await sourceFor(block);
      if (!source.canNavigate) throw new Error("来源暂不可定位");
      setComparison(source);
      setChat(false);
    } catch (e) {
      if (alive.current) setError(errorText(e));
    }
  }
  async function ask() {
    if (!selection) return;
    try {
      const source = await sourceFor(
        selection.block,
        selection.field,
        selection.start,
        selection.end,
        selection.translated,
      );
      setExcerpt({
        text: source.text,
        page: source.page,
        document: selection.translated ? "translated" : "original",
        title: paper.title,
        sourceId: source.id,
      });
      setChat(true);
      setComparison(null);
      setSelection(null);
      window.getSelection()?.removeAllRanges();
    } catch (e) {
      if (alive.current) setError(errorText(e));
    }
  }
  const notes = useAnnotations(paper.id, result?.id || null);
  const loadNote = useCallback(() => {
    let alive = true;
    void api<{ note: NotePayload }>(`/api/paper/${encodeURIComponent(paper.id)}/reading/note`)
      .then((value) => {
        if (alive) setNote(value.note);
      })
      .catch((e) => {
        if (alive) setNoteError(errorText(e));
      });
    return () => {
      alive = false;
    };
  }, [paper.id]);
  useEffect(() => {
    loadNote();
  }, [loadNote]);
  useEffect(() => {
    // The chat can insert an answer into this note; refetch so it shows up
    // immediately without polling.
    const refresh = () => loadNote();
    window.addEventListener("paper-notes:refresh", refresh);
    return () => window.removeEventListener("paper-notes:refresh", refresh);
  }, [loadNote]);
  const structuredAnchor = (value: any) =>
    value && value.block
      ? {
          mode: "structure" as const,
          blockId: value.block.id,
          field: value.field || "text",
          start: value.start,
          end: value.end,
          ...(value.translated && value.block.translation?.revision
            ? { translationRevision: value.block.translation.revision }
            : {}),
        }
      : null;
  return (
    <>
      <div className="structure-subtoolbar">
        <button aria-label="显示阅读导航" onClick={() => setNavOpen((v) => !v)}>
          <PanelLeft size={17} />
        </button>
        <button
          aria-label="搜索全文"
          onClick={() => {
            setNavOpen(true);
            setNavTab("search");
          }}
        >
          <Search size={17} />
        </button>
        <div className="segmented">
          {(["original", "translated", "bilingual"] as Display[]).map((v) => (
            <button
              key={v}
              className={display === v ? "active" : ""}
              disabled={
                v !== "original" && result.kind !== "structured_translation"
              }
              onClick={() => {
                heights.current.clear();
                setDisplay(v);
              }}
            >
              {{ original: "原文", translated: "译文", bilingual: "双语" }[v]}
            </button>
          ))}
        </div>
        <span className="muted">
          {result.model || "MinerU 结构解析"} · {processingNames[result.status]}
          {result.stale ? " · 来源已变化" : ""}
        </span>
        <button
          className="chat-toggle"
          onClick={() => {
            setChat((v) => !v);
            setComparison(null);
          }}
        >
          <MessageSquare size={16} />
          {chat ? "收起问答" : "论文问答"}
        </button>
      </div>
      {result.stale && (
        <p className="notice">
          原始 PDF
          已发生变化；此版本仍可阅读，来源跳转已停用。请为新文件生成结构内容。
        </p>
      )}
      <div
        ref={body}
        className={
          "structure-body " +
          (navOpen ? "with-navigation " : "") +
          (chat ? "with-chat" : "") +
          (comparison ? " with-comparison" : "")
        }
      >
        {navOpen && (
          <StructureNavigation
            paperId={paper.id}
            result={result}
            tab={navTab}
            setTab={(v) => {
              setNavTab(v);
              if (v !== "search") setSearchHit(null);
            }}
            current={() => {
              capture();
              return (
                lastPosition.current || {
                  blockId: blocks[0]?.id,
                  offset: 0,
                  display,
                }
              );
            }}
            onJump={jumpBlock}
            onBookmark={(b) => props.onBookmarkNavigate?.(b)}
            onClose={() => {
              setNavOpen(false);
              setSearchHit(null);
            }}
          />
        )}
        <section className="structure-panel">
          <Status error={error} loading={loading && !blocks.length} />
          <div
            className="structure-scroll"
            ref={root}
            onScroll={() => {
              setScroll(root.current!.scrollTop);
              capture();
            }}
          >
            {blocks[0]?.order > 0 && (
              <button
                className="load-previous"
                onClick={() =>
                  void load(Math.max(-1, blocks[0].order - 51), true)
                }
              >
                阅读前面的内容
              </button>
            )}
            <Sized height={offsets[first] || 0} />
            {blocks.slice(first, last).map((block) => (
              <MeasuredBlock
                key={block.id}
                block={block}
                onMeasure={measured}
                searchHit={searchHit?.blockId === block.id ? searchHit : null}
              >
                <div className="block-heading">
                  <span>
                    {block.source.page
                      ? `第 ${block.source.page} 页`
                      : "页码未确认"}
                  </span>
                  <div>
                    <button
                      disabled={
                        result.stale || block.source.precision === "none"
                      }
                      onClick={() => void navigate(block)}
                    >
                      <MapPin size={14} />
                      {block.source.precision === "region"
                        ? "查看来源"
                        : "查看原文页面"}
                    </button>
                    <button
                      disabled={
                        result.stale || block.source.precision === "none"
                      }
                      onClick={() => void compare(block)}
                    >
                      原文对照
                    </button>
                    {result.kind === "structured_translation" && (
                      <button onClick={() => setRetryBlock(block.id)}>
                        <RefreshIcon />
                        {block.translation?.status === "failed"
                          ? "重试此块"
                          : "重译"}
                      </button>
                    )}
                  </div>
                </div>
                <div className={"block-pair " + display}>
                  {display !== "translated" && (
                    <BlockContent
                      annotations={notes.items}
                      content={block}
                      block={block}
                      translated={false}
                      label="原文"
                      onSource={
                        block.source.precision !== "none" && !result.stale
                          ? () => void navigate(block)
                          : undefined
                      }
                      onSelection={(
                        block,
                        field,
                        start,
                        end,
                        text,
                        translated,
                      ) =>
                        setSelection({
                          block,
                          field,
                          start,
                          end,
                          text,
                          translated,
                        })
                      }
                    />
                  )}
                  {display !== "original" &&
                    (block.translation?.content ? (
                      <BlockContent
                        annotations={notes.items}
                        content={block.translation.content}
                        block={block}
                        translated
                        label="译文"
                        onSource={
                          block.source.precision !== "none" && !result.stale
                            ? () => void navigate(block)
                            : undefined
                        }
                        onSelection={(
                          block,
                          field,
                          start,
                          end,
                          text,
                          translated,
                        ) =>
                          setSelection({
                            block,
                            field,
                            start,
                            end,
                            text,
                            translated,
                          })
                        }
                      />
                    ) : (
                      <div className="block-language missing-translation">
                        <span>
                          此块
                          {block.translation?.status === "failed"
                            ? "翻译失败"
                            : "尚无译文"}
                        </span>
                        <button onClick={() => setRetryBlock(block.id)}>
                          翻译此块
                        </button>
                      </div>
                    ))}
                </div>
                {block.translation?.status === "failed" &&
                  block.translation.content && (
                    <p className="muted">
                      最新尝试失败，继续显示上次成功保存的译文。
                    </p>
                  )}
              </MeasuredBlock>
            ))}
            <Sized
              height={Math.max(
                0,
                fullHeight -
                  (last < blocks.length ? offsets[last] : fullHeight),
              )}
            />
            {loading && <p className="loading-inline">正在读取后续内容…</p>}
            {!loading && !blocks.length && (
              <div className="empty-state">此解析结果尚无可显示的结构块。</div>
            )}
          </div>
          {selection && (
            <div className="selection-actions">
              <Quote size={16} />
              <span>已选择 {Array.from(selection.text).length} 字</span>
              <button onClick={() => void translateSelected()}>翻译</button>
              <button className="primary" onClick={() => void ask()}>
                带来源提问
              </button>
              {structuredAnchor(selection) && (
                <>
                  <span className="selection-color-row">
                    {ANNOTATION_COLORS.map((item) => (
                      <button
                        key={item.value}
                        type="button"
                        className={
                          "annotation-swatch color-" + item.value + (annotationColor === item.value ? " selected" : "")
                        }
                        aria-label={`使用${item.label}高亮`}
                        aria-pressed={annotationColor === item.value}
                        onClick={() => setAnnotationColor(item.value)}
                      />
                    ))}
                  </span>
                  <input
                    className="selection-comment"
                    value={annotationComment}
                    maxLength={2000}
                    placeholder="批注（可留空）"
                    aria-label="批注内容"
                    onChange={(e) => setAnnotationComment(e.target.value)}
                  />
                  <button
                    disabled={annotationState === "saving"}
                    onClick={async () => {
                      const anchor = structuredAnchor(selection);
                      if (!anchor || !result) return;
                      setAnnotationState("saving");
                      const created = await notes.create({
                        kind: annotationComment.trim() ? "note" : "highlight",
                        color: annotationColor,
                        comment: annotationComment.trim(),
                        excerpt: selection.text,
                        documentId: result.documentId,
                        resultId: result.id,
                        anchor,
                        context: {},
                      });
                      setAnnotationState(created ? "saved" : "failed");
                      if (created) {
                        setAnnotationComment("");
                        setPanelTab("annotations");
                        setChat(true);
                      }
                    }}
                  >
                    {annotationState === "saving"
                      ? "正在保存…"
                      : annotationState === "saved"
                        ? "已保存"
                        : "保存高亮/批注"}
                  </button>
                  {annotationState === "failed" && <span role="alert">保存失败，可重试</span>}
                </>
              )}
              <button aria-label="清除选区" onClick={() => setSelection(null)}>
                <X size={16} />
              </button>
            </div>
          )}
        </section>
        {comparison && (
          <aside className="structure-comparison">
            <div className="panel-heading">
              <h2>原始 PDF · 来源对照</h2>
              <button
                aria-label="关闭原文对照"
                onClick={() => setComparison(null)}
              >
                <X size={16} />
              </button>
            </div>
            <PdfReader
              {...props}
              embedded
              preferences={{ ...props.preferences, thumbnailOpen: false }}
              translated={false}
              sourceTarget={comparison}
              onClose={() => setComparison(null)}
              toolbarContent={<span>原始 PDF</span>}
            />
          </aside>
        )}
        {chat && (
          <>
            <div
              className="resize-handle"
              role="separator"
              tabIndex={0}
              aria-label="调整问答宽度"
              aria-orientation="vertical"
              onPointerDown={resize}
              onKeyDown={(e) => {
                if (["ArrowLeft", "ArrowRight"].includes(e.key)) {
                  e.preventDefault();
                  const w =
                    body.current
                      ?.querySelector(".structure-chat")
                      ?.getBoundingClientRect().width || 380;
                  props.onPreferences({
                    chatWidth: setWidth(w + (e.key === "ArrowLeft" ? 20 : -20)),
                  });
                }
              }}
            />
            <aside className="structure-chat">
              <div className="panel-heading">
                <h2>阅读工作面</h2>
                <button
                  onClick={() => setChat(false)}
                  aria-label="返回结构阅读"
                >
                  <X size={16} />
                </button>
              </div>
              <div className="reader-panel-tabs" role="tablist" aria-label="阅读工作面">
                {(
                  [
                    ["chat", "问答"],
                    ["annotations", "批注"],
                    ["note", "笔记"],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    role="tab"
                    aria-selected={panelTab === value}
                    className={panelTab === value ? "selected" : ""}
                    onClick={() => setPanelTab(value)}
                  >
                    {label}
                    {value === "annotations" && notes.items.length > 0 && (
                      <small>{notes.items.length}</small>
                    )}
                  </button>
                ))}
              </div>
              {panelTab === "annotations" && (
                <AnnotationsPanel
                  annotations={notes.items}
                  loading={notes.loading}
                  error={notes.error}
                  note={note}
                  onRefresh={() => void notes.refresh()}
                  onNote={setNote}
                  onError={setNoteError}
                  onOpen={(annotation) => {
                    setOpenedAnnotation(annotation);
                    if (annotation.canNavigate && annotation.anchor?.mode === "structure") {
                      void jumpBlock((annotation.anchor as any).blockId);
                    }
                  }}
                  onDelete={(annotation) => void notes.remove(annotation.id, annotation.revision)}
                  onRestore={(annotation) => void notes.restore(annotation.id, annotation.revision)}
                />
              )}
              {panelTab === "note" && (
                <div className="reader-note-panel">
                  {noteError && <p className="notice error">{noteError}</p>}
                  <NoteEditor
                    paperId={paper.id}
                    note={note}
                    onNote={setNote}
                    onError={setNoteError}
                    onResolveConflict={async (conflictId, choice) => {
                      const value = await api<{ note: NotePayload }>(
                        `/api/paper/${encodeURIComponent(paper.id)}/reading/note/conflicts/${conflictId}`,
                        "POST",
                        { choice },
                      );
                      setNote(value.note);
                    }}
                    onInsertExcerpt={
                      openedAnnotation
                        ? async () => {
                            if (!note) return;
                            try {
                              const value = await api<{ note: NotePayload }>(
                                `/api/paper/${encodeURIComponent(paper.id)}/reading/note/excerpts`,
                                "POST",
                                { annotationId: openedAnnotation.id, revision: note.revision },
                              );
                              setNote(value.note);
                            } catch (e) {
                              setNoteError(errorText(e));
                            }
                          }
                        : undefined
                    }
                  />
                </div>
              )}
              {panelTab === "chat" && (
              <Chat
                paperId={paper.id}
                onExpired={props.onExpired}
                initialSession={session}
                onSessionChange={changeSession}
                excerpt={excerpt}
                onClearExcerpt={() => setExcerpt(null)}
                drafts={props.drafts}
                onSource={props.onSource}
                prepareSources={async (signal) => {
                  capture();
                  const block =
                    blocks.find(
                      (b) => b.id === lastPosition.current?.blockId,
                    ) || blocks[0];
                  if (result.stale) throw new ApiError(409, "source_expired");
                  if (!block) throw new ApiError(409, "empty_source_context");
                  const field = block.text.trim()
                    ? "text"
                    : block.caption.trim()
                      ? "caption"
                      : block.table?.[0]?.[0]?.text
                        ? "cell:0:0"
                        : "";
                  if (!field) throw new ApiError(409, "empty_source_context");
                  const source = await sourceFor(block, field);
                  if (signal.aborted)
                    throw new DOMException("Aborted", "AbortError");
                  return source.text.trim() ? [source.id] : [];
                }}
              />
              )}
            </aside>
          </>
        )}
      </div>
      {translateSelection && (
        <SelectionPopup
          key={translateSelection.text + translateSelection.documentId}
          paperId={paper.id}
          selection={translateSelection}
          onClose={() => setTranslateSelection(null)}
          onAsk={(v) => {
            setExcerpt(v);
            setChat(true);
            setComparison(null);
            setTranslateSelection(null);
          }}
        />
      )}
      {retryBlock && (
        <TranslationDialog
          paper={paper}
          initial="structure"
          parseId={result.parseId || result.id}
          targetLanguage={result.targetLanguage}
          sourceLanguage={result.sourceLanguage}
          translationId={
            result.kind === "structured_translation" ? result.id : undefined
          }
          blockId={retryBlock}
          onClose={() => {
            setRetryBlock(null);
            void load(Math.max(-1, (blocks[0]?.order ?? 0) - 1), true);
          }}
          onSubmitted={props.onChanged}
        />
      )}
    </>
  );
}
function RefreshIcon() {
  return <Languages size={14} />;
}
