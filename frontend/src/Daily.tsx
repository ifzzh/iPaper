import { useEffect, useState } from "react";
import {
  Rss,
  RefreshCw,
  BookOpen,
  Plus,
  Search,
  Calendar,
  ImageOff,
  Clock,
} from "lucide-react";
import {
  api,
  useResource,
  Status,
  Confirm,
  dateText,
  timestampText,
  hasInstant,
  APP_TIME_ZONE_LABEL,
  Modal,
  Markdown,
} from "./ui";
import { errorText } from "./api";
export function Daily({
  onRead,
  onSettings,
  onChanged,
}: {
  onRead: (id: string) => void;
  onSettings: () => void;
  onChanged: () => void;
}) {
const dates = useResource<any>("/api/daily-arxiv/dates", { dates: [] }),
    [date, setDate] = useState(""),
    [category, setCategory] = useState(""),
    [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [fetching, setFetching] = useState(false),
    [startScheduler, setStartScheduler] = useState(false),
    [selected, setSelected] = useState<any>(null),
    [pending, setPending] = useState<Record<string, string>>({}),
    [cardErrors, setCardErrors] = useState<Record<string, string>>({}),
    [operation, setOperation] = useState(""),
    [readIds, setReadIds] = useState<string[]>([]);
  useEffect(() => {
    if (!date) setDate(dates.data.dates?.[0] || dates.data.today || "");
  }, [dates.data]);
  const papers = useResource<any>(
      date
        ? "/api/daily-arxiv/papers/" +
            encodeURIComponent(date) +
            (category ? "?category=" + encodeURIComponent(category) : "")
        : null,
      { papers: [] },
    ),
    settings = useResource<any>("/api/settings/daily-arxiv", {}),
    scheduler = useResource<any>("/api/daily-arxiv/scheduler/status", {});
  const dailyErrors = Object.values(
    (scheduler.data.last_errors || {}) as Record<string, string>,
  );
  const [coverVersions, setCoverVersions] = useState<Record<string, number>>({});
  const list: any[] = papers.data.papers || [];
  const visible = list.filter((p: any) =>
    (p.title + " " + p.abstract).toLowerCase().includes(query.toLowerCase()),
  );
  // Keep refreshing while this view still has papers whose PDF or preview is
  // being produced, so cards recover on their own. Stops as soon as every
  // visible paper reached a terminal state, and never runs while hidden or
  // while a request is already in flight.
  const activeAssets = list.filter((p: any) => isAssetActive(p)).length;
  useEffect(() => {
    if (!activeAssets) return;
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (papers.loading) return;
      papers.refresh();
    }, 6000);
    return () => clearInterval(timer);
  }, [activeAssets, papers.loading]);
  useEffect(() => {
    const c = new AbortController();
    const ids = list.map((p: any) => p.arxiv_id);
    if (ids.length)
      api("/api/daily-arxiv/read-status", "POST", { arxiv_ids: ids }, c.signal)
        .then((r) => {
          if (!c.signal.aborted) setReadIds(r.read_ids || []);
        })
        .catch(() => {});
    return () => c.abort();
  }, [papers.data]);
  useEffect(() => {
    const t = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (scheduler.loading) return;
      scheduler.refresh();
    }, 15000);
    return () => clearInterval(t);
  }, [scheduler.loading]);
  function markPending(arxivId: string, action: string) {
    setPending((v) => ({ ...v, [arxivId]: action }));
  }
  function clearPending(arxivId: string) {
    setPending((v) => {
      const next = { ...v };
      delete next[arxivId];
      return next;
    });
  }
  function setCardError(arxivId: string, message: string) {
    setCardErrors((v) => ({ ...v, [arxivId]: message }));
  }
  function clearCardError(arxivId: string) {
    setCardErrors((v) => {
      const next = { ...v };
      delete next[arxivId];
      return next;
    });
  }
  async function add(p: any, read: boolean) {
    if (pending[p.arxiv_id]) return; // one action per paper, duplicates ignored
    markPending(p.arxiv_id, read ? "read" : "add");
    setError("");
    clearCardError(p.arxiv_id);
    try {
      let paperId = p.paper_id as string | undefined;
      let pdfReady = p.pdf_status === "ready" || p.artifact_status === "ready";
      if (!paperId) {
        const r = await api("/api/daily-arxiv/add-to-library", "POST", {
          arxiv_id: p.arxiv_id,
          date,
          fetch_category: p.fetch_category || p.category || category,
          use_temp_dir: true,
          topicIds: [],
        });
        paperId = r.paper?.id || r.paper_id;
        pdfReady = !!paperId; // the library copy is the readable asset
        onChanged();
      }
      papers.refresh();
      if (!read) return;
      if (p.pdf_status && !pdfReady) {
        // Never claim a paper was opened when there is no readable PDF yet.
        setCardError(
          p.arxiv_id,
          "PDF 仍在获取，完成后可打开；也可先加入 Reading List。",
        );
        return;
      }
      if (!paperId) {
        setCardError(p.arxiv_id, "已加入，但未取得可打开的文献记录，请重试。");
        return;
      }
      // Only after the paper is genuinely available (and about to open) is it
      // recorded as read.
      await api("/api/daily-arxiv/read/mark", "POST", { arxiv_id: p.arxiv_id });
      setReadIds((v) => (v.includes(p.arxiv_id) ? v : [...v, p.arxiv_id]));
      onRead(paperId);
    } catch (e) {
      const message = errorText(e);
      setCardError(p.arxiv_id, message);
      setError(message);
    } finally {
      clearPending(p.arxiv_id);
    }
  }
  async function retryAsset(p: any, stage: "pdf" | "thumbnail") {
    markPending(p.arxiv_id, "retry");
    clearCardError(p.arxiv_id);
    try {
      await api(
        "/api/daily-arxiv/papers/" + encodeURIComponent(p.arxiv_id) + "/retry",
        "POST",
        { stage },
      );
      // A retried cover must not reuse the browser's cached miss.
      setCoverVersions((v) => ({
        ...v,
        [p.arxiv_id]: (v[p.arxiv_id] || 0) + 1,
      }));
      papers.refresh();
      scheduler.refresh();
    } catch (e) {
      const message = errorText(e);
      setCardError(p.arxiv_id, message);
      setError(message);
    } finally {
      clearPending(p.arxiv_id);
    }
  }
  return (
    <section className="daily-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">DISCOVER & READ</span>
          <h1>Daily arXiv</h1>
          <p>发现与你研究相关的新论文，继续你的阅读线索。</p>
        </div>
        <button
          onClick={() => {
            dates.refresh();
            papers.refresh();
            scheduler.refresh();
          }}
        >
          <RefreshCw size={16} />
          刷新列表
        </button>
        <button className="primary" onClick={() => setFetching(true)}>
          <Rss size={16} />
          获取论文
        </button>
        <button onClick={onSettings}>发现设置</button>
      </header>
      {dates.data.enabled === false && (
        <div className="notice">
          Daily arXiv 尚未启用。<button onClick={onSettings}>前往设置</button>
        </div>
      )}
      <div className="daily-run-status daily-scheduler-status">
        <span>{dailyStateLabel(scheduler.data)}</span>
        <span className="daily-times">
          <span>
            最近检查：
            {hasInstant(scheduler.data.last_check_at)
              ? timestampText(scheduler.data.last_check_at)
              : "本次启动后尚未检查"}
          </span>
          <span>
            最近成功更新：
            {hasInstant(scheduler.data.last_success_at)
              ? timestampText(scheduler.data.last_success_at)
              : "暂无（未获取到新论文不是故障）"}
          </span>
          {scheduler.data.is_running && hasInstant(scheduler.data.next_check_at) && (
            <span>下次检查：{timestampText(scheduler.data.next_check_at)}</span>
          )}
          <span className="daily-time-zone">{APP_TIME_ZONE_LABEL}</span>
        </span>
        {dailyErrors.length > 0 && (
          <span role="alert">最近检查失败：{dailyErrors[0]}</span>
        )}
        {scheduler.data.llm_api_error && (
          <span role="alert">模型服务连接异常，请检查设置。</span>
        )}
        {!scheduler.data.is_running && (
          <button onClick={() => setStartScheduler(true)}>启动自动更新</button>
        )}
        {scheduler.data.is_running &&
          (scheduler.data.paused ? (
            <button
              onClick={async () => {
                await api("/api/daily-arxiv/scheduler/resume", "POST", {});
                scheduler.refresh();
              }}
            >
              恢复获取
            </button>
          ) : (
            <button
              onClick={async () => {
                await api("/api/daily-arxiv/scheduler/pause", "POST", {
                  reason: "manual",
                });
                scheduler.refresh();
              }}
            >
              暂停获取
            </button>
          ))}
      </div>
      <DailyProgress categories={settings.data.categories || []} />
      {startScheduler && (
        <Confirm
          title="启动 Daily 自动更新"
          detail="将按发现设置定时获取论文，可能调用已配置的筛选模型。"
          onClose={() => setStartScheduler(false)}
          onConfirm={async () => {
            await api("/api/daily-arxiv/scheduler/start", "POST", {});
            scheduler.refresh();
          }}
        />
      )}
      <div className="daily-filters">
        <label>
          <Calendar size={17} />
          <select
            aria-label="论文日期"
            value={date}
            onChange={(e) => setDate(e.target.value)}
          >
            {[...new Set<string>([date, ...(dates.data.dates || [])])]
              .filter(Boolean)
              .map((d) => (
                <option key={d} value={d}>
                  {dateText(d)}
                </option>
              ))}
          </select>
        </label>
        <select
          aria-label="arXiv 分类"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">全部分类</option>
          {(settings.data.categories || []).map((c: string) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <label className="search">
          <Search size={17} />
          <input
            aria-label="搜索发现论文"
            placeholder="搜索当天论文…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <span>{visible.length} 篇</span>
      </div>
      <Status
        error={error || dates.error || papers.error}
        loading={papers.loading && !papers.loaded}
      />
<div className="daily-grid">
        {visible.map((p: any) => {
          const action = primaryAction(p);
          const busy = pending[p.arxiv_id];
          const pdfStatus = p.pdf_status || p.artifact_status;
          const coverStatus = p.cover_status;
          const coverBadge =
            coverStatus && coverStatus !== "ready" && coverStatus !== "pending"
              ? coverLabels[coverStatus] || ""
              : "";
          const retryAt = p.asset_next_retry_at;
          return (
            <article className="daily-card" key={p.arxiv_id}>
              <DailyImage
                date={date}
                category={p.fetch_category || p.category || category}
                id={p.arxiv_id}
                status={pdfStatus}
                coverStatus={coverStatus}
                thumbnailReady={!!p.thumbnail_ready}
                coverVersion={coverVersions[p.arxiv_id] || 0}
              />
              <div className="daily-card-body">
                <div className="badges">
                  {readIds.includes(p.arxiv_id) && (
                    <span className="badge">已读</span>
                  )}
                  <span className="badge">
                    {p.category || p.categories?.[0] || "arXiv"}
                  </span>
                  <span
                    className={
                      "badge" + (pdfStatus === "failed" ? " danger" : "")
                    }
                  >
                    {artifactLabels[pdfStatus] || "仅元数据"}
                  </span>
                  {coverBadge && (
                    <span
                      className={
                        "badge" + (coverStatus === "failed" ? " danger" : "")
                      }
                    >
                      {coverBadge}
                    </span>
                  )}
                </div>
                <button
                  className="card-title"
                  title={p.title}
                  onClick={() => setSelected(p)}
                >
                  {p.title}
                </button>
                <p className="daily-authors">
                  {Array.isArray(p.authors) ? p.authors.join(", ") : p.authors}
                </p>
                <p className="daily-summary">
                  {p.summary || p.abstract || "暂无摘要"}
                </p>
                {pdfStatus === "retry_wait" && retryAt && (
                  <p className="daily-wait">
                    {hasInstant(retryAt)
                      ? `等待重试：${timestampText(retryAt)}`
                      : "等待下一次自动重试"}
                  </p>
                )}
                {cardErrors[p.arxiv_id] && (
                  <p className="daily-card-error" role="alert">
                    {cardErrors[p.arxiv_id]}
                    <button onClick={() => clearCardError(p.arxiv_id)}>
                      知道了
                    </button>
                  </p>
                )}
                <div className="action-row">
                  {action.kind === "retry-pdf" ? (
                    <button
                      className="primary"
                      disabled={!!busy}
                      onClick={() => retryAsset(p, "pdf")}
                    >
                      <RefreshCw size={15} />
                      {busy === "retry" ? "正在重试…" : action.label}
                    </button>
                  ) : action.kind === "wait" ? (
                    <button disabled title="PDF 尚未就绪">
                      <Clock size={15} />
                      {artifactLabels[pdfStatus] || "PDF 待获取"}
                    </button>
                  ) : (
                    <button
                      className="primary"
                      disabled={!!busy}
                      onClick={() => add(p, true)}
                    >
                      <BookOpen size={15} />
                      {busy === "read" ? "正在准备…" : action.label}
                    </button>
                  )}
                  {!p.paper_id && (
                    <button
                      aria-label="加入 Reading List"
                      disabled={!!busy}
                      onClick={() => add(p, false)}
                    >
                      <Plus size={16} />
                    </button>
                  )}
                  {pdfStatus === "ready" &&
                    (coverStatus === "failed" || coverStatus === "missing") && (
                    <button
                      disabled={!!busy}
                      onClick={() => retryAsset(p, "thumbnail")}
                    >
                      <ImageOff size={15} />
                      {busy === "retry" ? "正在重试…" : "重新生成封面"}
                    </button>
                  )}
                </div>
              </div>
            </article>
          );
        })}
      </div>
      {!papers.loading && !visible.length && (
        <div className="empty-state">
          <Rss size={38} />
          <h3>这一天还没有论文</h3>
          <p>更换日期或检查发现设置。</p>
        </div>
      )}
      {fetching && (
        <Confirm
          title="获取 Daily arXiv 论文"
          detail="将使用已配置的筛选模型及下载服务，可能产生调用费用。不会因刷新列表而自动执行。"
          onClose={() => setFetching(false)}
          onConfirm={async () => {
            await api("/api/daily-arxiv/fetch-all", "POST", {});
            dates.refresh();
            papers.refresh();
            scheduler.refresh();
          }}
        />
      )}
      {selected && (
        <Modal title="论文详情" onClose={() => setSelected(null)} wide>
          <h2>{selected.title}</h2>
          <p>
            {Array.isArray(selected.authors)
              ? selected.authors.join(", ")
              : selected.authors}
          </p>
          <Markdown
            text={selected.summary || selected.abstract || "暂无摘要"}
          />
          <div className="action-row">
            <button onClick={() => setOperation("generate-summary")}>
              生成简要总结
            </button>
            <button onClick={() => setOperation("extract-affiliations")}>
              识别作者机构
            </button>
          </div>
          <button className="primary" onClick={() => add(selected, true)}>
            加入并阅读
          </button>
        </Modal>
      )}
      {operation && selected && (
        <Confirm
          title={
            operation === "generate-summary" ? "生成简要总结" : "识别作者机构"
          }
          detail="此操作会调用已配置的模型，可能产生费用。失败不会自动重试。"
          onClose={() => setOperation("")}
          onConfirm={async () => {
            const r = await api("/api/daily-arxiv/" + operation, "POST", {
              arxiv_id: selected.arxiv_id,
              date,
              fetch_category:
                selected.fetch_category || selected.category || category,
            });
            setSelected((p: any) => ({
              ...p,
              ...(r.paper || {}),
              ...(r.summary ? { summary: r.summary } : {}),
            }));
            papers.refresh();
          }}
        />
      )}
    </section>
  );
}
// One honest label for the discovery scheduler. The API reports the state
// (stopped/disabled/paused/idle/round_active/paused_waiting); the page never
// guesses it from log lines or a momentary gap between categories.
function dailyStateLabel(state: any) {
  switch (state?.state) {
    case "round_active":
      return "正在获取论文（本轮进行中）";
    case "paused_waiting":
      return "已暂停新增，等待当前论文收尾";
    case "paused":
      return "获取已暂停（维护中）";
    case "disabled":
      return "自动更新已关闭";
    case "idle":
      return "自动更新已启动，等待下次检查";
    case "stopped":
      return "自动更新未启动";
    default:
      return state?.is_running ? "自动更新已启动" : "自动更新未启动";
  }
}

// Status text shared by the card badge, the cover placeholder and the actions.
// Keys mirror the pdf_status / cover_status values the API reports.
const artifactLabels: Record<string, string> = {
  ready: "PDF 已就绪",
  candidate: "PDF 待获取",
  queued: "PDF 排队中",
  downloading: "PDF 获取中",
  validating: "PDF 校验中",
  retry_wait: "PDF 等待重试",
  missing: "文件缺失，正在重新获取",
  failed: "PDF 获取失败",
};

const coverLabels: Record<string, string> = {
  ready: "封面已就绪",
  pending: "封面待生成",
  generating: "封面生成中",
  failed: "封面生成失败",
  missing: "封面待生成",
  unavailable: "封面不可用",
};

const PENDING_STATUSES = new Set([
  "candidate",
  "queued",
  "downloading",
  "validating",
  "retry_wait",
]);

function isAssetActive(p: any) {
  const pdf = String(p.pdf_status || p.artifact_status || "");
  const cover = String(p.cover_status || "");
  if (PENDING_STATUSES.has(pdf)) return true;
  if (cover === "pending" || cover === "generating") return true;
  // A ready PDF without a preview still has background work worth watching.
  if (pdf === "ready" && !p.thumbnail_ready && cover !== "failed" && cover !== "unavailable")
    return true;
  return false;
}

// What the primary action should offer for this paper, and what it must not
// promise. "read" opens an already readable paper; "prepare" fetches it first.
function primaryAction(p: any) {
  const pdfReady = p.pdf_status === "ready" || p.artifact_status === "ready";
  if (p.paper_id && pdfReady) return { kind: "read" as const, label: "打开阅读" };
  if (pdfReady) return { kind: "read" as const, label: "加入并阅读" };
  if (String(p.artifact_status) === "failed") {
    return { kind: "retry-pdf" as const, label: "重试获取 PDF" };
  }
  return { kind: "wait" as const, label: "加入阅读列表" };
}
function DailyImage({
  date,
  category,
  id,
  status,
  coverStatus,
  thumbnailReady,
  coverVersion,
}: {
  date: string;
  category: string;
  id: string;
  status?: string;
  coverStatus?: string;
  thumbnailReady?: boolean;
  coverVersion?: number;
}) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);
  // The cache key is driven by the asset revision (not by render time): a
  // preview that appears later is fetched once, and a retried cover is not
  // served from the browser's earlier miss.
  const revision = `${coverVersion || 0}-${thumbnailReady ? 1 : 0}-${status || ""}-${coverStatus || ""}`;
  const src = `/api/daily-arxiv/thumbnail/${encodeURIComponent(date)}/${encodeURIComponent(category || "all")}/${encodeURIComponent(id)}?v=${revision}-${attempt}`;
  const waiting =
    PENDING_STATUSES.has(status || "") ||
    coverStatus === "pending" ||
    coverStatus === "generating";

  // Reset when the paper, date or asset revision changes: a preview that becomes
  // available later must be retried, not remembered as failed.
  useEffect(() => {
    setFailed(false);
    setAttempt(0);
  }, [id, date, category, revision]);

  // Bounded automatic retry, and only while the asset really is in progress.
  useEffect(() => {
    if (!failed || !waiting) return;
    if (attempt >= 2) return;
    const timer = setTimeout(() => {
      setFailed(false);
      setAttempt((v) => v + 1);
    }, 4000 + attempt * 8000);
    return () => clearTimeout(timer);
  }, [failed, attempt, waiting]);

  if (failed || coverStatus === "failed" || coverStatus === "unavailable") {
    const settled = coverStatus === "failed" || coverStatus === "unavailable";
    return (
      <div className={"daily-cover-placeholder" + (settled ? " settled" : "")}>
        <ImageOff size={22} />
        <strong>{category || "arXiv"}</strong>
        <small>
          {settled
            ? "预览暂不可用，可用卡片上的“重新生成封面”"
            : waiting
              ? "预览生成中…"
              : coverStatus === "missing"
                ? "预览待生成，可用卡片上的“重新生成封面”"
                : "暂无预览"}
        </small>
      </div>
    );
  }
  return (
    <div className="daily-cover-frame">
      <img
        className="daily-cover"
        alt="论文首页预览"
        loading="lazy"
        src={src}
        onError={() => setFailed(true)}
      />
      {waiting && !thumbnailReady && (
        <span className="daily-cover-hint">预览生成中…</span>
      )}
    </div>
  );
}

