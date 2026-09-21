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
// Budget wording shared with the backend contract (dimension keys are stable).
const budgetLabels: Record<string, string> = {
  requests: "模型请求",
  inputTokens: "输入 token 预留",
  outputTokens: "输出 token 预留",
  seconds: "累计执行时间",
};

function formatHours(seconds: number): string {
  const hours = seconds / 3600;
  return Number.isInteger(hours) ? String(hours) : hours.toFixed(1);
}

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
    [scopeError, setScopeError] = useState(""),
    [budgetDraft, setBudgetDraft] = useState<any>(null),
    [budgetOpen, setBudgetOpen] = useState(false),
    [budgetNotice, setBudgetNotice] = useState("");
  const abort = useRef<AbortController | null>(null);
  // Defaults and ceilings come from the API for this job kind, so the page
  // never keeps its own copy of the numbers.
  const budgetLimits = scope?.limits || preview?.limits || null;
  // Only the kind-aware estimate response carries the envelope that will be
  // used; the generic preflight budget is not shown as if it were this task's.
  const effectiveBudget =
    budgetDraft || (scope?.budget ? { ...scope.budget } : null);
  function budgetValue(key: string): number | "" {
    const value = effectiveBudget?.[key];
    return typeof value === "number" ? value : "";
  }
  function changeBudget(key: string, raw: string) {
    const base = { ...(effectiveBudget || {}) };
    const parsed = raw.trim() === "" ? undefined : Number(raw);
    if (parsed === undefined || !Number.isFinite(parsed)) delete base[key];
    else base[key] = Math.floor(parsed);
    setBudgetDraft(base);
  }
  function budgetProblem(): string {
    if (!effectiveBudget) return "";
    for (const key of ["requests", "inputTokens", "outputTokens", "seconds"] as const) {
      const value = effectiveBudget[key];
      if (value === undefined) return `请输入${budgetLabels[key]}。`;
      if (!Number.isInteger(value) || value < 1)
        return `${budgetLabels[key]}必须是正整数。`;
      if (budgetLimits && value > budgetLimits[key])
        return `${budgetLabels[key]}超过本次部署上限 ${budgetLimits[key].toLocaleString()}。`;
    }
    return "";
  }
  function applyRequiredBudget() {
    if (!scope?.overage) return;
    const next = { ...(effectiveBudget || scope.budget) };
    for (const detail of Object.values<any>(scope.overage))
      next[detail.dimension] = detail.required;
    setBudgetDraft(next);
    setBudgetOpen(true);
  }
  // An explicit, user-chosen envelope that also covers the one allowed 429
  // retry for every base request. It is never applied automatically, and it
  // covers requests, input and output together (not just a doubled request count).
  function applyRetryAllowance() {
    const estimate = scope?.estimate;
    if (!estimate?.requestsWithRetryAllowance) {
      setBudgetNotice("当前范围还没有可用于重试余量的估算。");
      return;
    }
    const next = {
      ...(effectiveBudget || scope.budget),
      requests: estimate.requestsWithRetryAllowance,
      inputTokens: estimate.worstCaseInputTokens,
      outputTokens: estimate.worstCaseOutputTokens,
    };
    if (
      budgetLimits &&
      (next.requests > budgetLimits.requests ||
        next.inputTokens > budgetLimits.inputTokens ||
        next.outputTokens > budgetLimits.outputTokens)
    ) {
      setBudgetNotice(
        "含 429 重试余量的额度超过本次部署上限，请手动填写或让管理员调整上限。",
      );
      return;
    }
    setBudgetNotice("");
    setBudgetDraft(next);
    setBudgetOpen(true);
  }
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
      // Only an explicit user adjustment is sent: the kind's default comes from
      // the server, so switching job kinds can never carry a stale envelope.
      ...(budgetDraft && !budgetProblem()
        ? {
            budget: {
              requests: effectiveBudget.requests,
              inputTokens: effectiveBudget.inputTokens,
              outputTokens: effectiveBudget.outputTokens,
              seconds: effectiveBudget.seconds,
            },
          }
        : {}),
    };
  }
  useEffect(() => {
    setBudgetDraft(null);
    setBudgetOpen(false);
  }, [kind]);
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
    budgetDraft,
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
                  <div className="processing-budget">
                    <p className="muted">
                      {effectiveBudget ? (
                        <>
                          本次额度：{effectiveBudget.requests} 次模型请求、
                          {effectiveBudget.inputTokens.toLocaleString()} 输入预留、
                          {effectiveBudget.outputTokens.toLocaleString()} 输出预留、
                          {formatHours(effectiveBudget.seconds)} 小时
                          {budgetLimits
                            ? `（部署上限 ${budgetLimits.requests} 次 / ${formatHours(budgetLimits.seconds)} 小时）`
                            : ""}
                          。任务完成即停止，不会为了用满额度发请求；每次请求最长
                          120 秒，结果不确定时不会自动重发。
                        </>
                      ) : (
                        "正在读取本次可用额度…"
                      )}
                    </p>
                    {scope && (
                      <details
                        className="processing-budget-editor"
                        open={budgetOpen}
                        onToggle={(e) =>
                          setBudgetOpen((e.target as HTMLDetailsElement).open)
                        }
                      >
                        <summary>调整本次额度</summary>
                        <div className="form-grid">
                          <Field label="模型请求">
                            <input
                              type="number"
                              min={1}
                              value={budgetValue("requests")}
                              onChange={(e) =>
                                changeBudget("requests", e.target.value)
                              }
                            />
                          </Field>
                          <Field label="输入 token 预留">
                            <input
                              type="number"
                              min={1}
                              value={budgetValue("inputTokens")}
                              onChange={(e) =>
                                changeBudget("inputTokens", e.target.value)
                              }
                            />
                          </Field>
                          <Field label="输出 token 预留">
                            <input
                              type="number"
                              min={1}
                              value={budgetValue("outputTokens")}
                              onChange={(e) =>
                                changeBudget("outputTokens", e.target.value)
                              }
                            />
                          </Field>
                          <Field label="累计小时数">
                            <input
                              type="number"
                              min={1}
                              value={
                                typeof effectiveBudget?.seconds === "number"
                                  ? formatHours(effectiveBudget.seconds)
                                  : ""
                              }
                              onChange={(e) =>
                                changeBudget(
                                  "seconds",
                                  e.target.value === ""
                                    ? ""
                                    : String(
                                        Math.floor(Number(e.target.value) * 3600),
                                      ),
                                )
                              }
                            />
                          </Field>
                        </div>
                        <p className="muted">
                          额度是整个任务的累计总额；续跑时不会清零已用部分，也不能低于已用量。
                        </p>
                        {scope?.estimate?.requestsWithRetryAllowance && (
                          <button type="button" onClick={applyRetryAllowance}>
                            含 429 重试余量（请求 {scope.estimate.requestsWithRetryAllowance}、输入{" "}
                            {scope.estimate.worstCaseInputTokens.toLocaleString()}、输出{" "}
                            {scope.estimate.worstCaseOutputTokens.toLocaleString()}）
                          </button>
                        )}
                        {budgetNotice && (
                          <p className="notice error">{budgetNotice}</p>
                        )}
                      </details>
                    )}
                  </div>
                  <p className="muted">
                    {estimating
                      ? "正在核对处理范围与缓存…"
                      : scope?.estimate
                        ? `所选 ${scope.selectedPages} 页共 ${scope.estimate.selectedBlocks} 块，已有 ${scope.estimate.cachedBlocks} 块可复用；基础请求 ${scope.estimate.requests} 次，若每次都用一次允许的 429 重试则最多 ${scope.estimate.requestsWithRetryAllowance} 次；预留 ${scope.estimate.inputTokens.toLocaleString()} 输入、${scope.estimate.outputTokens.toLocaleString()} 输出 token（保守预留，不是计费 token）。`
                        : kind === "parse"
                          ? "仅解析全文，不调用翻译模型。"
                          : preview.estimateNote}
                    {scope?.parseRequired &&
                      kind !== "parse" &&
                      " 解析会处理全文，翻译仅处理所选范围。"}
                    已有版式译文不会被覆盖。
                  </p>
                  {budgetProblem() && (
                    <p className="notice error">{budgetProblem()}</p>
                  )}
                  {scope?.exceedsBudget && (
                    <div className="notice error">
                      <span>
                        所选范围超出本次额度：
                        {Object.values<any>(scope.overage || {})
                          .map(
                            (detail) =>
                              `${detail.dimensionLabel}需要 ${detail.required.toLocaleString()}（当前额度 ${detail.limit.toLocaleString()}）`,
                          )
                          .join("，")}
                        。
                      </span>
                      <button onClick={applyRequiredBudget}>
                        使用所需额度
                      </button>
                    </div>
                  )}
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
                    !!budgetProblem() ||
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
      <Status
        error={error || resource.error}
        loading={resource.loading && !resource.loaded}
      />
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
// Top-up continuation for a stopped whole-paper translation: the plan is read
// first (no state change, no model call), then the user confirms the new
// whole-task total which must cover what the task already used.
function ResumeBudget({ job, onDone }: { job: any; onDone: () => void }) {
  const [plan, setPlan] = useState<any>(null);
  const [draft, setDraft] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState(false);
  const limits = plan?.limits || null;
  const current = draft || plan?.budget || null;
  const value = (key: string) =>
    typeof current?.[key] === "number" ? current[key] : "";
  const required = (key: string) => plan?.required?.[key] ?? 0;
  function change(key: string, raw: string) {
    const base = { ...(current || {}) };
    const parsed = raw.trim() === "" ? undefined : Number(raw);
    if (parsed === undefined || !Number.isFinite(parsed)) delete base[key];
    else base[key] = Math.floor(parsed);
    setDraft(base);
  }
  function problem(): string {
    if (!current) return "";
    for (const key of ["requests", "inputTokens", "outputTokens", "seconds"] as const) {
      const number = current[key];
      if (number === undefined || !Number.isInteger(number) || number < 1)
        return `${budgetLabels[key]}必须是正整数。`;
      if (limits && number > limits[key])
        return `${budgetLabels[key]}超过本次部署上限 ${limits[key].toLocaleString()}。`;
      if (number < required(key))
        return `${budgetLabels[key]}不能低于已用与仍需的合计 ${required(key).toLocaleString()}。`;
    }
    return "";
  }
  async function load() {
    setBusy(true);
    setError("");
    try {
      const value = await api(
        `/api/processing/jobs/${job.id}/resume-plan`,
        "POST",
        {},
      );
      setPlan(value.plan);
      setDraft({ ...value.plan.budget });
      setOpen(true);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  async function resume() {
    setBusy(true);
    setError("");
    try {
      await api(`/api/processing/jobs/${job.id}/resume`, "POST", {
        budget: {
          requests: current.requests,
          inputTokens: current.inputTokens,
          outputTokens: current.outputTokens,
          seconds: current.seconds,
        },
      });
      setPlan(null);
      setDraft(null);
      setOpen(false);
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  // Any dimension may be the one that needs raising — including the累计 hours.
  const needsMore =
    plan &&
    !problem() &&
    ["requests", "inputTokens", "outputTokens", "seconds"].some(
      (key) => current[key] > plan.budget[key],
    );
  return (
    <div className="processing-resume-budget">
      <Status error={error} loading={busy} />
      {!open && (
        <button onClick={() => void load()}>调整额度并继续未完成部分</button>
      )}
      {open && plan && (
        <>
          <p className="muted">
            已预留 {plan.usage.requests} 次请求；未完成范围仍需{" "}
            {plan.estimate ? `${plan.estimate.requests} 次基础请求` : "解析后才能估算"}
            。{plan.secondsBasis}
          </p>
          {plan.parseRequired && (
            <p className="notice">
              尚未解析：解析完成后会显示新的范围与所需额度，解析结果会保留。
            </p>
          )}
          {plan.time?.exhausted && (
            <p className="notice">
              累计执行时间已用完（{Math.round(plan.time.usedSeconds / 60)} 分钟 /
              上限 {Math.round(plan.time.limitSeconds / 60)} 分钟），剩余时长无法预先估算；
              请至少增加到 {Math.ceil((plan.time.usedSeconds + 1) / 3600)} 小时以上再继续。
            </p>
          )}
          <div className="form-grid">
            <Field label="模型请求（整个任务累计）">
              <input
                type="number"
                min={1}
                value={value("requests")}
                onChange={(e) => change("requests", e.target.value)}
              />
            </Field>
            <Field label="输入 token 预留（累计）">
              <input
                type="number"
                min={1}
                value={value("inputTokens")}
                onChange={(e) => change("inputTokens", e.target.value)}
              />
            </Field>
            <Field label="输出 token 预留（累计）">
              <input
                type="number"
                min={1}
                value={value("outputTokens")}
                onChange={(e) => change("outputTokens", e.target.value)}
              />
            </Field>
            <Field label="累计小时数">
              <input
                type="number"
                min={1}
                value={
                  typeof current?.seconds === "number"
                    ? formatHours(current.seconds)
                    : ""
                }
                onChange={(e) =>
                  change(
                    "seconds",
                    e.target.value === ""
                      ? ""
                      : String(Math.floor(Number(e.target.value) * 3600)),
                  )
                }
              />
            </Field>
          </div>
          <p className="muted">
            新额度是整个任务的累计总额：已用部分不清零，已完成的块与子单元继续复用。
          </p>
          {plan.estimate?.requestsWithRetryAllowance && (
            <button
              type="button"
              onClick={() => {
                const next = {
                  ...current,
                  requests: Math.max(
                    required("requests"),
                    plan.estimate.requestsWithRetryAllowance,
                  ),
                  inputTokens: Math.max(
                    required("inputTokens"),
                    plan.estimate.worstCaseInputTokens,
                  ),
                  outputTokens: Math.max(
                    required("outputTokens"),
                    plan.estimate.worstCaseOutputTokens,
                  ),
                };
                if (
                  limits &&
                  (next.requests > limits.requests ||
                    next.inputTokens > limits.inputTokens ||
                    next.outputTokens > limits.outputTokens)
                ) {
                  setError(
                    "含 429 重试余量的额度超过本次部署上限，请手动填写。",
                  );
                  return;
                }
                setError("");
                setDraft(next);
              }}
            >
              含 429 重试余量
            </button>
          )}
          {problem() && <p className="notice error">{problem()}</p>}
          <div className="button-row">
            <button
              disabled={busy || !!problem() || !needsMore}
              onClick={() => void resume()}
            >
              保存额度并继续
            </button>
            <button
              onClick={() => {
                setOpen(false);
                setPlan(null);
                setDraft(null);
              }}
            >
              取消
            </button>
          </div>
        </>
      )}
    </div>
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
    // A running job needs a close watch; a page left in the background does not.
    const t = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (task.loading) return;
      task.refresh();
      events.refresh();
    }, 3000);
    return () => clearInterval(t);
  }, [id, task.loading]);
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
  const translationKind = ["translate", "parse_translate", "retranslate"].includes(
    j?.kind,
  );
  const stoppable = ["failed", "partial", "interrupted", "cancelled"].includes(
    j?.status || "",
  );
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
            已预留 {j.usage.requests}/{j.budget.requests} 次请求、
            {j.usage.inputTokens.toLocaleString()}/{j.budget.inputTokens.toLocaleString()}{" "}
            输入、{j.usage.outputTokens.toLocaleString()}/
            {j.budget.outputTokens.toLocaleString()} 输出预留、已用{" "}
            {formatHours(j.usage.seconds)}/{formatHours(j.budget.seconds)} 小时。
            预留是按字节与生成上限的保守预扣，不是计费用量。
          </p>
          {j.actualUsage && (
            <p className="muted">
              供应商实际用量：
              {j.actualUsage.inputTokens.toLocaleString()} 输入
              {j.actualUsage.inputMissingAttempts
                ? `（${j.actualUsage.inputMissingAttempts} 次未返回输入用量，未按 0 记）`
                : ""}
              、{j.actualUsage.outputTokens.toLocaleString()} 输出
              {j.actualUsage.outputMissingAttempts
                ? `（${j.actualUsage.outputMissingAttempts} 次未返回输出用量，未按 0 记）`
                : ""}
              {j.actualUsage.complete
                ? "；输入与输出都有供应商数据。"
                : j.actualUsage.requests === 0
                  ? "；尚无模型请求。"
                  : "；统计不完整，缺失项不代表未计费。"}
            </p>
          )}
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
              stoppable && (
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
          {translationKind && stoppable && (
            <ResumeBudget
              job={j}
              onDone={() => {
                task.refresh();
                events.refresh();
              }}
            />
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
