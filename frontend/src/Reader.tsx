import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
  type ReactNode,
} from "react";
import {
  getDocument,
  GlobalWorkerOptions,
  TextLayer,
  type PDFDocumentProxy,
  type PDFPageProxy,
  type RenderTask,
} from "pdfjs-dist/legacy/build/pdf.mjs";
import workerUrl from "pdfjs-dist/legacy/build/pdf.worker.mjs?url";
import "pdfjs-dist/web/pdf_viewer.css";
import {
  PanelLeft,
  MessageSquare,
  ChevronLeft,
  ChevronRight,
  RotateCw,
  Expand,
  BookOpen,
  X,
  Quote,
  LoaderCircle,
  Search,
} from "lucide-react";
import { Chat, type Excerpt } from "./Chat";
import { StickyNote } from "lucide-react";
import {
  AnnotationsPanel,
  HighlightOverlay,
  NoteEditor,
  selectionContext,
  selectionRects,
  useAnnotations,
  NoteConflictBanner,
  PageNoteDialog,
  noteOwner,
  type Annotation,
  type AnnotationColor,
  type NotePayload,
} from "./PaperNotes";
import { api, Modal, Field, Status } from "./ui";
import { type Paper, errorText } from "./api";
import {
  PdfNavigation,
  useDocumentIdentity,
  useReadingShortcut,
  SelectionPopup,
  type SelectionInput,
  type Bookmark,
} from "./ReadingTools";
import { highlightMatch } from "./pdfSearch";
import type { Match } from "./readingSearch";
import type { ProcessingResult } from "./Processing";
import { CachedTranslation } from "./CachedTranslation";
GlobalWorkerOptions.workerSrc = workerUrl;
const assetRoot = `${import.meta.env.BASE_URL}pdfjs/`;
type Position = {
  document: "original" | "translated";
  page: number;
  offset: number;
  zoom: number | "width" | "page";
  rotation: number;
  fingerprint: string;
  sessionId?: string | null;
};
function PdfPage({
  doc,
  number,
  scale,
  rotation,
  root,
  onReady,
  onLayout,
  estimatedSize,
  thumbnail = false,
  sourceRegions = [],
  searchMatch = null,
  onMatchMissing,
  onRegion,
  annotations = [],
  onAnnotationOpen,
}: {
  doc: PDFDocumentProxy;
  number: number;
  scale: number;
  rotation: number;
  root: RefObject<HTMLDivElement | null>;
  onReady?: (n: number) => void;
  onLayout?: () => void;
  estimatedSize: { width: number; height: number };
  thumbnail?: boolean;
  sourceRegions?: number[][];
  searchMatch?: Match | null;
  onMatchMissing?: () => void;
  onRegion?: (page: number, x: number, y: number) => void;
  annotations?: Annotation[];
  onAnnotationOpen?: (annotation: Annotation) => void;
}) {
  const host = useRef<HTMLDivElement>(null),
    [visible, setVisible] = useState(false),
    [size, setSize] = useState(estimatedSize),
    [error, setError] = useState("");
  const renderedText = useRef<{
    surface: HTMLElement;
    divs: HTMLElement[];
  } | null>(null);
  const currentMatch = useRef(searchMatch),
    regionHandler = useRef(onRegion);
  currentMatch.current = searchMatch;
  regionHandler.current = onRegion;
  const mark = () => {
    const value = renderedText.current;
    if (
      value &&
      !highlightMatch(value.surface, value.divs, currentMatch.current)
    )
      onMatchMissing?.();
  };
  useEffect(mark, [searchMatch]);
  const height = (rotation % 180 ? size.width : size.height) * scale;
  const width = (rotation % 180 ? size.height : size.width) * scale;
  useLayoutEffect(() => {
    const h = host.current!;
    h.style.height = `${height}px`;
    h.style.width = thumbnail ? "100%" : `${width}px`;
    if (!thumbnail) onLayout?.();
  }, [height, width, thumbnail]);
  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => setVisible(entries[0].isIntersecting),
      { root: root.current, rootMargin: thumbnail ? "250px" : "900px" },
    );
    observer.observe(host.current!);
    return () => observer.disconnect();
  }, [root]);
  useEffect(() => {
    if (!visible) return;
    let active = true,
      render: RenderTask | undefined,
      text: TextLayer | undefined,
      canvas: HTMLCanvasElement | undefined;
    let pdfPage: PDFPageProxy | undefined;
    let clickTimer: ReturnType<typeof setTimeout> | undefined;
    const el = host.current!;
    setError("");
    void (async () => {
      pdfPage = await doc.getPage(number);
      if (!active) return;
      const vp = pdfPage.getViewport({
        scale,
        rotation: (pdfPage.rotate + rotation) % 360,
      });
      const natural = pdfPage.getViewport({ scale: 1 });
      setSize({ width: natural.width, height: natural.height });
      const surface = document.createElement("div");
      surface.className = "pdf-page";
      surface.style.width = `${vp.width}px`;
      surface.style.height = `${vp.height}px`;
      surface.style.setProperty("--total-scale-factor", String(vp.scale));
      surface.style.setProperty("--scale-round-x", "1px");
      surface.style.setProperty("--scale-round-y", "1px");
      canvas = document.createElement("canvas");
      canvas.setAttribute("aria-label", `PDF 第 ${number} 页`);
      const ratio = Math.min(
        devicePixelRatio || 1,
        2,
        Math.sqrt(16000000 / (vp.width * vp.height)),
      );
      canvas.width = Math.floor(vp.width * ratio);
      canvas.height = Math.floor(vp.height * ratio);
      canvas.style.width = `${vp.width}px`;
      canvas.style.height = `${vp.height}px`;
      surface.append(canvas);
      el.replaceChildren(surface);
      render = pdfPage.render({
        canvas,
        viewport: vp,
        transform: [ratio, 0, 0, ratio, 0, 0],
      });
      await render!.promise;
      if (!active) return;
      if (!thumbnail) {
        const layer = document.createElement("div");
        layer.className = "textLayer";
        surface.append(layer);
        text = new TextLayer({
          textContentSource: await pdfPage.getTextContent(),
          container: layer,
          viewport: vp,
        });
        await text.render();
        if (!active) return;
        renderedText.current = { surface, divs: text.textDivs };
        mark();
        let down: { x: number; y: number; at: number } | null = null;
        surface.addEventListener("pointerdown", (e) => {
          down = { x: e.clientX, y: e.clientY, at: performance.now() };
        });
        surface.addEventListener("click", (e) => {
          clearTimeout(clickTimer);
          if (
            e.detail !== 1 ||
            !down ||
            Math.hypot(e.clientX - down.x, e.clientY - down.y) > 6 ||
            performance.now() - down.at > 450 ||
            (e.target as HTMLElement)?.closest("a,button,input")
          )
            return;
          const box = surface.getBoundingClientRect(),
            [x, y] = vp.convertToPdfPoint(
              e.clientX - box.left,
              e.clientY - box.top,
            );
          clickTimer = setTimeout(() => {
            if (active && window.getSelection()?.isCollapsed)
              regionHandler.current?.(number, x, y);
          }, 260);
        });
        surface.addEventListener("dblclick", () => clearTimeout(clickTimer));
      }
      for (const rectangle of sourceRegions) {
        const coords = [
          ...vp.convertToViewportPoint(rectangle[0], rectangle[1]),
          ...vp.convertToViewportPoint(rectangle[2], rectangle[3]),
        ];
        const highlight = document.createElement("div");
        highlight.className = "pdf-source-highlight";
        highlight.setAttribute("aria-label", "引用来源区域");
        highlight.style.left = Math.min(coords[0], coords[2]) + "px";
        highlight.style.top = Math.min(coords[1], coords[3]) + "px";
        highlight.style.width = Math.abs(coords[2] - coords[0]) + "px";
        highlight.style.height = Math.abs(coords[3] - coords[1]) + "px";
        surface.append(highlight);
      }
      surface.dataset.rendered = "true";
      onReady?.(number);
    })().catch((e) => {
      if (active && e?.name !== "RenderingCancelledException") {
        console.error("PDF page render failed", {
          name: e?.name,
          page: number,
        });
        setError("此页渲染失败，请重新打开文档。");
        el.replaceChildren();
      }
    });
    return () => {
      active = false;
      clearTimeout(clickTimer);
      renderedText.current = null;
      render?.cancel();
      text?.cancel();
      el.replaceChildren();
      void Promise.resolve(render?.promise)
        .catch(() => {})
        .then(() => {
          if (canvas) canvas.width = 0;
          pdfPage?.cleanup();
        });
    };
  }, [doc, number, scale, rotation, visible, JSON.stringify(sourceRegions)]);
  return (
    <div className="page-wrap">
      <div
        className={"page-host " + (thumbnail ? "thumbnail-page" : "")}
        data-page={thumbnail ? undefined : number}
        ref={host}
        aria-label={`第 ${number} 页`}
      >
        {error && <p role="alert">{error}</p>}
      </div>
      {!thumbnail && (
        <HighlightOverlay
          annotations={annotations}
          page={number}
          rotation={rotation}
          onOpen={onAnnotationOpen}
        />
      )}
    </div>
  );
}
export function PdfReader({
  preferences,
  onPreferences,
  paper,
  translated,
  onVersion,
  onClose,
  onExpired,
  drafts,
  toolbarContent,
  toolbarLead,
  sourceTarget,
  fileDocumentId,
  onSource,
  embedded = false,
  structureResult,
  translationResults = [],
  cachedTranslationId,
  onCachedTranslation,
  onBookmarkNavigate,
  bookmarkTarget,
}: {
  preferences: any;
  onPreferences: (v: Record<string, unknown>) => void;
  paper: Paper;
  translated: boolean;
  onVersion: (v: boolean) => void;
  onClose: () => void;
  onExpired: () => void;
  drafts?: Map<string, string>;
  toolbarContent?: ReactNode;
  toolbarLead?: ReactNode;
  embedded?: boolean;
  structureResult?: ProcessingResult;
  translationResults?: ProcessingResult[];
  cachedTranslationId?: string;
  onCachedTranslation?: (id: string) => void;
  onBookmarkNavigate?: (bookmark: Bookmark) => void;
  bookmarkTarget?: Bookmark;
  fileDocumentId?: string;
  sourceTarget?: {
    id: string;
    documentId: string;
    page?: number;
    regions: { page: number; rect: number[] }[];
  };
  onSource?: (id: string) => void;
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null),
    [page, setPage] = useState(1),
    [zoom, setZoom] = useState<Position["zoom"]>("width"),
    [rotation, setRotation] = useState(0),
    [thumbs, setThumbs] = useState(
      () =>
        !embedded &&
        !matchMedia("(max-width:640px)").matches &&
        preferences.thumbnailOpen !== false,
    ),
    [chat, setChat] = useState(!embedded),
    [mobileChat, setMobileChat] = useState(false),
    [panelTab, setPanelTab] = useState<"chat" | "annotations" | "note">("chat"),
    [note, setNote] = useState<NotePayload | null>(null),
    [noteError, setNoteError] = useState(""),
    [openedAnnotation, setOpenedAnnotation] = useState<Annotation | null>(null),
    [pageNote, setPageNote] = useState(false),
    [status, setStatus] = useState("正在加载 PDF…"),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [retry, setRetry] = useState(0),
    [scale, setScale] = useState(1),
    [password, setPassword] = useState(""),
    [passwordNeeded, setPasswordNeeded] = useState(false),
    [excerpt, setExcerpt] = useState<Excerpt | null>(null),
    [selection, setSelection] = useState<Excerpt | null>(null),
    [sessionId, setSessionId] = useState("");
  const [pageInput, setPageInput] = useState("1");
  const [navTab, setNavTab] = useState(
      preferences.navigationPanel || "outline",
    ),
    [match, setMatch] = useState<{ page: number; match: Match | null }>({
      page: 0,
      match: null,
    }),
    [translateSelection, setTranslateSelection] =
      useState<SelectionInput | null>(null),
    [regionClick, setRegionClick] = useState<{
      page: number;
      x: number;
      y: number;
    } | null>(null);
  const identity = useDocumentIdentity(
    paper.id,
    translated ? "translated" : "original",
    fileDocumentId || sourceTarget?.documentId,
    !!doc,
  );

  useEffect(() => setPageInput(String(page)), [page]);
  useEffect(() => {
    const narrow = matchMedia("(max-width:900px)");
    const changed = () => {
      if (narrow.matches) setThumbs(false);
    };
    narrow.addEventListener("change", changed);
    return () => narrow.removeEventListener("change", changed);
  }, []);
  useEffect(() => {
    if (thumbs) setRegionClick(null);
  }, [thumbs]);
  const host = useRef<HTMLDivElement>(null),
    thumbRoot = useRef<HTMLDivElement>(null),
    workspace = useRef<HTMLDivElement>(null),
    passwordCallback = useRef<((v: string) => void) | null>(null),
    baseSize = useRef({ width: 612, height: 792 }),
    restore = useRef<Position | null>(null),
    point = useRef<Position | null>(null),
    latestSaved = useRef(""),
    positionLoaded = useRef(false),
    destroying = useRef<Promise<void>>(Promise.resolve()),
    alive = useRef(true);
  useReadingShortcut(
    workspace,
    () => {
      setThumbs(true);
      setNavTab("search");
    },
    !embedded,
  );
  useEffect(() => {
    window.getSelection()?.removeAllRanges();
    setTranslateSelection(null);
    setRegionClick(null);
    setMatch({ page: 0, match: null });
  }, [paper.id, translated, fileDocumentId]);
  useEffect(() => {
    if (preferences.chatWidth)
      workspace.current?.style.setProperty(
        "--chat-width",
        preferences.chatWidth + "px",
      );
  }, [preferences.chatWidth]);
  const variant = translated ? "translated" : "original",
    url =
      fileDocumentId || sourceTarget?.documentId
        ? `/api/documents/${encodeURIComponent(fileDocumentId || sourceTarget!.documentId)}/file`
        : `/api/paper/${encodeURIComponent(paper.id)}/${translated ? "chinese/" : ""}file`,
    positionUrl = fileDocumentId
      ? `/api/documents/${encodeURIComponent(fileDocumentId)}/reading-position`
      : `/api/paper/${encodeURIComponent(paper.id)}/reading-position`;
  async function save(keepalive = false) {
    const p = point.current;
    if (
      !positionLoaded.current ||
      !p ||
      JSON.stringify(p) === latestSaved.current
    )
      return;
    const snapshot = JSON.stringify(p);
    try {
      await api(positionUrl, "PUT", p, undefined, keepalive);
      latestSaved.current = snapshot;
    } catch (e) {
      if (alive.current) setNotice("阅读位置保存失败，请保持页面并稍后重试。");
    }
  }
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    let task: ReturnType<typeof getDocument> | undefined;
    setDoc(null);
    setError("");
    setStatus("正在加载 PDF…");
    setSelection(null);
    setExcerpt(null);
    setPassword("");
    setPasswordNeeded(false);
    setNotice("");
    point.current = null;
    positionLoaded.current = false;
    latestSaved.current = "";
    void (async () => {
      await destroying.current;
      if (!active) return;
      const positions = api<Record<string, Position>>(
        positionUrl,
        "GET",
        undefined,
        controller.signal,
      )
        .then((value) => {
          if (active) positionLoaded.current = true;
          return value;
        })
        .catch((e) => {
          if (active && !controller.signal.aborted)
            setNotice(
              "阅读位置暂时无法加载，本次阅读不会覆盖已保存位置；请重新加载重试。",
            );
          return {} as Record<string, Position>;
        });
      task = getDocument({
        url,
        withCredentials: true,
        cMapUrl: assetRoot + "cmaps/",
        cMapPacked: true,
        standardFontDataUrl: assetRoot + "standard_fonts/",
        wasmUrl: assetRoot + "wasm/",
        useWasm: false,
        useSystemFonts: false,
        disableAutoFetch: true,
        disableStream: true,
        rangeChunkSize: 65536,
      });
      task.onPassword = (update: (value: string) => void, reason: number) => {
        if (active) {
          passwordCallback.current = update;
          setPasswordNeeded(true);
          setStatus(
            reason === 2 ? "密码不正确，请重试。" : "此 PDF 需要密码。",
          );
        }
      };
      const value = await task.promise;
      if (!active) return;
      const saved = (await positions)[variant];
      const first = await value.getPage(1);
      if (!active) return;
      const vp = first.getViewport({ scale: 1 });
      baseSize.current = { width: vp.width, height: vp.height };
      const valid =
        saved?.fingerprint === value.fingerprints[0] &&
        saved.page <= value.numPages;
      const initial: Position = valid
        ? saved
        : {
            document: variant,
            page: 1,
            offset: 0,
            zoom: "width",
            rotation: 0,
            fingerprint: value.fingerprints[0] || "unknown",
          };
      if (saved && !valid) setNotice("文档已变化，已从第一页开始阅读。");
      if (sourceTarget?.page && sourceTarget.page <= value.numPages) {
        initial.page = sourceTarget.page;
        initial.offset = 0;
      }
      restore.current = initial;
      point.current = initial;
      setPage(initial.page);
      setZoom(initial.zoom);
      setRotation(initial.rotation);
      setSessionId(initial.sessionId || "");
      setDoc(value);
      setStatus("正在渲染…");
      setPasswordNeeded(false);
    })().catch((e) => {
      if (!active) return;
      setStatus("");
      if (e?.status === 401 || e?.status === 403) {
        onExpired();
        return;
      }
      const messages: Record<string, string> = {
        MissingPDFException: "PDF 文件不存在。",
        InvalidPDFException: "PDF 内容无法解析。",
        PasswordException: "PDF 密码不正确。",
      };
      setError(
        messages[e?.name] ||
          (e?.status === 404
            ? "PDF 文件不存在或已无法访问。"
            : "PDF 加载失败，请检查连接并重新加载。"),
      );
      console.error("PDF load failed", { name: e?.name, status: e?.status });
    });
    return () => {
      active = false;
      controller.abort();
      passwordCallback.current = null;
      window.getSelection()?.removeAllRanges();
      void save();
      destroying.current = task?.destroy().catch(() => {}) || Promise.resolve();
    };
  }, [url, retry]);
  useEffect(() => {
    if (!doc) return;
    let active = true;
    const size = () => {
      const el = host.current;
      if (!el) return;
      const odd = rotation % 180 !== 0;
      const w = odd ? baseSize.current.height : baseSize.current.width,
        h = odd ? baseSize.current.width : baseSize.current.height;
      setScale(
        typeof zoom === "number"
          ? zoom
          : zoom === "width"
            ? Math.max(0.25, (el.clientWidth - 48) / w)
            : Math.max(
                0.25,
                Math.min((el.clientWidth - 48) / w, (el.clientHeight - 40) / h),
              ),
      );
    };
    const obs = new ResizeObserver(size);
    if (host.current) obs.observe(host.current);
    size();
    void doc
      .getPage(page)
      .then((p) => {
        if (!active) return;
        const vp = p.getViewport({ scale: 1 });
        baseSize.current = { width: vp.width, height: vp.height };
        size();
      })
      .catch(() => {});
    return () => {
      active = false;
      obs.disconnect();
    };
  }, [doc, page, zoom, rotation, chat, thumbs]);
  const layoutFrame = useRef(0),
    settledFrame = useRef(0);
  function positionDOM(n: number, offset: number) {
    const el = host.current?.querySelector<HTMLElement>(
      `.page-host[data-page="${n}"]`,
    );
    if (!el || !host.current) return;
    host.current.scrollTop +=
      el.getBoundingClientRect().top -
      host.current.getBoundingClientRect().top +
      offset * el.clientHeight -
      20;
  }
  // Keep one PDF-space anchor while placeholder sizes, zoom and rendering settle.
  // Browser scroll anchoring must not compete with this correction.
  function reflowPosition() {
    const anchor = restore.current || point.current;
    if (!anchor) return;
    restore.current = anchor;
    cancelAnimationFrame(layoutFrame.current);
    cancelAnimationFrame(settledFrame.current);
    layoutFrame.current = requestAnimationFrame(() => {
      positionDOM(anchor.page, anchor.offset);
      settledFrame.current = requestAnimationFrame(() => {
        positionDOM(anchor.page, anchor.offset);
        if (restore.current === anchor) restore.current = null;
      });
    });
  }
  useEffect(() => {
    let active = true;
    if (doc && sourceTarget?.page) {
      void doc
        .getPage(sourceTarget.page)
        .then((pdfPage) => {
          if (!active) return;
          const region = sourceTarget.regions.find(
            (r) => r.page === sourceTarget.page,
          );
          let offset = 0;
          if (region) {
            const vp = pdfPage.getViewport({
              scale: 1,
              rotation: (pdfPage.rotate + rotation) % 360,
            });
            const a = vp.convertToViewportPoint(region.rect[0], region.rect[1]),
              b = vp.convertToViewportPoint(region.rect[2], region.rect[3]);
            offset = Math.max(
              0,
              Math.min(0.95, Math.min(a[1], b[1]) / vp.height - 0.08),
            );
          }
          jump(sourceTarget.page!, offset);
        })
        .catch(() => {});
    }
    return () => {
      active = false;
    };
  }, [doc, sourceTarget?.id]);
  useEffect(() => {
    if (
      bookmarkTarget &&
      identity.identity?.id === bookmarkTarget.documentId &&
      doc
    )
      jump(bookmarkTarget.location.page, bookmarkTarget.location.offset);
  }, [bookmarkTarget?.id, doc, identity.identity?.id]);
  function jump(n: number, offset = 0) {
    if (!doc || !point.current || n < 1 || n > doc.numPages) return;
    const next = { ...point.current, page: n, offset };
    point.current = next;
    restore.current = next;
    setPage(n);
    positionDOM(n, offset);
    reflowPosition();
  }
  useLayoutEffect(() => {
    if (doc) reflowPosition();
    return () => {
      cancelAnimationFrame(layoutFrame.current);
      cancelAnimationFrame(settledFrame.current);
    };
  }, [doc, scale, rotation]);
  useEffect(() => {
    if (point.current)
      point.current = {
        ...point.current,
        zoom,
        rotation,
        sessionId: sessionId || null,
      };
  }, [zoom, rotation, sessionId]);
  useEffect(() => {
    const t = setInterval(() => void save(), 3000);
    const hide = () => {
      if (document.visibilityState === "hidden") void save(true);
    };
    const leave = () => void save(true);
    window.addEventListener("pagehide", leave);
    document.addEventListener("visibilitychange", hide);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", hide);
      window.removeEventListener("pagehide", leave);
    };
  }, [positionUrl]);
  useEffect(() => {
    const t = setInterval(() => {
      if (
        doc &&
        !embedded &&
        document.visibilityState === "visible" &&
        document.hasFocus() &&
        !(mobileChat && matchMedia("(max-width:640px)").matches)
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
    return () => clearInterval(t);
  }, [doc, paper.id, mobileChat, embedded]);
  useEffect(() => {
    const changed = () => {
      const s = window.getSelection();
      if (
        !s?.rangeCount ||
        s.isCollapsed ||
        !host.current?.contains(s.anchorNode) ||
        !host.current.contains(s.focusNode)
      ) {
        setSelection(null);
        return;
      }
      const element =
        s.anchorNode instanceof Element
          ? s.anchorNode
          : s.anchorNode?.parentElement;
      const n = Number(
        element?.closest("[data-page]")?.getAttribute("data-page") || page,
      );
      const focus =
        s.focusNode instanceof Element
          ? s.focusNode
          : s.focusNode?.parentElement;
      const endPage = Number(
        focus?.closest("[data-page]")?.getAttribute("data-page"),
      );
      const text = s.toString().trim();
      if (text) {
        // Page-relative line rectangles (rotation independent) plus a short
        // context, so the annotation can be verified and revisited later.
        const range = s.rangeCount ? s.getRangeAt(0) : null;
        const rects = range ? selectionRects(range, rotation) : [];
        const page = n === endPage ? n : undefined;
        setSelection({
          text,
          page,
          document: variant,
          title: paper.title,
          anchor: rects.length
            ? { mode: "pdf", page: page || rects[0].page, rects }
            : page
              ? { mode: "page", page, rects: [] }
              : null,
          context: range ? selectionContext(range) : {},
        } as any);
      }
    };
    document.addEventListener("selectionchange", changed);
    return () => document.removeEventListener("selectionchange", changed);
  }, [paper.id, variant, page, rotation]);
  const notes = useAnnotations(paper.id, identity.identity?.id || null);
  const loadNote = useCallback(async () => {
    try {
      const value = await api<{ note: NotePayload }>(
        `/api/paper/${encodeURIComponent(paper.id)}/reading/note`,
      );
      setNote(value.note);
      setNoteError("");
      return value.note;
    } catch (e) {
      setNoteError(errorText(e));
      return null;
    }
  }, [paper.id]);
  useEffect(() => {
    loadNote();
  }, [loadNote]);
  const liveNoteDraft = useRef<string | null>(null);
  const liveNoteConflict = useRef<{ id: string | null; revision: string | null } | null>(null);
  const resolveNoteConflict = useCallback(
    async (
      conflictId: string | null,
      choice: "current" | "draft",
      options?: { markdown?: string; revision?: string | null },
    ) => {
      if (!conflictId) return null;
      try {
        // The decision must bind to the revision the user was shown: prefer the
        // one the editor captured, then this page's note, and only fetch when
        // neither is known.
        let revision =
          options?.revision ?? liveNoteConflict.current?.revision ?? note?.revision ?? null;
        if (revision === null && choice) {
          const fresh = await loadNote();
          revision = fresh?.revision ?? null;
        }
        const value = await api<{ note: NotePayload }>(
          `/api/paper/${encodeURIComponent(paper.id)}/reading/note/conflicts/${conflictId}`,
          "POST",
          {
            choice,
            revision,
            ...(options?.markdown !== undefined
              ? { markdown: options.markdown }
              : choice === "draft" && liveNoteDraft.current
                ? { markdown: liveNoteDraft.current }
                : {}),
          },
        );
        setNote(value.note);
        setNoteError("");
        return value.note;
      } catch (e) {
        setNoteError(errorText(e));
        // The decision was made against a version that moved on: show the
        // preserved newer content instead of pretending nothing happened.
        await loadNote();
        return null;
      }
    },
    [paper.id, note?.revision, loadNote],
  );
  /** Note excerpts link back through the annotation's own controlled source. */
  const openNoteAnnotation = useCallback(
    async (annotationId: string) => {
      if (!annotationId) return false;
      const known = notes.items.find((item) => item.id === annotationId);
      let target = known;
      if (!target) {
        try {
          const value = await api<{ annotation: Annotation }>(
            `/api/paper/${encodeURIComponent(paper.id)}/reading/annotations/${annotationId}`,
          );
          target = value.annotation;
        } catch {
          setNoteError("这条摘录的来源批注已被删除或不可用；摘录本身已保留。");
          return false;
        }
      }
      if (target.deleted) {
        // The excerpt stays; the annotation that produced it is gone.
        setNoteError("这条摘录的来源批注已被删除；摘录已保留，定位仅供参考。");
      }
      setOpenedAnnotation(target);
      setPanelTab("annotations");
      setChat(true);
      if (target.canNavigate && target.anchor?.mode === "pdf") {
        jump((target.anchor as any).page);
      } else if (target.canNavigate && target.anchor?.mode === "structure") {
        // Structure records live in the structured reader; open that view.
        location.assign(
          `/?view=reader&paper=${encodeURIComponent(paper.id)}&content=structure`,
        );
      }
      if (!target.canNavigate) {
        setNoteError(target.notice || "来源已变化，无法定位到原位置；摘录已保留。");
      }
      return true;
    },
    [paper.id, notes.items],
  );
  const editAnnotation = useCallback(
    async (annotation: Annotation, values: { comment?: string; color?: AnnotationColor }) => {
      const updated = await notes.update(annotation.id, {
        revision: annotation.revision,
        ...values,
      });
      return Boolean(updated);
    },
    [notes],
  );
  useEffect(() => {
    // The chat can insert an answer into this note; refetch so it shows up
    // immediately without polling.
    const refresh = () => loadNote();
    window.addEventListener("paper-notes:refresh", refresh);
    return () => window.removeEventListener("paper-notes:refresh", refresh);
  }, [loadNote]);
  const latestSelection = useRef(selection);
  latestSelection.current = selection;
  function openSelectionPanel() {
    // Opens the same single selection surface without preparing a translation,
    // so a highlight/annotation is one click away.
    const snapshot = selection,
      document = identity.identity;
    if (!snapshot || !document) return;
    setTranslateSelection({ ...snapshot, documentId: document.id } as any);
  }
  async function openSelectionTranslation() {
    const snapshot = selection,
      document = identity.identity;
    if (!snapshot || !document) return;
    let sourceId: string | undefined;
    if (snapshot.page) {
      try {
        const response = await api(
          `/api/paper/${encodeURIComponent(paper.id)}/pdf-sources`,
          "POST",
          {
            document: variant,
            documentId: document.id,
            page: snapshot.page,
            text: snapshot.text,
          },
        );
        sourceId = response.source.id;
      } catch {
        /* The selection remains explicitly unverified; translation does not require parsing. */
      }
    }
    if (alive.current && latestSelection.current === snapshot)
      setTranslateSelection({
        ...snapshot,
        document: variant,
        documentId: document.id,
        sourceId,
      });
  }
  function onScroll() {
    if (!host.current || !doc || restore.current) return;
    const top = host.current.getBoundingClientRect().top + 40;
    let best: HTMLElement | null = null;
    for (const el of host.current.querySelectorAll<HTMLElement>(
      ".page-host[data-page]",
    )) {
      if (el.getBoundingClientRect().top <= top) best = el;
      else break;
    }
    if (best) {
      const n = Number(best.dataset.page);
      setPage(n);
      point.current = {
        document: variant,
        page: n,
        offset: Math.max(
          0,
          Math.min(
            1,
            (top - best.getBoundingClientRect().top) / best.clientHeight,
          ),
        ),
        zoom,
        rotation,
        fingerprint: doc.fingerprints[0] || "unknown",
        sessionId: sessionId || null,
      };
    }
  }
  const revealPending = useRef(false);
  function revealSearch() {
    if (!revealPending.current || !match.match || !host.current) return;
    const surface = host.current.querySelector<HTMLElement>(
        `.page-host[data-page="${match.page}"] .pdf-page`,
      ),
      marker = surface?.querySelector<HTMLElement>(".pdf-search-highlight");
    if (!surface || !marker) return;
    revealPending.current = false;
    jump(
      match.page,
      Math.max(
        0,
        Math.min(
          0.98,
          (marker.offsetTop - host.current.clientHeight * 0.25) /
            surface.clientHeight,
        ),
      ),
    );
    host.current.scrollLeft = Math.max(
      0,
      marker.getBoundingClientRect().left -
        host.current.getBoundingClientRect().left +
        host.current.scrollLeft -
        host.current.clientWidth * 0.4,
    );
  }
  useEffect(() => {
    revealPending.current = !!match.match;
    const timer = setTimeout(revealSearch, 80);
    return () => clearTimeout(timer);
  }, [match, scale, rotation]);
  function pageReady(_n: number) {
    setStatus("");
    if (_n === match.page) requestAnimationFrame(revealSearch);
  }

  const resized = useRef<(() => void) | null>(null);
  useEffect(() => () => resized.current?.(), []);
  function resize(e: React.PointerEvent) {
    const start = e.clientX,
      w =
        workspace.current
          ?.querySelector(".reader-chat")
          ?.getBoundingClientRect().width || 380;
    const move = (event: PointerEvent) =>
      workspace.current?.style.setProperty(
        "--chat-width",
        Math.max(300, Math.min(640, w + start - event.clientX)) + "px",
      );
    const stop = () => {
      onPreferences({
        chatWidth: Math.round(
          workspace.current
            ?.querySelector(".reader-chat")
            ?.getBoundingClientRect().width || 380,
        ),
      });
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    resized.current = stop;
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  }
  return (
    <main
      className={
        "reader-workspace " +
        (embedded ? "embedded-reader " : "") +
        (chat ? "with-chat " : "") +
        (thumbs ? "with-thumbnails " : "") +
        (mobileChat ? "mobile-chat" : "")
      }
      ref={workspace}
    >
      <div className="reader-toolbar">
        <button
          className="icon-button"
          aria-label="显示阅读导航"
          aria-pressed={thumbs}
          onClick={() => {
            setThumbs((v) => !v);
            onPreferences({ thumbnailOpen: !thumbs });
          }}
        >
          <PanelLeft size={18} />
        </button>
        <button
          className="icon-button"
          aria-label="搜索全文"
          onClick={() => {
            setThumbs(true);
            setNavTab("search");
          }}
        >
          <Search size={17} />
        </button>
        {toolbarLead}
        <span className="reader-document-title" title={paper.title}>
          {paper.title}
        </span>
        {toolbarContent || (
          <label>
            <span className="sr-only">文档版本</span>
            <select
              aria-label="文档版本"
              value={variant}
              onChange={(e) => {
                void save();
                onVersion(e.target.value === "translated");
              }}
            >
              <option value="original">原文</option>
              {(paper.translated || translated) && (
                <option value="translated">译文</option>
              )}
            </select>
          </label>
        )}
        <div className="toolbar-separator" />
        <button
          className="icon-button"
          aria-label="上一页"
          disabled={!doc || page <= 1}
          onClick={() => jump(page - 1)}
        >
          <ChevronLeft size={17} />
        </button>
        <input
          aria-label="页码"
          className="page-number"
          type="number"
          value={pageInput}
          min={1}
          max={doc?.numPages || 1}
          onChange={(e) => setPageInput(e.target.value)}
          onBlur={() => {
            const n = Number(pageInput);
            if (Number.isInteger(n) && n > 0 && n <= (doc?.numPages || 1))
              jump(n);
            else setPageInput(String(page));
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
          }}
        />
        <span className="page-count">/ {doc?.numPages || "—"}</span>
        <button
          className="icon-button"
          aria-label="下一页"
          disabled={!doc || page >= doc.numPages}
          onClick={() => jump(page + 1)}
        >
          <ChevronRight size={17} />
        </button>
        <div className="toolbar-separator" />
        <select
          aria-label="缩放"
          value={zoom}
          onChange={(e) => {
            const v = e.target.value;
            setZoom(v === "width" || v === "page" ? v : Number(v));
          }}
        >
          <option value="width">适合宽度</option>
          <option value="page">适合页面</option>
          {[0.5, 0.75, 1, 1.25, 1.5, 2, 3].map((v) => (
            <option value={v} key={v}>
              {v * 100}%
            </option>
          ))}
        </select>
        <button
          className="icon-button"
          aria-label="页级记录"
          title="这一页没有可选文字时使用页级记录"
          disabled={!doc}
          onClick={() => setPageNote(true)}
        >
          <StickyNote size={17} />
        </button>
        <button
          className="icon-button rotate-button"
          aria-label="旋转"
          title="旋转页面"
          onClick={() => setRotation((v) => (v + 90) % 360)}
          disabled={!doc}
        >
          <RotateCw size={17} />
        </button>
        <button
          aria-pressed={chat}
          className="chat-toggle"
          onClick={() => {
            if (matchMedia("(max-width:640px)").matches) {
              setChat(true);
              setMobileChat((v) => !v);
            } else {
              setChat((v) => !v);
              setMobileChat(false);
            }
          }}
        >
          <MessageSquare size={17} />
          <span>{mobileChat ? "返回阅读" : "论文问答"}</span>
        </button>
      </div>
      {translateSelection && (
        <SelectionPopup
          key={translateSelection.text + translateSelection.documentId}
          paperId={paper.id}
          selection={translateSelection}
          onClose={() => setTranslateSelection(null)}
          onSaveAnnotation={async (payload) => {
            const document = identity.identity;
            if (!document) return false;
            const created = await notes.create({
              kind: payload.kind,
              color: payload.color,
              comment: payload.comment,
              excerpt: payload.excerpt,
              documentId: document.id,
              anchor: payload.anchor || { mode: "page", page: translateSelection.page || 1, rects: [] },
              context: payload.context,
            });
            if (!created) return false;
            setPanelTab("annotations");
            setChat(true);
            return true;
          }}
          onAsk={(value) => {
            setExcerpt(value);
            setChat(true);
            setMobileChat(matchMedia("(max-width:640px)").matches);
            setTranslateSelection(null);
          }}
        />
      )}
      {pageNote && identity.identity && (
        <PageNoteDialog
          paperId={paper.id}
          documentId={identity.identity.id}
          page={page}
          pageCount={identity.identity.pageCount || 1}
          onClose={() => setPageNote(false)}
          onCreated={() => {
            void notes.refresh();
            setPanelTab("annotations");
            setChat(true);
          }}
        />
      )}
      {regionClick && identity.identity && !translated && (
        <CachedTranslation
          key={`${regionClick.page}:${regionClick.x}:${regionClick.y}`}
          paperId={paper.id}
          document={identity.identity}
          point={regionClick}
          results={translationResults}
          selectedId={cachedTranslationId}
          onSelected={(id) => onCachedTranslation?.(id)}
          onClose={() => setRegionClick(null)}
          onAsk={(value) => {
            setExcerpt({ ...value, title: paper.title, document: "original" });
            setChat(true);
            setRegionClick(null);
          }}
        />
      )}
      <div className="reader-body">
        {thumbs && (
          <PdfNavigation
            doc={doc}
            paperId={paper.id}
            identity={identity.identity}
            identityError={identity.error}
            onIdentityRetry={identity.retry}
            current={() => ({
              page: point.current?.page || page,
              offset: point.current?.offset || 0,
            })}
            onJump={jump}
            onBookmark={(b) => {
              if (b.resultId || b.documentId !== identity.identity?.id)
                onBookmarkNavigate?.(b);
              else {
                if (b.notice) setNotice(b.notice);
                jump(b.location.page, b.location.offset);
              }
            }}
            structureResult={structureResult}
            tab={navTab}
            setTab={setNavTab}
            onClose={() => {
              setThumbs(false);
              setMatch({ page: 0, match: null });
            }}
            onMatch={(page, match) => setMatch({ page, match })}
            thumbnail={
              <div className="thumbnail-sidebar" ref={thumbRoot}>
                {" "}
                {doc &&
                  Array.from({ length: doc.numPages }, (_, i) => (
                    <button
                      className={
                        page === i + 1 ? "thumbnail selected" : "thumbnail"
                      }
                      key={i}
                      onClick={() => jump(i + 1)}
                      aria-label={`跳到第 ${i + 1} 页`}
                    >
                      <PdfPage
                        doc={doc}
                        number={i + 1}
                        scale={0.16}
                        rotation={0}
                        root={thumbRoot}
                        estimatedSize={baseSize.current}
                        thumbnail
                      />
                      <span>{i + 1}</span>
                    </button>
                  ))}
              </div>
            }
          />
        )}
        <section className="pdf-panel" aria-label="PDF 阅读">
          <div className="pdf-status" role="status">
            {status}
          </div>
          {error && (
            <div className="pdf-error">
              <BookOpen size={38} />
              <h2>暂时无法打开文档</h2>
              <p role="alert">{error}</p>
              <button onClick={() => setRetry((n) => n + 1)}>
                重新加载 PDF
              </button>
            </div>
          )}
          {notice && (
            <div className="notice">
              {notice}
              <button
                onClick={() => {
                  if (positionLoaded.current) void save();
                  else setRetry((n) => n + 1);
                  setNotice("");
                }}
              >
                {positionLoaded.current ? "重试保存" : "重新加载位置"}
              </button>
            </div>
          )}
          <div className="pdf-scroll" ref={host} onScroll={onScroll}>
            {doc &&
              Array.from({ length: doc.numPages }, (_, i) => (
                <PdfPage
                  key={`${doc.fingerprints[0]}-${i}`}
                  annotations={notes.items}
                  onAnnotationOpen={(annotation) => {
                    setOpenedAnnotation(annotation);
                    setPanelTab("annotations");
                    setChat(true);
                  }}
                  doc={doc}
                  number={i + 1}
                  scale={scale}
                  rotation={rotation}
                  root={host}
                  estimatedSize={baseSize.current}
                  searchMatch={match.page === i + 1 ? match.match : null}
                  onMatchMissing={() =>
                    setNotice(
                      "匹配已定位到页面，但此处文字层无法精确映射，未绘制猜测高亮。",
                    )
                  }
                  onRegion={
                    !translated && identity.identity
                      ? (page, x, y) => {
                          if (matchMedia("(max-width:900px)").matches)
                            setThumbs(false);
                          setRegionClick({ page, x, y });
                        }
                      : undefined
                  }
                  sourceRegions={sourceTarget?.regions
                    .filter((r) => r.page === i + 1)
                    .map((r) => r.rect)}
                  onLayout={reflowPosition}
                  onReady={pageReady}
                />
              ))}
          </div>
          {selection && (
            <div className="selection-actions">
              <Quote size={16} />
              <span>已选择 {Array.from(selection.text).length} 字</span>
              <button disabled={!identity.identity} onClick={openSelectionPanel}>
                高亮/批注
              </button>
              <button
                disabled={!identity.identity}
                onClick={() => void openSelectionTranslation()}
              >
                翻译
              </button>
              <button
                className="primary"
                disabled={!selection.page}
                title={
                  !selection.page
                    ? "跨页选区请分页面提问，以核实来源"
                    : undefined
                }
                onClick={async () => {
                  const snapshot = selection;
                  try {
                    const verified = await api(
                      `/api/paper/${encodeURIComponent(paper.id)}/pdf-sources`,
                      "POST",
                      {
                        document: variant,
                        documentId: fileDocumentId,
                        page: selection.page,
                        text: selection.text,
                      },
                    );
                    if (!alive.current || latestSelection.current !== snapshot)
                      return;
                    setExcerpt({ ...snapshot, sourceId: verified.source.id });
                  } catch (e) {
                    setNotice(
                      "选区暂时无法核实到此 PDF 页面，请重新选择或稍后重试。",
                    );
                    return;
                  }
                  setChat(true);
                  setMobileChat(matchMedia("(max-width:640px)").matches);
                  setSelection(null);
                  window.getSelection()?.removeAllRanges();
                }}
              >
                选区提问
              </button>
              <button
                className="icon-button"
                aria-label="关闭选区操作"
                onClick={() => {
                  setSelection(null);
                  window.getSelection()?.removeAllRanges();
                }}
              >
                <X size={15} />
              </button>
            </div>
          )}
        </section>
        {chat && (
          <>
            <div
              className="resize-handle"
              role="separator"
              aria-label="调整问答宽度"
              aria-orientation="vertical"
              tabIndex={0}
              onPointerDown={resize}
              onKeyDown={(e) => {
                if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                  const w =
                    workspace.current
                      ?.querySelector(".reader-chat")
                      ?.getBoundingClientRect().width || 380;
                  workspace.current?.style.setProperty(
                    "--chat-width",
                    Math.max(
                      300,
                      Math.min(640, w + (e.key === "ArrowLeft" ? 20 : -20)),
                    ) + "px",
                  );
                  onPreferences({
                    chatWidth: Math.max(
                      300,
                      Math.min(640, w + (e.key === "ArrowLeft" ? 20 : -20)),
                    ),
                  });
                }
              }}
            />
            <section className="reader-chat">
              <button
                className="icon-button chat-close"
                aria-label="收起问答"
                onClick={() => {
                  setChat(false);
                  setMobileChat(false);
                }}
              >
                <X size={17} />
              </button>
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
              {noteError && <p className="notice error reader-panel-notice">{noteError}</p>}
              <NoteConflictBanner
                note={note}
                onResolve={resolveNoteConflict}
                onOpen={() => setPanelTab("note")}
              />
              {panelTab === "annotations" && (
                <AnnotationsPanel
                  annotations={notes.items}
                  loading={notes.loading}
                  error={notes.error}
                  onRefresh={() => void notes.refresh()}
                  onEdit={editAnnotation}
                  onOpenNote={() => setPanelTab("note")}
                  onLoadMore={() => void notes.loadMore()}
                  hasMore={notes.hasMore}
                  onOpen={(annotation) => {
                    setOpenedAnnotation(annotation);
                    if (annotation.canNavigate && annotation.anchor?.mode === "pdf") {
                      jump((annotation.anchor as any).page);
                    }
                  }}
                  onDelete={async (annotation) => {
                    const value = await notes.remove(annotation.id, annotation.revision);
                    return (value as any)?.annotation ?? null;
                  }}
                  onRestore={async (annotation) => {
                    const value = await notes.restore(annotation.id, annotation.revision);
                    return (value as any)?.annotation ?? null;
                  }}
                />
              )}
              {panelTab === "note" && (
                <div className="reader-note-panel">
                  {noteError && <p className="notice error">{noteError}</p>}
                  <NoteEditor
                    paperId={paper.id}
                    ownerId={noteOwner()}
                    note={note}
                    onNote={setNote}
                    onError={setNoteError}
                    entries={note?.entries}
                    onOpenSource={(source) => onSource?.(source.sourceId)}
                    onReloadNote={loadNote}
                    onResolveConflict={resolveNoteConflict}
                    onOpenAnnotation={openNoteAnnotation}
                    onDraftChange={(text) => {
                      liveNoteDraft.current = text;
                    }}
                    onConflictChange={(value) => {
                      liveNoteConflict.current = value;
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
                key={paper.id}
                paperId={paper.id}
                onExpired={onExpired}
                onSource={onSource}
                initialSession={sessionId}
                onSessionChange={(id) => {
                  setSessionId(id);
                  if (point.current) {
                    point.current = { ...point.current, sessionId: id || null };
                    void save();
                  }
                }}
                excerpt={excerpt}
                onClearExcerpt={() => setExcerpt(null)}
                drafts={drafts}
              />
              )}
              {openedAnnotation && panelTab === "annotations" && (
                <div className="annotation-detail">
                  <p>
                    <strong>{openedAnnotation.excerpt || "（页级记录）"}</strong>
                  </p>
                  {openedAnnotation.comment && <p>{openedAnnotation.comment}</p>}
                  <p className="muted">
                    {openedAnnotation.canNavigate
                      ? "已定位到来源；缩放、旋转后位置仍然对应。"
                      : openedAnnotation.notice ||
                        "来源不可用或已变化，不会跳到别的内容。"}
                  </p>
                  {openedAnnotation.anchor?.mode === "structure" && (
                    <button
                      onClick={() => {
                        location.assign(
                          `/?view=reader&paper=${encodeURIComponent(paper.id)}&content=structure`,
                        );
                      }}
                    >
                      在结构阅读中打开
                    </button>
                  )}
                  <button onClick={() => setOpenedAnnotation(null)}>关闭详情</button>
                </div>
              )}
            </section>
          </>
        )}
      </div>
      {passwordNeeded && (
        <Modal
          title="输入 PDF 密码"
          onClose={() => {
            setPasswordNeeded(false);
            setPassword("");
            setError("已取消输入密码，可重新加载 PDF。");
          }}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              passwordCallback.current?.(password);
              setPassword("");
              setPasswordNeeded(false);
            }}
          >
            <p>{status}</p>
            <Field label="文档密码">
              <input
                type="password"
                autoComplete="off"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </Field>
            <button className="primary">打开 PDF</button>
          </form>
        </Modal>
      )}
    </main>
  );
}