function DailyProgress({ categories }: { categories: string[] }) {
  const [items, setItems] = useState<Record<string, any>>({});
  const key = categories.join(",");
  useEffect(() => {
    const c = new AbortController();
    let running = false;
    async function load() {
      if (running) return;
      running = true;
      try {
        const values = await Promise.all(
          categories
            .slice(0, 30)
            .map(async (category) => [
              category,
              (
                await api(
                  "/api/daily-arxiv/progress/" + encodeURIComponent(category),
                  "GET",
                  undefined,
                  c.signal,
                )
              ).progress,
            ]),
        );
        if (!c.signal.aborted) setItems(Object.fromEntries(values));
      } catch {
      } finally {
        running = false;
      }
    }
    void load();
    const t = setInterval(load, 10000);
    return () => {
      c.abort();
      clearInterval(t);
    };
  }, [key]);
  const active = Object.entries(items).filter(
    ([, p]) => p && p.status !== "idle",
  );
  if (!active.length) return null;
  const names: Record<string, string> = {
    fetching: "正在获取",
    processing: "正在处理",
    done: "完成",
    error: "失败",
  };
  return (
    <div className="daily-run-status" aria-label="获取进度">
      {active.map(([category, p]) => (
        <span key={category}>
          {category} · {names[p.status] || "处理中"} · {Number(p.current) || 0}/
          {Number(p.total) || 0}
          {p.current_paper ? " · " + p.current_paper : ""}
        </span>
      ))}
    </div>
  );
}
