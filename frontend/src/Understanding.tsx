import { useEffect, useRef, useState, type ComponentProps } from "react";
import {
  ArrowLeft,
  BookOpen,
  Download,
  MessageSquare,
  Printer,
  RefreshCw,
  Sparkles,
  FileText,
  Settings as SettingsIcon,
} from "lucide-react";
import { Reader as DocumentReader } from "./StructuredReader";
import { Chat, type Excerpt } from "./Chat";
import { api, Markdown, Modal, Status, timestampText, useResource } from "./ui";
import { errorText } from "./api";
import {
  ProcessingTaskDetails,
  TranslationDialog,
  processingNames,
} from "./Processing";
import { ResizeHandle } from "./ResizeHandle";
import "./understanding.css";

type Mode = "reader" | "overview" | "interpretation";
const names: Record<Mode, string> = {
  reader: "阅读正文",
  overview: "AI 概览",
  interpretation: "深度解读",
};
export function coverageText(coverage: any) {
  if (!coverage) return "正在确认正文范围";
  if (coverage.complete) return `完整解析原文 · ${coverage.totalPages} 页`;
  if (coverage.reason === "legacy_coverage_unknown")
    return "旧 Markdown 正文 · 全文完整性未知";
  if (coverage.reason === "no_content") return "尚无可用解析正文";
  return `部分或待核实解析 · 已确认 ${coverage.pages?.length || 0} 页${coverage.totalPages ? ` / ${coverage.totalPages} 页` : ""}`;
}

