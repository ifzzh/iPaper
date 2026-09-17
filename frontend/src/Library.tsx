import { TopicSidebar, TopicManager, PaperTopics, TopicBatchDialog } from "./Topics";
import {
  TagChips,
  PaperKeywords,
  TagManager,
  KeywordBatchDialog,
  type Tag,
} from "./Keywords";
import {
  MetadataDetails,
  MetadataEditor,
  MetadataBatchDialog,
} from "./Metadata";
import { TranslationDialog } from "./Processing";
import { ResizeHandle } from "./ResizeHandle";
import { useEffect, useMemo, useState, useRef } from "react";
import {
  Library as LibraryIcon,
  Star,
  Folder,
  Plus,
  Search,
  RefreshCw,
  BookOpen,
  ChevronRight,
  ChevronLeft,
  MoreHorizontal,
  FileText,
  Languages,
  Sparkles,
  Bookmark,
  Trash2,
  Pencil,
  FolderInput,
  Download,
  Check,
  Tags,
  SlidersHorizontal,
  X,
  ArrowUpRight,
} from "lucide-react";
import { type Paper, paperFrom, errorText } from "./api";
import {
  api,
  useResource,
  Status,
  Modal,
  Field,
  Confirm,
  Markdown,
  dateText,
} from "./ui";
export type Category = {
  id: string;
  name: string;
  display_name?: string;
  children: Category[];
  pdf_count?: number;
  pinned?: boolean;
  color?: string;
};
export function flattenCategories(
  c: Category,
  depth = 0,
): { category: Category; depth: number }[] {
  return [
    { category: c, depth },
    ...[...(c.children || [])]
      .sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned))
      .flatMap((x) => flattenCategories(x, depth + 1)),
  ];
}
export function CategoryOptions({
  tree,
  exclude,
}: {
  tree: Category;
  exclude?: string;
}) {
  return (
    <>
      {flattenCategories(tree)
        .filter((x) => x.category.id !== exclude)
        .map(({ category, depth }) => (
          <option key={category.id} value={category.id}>
            {"　".repeat(depth)}
            {category.id === "root"
              ? "未分类"
              : category.display_name || category.name}
          </option>
        ))}
    </>
  );
}
export function Library({
  preferences,
  onPreferences,
  items,
  selected,
  onSelect,
  onRead,
  onChanged,
  onImport,
  onTasks,
  onAnalysis,
  loading,
  error,
  tree,
  refreshTree,
  filter,
  setFilter,
}: {
  preferences: any;
  onPreferences: (v: Record<string, unknown>) => void;
  items: Paper[];
  selected: string;
  onSelect: (id: string) => void;
  onRead: (p: Paper) => void;
  onChanged: () => void;
  onImport: () => void;
  onTasks: (task?: { id: string; kind: "analysis"; label: string }) => void;
  onAnalysis: (p: Paper) => void;
  loading: boolean;
  error: string;
  tree: Category;
  refreshTree: () => void;
  filter: string;
  setFilter: (s: string) => void;
}) {
  const layout = useRef<HTMLDivElement>(null);
  useEffect(() => {
    for (const [key, variable] of [
      ["categoryWidth", "--category-width"],
      ["detailWidth", "--detail-width"],
    ])
      if (preferences[key])
        layout.current?.style.setProperty(variable, preferences[key] + "px");
  }, [preferences.categoryWidth, preferences.detailWidth]);
  const [query, setQuery] = useState(""),
    [page, setPage] = useState(1),
    [order, setOrder] = useState("recent"),
    [action, setAction] = useState(""),
    [failure, setFailure] = useState(""),
    [busy, setBusy] = useState(false),
    [checked, setChecked] = useState<string[]>([]),
    [metadataRevision, setMetadataRevision] = useState(0),
    [metadataSelection, setMetadataSelection] = useState<Record<
      string,
      unknown
    > | null>(null);
  const [topicRevision, setTopicRevision] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [topicFilter, setTopicFilter] = useState<string>(preferences.topicFilter || 'all');
  useEffect(() => {if(preferences.topicFilter) setTopicFilter(preferences.topicFilter);}, [preferences.topicFilter]);
  function chooseTopics(value: string) {
    setTopicFilter(value);
    onPreferences({topicFilter: value});
  }
  const topicCatalog = useResource<{topics: {id: string; name: string}[]; legacyMapping: Record<string,string>}>("/api/topics", {topics: [], legacyMapping: {}});
  useEffect(() => {
    if (filter === 'root') setFilter('all');
    else if(filter.startsWith('topic:') || filter === 'unorganized') { chooseTopics(filter); setFilter('all'); }
    else if(topicCatalog.data.legacyMapping?.[filter]) { chooseTopics('topic:' + topicCatalog.data.legacyMapping[filter]); setFilter('all'); }
  }, [filter, topicCatalog.data]);
  const [topicSelection, setTopicSelection] = useState<Record<string, unknown> | null>(null);
  const [tagIds, setTagIds] = useState<string[]>([]),
    [tagMode, setTagMode] = useState("all"),
    [keywordSelection, setKeywordSelection] = useState<Record<
      string,
      unknown
    > | null>(null),
    [selectionId, setSelectionId] = useState("");
  const catalog = useResource<{
    tags: Tag[];
    redirects?: Record<string, string | null>;
  }>("/api/tags", { tags: [] });
  useEffect(() => {
    const aliases = catalog.data.redirects || {};
    setTagIds((previous) => {
      const next = Array.from(
        new Set(
          previous.flatMap((id) =>
            id in aliases ? (aliases[id] ? [aliases[id]!] : []) : [id],
          ),
        ),
      );
      return next.join() === previous.join() ? previous : next;
    });
  }, [catalog.data]);
  const reading = useResource<any[]>("/api/reading-list", []);
  const criteria = useMemo(
    () => ({
      scope: ["all", "favorites", "reading"].includes(filter) ? filter : "all",
      topicIds: topicFilter.startsWith("topic:") ? topicFilter.slice(6).split(",").filter(Boolean) : [],
      unorganized: topicFilter === "unorganized",
      query,
      tagIds,
      tagMode,
      order,
    }),
    [filter, topicFilter, query, tagIds, tagMode, order],
  );
  const list = useResource<any>(
    "/api/library/papers?filter=" +
      encodeURIComponent(JSON.stringify(criteria)) +
      "&page=" +
      page,
    { items: [], total: 0 },
  );
  const visible: Paper[] = list.data.items.map(paperFrom),
    total: number = list.data.total;
  const paper =
      items.find((p) => p.id === selected) ||
      visible.find((p) => p.id === selected),
    readIds = new Set(reading.data.map((p) => p.id));
  function changedKeywords() {
    list.refresh();
    catalog.refresh();
  }
  function addFilter(tag: Tag) {
    setTagIds((v) => (v.includes(tag.id) ? v : [...v, tag.id]));
  }
  async function allSelection() {
    const result = await api("/api/library/selections", "POST", {
      selection: criteria,
    });
    setChecked(result.paperIds);
    setSelectionId(result.id);
    return result;
  }
  useEffect(() => {
    setPage(1);
    setChecked([]);
    setSelectionId("");
  }, [filter, topicFilter, query, tagIds, tagMode, order]);
  useEffect(() => {
    list.refresh();
    catalog.refresh();
  }, [items]);
  useEffect(() => {
    const timer = setInterval(() => {
      list.refresh();
      catalog.refresh();
    }, 5000);
    return () => clearInterval(timer);
  }, []);
  useEffect(() => {
    if (total > 0 && page > Math.ceil(total / 50))
      setPage(Math.ceil(total / 50));
  }, [total, page]);
  async function perform(fn: () => Promise<unknown>) {
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      await fn();
      onChanged();
      reading.refresh();
      refreshTree();
      setAction("");
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  const scopeTitle = filter === 'favorites' ? '收藏' : filter === 'reading' ? 'Reading List' : '全部文献';
  const topicTitle = topicFilter === 'unorganized' ? '待整理' : topicFilter.startsWith('topic:') ? topicFilter.slice(6).split(',').map(id => topicCatalog.data.topics.find(t => t.id === id)?.name || '主题').join('、') : '';
  const title = topicTitle ? `${scopeTitle} · ${topicTitle}` : scopeTitle;
  const activeFilters =
    (filter !== 'all' ? 1 : 0) + (topicFilter !== 'all' ? 1 : 0) + tagIds.length;
  return (
    <div
      ref={layout}
      className={"library-workspace " + (paper ? "has-selection" : "")}
    >
      <section className="library-list-panel">
        <header className="page-heading">
          <div>
            <h1>{title}</h1>
            <p>
              {total} 篇文献
              {activeFilters > 0 ? ` · 已筛选 ${activeFilters} 项` : ""}
            </p>
          </div>
          <button className="primary" onClick={onImport}>
            <Plus size={16} />
            导入文献
          </button>
        </header>
        <div className="scope-tabs" role="tablist" aria-label="文献范围">
          {[
            ["all", "全部文献", items.length],
            ["favorites", "收藏", items.filter((p) => p.starred).length],
            ["reading", "Reading List", reading.data.length],
          ].map(([id, label, count]: any) => (
            <button
              role="tab"
              aria-selected={filter === id}
              className={filter === id ? "active" : ""}
              key={id}
              onClick={() => setFilter(id)}
            >
              {label}
              <small>{count}</small>
            </button>
          ))}
        </div>
        <div className="list-toolbar">
          <label className="search">
            <Search size={16} />
            <input
              aria-label="搜索文献"
              placeholder="搜索标题、作者、摘要…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button
            className="filter-button"
            aria-expanded={filtersOpen}
            onClick={() => setFiltersOpen(true)}
          >
            <SlidersHorizontal size={15} />
            <span>筛选</span>
            {activeFilters > 0 && <b>{activeFilters}</b>}
          </button>
          <select
            aria-label="文献排序"
            value={order}
            onChange={(e) => setOrder(e.target.value)}
          >
            <option value="recent">最近导入</option>
            <option value="title">按标题</option>
            <option value="year">按年份</option>
          </select>
          <button
            className="icon-button"
            aria-label="刷新文献"
            title="刷新文献"
            onClick={onChanged}
          >
            <RefreshCw size={16} />
          </button>
        </div>
        {activeFilters > 0 && (
          <div className="applied-filters" aria-label="已选条件">
            {filter !== "all" && (
              <button
                className="filter-chip"
                title="移除范围筛选"
                onClick={() => setFilter("all")}
              >
                {filter === "favorites" ? "收藏" : "Reading List"}
                <X size={12} />
              </button>
            )}
            {topicFilter !== "all" && (
              <button
                className="filter-chip"
                title="移除主题筛选"
                onClick={() => chooseTopics("all")}
              >
                {topicTitle ||
                  (topicFilter === "unorganized" ? "待整理" : "研究主题")}
                <X size={12} />
              </button>
            )}
            {tagIds.map((id) => (
              <button
                className="filter-chip"
                key={id}
                title="移除关键词筛选"
                onClick={() => setTagIds((v) => v.filter((x) => x !== id))}
              >
                {catalog.data.tags.find((t) => t.id === id)?.name || "标签"}
                <X size={12} />
              </button>
            ))}
            {tagIds.length > 1 && (
              <span className="filter-mode">
                {tagMode === "all" ? "同时满足" : "任一满足"}
              </span>
            )}
            <button
              className="text-button"
              onClick={() => {
                setFilter("all");
                chooseTopics("all");
                setTagIds([]);
              }}
            >
              清除全部
            </button>
          </div>
        )}
        <Status
          error={error || failure || list.error}
          loading={loading || list.loading}
          retry={onChanged}
        />
        {checked.length > 0 && (
          <div className="batch-toolbar">
            <span>已选择 {checked.length} 篇</span>
            <button onClick={() => setMetadataSelection({ paperIds: checked })}>
              补全信息
            </button>
            <button
              onClick={() =>
                setKeywordSelection(
                  selectionId ? { selectionId } : { paperIds: checked },
                )
              }
            >
              整理／编辑标签
            </button>
            <button
              onClick={() =>
                perform(async () => {
                  await allSelection();
                })
              }
            >
              选择全部匹配（{total} 篇）
            </button>
            <button
              onClick={() =>
                setTopicSelection(
                  selectionId ? { selectionId } : { paperIds: checked },
                )
              }
            >
              整理论文主题
            </button>
            <button
              onClick={() =>
                perform(async () => {
                  for (const id of checked)
                    await api("/api/paper/" + encodeURIComponent(id), "PUT", {
                      starred: true,
                    });
                })
              }
            >
              收藏
            </button>
            <button onClick={() => setAction("bulk-delete")}>删除</button>
            <button
              onClick={() => {
                setChecked([]);
                setSelectionId("");
              }}
            >
              取消选择
            </button>
          </div>
        )}
        <div className="paper-list">
          {visible.map((p) => (
            <article
              className={"paper-row " + (selected === p.id ? "selected" : "")}
              key={p.id}
              tabIndex={0}
              onClick={() => onSelect(p.id)}
              onDoubleClick={() => onRead(p)}
              onKeyDown={(e) => {
                if (e.key === "Enter") onRead(p);
              }}
            >
              <input
                type="checkbox"
                aria-label={"选择 " + p.title}
                checked={checked.includes(p.id)}
                onClick={(e) => e.stopPropagation()}
                onChange={(e) => (
                  setSelectionId(""),
                  setChecked((v) =>
                    e.target.checked
                      ? [...v, p.id]
                      : v.filter((id) => id !== p.id),
                  )
                )}
              />
              <div className="paper-row-main">
                <div className="paper-title">
                  {p.starred && <Star size={13} className="starred" />}
                  <h3 title={p.title}>{p.title}</h3>
                </div>
                <AuthorLine
                  authors={p.authors}
                  year={p.year}
                  paperId={p.id}
                />
                <div className="paper-tags">
                  {(p.tags || []).slice(0, 3).map((t) => (
                    <button
                      className="tag-link"
                      key={t.id}
                      title={"筛选：" + t.name}
                      onClick={(e) => {
                        e.stopPropagation();
                        addFilter(t);
                      }}
                    >
                      {t.name}
                    </button>
                  ))}
                  {(p.tags || []).length > 3 && (
                    <span className="tag-more">
                      +{(p.tags || []).length - 3}
                    </span>
                  )}
                  <span className="asset-state">
                    {p.translated ? "译文可读" : "原文 PDF"}
                  </span>
                  {p.has_analysis_result && (
                    <span className="asset-state">分析已完成</span>
                  )}
                  {p.analysis === "failed" && (
                    <span className="asset-state warn">分析失败</span>
                  )}
                </div>
              </div>
              <div className="paper-row-aside">
                <button
                  className={"icon-button " + (p.starred ? "starred" : "")}
                  aria-label={p.starred ? "取消收藏" : "收藏论文"}
                  onClick={(e) => {
                    e.stopPropagation();
                    perform(() =>
                      api("/api/paper/" + encodeURIComponent(p.id), "PUT", {
                        starred: !p.starred,
                      }),
                    );
                  }}
                >
                  <Star size={16} />
                </button>
              </div>
            </article>
          ))}
          {!loading && total === 0 && (
            <div className="empty-state">
              <LibraryIcon size={38} />
              <h3>
                {query || tagIds.length || filter !== "all"
                  ? "没有匹配的文献"
                  : "从第一篇论文开始"}
              </h3>
              <p>
                {query || tagIds.length || filter !== "all"
                  ? "尝试调整搜索、标签或分类条件。"
                  : "导入 PDF，或从 Daily arXiv 加入感兴趣的论文。"}
              </p>
              {!query && (
                <button className="primary" onClick={onImport}>
                  <Plus size={16} />
                  导入文献
                </button>
              )}
            </div>
          )}
        </div>
        <div className="pagination">
          <button
            className="text-button"
            disabled={!total}
            onClick={() => {
              setChecked(visible.map((p) => p.id));
              setSelectionId("");
            }}
          >
            选择当前页
          </button>
          <span>
            {Math.min((page - 1) * 50 + 1, total)}–{Math.min(page * 50, total)}{" "}
            / {total}
          </span>
          <button
            aria-label="上一页列表"
            disabled={page <= 1}
            onClick={() => setPage((n) => n - 1)}
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            第 {page} / {Math.max(1, Math.ceil(total / 50))} 页
          </span>
          <button
            aria-label="下一页列表"
            disabled={page >= Math.ceil(total / 50)}
            onClick={() => setPage((n) => n + 1)}
          >
            <ChevronRight size={16} />
          </button>
        </div>
      </section>
      <aside className="paper-details">
        <ResizeHandle
          host={layout}
          property="--detail-width"
          label="调整详情栏宽度"
          min={280}
          max={560}
          initial={preferences.detailWidth || 420}
          direction={-1}
          onChange={(v) => onPreferences({ detailWidth: v })}
        />
        <div className="panel-heading">
          <div>
            <span className="eyebrow">PAPER DETAILS</span>
            <h2>论文详情</h2>
          </div>
          <button className="mobile-only" onClick={() => onSelect("")}>
            返回列表
          </button>
        </div>
        {paper ? (
          <div className="details-scroll">
            <div className="detail-actions">
              <span className="badge">{paper.year || "论文"}</span>
              <button
                aria-label="编辑元数据"
                className="icon-button"
                onClick={() => setAction("edit")}
              >
                <Pencil size={16} />
              </button>
              <button
                aria-label="更多论文操作"
                className="icon-button"
                onClick={() => setAction("paper-menu")}
              >
                <MoreHorizontal size={18} />
              </button>
            </div>
            <h1 className="detail-title">{paper.title}</h1>
            <p className="detail-authors">
              {paper.authors || "作者信息待补充"}
            </p>
            <button className="primary dark full" onClick={() => onRead(paper)}>
              <BookOpen size={17} />
              打开阅读
            </button>
            <section className="detail-section">
              <div className="section-label">文献处理</div>
              <button
                className="pipeline-action"
                onClick={() => setAction("translate")}
              >
                <Languages size={20} />
                <span>
                  <strong>{paper.translated ? "重新翻译" : "全文翻译"}</strong>
                  <small>
                    {paper.translated
                      ? "已有译文可在阅读器内切换"
                      : "通过 BabelDOC 生成双语 PDF"}
                  </small>
                </span>
                <ChevronRight size={16} />
              </button>
              <button
                className="pipeline-action"
                onClick={() => onAnalysis(paper)}
              >
                <Sparkles size={20} />
                <span>
                  <strong>概览与深度解读</strong>
                  <small>复用解析原文，速读与深入理解</small>
                </span>
                <ChevronRight size={16} />
              </button>
              <button className="text-button" onClick={() => onTasks()}>
                查看任务进度与日志
              </button>
            </section>
            <section className="detail-section">
              <div className="section-label">摘要</div>
              <p className="abstract">
                {paper.abstract || "暂无摘要，可在编辑元数据中补充。"}
              </p>
            </section>
            <PaperTopics key={paper.id + ":topics"} paperId={paper.id} onChanged={() => {setTopicRevision(v => v + 1); list.refresh();}}/>
            <PaperKeywords
              key={paper.id + ":tags"}
              id={paper.id}
              onFilter={addFilter}
              onChanged={changedKeywords}
              onTasks={onTasks}
            />
            <MetadataDetails
              key={paper.id + ":" + metadataRevision}
              id={paper.id}
              onChanged={onChanged}
              onSelect={onSelect}
            />
            {paper.notes && (
              <section className="detail-section">
                <span className="section-label">文献备注</span>
                <p>{paper.notes}</p>
              </section>
            )}
          </div>
        ) : (
          <div className="empty-state">
            <FileText size={38} />
            <h3>选择一篇论文</h3>
            <p>单击预览详情，双击开始阅读。</p>
          </div>
        )}
      </aside>
      {action === "tags" && (
        <TagManager
          onClose={() => setAction("")}
          onChanged={changedKeywords}
          onMerged={(from, to) =>
            setTagIds((v) =>
              Array.from(
                new Set(
                  v.flatMap((id) => (id === from ? (to ? [to.id] : []) : [id])),
                ),
              ),
            )
          }
        />
      )}
      {keywordSelection && (
        <KeywordBatchDialog
          selection={keywordSelection}
          onClose={() => setKeywordSelection(null)}
          onChanged={changedKeywords}
          onCreated={() => {
            setKeywordSelection(null);
            changedKeywords();
            onTasks();
          }}
        />
      )}
      {action === "edit" && paper && (
        <MetadataEditor
          key={paper.id}
          id={paper.id}
          onClose={() => setAction("")}
          onSaved={() => {
            setMetadataRevision((v) => v + 1);
            onChanged();
          }}
        />
      )}
      {metadataSelection && (
        <MetadataBatchDialog
          selection={metadataSelection}
          onClose={() => setMetadataSelection(null)}
          onCreated={() => {
            setMetadataSelection(null);
            onTasks();
          }}
        />
      )}
      {action === "notes" && paper && (
        <EditNotes
          paper={paper}
          onClose={() => setAction("")}
          onSaved={onChanged}
        />
      )}
      {action === "translate" && paper && (
        <TranslationDialog
          paper={paper}
          onClose={() => setAction("")}
          onSubmitted={() => {
            onChanged();
            onTasks();
          }}
        />
      )}
      {["analyze", "bulk-delete", "delete"].includes(action) && (
        <Confirm
          title={
            action.includes("delete")
              ? "删除文献"
              : action === "translate"
                ? "启动全文翻译"
                : "启动解析与分析"
          }
          detail={
            action.includes("delete")
              ? "将删除所选论文及其资产，不能通过关闭标签撤销。"
              : "该操作会创建处理任务并使用已配置的服务。已有内容不会因查看页面而自动重新生成。"
          }
          onClose={() => setAction("")}
          onConfirm={async () => {
            if (action === "bulk-delete") {
              for (const id of checked)
                await api("/api/paper/" + encodeURIComponent(id), "DELETE");
              setChecked([]);
            } else if (action === "delete" && paper) {
              await api("/api/paper/" + encodeURIComponent(paper.id), "DELETE");
              onSelect("");
            } else if (paper) {
              const result = await api(
                "/api/paper/" +
                  (action === "translate" ? "translate" : "analyze"),
                "POST",
                { paper_id: paper.id },
              );
              if (action !== "translate" && result.task_id)
                onTasks({
                  id: result.task_id,
                  kind: "analysis",
                  label: paper.title,
                });
            }
            onChanged();
            if (!action.includes("delete")) onTasks();
          }}
        />
      )}
      {action === "paper-menu" && paper && (
        <Modal title="论文操作" onClose={() => setAction("")}>
          <div className="menu-list">
            <button
              onClick={() =>
                perform(() =>
                  api(
                    "/api/reading-list/" +
                      encodeURIComponent(paper.id) +
                      (readIds.has(paper.id) ? "/remove" : "/add"),
                    "POST",
                  ),
                )
              }
            >
              <Bookmark size={16} />
              {readIds.has(paper.id)
                ? "移出 Reading List"
                : "加入 Reading List"}
            </button>
            <button onClick={() => setAction("notes")}>
              <Pencil size={16} />
              编辑文献备注
            </button>
            <button onClick={() => {setAction(""); if(paper) setTopicSelection({paperIds: [paper.id]});}}>
              <FolderInput size={16} />
              调整研究主题
            </button>
            <a
              href={"/api/paper/" + encodeURIComponent(paper.id) + "/file"}
              download
            >
              <Download size={16} />
              下载原文
            </a>
            {paper.translated && (
              <a
                href={
                  "/api/paper/" + encodeURIComponent(paper.id) + "/chinese/file"
                }
                download
              >
                下载译文
              </a>
            )}
            <button onClick={() => setAction("delete")} className="danger">
              <Trash2 size={16} />
              删除论文
            </button>
          </div>
        </Modal>
      )}
      {filtersOpen && (
        <Modal title="筛选文献" onClose={() => setFiltersOpen(false)} wide>
          <div className="filter-dialog">
            <section>
              <div className="section-label">范围</div>
              <div className="scope-tabs" role="tablist" aria-label="文献范围">
                {[
                  ["all", "全部文献", items.length],
                  ["favorites", "收藏", items.filter((p) => p.starred).length],
                  ["reading", "Reading List", reading.data.length],
                ].map(([id, label, count]: any) => (
                  <button
                    role="tab"
                    aria-selected={filter === id}
                    className={filter === id ? "active" : ""}
                    key={id}
                    onClick={() => setFilter(id)}
                  >
                    {label}
                    <small>{count}</small>
                  </button>
                ))}
              </div>
            </section>
            <section>
              <div className="section-label">研究主题</div>
              <TopicSidebar
                filter={topicFilter}
                onFilter={chooseTopics}
                changed={topicRevision}
                collapsed={preferences.topicCollapsed || []}
                onCollapsed={(ids) => onPreferences({ topicCollapsed: ids })}
              />
            </section>
            <section>
              <div className="section-label">
                关键词
                <button
                  className="text-button"
                  onClick={() => {
                    setFiltersOpen(false);
                    setAction("tags");
                  }}
                >
                  管理标签
                </button>
              </div>
              <div className="filter-keywords">
                <select
                  aria-label="按关键词筛选"
                  value=""
                  onChange={(e) => {
                    const t = catalog.data.tags.find((x) => x.id === e.target.value);
                    if (t) addFilter(t);
                  }}
                >
                  <option value="">选择标签</option>
                  {catalog.data.tags.map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}（{t.count}）
                    </option>
                  ))}
                </select>
                {tagIds.length > 1 && (
                  <select
                    aria-label="多标签匹配方式"
                    value={tagMode}
                    onChange={(e) => setTagMode(e.target.value)}
                  >
                    <option value="all">同时满足</option>
                    <option value="any">任一满足</option>
                  </select>
                )}
                <div className="keyword-chips">
                  {tagIds.map((id) => (
                    <button
                      className="keyword-chip"
                      key={id}
                      onClick={() => setTagIds((v) => v.filter((x) => x !== id))}
                    >
                      {catalog.data.tags.find((t) => t.id === id)?.name || "标签"}
                      <X size={12} />
                    </button>
                  ))}
                </div>
              </div>
            </section>
            <section>
              <div className="section-label">排序</div>
              <select
                aria-label="文献排序（弹窗）"
                value={order}
                onChange={(e) => setOrder(e.target.value)}
              >
                <option value="recent">最近导入</option>
                <option value="title">按标题</option>
                <option value="year">按年份</option>
              </select>
            </section>
          </div>
          <footer>
            <button
              className="text-button"
              onClick={() => {
                setFilter("all");
                chooseTopics("all");
                setTagIds([]);
              }}
            >
              清除全部
            </button>
            <button className="primary" onClick={() => setFiltersOpen(false)}>
              查看结果（{total} 篇）
            </button>
          </footer>
        </Modal>
      )}
      {action === "topic-manager" && <TopicManager onClose={() => {setAction(""); setTopicRevision(v => v + 1); list.refresh();}}/>}
      {topicSelection && <TopicBatchDialog selection={topicSelection} onClose={() => setTopicSelection(null)} onChanged={() => {setTopicRevision(v => v + 1); list.refresh();}}/>}
    </div>
  );
}
function AuthorLine({
  authors,
  year,
  paperId,
}: {
  authors: string;
  year: string;
  paperId: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const value = authors || "作者信息待补充";
  const long = value.length > 48 || value.split(",").length > 3;
  return (
    <p className={"paper-meta" + (expanded ? " expanded" : "")}>
      <span title={value}>{value}</span>
      <span className="paper-meta-tail">
        {year && <span>· {year}</span>}
        {long && (
          <>
            <span aria-hidden="true">·</span>
            <button
              className="text-button inline"
              aria-expanded={expanded}
              aria-label={(expanded ? "收起作者 " : "展开作者 ") + paperId}
              onClick={(e) => {
                e.stopPropagation();
                setExpanded((v) => !v);
              }}
            >
              {expanded ? "收起" : "全部作者"}
            </button>
          </>
        )}
      </span>
    </p>
  );
}
function EditNotes({
  paper,
  onClose,
  onSaved,
}: {
  paper: Paper;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState({ notes: paper.notes || "" }),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <Modal title="文献备注" onClose={onClose} wide>
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          try {
            await api(
              "/api/paper/" + encodeURIComponent(paper.id),
              "PUT",
              draft,
            );
            onSaved();
            onClose();
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        {Object.entries({
          notes: "文献备注",
        }).map(([key, label]) => (
          <Field key={key} label={label}>
            {["abstract", "notes"].includes(key) ? (
              <textarea
                rows={7}
                value={draft[key as keyof typeof draft]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            ) : (
              <input
                value={draft[key as keyof typeof draft]}
                onChange={(e) => setDraft({ ...draft, [key]: e.target.value })}
              />
            )}
          </Field>
        ))}
        <Status error={error} />
        <footer>
          <button type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary" disabled={busy}>
            保存
          </button>
        </footer>
      </form>
    </Modal>
  );
}
