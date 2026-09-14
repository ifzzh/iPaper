import "./structured.css";
import { useEffect, useRef, useState } from "react";
import { Languages, FileText, Layers, RefreshCw } from "lucide-react";
import { api, Field, Modal, Status, useResource, timestampText } from "./ui";
import { errorText, type Paper } from "./api";

export type ProcessingResult = {
  id: string;
  paperId: string;
  kind: string;
  status: string;
  documentId: string;
  sourceHash: string;
  pageCount: number;
  parseId: string | null;
  targetLanguage: string;
  sourceLanguage: string;
  model: string | null;
  blockCount: number;
  stale: boolean;
  createdAt: string;
  provenance?: string | null;
};
export const languageLabels: Record<string, string> = {
  "zh-CN": "简体中文",
  "zh-TW": "繁體中文",
  en: "英语",
  ja: "日语",
  de: "德语",
  fr: "法语",
  es: "西班牙语",
};
function selectedPages(range: string, count: number): number[] | undefined {
  if (!range.trim()) return undefined;
  const pages = new Set<number>();
  for (const part of range.split(/[,，]/)) {
    if (!/^\s*\d+\s*(?:-\s*\d+\s*)?$/.test(part))
      throw new Error("请输入有效页码，例如 1-5,8。");
    const [a, end] = part.trim().split("-").map(Number),
      b = end ?? a;
    if (a < 1 || b < a || b > count)
      throw new Error("请输入有效页码，例如 1-5,8。");
    for (let page = a; page <= b; page++) pages.add(page);
  }
  return [...pages];
}
export type Source = {
  id: string;
  document?: string;
  paperId: string;
  documentId: string;
  resultId: string;
  blockId: string;
  revisionId: string | null;
  text: string;
  stale: boolean;
  page: number;
  precision: string;
  regions: { page: number; rect: number[] }[];
  canNavigate: boolean;
};
export const processingNames: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  pending: "等待处理",
  completed: "已完成",
  partial: "部分完成",
  failed: "处理失败",
  interrupted: "已中断，等待确认继续",
  cancelled: "已取消后续处理",
  cancelling: "正在停止后续处理",
};
const stages: Record<string, string> = {
  selection_translate: "翻译临时选区",
  queued: "等待调度",
  cloud_upload: "上传解析分段",
  normalizing: "校验并整理结构",
  parsed: "解析完成",
  translating: "翻译结构块",
  understanding: "阅读原文并生成解读",
  completed: "完成",
};
export function TranslationDialog({
  paper,
  onClose,
  onSubmitted,
  initial = "babeldoc",
  parseId,
  translationId,
  blockId,
  targetLanguage = "zh-CN",
  sourceLanguage = "auto",
}: {
  paper: Paper;
  onClose: () => void;
  onSubmitted?: () => void;
  initial?: string;
  parseId?: string;
  translationId?: string;
  blockId?: string;
  targetLanguage?: string;
  sourceLanguage?: string;
}) {
  const [kind, setKind] = useState(initial),
    [output, setOutput] = useState("dual"),
    [preview, setPreview] = useState<any>(null),
    [target, setTarget] = useState(targetLanguage),
    [range, setRange] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [job, setJob] = useState<any>(null),
    [scope, setScope] = useState<any>(null),
    [estimating, setEstimating] = useState(false),
    [scopeError, setScopeError] = useState("");
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);
  useEffect(() => {
    if (kind === "babeldoc" || preview) return;
    const c = new AbortController();
    abort.current = c;
    setBusy(true);
    setError("");
    api(
      `/api/paper/${encodeURIComponent(paper.id)}/processing/preview`,
      "POST",
      {},
      c.signal,
    )
      .then((v) => {
        if (!c.signal.aborted) setPreview(v);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setBusy(false);
      });
    return () => c.abort();
  }, [kind]);
  function requestData() {
    return {
      kind: blockId
        ? "retranslate"
        : kind === "parse"
          ? "parse"
          : "parse_translate",
      preflightId: preview.preflightId,
      parseResultId: parseId || preview.parseResultId || undefined,
      translationResultId: translationId,
      blockIds: blockId ? [blockId] : undefined,
      targetLanguage: target,
      sourceLanguage,
      pages: selectedPages(range, preview.pageCount),
    };
  }
  useEffect(() => {
    if (kind === "babeldoc" || !preview || job) return;
    const controller = new AbortController();
    setScope(null);
    setScopeError("");
    setEstimating(true);
    const timer = window.setTimeout(() => {
      let data;
      try {
        data = requestData();
      } catch (e) {
        setScopeError((e as Error).message);
        setEstimating(false);
        return;
      }
      api(
        `/api/paper/${encodeURIComponent(paper.id)}/processing/estimate`,
        "POST",
        data,
        controller.signal,
      )
        .then((value) => {
          if (!controller.signal.aborted) setScope(value);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setScopeError(errorText(e));
        })
        .finally(() => {
          if (!controller.signal.aborted) setEstimating(false);
        });
    }, 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [
    kind,
    preview,
    range,
    target,
    parseId,
    translationId,
    blockId,
    sourceLanguage,
    job,
  ]);
  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    const c = new AbortController();
    abort.current = c;
    try {
      if (kind === "babeldoc") {
        await api(
          "/api/paper/translate",
          "POST",
          { paper_id: paper.id, output_mode: output },
          c.signal,
        );
        onSubmitted?.();
        onClose();
        return;
      }
      const r = await api(
        `/api/paper/${encodeURIComponent(paper.id)}/processing/jobs`,
        "POST",
        requestData(),
        c.signal,
      );
      setJob(r.job);
      onSubmitted?.();
    } catch (e) {
      if (!c.signal.aborted)
        setError(
          e instanceof Error && e.message.startsWith("请输入")
            ? e.message
            : errorText(e),
        );
    } finally {
      if (!c.signal.aborted) setBusy(false);
    }
  }
  return (
    <Modal
      title={
        blockId
          ? "重新翻译此块"
          : kind === "parse"
            ? "准备论文解析"
            : "生成论文翻译"
      }
      onClose={onClose}
      wide
    >
      {job ? (
        <ProcessingTaskDetails id={job.id} onTerminal={onSubmitted} />
      ) : (
        <>
          <p className="muted">{paper.title}</p>
          {!blockId && (
            <div className="processing-options">
              <button
                className={kind === "babeldoc" ? "selected" : ""}
                onClick={() => setKind("babeldoc")}
              >
                <FileText />
                <strong>版式翻译</strong>
                <span>BabelDOC 保留 PDF 布局</span>
              </button>
              <button
                className={kind !== "babeldoc" ? "selected" : ""}
                onClick={() => setKind("structure")}
              >
                <Layers />
                <strong>结构化翻译</strong>
                <span>MinerU 解析 · 模型逐块翻译</span>
              </button>
            </div>
          )}
          {kind === "babeldoc" ? (
            <Field label="PDF 输出">
              <select
                value={output}
                onChange={(e) => setOutput(e.target.value)}
              >
                <option value="dual">双语 PDF（原文与译文）</option>
                <option value="mono">纯译文 PDF</option>
              </select>
            </Field>
          ) : (
            <>
              <p>MinerU 负责识别论文结构，翻译由独立配置的模型生成。</p>
              {preview && (
                <>
                  <div className="processing-summary">
                    <span>
                      {preview.pageCount} 页 · {preview.partCount} 个解析分段
                    </span>
                    <span>
                      {parseId || preview.parseResultId
                        ? "复用已有结构解析"
                        : "本任务包括云解析"}
                    </span>
                    <span>翻译模型：{preview.model.model || "尚未配置"}</span>
                  </div>
                  <div className="form-grid">
                    <Field label="目标语言">
                      <select
                        value={target}
                        disabled={!!translationId}
                        onChange={(e) => setTarget(e.target.value)}
                      >
                        {Object.entries(languageLabels).map(([v, t]) => (
                          <option key={v} value={v}>
                            {t}
                          </option>
                        ))}
                      </select>
                    </Field>
                    {!blockId && (
                      <Field label="处理页码（留空为全文）">
                        <input
                          value={range}
                          onChange={(e) => setRange(e.target.value)}
                          placeholder="例如 1-5,8"
                        />
                      </Field>
                    )}
                  </div>
                  <p className="muted">
                    本次上限：{preview.budget.requests} 次模型请求、
                    {preview.budget.inputTokens / 10000} 万输入 token、
                    {preview.budget.outputTokens / 10000} 万输出 token、2
                    小时。每次请求最长 120 秒；结果不确定时不会自动重发。
                  </p>
                  <p className="muted">
                    {estimating
                      ? "正在核对处理范围与缓存…"
                      : scope?.estimate
                        ? `所选 ${scope.selectedPages} 页共 ${scope.estimate.selectedBlocks} 块，已有 ${scope.estimate.cachedBlocks} 块可复用；本次最多 ${scope.estimate.requests} 次请求，预留 ${scope.estimate.inputTokens} 输入、${scope.estimate.outputTokens} 输出 token。`
                        : kind === "parse"
                          ? "仅解析全文，不调用翻译模型。"
                          : preview.estimateNote}
                    {scope?.parseRequired &&
                      kind !== "parse" &&
                      " 解析会处理全文，翻译仅处理所选范围。"}
                    {scope?.exceedsBudget &&
                      " 所选范围超过本次预算，请缩小页码范围。"}
                    已有版式译文不会被覆盖。
                  </p>
                  {!blockId && (
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={kind === "parse"}
                        onChange={(e) =>
                          setKind(e.target.checked ? "parse" : "structure")
                        }
                      />
                      仅解析结构，不调用翻译模型
                    </label>
                  )}
                </>
              )}
            </>
          )}
          <Status error={error || scopeError} loading={busy} />
          <div className="modal-actions">
            <button onClick={onClose}>关闭</button>
            <button
              className="primary"
              disabled={
                busy ||
                (kind !== "babeldoc" &&
                  (!preview ||
                    estimating ||
                    !scope ||
                    !!scopeError ||
                    scope.exceedsBudget))
              }
              onClick={() => void submit()}
            >
              <Languages size={16} />
              {blockId
                ? "确认重译"
                : kind === "parse"
                  ? "开始解析"
                  : "开始生成"}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}
export function StructuredSettings() {
  const resource = useResource<any>(
    "/api/settings/structured-translation",
    null,
  );
  const [key, setKey] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(initialize = false) {
    setBusy(true);
    setError("");
    try {
      const p = resource.data.profile;
      await api(
        "/api/settings/structured-translation" +
          (initialize ? "/initialize" : ""),
        initialize ? "POST" : "PUT",
        initialize
          ? {}
          : { model: p.model, baseUrl: p.baseUrl, ...(key ? { key } : {}) },
      );
      setKey("");
      resource.refresh();
      setNotice("独立翻译配置已保存。");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="settings-card">
      <h3>结构化翻译</h3>
      <p>独立于 BabelDOC；MinerU 沿用下方已有云解析配置。</p>
      <Status error={error || resource.error} loading={resource.loading} />
      {resource.data && (
        <>
          <Field label="模型名称">
            <input
              value={resource.data.profile.model}
              onChange={(e) =>
                resource.setData({
                  ...resource.data,
                  profile: { ...resource.data.profile, model: e.target.value },
                })
              }
            />
          </Field>
          <Field label="API 地址">
            <input
              value={resource.data.profile.baseUrl}
              onChange={(e) =>
                resource.setData({
                  ...resource.data,
                  profile: {
                    ...resource.data.profile,
                    baseUrl: e.target.value,
                  },
                })
              }
            />
          </Field>
          <Field
            label={
              resource.data.profile.keyConfigured ? "密钥（已配置）" : "密钥"
            }
          >
            <input
              type="password"
              autoComplete="new-password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="留空保留现有密钥"
            />
          </Field>
          <div className="button-row">
            <button disabled={busy} onClick={() => void save()}>
              保存结构化翻译配置
            </button>
            {!resource.data.profile.revision && (
              <button disabled={busy} onClick={() => void save(true)}>
                从版式翻译复制配置
              </button>
            )}
          </div>
        </>
      )}
      {notice && <p role="status">{notice}</p>}
    </section>
  );
}
export function ProcessingTaskDetails({
  id,
  onTerminal,
}: {
  id: string;
  onTerminal?: () => void;
}) {
  const [eventPages, setEventPages] = useState<number[]>([0]);
  const eventAfter = eventPages[eventPages.length - 1];
  const task = useResource<any>(`/api/processing/jobs/${id}`, null),
    events = useResource<any>(
      `/api/processing/jobs/${id}/events?after=${eventAfter}`,
      {
        events: [],
      },
    );
  const [error, setError] = useState("");
  const notified = useRef("");
  useEffect(() => {
    const state = task.data?.job?.status;
    if (
      state &&
      !["queued", "running", "cancelling"].includes(state) &&
      notified.current !== state
    ) {
      notified.current = state;
      onTerminal?.();
    }
  }, [task.data?.job?.status]);
  useEffect(() => {
    const t = setInterval(() => {
      task.refresh();
      events.refresh();
    }, 3000);
    return () => clearInterval(t);
  }, [id]);
  async function action(name: string) {
    try {
      await api(`/api/processing/jobs/${id}/${name}`, "POST", {});
      task.refresh();
      events.refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  const j = task.data?.job;
  return (
    <div className="processing-task">
      <Status error={error || task.error} loading={task.loading && !j} />
      {j && (
        <>
          <h3>{processingNames[j.status] || "文献处理"}</h3>
          <p>
            {stages[j.stage] || "准备处理"} · 已完成 {j.completed}
            {j.total ? ` / ${j.total}` : ""}{" "}
            {["cloud_upload", "normalizing"].includes(j.stage) ? "段" : "块"}
          </p>
          {j.error && (
            <p className="notice error">{errorText({ code: j.error })}</p>
          )}
          <p className="muted">
            已预留请求 {j.usage.requests}/{j.budget.requests}
            ；停止后不再启动新请求，云端已受理的处理可能继续。
          </p>
          <div className="button-row">
            {["queued", "running", "cancelling"].includes(j.status) ? (
              <button
                disabled={j.status === "cancelling"}
                onClick={() => void action("cancel")}
              >
                停止后续处理
              </button>
            ) : (
              j.kind !== "selection_translate" &&
              ["failed", "partial", "interrupted", "cancelled"].includes(
                j.status,
              ) && (
                <button onClick={() => void action("resume")}>
                  确认继续未完成部分
                </button>
              )
            )}
            <button
              onClick={() => {
                task.refresh();
                events.refresh();
              }}
            >
              <RefreshCw size={15} />
              刷新
            </button>
          </div>
          {j.kind === "selection_translate" &&
            ["failed", "interrupted", "cancelled"].includes(j.status) && (
              <p>临时划词任务不自动重发；请回到原选区确认重试。</p>
            )}
          <details>
            <summary>任务日志</summary>
            {(j.cloudTasks || []).map((t: any) => (
              <p key={t.part}>
                解析第 {t.part} 段 · 云任务编号：<code>{t.batchId}</code>
              </p>
            ))}
            <ol className="processing-events">
              {events.data.events.map((event: any) => (
                <li key={event.sequence}>
                  <time>{timestampText(event.createdAt)}</time>{" "}
                  {processingNames[event.kind] ||
                    (
                      {
                        progress: "进度已保存",
                        request_started: "请求开始",
                        request_finished: "请求状态已保存",
                        resume_requested: "已确认继续",
                      } as any
                    )[event.kind] ||
                    "任务事件"}
                  {event.data.stage && (
                    <> · {stages[event.data.stage] || "处理中"}</>
                  )}
                  {event.data.completed != null && (
                    <>
                      {" "}
                      · {event.data.completed}
                      {event.data.total ? ` / ${event.data.total}` : ""}
                    </>
                  )}
                  {event.data.status && (
                    <>
                      {" "}
                      ·{" "}
                      {(
                        {
                          completed: "请求完成",
                          rejected: "未受理",
                          unknown: "结果不确定",
                          failed: "失败",
                        } as any
                      )[event.data.status] || "状态已保存"}
                    </>
                  )}
                </li>
              ))}
            </ol>
            <Status
              error={events.error}
              loading={events.loading}
              retry={events.refresh}
            />
            <div className="button-row">
              {eventPages.length > 1 && (
                <button onClick={() => setEventPages((p) => p.slice(0, -1))}>
                  上一组日志
                </button>
              )}
              {events.data.events.length === 200 && (
                <button
                  onClick={() =>
                    setEventPages((p) => [
                      ...p,
                      events.data.events[199].sequence,
                    ])
                  }
                >
                  下一组日志
                </button>
              )}
            </div>
          </details>
        </>
      )}
    </div>
  );
}
