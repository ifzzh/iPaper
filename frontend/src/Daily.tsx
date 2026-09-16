import { useEffect, useState } from "react";
import {
  Rss,
  RefreshCw,
  BookOpen,
  Plus,
  Search,
  Calendar,
  ImageOff,
} from "lucide-react";
import {
  api,
  useResource,
  Status,
  Confirm,
  dateText,
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
    [busy, setBusy] = useState(""),
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
  const visible = (papers.data.papers || []).filter((p: any) =>
    (p.title + " " + p.abstract).toLowerCase().includes(query.toLowerCase()),
  );
  useEffect(() => {
    const c = new AbortController();
    const ids = (papers.data.papers || []).map((p: any) => p.arxiv_id);
    if (ids.length)
      api("/api/daily-arxiv/read-status", "POST", { arxiv_ids: ids }, c.signal)
        .then((r) => {
          if (!c.signal.aborted) setReadIds(r.read_ids || []);
        })
        .catch(() => {});
    return () => c.abort();
  }, [papers.data]);
  useEffect(() => {
    const t = setInterval(() => scheduler.refresh(), 15000);
    return () => clearInterval(t);
  }, []);
  async function add(p: any, read: boolean) {
    setBusy(p.arxiv_id);
    setError("");
    try {
      if (read) {
        await api("/api/daily-arxiv/read/mark", "POST", {
          arxiv_id: p.arxiv_id,
        });
        setReadIds((v) => [...v, p.arxiv_id]);
      }
      if (p.paper_id && read) {
        onRead(p.paper_id);
        return;
      }
      const r = await api("/api/daily-arxiv/add-to-library", "POST", {
        arxiv_id: p.arxiv_id,
        date,
        fetch_category: p.fetch_category || p.category || category,
        use_temp_dir: true,
        topicIds: [],
      });
      onChanged();
      papers.refresh();
      if (read && (r.paper?.id || r.paper_id))
        onRead(r.paper?.id || r.paper_id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy("");
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
      <div className="daily-run-status">
        <span>
          {scheduler.data.is_running ? "自动更新已启动" : "自动更新未启动"}
        </span>
        {scheduler.data.last_fetch_time && (
          <span>最近更新：{dateText(scheduler.data.last_fetch_time)}</span>
        )}
        {scheduler.data.llm_api_error && (
          <span role="alert">模型服务连接异常，请检查设置。</span>
        )}
        {!scheduler.data.is_running && (
          <button onClick={() => setStartScheduler(true)}>启动自动更新</button>
        )}
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
        loading={papers.loading}
      />
      <div className="daily-grid">
        {visible.map((p: any) => (
          <article className="daily-card" key={p.arxiv_id}>
            <DailyImage
              date={date}
              category={p.fetch_category || p.category || category}
              id={p.arxiv_id}
            />
            <div className="daily-card-body">
              <div className="badges">
                {readIds.includes(p.arxiv_id) && (
                  <span className="badge">已读</span>
                )}
                <span className="badge">
                  {p.category || p.categories?.[0] || "arXiv"}
                </span>
                <span className="badge">
                  {p.artifact_status === "ready" ? "PDF 已就绪" : "文献元数据"}
                </span>
              </div>
              <button className="card-title" onClick={() => setSelected(p)}>
                {p.title}
              </button>
              <p className="muted">
                {Array.isArray(p.authors) ? p.authors.join(", ") : p.authors}
              </p>
              <p className="daily-summary">
                {p.summary || p.abstract || "暂无摘要"}
              </p>
              <div className="action-row">
                <button
                  className="primary"
                  disabled={busy === p.arxiv_id}
                  onClick={() => add(p, true)}
                >
                  <BookOpen size={15} />
                  {p.paper_id ? "打开阅读" : "加入并阅读"}
                </button>
                <button
                  aria-label="加入 Reading List"
                  disabled={busy === p.arxiv_id}
                  onClick={() => add(p, false)}
                >
                  <Plus size={16} />
                </button>
                {p.artifact_status === "failed" && (
                  <button
                    onClick={async () => {
                      try {
                        await api(
                          "/api/daily-arxiv/papers/" +
                            encodeURIComponent(p.arxiv_id) +
                            "/retry",
                          "POST",
                          {},
                        );
                        papers.refresh();
                      } catch (e) {
                        setError(errorText(e));
                      }
                    }}
                  >
                    重试 PDF
                  </button>
                )}
              </div>
            </div>
          </article>
        ))}
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
function DailyImage({
  date,
  category,
  id,
}: {
  date: string;
  category: string;
  id: string;
}) {
  const [failed, setFailed] = useState(false);
  return failed ? (
    <div className="daily-cover-placeholder">
      <ImageOff size={24} />
      <strong>{category || "arXiv"}</strong>
      <small>缩略图暂不可用</small>
    </div>
  ) : (
    <img
      className="daily-cover"
      alt="论文首页预览"
      loading="lazy"
      src={`/api/daily-arxiv/thumbnail/${encodeURIComponent(date)}/${encodeURIComponent(category || "all")}/${encodeURIComponent(id)}`}
      onError={() => setFailed(true)}
    />
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