export function PaperWorkspace(
  props: ComponentProps<typeof DocumentReader> & {
    initialView?: "analysis" | "reader";
  },
) {
  const query = new URLSearchParams(location.search);
  const [mode, setMode] = useState<Mode>(() =>
    query.get("panel") === "interpretation"
      ? "interpretation"
      : query.get("panel") === "overview" || props.initialView === "analysis"
        ? "overview"
        : "reader",
  );
  const [questionExcerpt, setQuestionExcerpt] = useState<Excerpt | null>(null);
  const [resultIds, setResultIds] = useState<Record<string, string>>({});
  const positionLoaded = useRef(false);
  const [positionReady, setPositionReady] = useState(false);
  const positionTimer = useRef<ReturnType<typeof setTimeout> | null>(null),
    saveRef = useRef(() => {});
  const pendingPosition = useRef<any>(null),
    generatedKind = useRef<Mode | null>(null);
  const [generating, setGenerating] = useState(false),
    [parse, setParse] = useState(false),
    [jobId, setJobId] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<any>(null),
    [partial, setPartial] = useState(false),
    [source, setSource] = useState<any>(null),
    [excerpt, setExcerpt] = useState<any>(null),
    [returnMode, setReturnMode] = useState<Mode | null>(null);
  const [mobileChat, setMobileChat] = useState(false),
    [showChat, setShowChat] = useState(true),
    [session, setSession] = useState("");
  const host = useRef<HTMLDivElement>(null),
    article = useRef<HTMLDivElement>(null),
    positions = useRef<Record<string, number>>({}),
    alive = useRef(true),
    abort = useRef(new AbortController());
  const base = `/api/paper/${encodeURIComponent(props.paper.id)}`;
  const content = useResource<any>(base + "/content", {}),
    results = useResource<any>(base + "/understanding", {
      results: [],
      heads: {},
    });
  const chosen =
    resultIds[mode] ||
    results.data.heads?.[mode] ||
    results.data.results?.find((r: any) => r.kind === mode)?.id ||
    (mode === "interpretation" && content.data.hasLegacyAnalysis
      ? "legacy"
      : "");
  const detail = useResource<any>(
    mode !== "reader" && chosen ? base + "/understanding/" + chosen : null,
    {},
  );
  // A route change can precede its response; never label the previous result
  // as the newly selected analysis or let it overwrite that reading position.
  const activeResult =
    detail.data.result?.id === chosen && detail.data.result?.kind === mode
      ? detail.data.result
      : null;
  const body = activeResult?.body;
  const sectionKey = mode + "|" + chosen;
  const keyRef = useRef(sectionKey);
  keyRef.current = sectionKey;
  useEffect(() => {
    alive.current = true;
    const leave = () => saveRef.current();
    window.addEventListener("pagehide", leave);
    return () => {
      leave();
      window.removeEventListener("pagehide", leave);
      if (positionTimer.current) clearTimeout(positionTimer.current);
      alive.current = false;
      abort.current.abort();
      document.body.classList.remove("analysis-printing");
    };
  }, []);
  useEffect(() => {
    if (props.initialView === "analysis")
      setMode(
        new URLSearchParams(location.search).get("panel") === "interpretation"
          ? "interpretation"
          : "overview",
      );
  }, [props.initialView]);
  useEffect(() => {
    host.current?.style.setProperty(
      "--analysis-chat-width",
      `${props.preferences.chatWidth || 380}px`,
    );
  }, [props.preferences.chatWidth, mode, showChat]);
  useEffect(() => {
    api<any>(
      base + "/understanding-position",
      "GET",
      undefined,
      abort.current.signal,
    )
      .then((v) => {
        positionLoaded.current = true;
        const saved = v.positions || (v.kind ? { [v.kind]: v } : {});
        for (const [kind, p] of Object.entries(saved) as [string, any][]) {
          if (p.resultId)
            setResultIds((ids) => ({ ...ids, [kind]: p.resultId }));
          positions.current[kind + "|" + (p.resultId || "")] = p.offset || 0;
        }
        if (!query.has("panel") && v.kind && props.initialView !== "analysis")
          setMode(v.kind);
      })
      .catch(() => {
        if (alive.current)
          setError(
            "分析阅读位置加载失败，可继续阅读；本次位置暂不覆盖已有记录。",
          );
      })
      .finally(() => {
        if (alive.current) setPositionReady(true);
      });
  }, [base]);
  useEffect(() => {
    const url = new URL(location.href);
    url.searchParams.set("panel", mode);
    history.replaceState(null, "", url);
  }, [mode]);
  useEffect(() => {
    if (article.current && activeResult && positionReady && !detail.loading)
      article.current.scrollTop =
        (positions.current[sectionKey] || 0) *
        Math.max(
          1,
          article.current.scrollHeight - article.current.clientHeight,
        );
  }, [sectionKey, detail.loading, positionReady, activeResult?.id]);
  function savePosition() {
    if (!positionReady || !positionLoaded.current) return;
    const node = article.current;
    if (node && mode !== "reader" && activeResult) {
      const offset =
        node.scrollTop / Math.max(1, node.scrollHeight - node.clientHeight);
      positions.current[sectionKey] = offset;
      pendingPosition.current = {
        kind: mode,
        resultId: chosen || null,
        offset,
      };
    }
    if (!pendingPosition.current) return;
    void api(
      base + "/understanding-position",
      "PUT",
      pendingPosition.current,
      undefined,
      true,
    ).catch(() => {
      if (alive.current) setError("分析阅读位置保存失败，请稍后重试。");
    });
  }
  saveRef.current = savePosition;
  function change(next: Mode) {
    savePosition();
    setQuestionExcerpt(null);
    setMode(next);
    setMobileChat(false);
    setError("");
  }
  async function openSource(id: string) {
    try {
      const value = await api<any>(
        `/api/sources/${encodeURIComponent(id)}`,
        "GET",
        undefined,
        abort.current.signal,
      );
      savePosition();
      setQuestionExcerpt(null);
      if (!value.source.canNavigate) {
        setExcerpt(value.source);
        return;
      }
      setSource(value.source);
      setReturnMode(mode);
      setMode("reader");
    } catch (e) {
      setError(errorText(e));
    }
  }
  async function estimate() {
    setBusy(true);
    setError("");
    try {
      const value = await api<any>(
        base + "/processing/estimate",
        "POST",
        {
          kind: mode,
          contentVersion: content.data.version,
          allowPartial: partial,
          ...(chosen && chosen !== "legacy"
            ? { previousResultId: chosen }
            : {}),
        },
        abort.current.signal,
      );
      setPreview(value);
    } catch (e) {
      setError(errorText(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function generate() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await api<any>(
        base + "/processing/jobs",
        "POST",
        {
          kind: mode,
          contentVersion: content.data.version,
          allowPartial: partial,
          ...(chosen && chosen !== "legacy"
            ? { previousResultId: chosen }
            : {}),
        },
        abort.current.signal,
      );
      generatedKind.current = mode;
      setJobId(value.job.id);
      setGenerating(false);
      setPreview(null);
    } catch (e) {
      setError(errorText(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function download(format: string) {
    setBusy(true);
    setError("");
    try {
      const response = await api<any>(
        base + "/processing/jobs",
        "POST",
        { kind: "analysis_export", analysisResultId: chosen, format },
        abort.current.signal,
      );
      const id = response.job.id;
      setJobId(id);
      let job = response.job;
      while (["queued", "running"].includes(job.status) && alive.current) {
        await new Promise((resolve) => setTimeout(resolve, 500));
        job = (
          await api<any>(
            `/api/processing/jobs/${id}`,
            "GET",
            undefined,
            abort.current.signal,
          )
        ).job;
      }
      if (job.status !== "completed")
        throw { code: job.error || "analysis_export_failed" };
      if (alive.current) {
        const link = document.createElement("a");
        link.href = `/api/understanding/${job.resultId}/download`;
        link.download = "";
        link.click();
      }
    } catch (e) {
      if (alive.current) setError(errorText(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  }
  async function print() {
    if (!article.current) return;
    setBusy(true);
    setError("");
    try {
      for (const image of article.current.querySelectorAll("img"))
        image.loading = "eager";
      await Promise.all(
        [...article.current.querySelectorAll("img")].map((img) => img.decode()),
      );
      await document.fonts.ready;
      for (const math of article.current.querySelectorAll<HTMLElement>("math"))
        math.style.setProperty(
          "--print-math-scale",
          String(
            Math.min(1, 680 / Math.max(1, math.getBoundingClientRect().width)),
          ),
        );
      const details = [
        ...article.current.querySelectorAll<HTMLDetailsElement>(
          ".analysis-evidence",
        ),
      ];
      const previous = details.map((d) => d.open);
      details.forEach((d) => (d.open = true));
      document.body.classList.add("analysis-printing");
      const cleanup = () => {
        document.body.classList.remove("analysis-printing");
        details.forEach((d, i) => (d.open = previous[i]));
      };
      window.addEventListener("afterprint", cleanup, { once: true });
      window.print();
    } catch {
      setError("有图片尚未加载完成，请重试后再打印。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="paper-understanding">
      <nav className="paper-sections" aria-label="论文工作区">
        {(["overview", "interpretation", "reader"] as Mode[]).map((tab) => (
          <button
            key={tab}
            className={mode === tab ? "active" : ""}
            onClick={() => change(tab)}
          >
            {tab === "reader" ? (
              <BookOpen size={16} />
            ) : tab === "overview" ? (
              <Sparkles size={16} />
            ) : (
              <FileText size={16} />
            )}{" "}
            {names[tab]}
          </button>
        ))}
        {returnMode && mode === "reader" && (
          <button
            onClick={() => {
              setSource(null);
              setMode(returnMode);
              setReturnMode(null);
            }}
          >
            <ArrowLeft size={16} />
            返回{names[returnMode]}
          </button>
        )}
      </nav>
      {mode === "reader" ? (
        <DocumentReader {...props} sourceTarget={source || undefined} />
      ) : (
        <div
          ref={host}
          className={`analysis-workspace ${showChat ? "with-chat" : ""} ${mobileChat ? "show-mobile-chat" : ""}`}
        >
          <main className="understanding-main">
            <header className="understanding-heading">
              <div>
                <span className="eyebrow">{names[mode]}</span>
                <h1>{props.paper.title}</h1>
              </div>
              <button
                aria-label="切换论文问答"
                onClick={() => {
                  if (matchMedia("(max-width: 640px)").matches) {
                    setShowChat(true);
                    setMobileChat((v) => !v);
                  } else setShowChat((v) => !v);
                }}
              >
                <MessageSquare size={18} />
              </button>
            </header>
            <div className="understanding-actions">
              <span>{coverageText(content.data.coverage)}</span>
              <button
                className="primary"
                onClick={() => {
                  setGenerating(true);
                  setPreview(null);
                  setPartial(false);
                }}
              >
                {chosen ? <RefreshCw size={15} /> : <Sparkles size={15} />}{" "}
                {chosen ? "更新" : "生成"}
                {names[mode]}
              </button>
              {chosen && activeResult && (
                <>
                  <button
                    disabled={busy}
                    onClick={() => void download("markdown")}
                  >
                    <Download size={15} />
                    Markdown
                  </button>
                  <button disabled={busy} onClick={() => void download("zip")}>
                    含图片包
                  </button>
                  <button disabled={busy} onClick={() => void print()}>
                    <Printer size={15} />
                    浏览器打印
                  </button>
                </>
              )}
            </div>
            <Status
              error={error || content.error || results.error || detail.error}
              loading={content.loading || results.loading || detail.loading}
              retry={() => {
                content.refresh();
                results.refresh();
                detail.refresh();
              }}
            />
            <div
              className="understanding-scroll"
              ref={article}
              onScroll={() => {
                if (!activeResult) return;
                const node = article.current;
                if (node) {
                  const offset =
                    node.scrollTop /
                    Math.max(1, node.scrollHeight - node.clientHeight);
                  positions.current[keyRef.current] = offset;
                  pendingPosition.current = {
                    kind: mode,
                    resultId: chosen || null,
                    offset,
                  };
                }
                if (positionTimer.current) clearTimeout(positionTimer.current);
                positionTimer.current = setTimeout(
                  () => saveRef.current(),
                  700,
                );
              }}
              onBlur={savePosition}
            >
              <details className="author-abstract">
                <summary>作者摘要 · Abstract</summary>
                <p>{props.paper.abstract || "当前元数据没有作者摘要。"}</p>
              </details>
              {chosen && activeResult ? (
                <>
                  <div className="analysis-metadata">
                    <span>
                      {activeResult.status === "historical"
                        ? "历史结果 · 配置与覆盖范围未知"
                        : `${processingNames[activeResult.status] || activeResult.status} · ${timestampText(activeResult.createdAt)} · ${activeResult.model} · ${activeResult.language === "zh" ? "简体中文" : activeResult.language === "en" ? "英语" : activeResult.language}`}
                    </span>
                    <select
                      aria-label="分析版本"
                      value={chosen}
                      onChange={(e) => {
                        savePosition();
                        setResultIds((v) => ({ ...v, [mode]: e.target.value }));
                      }}
                    >
                      {results.data.results
                        .filter((r: any) => r.kind === mode)
                        .map((r: any) => (
                          <option key={r.id} value={r.id}>
                            {timestampText(r.createdAt)} ·{" "}
                            {processingNames[r.status]}
                          </option>
                        ))}
                      {mode === "interpretation" &&
                        content.data.hasLegacyAnalysis && (
                          <option value="legacy">历史 Markdown 解读</option>
                        )}
                    </select>
                  </div>
                  {activeResult.stale && (
                    <p className="notice">
                      源 PDF 已变化。此结果仍可读，不能定位到新文件。
                    </p>
                  )}
                  {!activeResult.stale &&
                    activeResult.contentChanged && (
                      <p className="notice">
                        当前解析正文已更新；此分析保留原版本内容与来源，可明确生成新版本。
                      </p>
                    )}
                  {activeResult.configurationChanged && (
                    <p className="notice">
                      提示词、语言或模型配置已更新；此结果仍使用生成时的配置。
                    </p>
                  )}
                  {activeResult.status === "partial" && (
                    <p className="notice">
                      已完成 {body?.coveredChunks ?? "部分"} /{" "}
                      {body?.totalChunks ?? "未知"}{" "}
                      个正文分段，不能作为全文分析。请查看任务日志和来源范围。
                    </p>
                  )}
                  <p className="analysis-input-note">
                    {activeResult.status === "historical"
                      ? "历史解读：生成时的上下文范围与图像输入方式未记录。"
                      : "依据解析原文、表格文字与图注生成；模型未接收图片像素。"}
                  </p>
                  <Markdown
                    text={body?.markdown || ""}
                    sources={body?.sources}
                    onSource={(id) => void openSource(id)}
                  />
                  {!!Object.keys(body?.sources || {}).length && (
                    <details className="analysis-evidence">
                      <summary>证据摘录与来源</summary>
                      {Object.entries(body.sources).map(
                        ([label, value]: [string, any]) => (
                          <blockquote key={label}>
                            <button
                              onClick={() => void openSource(value.sourceId)}
                            >
                              [{label}] 查看来源
                            </button>
                            <span className="print-source-label">
                              [{label}]{" "}
                              {value.page
                                ? `原始 PDF 第 ${value.page} 页`
                                : "旧正文摘录，PDF 页码未知"}{" "}
                              · 来源版本 {value.snapshotId}
                            </span>
                            <button
                              onClick={() => {
                                setQuestionExcerpt({
                                  sourceId: value.sourceId,
                                  text: value.quote,
                                  page: value.page || 0,
                                  locationLabel: value.page
                                    ? `原始 PDF 第 ${value.page} 页`
                                    : "历史正文摘录（页码未知）",
                                  document: "original",
                                  title: props.paper.title,
                                });
                                setShowChat(true);
                                setMobileChat(true);
                              }}
                            >
                              据此提问
                            </button>
                            <p>{value.quote}</p>
                          </blockquote>
                        ),
                      )}
                    </details>
                  )}
                </>
              ) : (
                !detail.loading && (
                  <div className="empty-state">
                    <Sparkles size={38} />
                    <h2>
                      {mode === "overview"
                        ? "先了解这篇论文"
                        : "深入理解方法与实验"}
                    </h2>
                    <p>
                      {mode === "overview"
                        ? "独立整理研究问题、方法、实验、发现与局限。"
                        : "保留长篇图文解读，解释关键公式、图表与实验结果。"}
                    </p>
                    <p>
                      {content.data.available
                        ? "将复用已有解析原文；不会重新解析或要求先翻译全文。"
                        : "尚无可用正文。先明确创建解析任务，再生成分析。"}
                    </p>
                    <button
                      className="primary"
                      onClick={() =>
                        content.data.available
                          ? setGenerating(true)
                          : setParse(true)
                      }
                    >
                      {content.data.available
                        ? `生成${names[mode]}`
                        : "准备解析正文"}
                    </button>
                    {content.data.hasLegacyAnalysis && mode === "overview" && (
                      <button onClick={() => change("interpretation")}>
                        阅读已有历史解读
                      </button>
                    )}
                  </div>
                )
              )}
            </div>
          </main>
          {showChat && (
            <>
              <ResizeHandle
                host={host}
                property="--analysis-chat-width"
                label="调整问答宽度"
                min={300}
                max={600}
                initial={props.preferences.chatWidth || 380}
                direction={-1}
                onChange={(w) => props.onPreferences({ chatWidth: w })}
              />
              <div className="understanding-chat">
                <button
                  className="mobile-analysis-return"
                  onClick={() => setMobileChat(false)}
                >
                  <ArrowLeft size={16} />
                  返回分析
                </button>
                <Chat
                  paperId={props.paper.id}
                  excerpt={questionExcerpt}
                  onClearExcerpt={() => setQuestionExcerpt(null)}
                  initialSession={session}
                  onSessionChange={setSession}
                  onExpired={props.onExpired}
                  drafts={props.drafts}
                  onSource={(id) => void openSource(id)}
                />
              </div>
            </>
          )}
        </div>
      )}
      {generating && (
        <Modal
          title={`${chosen ? "更新" : "生成"}${names[mode]}`}
          onClose={() => setGenerating(false)}
        >
          <p>
            {coverageText(content.data.coverage)}
            。将复用解析原文，不依赖译文完成数量。
          </p>
          {!content.data.available ? (
            <>
              <p>尚无正文，需单独确认解析范围和预算。</p>
              <button
                onClick={() => {
                  setGenerating(false);
                  setParse(true);
                }}
              >
                准备解析正文
              </button>
            </>
          ) : (
            <>
              {!content.data.coverage?.complete && (
                <label>
                  <input
                    type="checkbox"
                    checked={partial}
                    onChange={(e) => {
                      setPartial(e.target.checked);
                      setPreview(null);
                    }}
                  />
                  我理解覆盖范围不完整或未知，仅分析当前可用正文。
                </label>
              )}
              <p>
                使用现有“论文问答与分析”模型。提示词和语言可在设置中分别调整。失败会保留已有结果。
              </p>
              {preview ? (
                <div className="generation-budget">
                  <p>
                    模型：{preview.model} · 输出语言：
                    {preview.language === "zh"
                      ? "简体中文"
                      : preview.language === "en"
                        ? "英语"
                        : preview.language}
                  </p>
                  <p>
                    正文 {preview.estimate.sourceUnits} 个单元，
                    {preview.estimate.chunks} 个处理分段。
                  </p>
                  <p>
                    保守上限：{preview.estimate.requests} 次请求，输入{" "}
                    {preview.estimate.inputTokens.toLocaleString()} token，输出{" "}
                    {preview.estimate.outputTokens.toLocaleString()} token。
                  </p>
                  <p>实际 token 以服务商报告为准；不会隐含执行连通性测试。</p>
                  {preview.exceedsBudget && (
                    <p className="notice error">
                      本次预估超出现有任务预算，无法提交。
                    </p>
                  )}
                  <button
                    className="primary"
                    disabled={busy || preview.exceedsBudget}
                    onClick={() => void generate()}
                  >
                    确认生成
                  </button>
                </div>
              ) : (
                <button
                  disabled={
                    busy || (!content.data.coverage?.complete && !partial)
                  }
                  onClick={() => void estimate()}
                >
                  核对处理量与预算
                </button>
              )}
            </>
          )}
          <Status error={error} loading={busy} />
        </Modal>
      )}
      {parse && (
        <TranslationDialog
          paper={props.paper}
          initial="parse"
          onClose={() => {
            setParse(false);
            content.refresh();
          }}
          onSubmitted={() => {
            content.refresh();
            results.refresh();
          }}
        />
      )}
      {jobId && (
        <Modal
          title="任务进度与日志"
          onClose={() => {
            setJobId("");
            content.refresh();
            results.refresh();
          }}
        >
          <ProcessingTaskDetails
            id={jobId}
            onTerminal={() => {
              const kind = generatedKind.current;
              generatedKind.current = null;
              if (kind)
                setResultIds((ids) => {
                  const next = { ...ids };
                  delete next[kind];
                  return next;
                });
              content.refresh();
              results.refresh();
              detail.refresh();
            }}
          />
        </Modal>
      )}
      {excerpt && (
        <Modal title="来源摘录" onClose={() => setExcerpt(null)}>
          <p>
            {excerpt.stale
              ? "原始文件已变化，以下为当时保存的正文。"
              : "此来源没有可靠 PDF 坐标，保留正文摘录供核对。"}
          </p>
          <p className="source-excerpt">{excerpt.text}</p>
        </Modal>
      )}
    </div>
  );
}

export function UnderstandingSettings() {
  const value = useResource<any>("/api/settings/paper-understanding", {
    settings: {},
  });
  const [error, setError] = useState(""),
    [saved, setSaved] = useState(false);
  return (
    <section className="understanding-settings">
      <h3>
        <SettingsIcon size={17} /> 论文概览与深度解读
      </h3>
      <p>
        共用论文问答与分析模型：{value.data.model || "尚未配置"}
        。两类结果独立生成，作者摘要不会被覆盖。
      </p>
      {(["overview", "interpretation"] as const).map(
        (kind) =>
          value.data.settings[kind] && (
            <details key={kind}>
              <summary>{names[kind]} · 提示词与语言</summary>
              <label>
                输出语言
                <select
                  value={value.data.settings[kind].language}
                  onChange={(e) => {
                    setSaved(false);
                    value.setData((d: any) => ({
                      ...d,
                      settings: {
                        ...d.settings,
                        [kind]: {
                          ...d.settings[kind],
                          language: e.target.value,
                        },
                      },
                    }));
                  }}
                >
                  <option value="zh">简体中文</option>
                  <option value="en">英语</option>
                </select>
              </label>
              <label>
                写作要求
                <textarea
                  rows={6}
                  maxLength={12000}
                  value={value.data.settings[kind].prompt}
                  onChange={(e) => {
                    setSaved(false);
                    value.setData((d: any) => ({
                      ...d,
                      settings: {
                        ...d.settings,
                        [kind]: { ...d.settings[kind], prompt: e.target.value },
                      },
                    }));
                  }}
                />
              </label>
              <p className="muted">来源校验、输出安全和预算约束始终生效。</p>
            </details>
          ),
      )}
      <button
        onClick={async () => {
          try {
            await api(
              "/api/settings/paper-understanding",
              "PUT",
              value.data.settings,
            );
            setSaved(true);
            setError("");
          } catch (e) {
            setError(errorText(e));
          }
        }}
      >
        保存解读偏好
      </button>
      {saved && <span role="status"> 已保存</span>}
      <Status error={error || value.error} loading={value.loading} />
    </section>
  );
}
