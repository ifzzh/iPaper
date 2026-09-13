import { TranslationDialog } from "./Processing";
import { ResizeHandle } from "./ResizeHandle";
import { useEffect, useMemo, useState, useRef } from "react";
import {
  Library as LibraryIcon,
  Star,
  Folder,
  Plus,
  Search,
  Upload,
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
    [category, setCategory] = useState<Category | null>(null),
    [checked, setChecked] = useState<string[]>([]);
  const reading = useResource<any[]>("/api/reading-list", []),
    subset = useResource<any[]>(
      !["all", "favorites", "reading"].includes(filter)
        ? "/api/papers/" + encodeURIComponent(filter) + "/recursive"
        : null,
      [],
    );
  const paper = items.find((p) => p.id === selected),
    readIds = new Set(reading.data.map((p) => p.id));
  const visible = useMemo(() => {
    let list = items.filter((p) =>
      filter === "favorites"
        ? p.starred
        : filter === "reading"
          ? readIds.has(p.id)
          : filter === "all"
            ? true
            : subset.data.some((x) => x.id === p.id),
    );
    if (query.trim())
      list = list.filter((p) =>
        (p.title + " " + p.authors + " " + p.abstract + " " + p.year)
          .toLowerCase()
          .includes(query.toLowerCase()),
      );
    if (order === "title")
      list = [...list].sort((a, b) => a.title.localeCompare(b.title));
    if (order === "year")
      list = [...list].sort((a, b) => b.year.localeCompare(a.year));
    return list;
  }, [items, filter, query, order, reading.data, subset.data]);
  useEffect(() => {
    setPage(1);
    setChecked([]);
  }, [filter, query]);
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
  const title =
    filter === "all"
      ? "全部文献"
      : filter === "favorites"
        ? "收藏"
        : filter === "reading"
          ? "Reading List"
          : flattenCategories(tree).find((x) => x.category.id === filter)
              ?.category.name || "文献分类";
  return (
    <div
      ref={layout}
      className={"library-workspace " + (paper ? "has-selection" : "")}
    >
      <aside className="category-sidebar">
        <ResizeHandle
          host={layout}
          property="--category-width"
          label="调整分类栏宽度"
          min={180}
          max={420}
          initial={preferences.categoryWidth || 248}
          onChange={(v) => onPreferences({ categoryWidth: v })}
        />
        <div className="panel-heading">
          <div>
            <span className="eyebrow">IPAPER</span>
            <h2>我的文献库</h2>
          </div>
          <button
            className="icon-button"
            title="新建分类"
            aria-label="新建分类"
            onClick={() => {
              setCategory(tree);
              setAction("create-category");
            }}
          >
            <Plus size={18} />
          </button>
        </div>
        <nav className="library-navigation">
          {[
            ["all", "全部文献", LibraryIcon, items.length],
            ["favorites", "收藏", Star, items.filter((p) => p.starred).length],
            ["reading", "Reading List", Bookmark, reading.data.length],
          ].map(([id, label, Icon, count]: any) => (
            <button
              className={filter === id ? "active" : ""}
              key={id}
              onClick={() => setFilter(id)}
            >
              <Icon size={17} />
              <span>{label}</span>
              <small>{count}</small>
            </button>
          ))}
        </nav>
        <div className="section-label">
          分类{" "}
          <button
            className="icon-button"
            aria-label="刷新分类"
            onClick={refreshTree}
          >
            <RefreshCw size={13} />
          </button>
        </div>
        <nav className="category-tree">
          {flattenCategories(tree).map(({ category: c, depth }) => (
            <div
              className={
                "category-row depth-" +
                Math.min(depth, 4) +
                (filter === c.id ? " active" : "")
              }
              key={c.id}
            >
              <button onClick={() => setFilter(c.id)}>
                <Folder size={16} />
                <span>
                  {c.id === "root" ? "未分类" : c.display_name || c.name}
                </span>
              </button>
              <button
                className="icon-button"
                aria-label={"管理分类 " + c.name}
                onClick={() => {
                  setCategory(c);
                  setAction("category");
                }}
              >
                <MoreHorizontal size={15} />
              </button>
            </div>
          ))}
        </nav>
        <div className="sidebar-foot">
          <BookOpen size={17} />
          <div>
            专注阅读，连接思考<small>{items.length} 篇文献 · 安全存储</small>
          </div>
        </div>
      </aside>
      <section className="library-list-panel">
        <div className="list-toolbar">
          <label className="search">
            <Search size={17} />
            <input
              aria-label="搜索文献"
              placeholder="搜索标题、作者、摘要…"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <button className="primary" onClick={onImport}>
            <Upload size={16} />
            <span>导入文献</span>
          </button>
          <button
            className="icon-button"
            aria-label="刷新文献"
            onClick={onChanged}
          >
            <RefreshCw size={17} />
          </button>
        </div>
        <div className="list-meta">
          <select
            className="mobile-only"
            aria-label="筛选分类"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="all">全部文献</option>
            <option value="favorites">收藏</option>
            <option value="reading">Reading List</option>
            <CategoryOptions tree={tree} />
          </select>
          <span>
            {title} <strong>{visible.length}</strong>
          </span>
          <select
            aria-label="文献排序"
            value={order}
            onChange={(e) => setOrder(e.target.value)}
          >
            <option value="recent">最近导入</option>
            <option value="title">按标题</option>
            <option value="year">按年份</option>
          </select>
        </div>
        <Status
          error={error || failure || subset.error}
          loading={loading || subset.loading}
          retry={onChanged}
        />
        {checked.length > 0 && (
          <div className="batch-toolbar">
            <span>已选择 {checked.length} 篇</span>
            <button onClick={() => setAction("bulk-move")}>移动</button>
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
            <button onClick={() => setChecked([])}>取消选择</button>
          </div>
        )}
        <div className="paper-list">
          {visible.slice((page - 1) * 50, page * 50).map((p) => (
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
                onChange={(e) =>
                  setChecked((v) =>
                    e.target.checked
                      ? [...v, p.id]
                      : v.filter((id) => id !== p.id),
                  )
                }
              />
              <div className="paper-row-main">
                <div className="paper-title">
                  {p.starred && <Star size={13} className="starred" />}
                  <h3>{p.title}</h3>
                </div>
                <p>{p.authors || "作者信息待补充"}</p>
                <div className="badges">
                  <span className={p.translated ? "badge success" : "badge"}>
                    {p.translated ? "译文可读" : "原文 PDF"}
                  </span>
                  {p.has_analysis_result && (
                    <span className="badge info">分析已完成</span>
                  )}
                  {p.analysis === "failed" && (
                    <span className="badge warning">分析失败</span>
                  )}
                </div>
              </div>
              <div className="paper-row-aside">
                <span>{p.year || "—"}</span>
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
          {!loading && visible.length === 0 && (
            <div className="empty-state">
              <LibraryIcon size={38} />
              <h3>{query ? "没有匹配的文献" : "从第一篇论文开始"}</h3>
              <p>
                {query
                  ? "尝试其他标题或作者。"
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
          <span>
            {Math.min((page - 1) * 50 + 1, visible.length)}–
            {Math.min(page * 50, visible.length)} / {visible.length}
          </span>
          <button
            aria-label="上一页列表"
            disabled={page <= 1}
            onClick={() => setPage((n) => n - 1)}
          >
            <ChevronLeft size={16} />
          </button>
          <span>
            第 {page} / {Math.max(1, Math.ceil(visible.length / 50))} 页
          </span>
          <button
            aria-label="下一页列表"
            disabled={page >= Math.ceil(visible.length / 50)}
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
            <section className="detail-section">
              <div className="section-label">文献信息</div>
              <dl>
                <dt>年份</dt>
                <dd>{paper.year || "—"}</dd>
                <dt>发布日期</dt>
                <dd>{dateText(paper.published)}</dd>
              </dl>
              {paper.notes && <p className="abstract">备注：{paper.notes}</p>}
              {paper.affiliation && <p>机构：{paper.affiliation}</p>}
              {(
                [
                  ["代码仓库", paper.github],
                  ["项目主页", paper.homepage],
                ] as const
              ).map(([label, url]) =>
                url && /^https?:\/\//.test(url) ? (
                  <a
                    key={label}
                    className="text-button"
                    href={url}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {label}
                    <ArrowUpRight size={14} />
                  </a>
                ) : null,
              )}
              {paper.arxiv_url && /^https?:\/\//.test(paper.arxiv_url) && (
                <a
                  className="text-button"
                  href={paper.arxiv_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  访问来源
                  <ArrowUpRight size={14} />
                </a>
              )}
            </section>
          </div>
        ) : (
          <div className="empty-state">
            <FileText size={38} />
            <h3>选择一篇论文</h3>
            <p>单击预览详情，双击开始阅读。</p>
          </div>
        )}
      </aside>
      {action === "edit" && paper && (
        <EditPaper
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
            <button onClick={() => setAction("move")}>
              <FolderInput size={16} />
              移动到分类
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
      {(action === "move" || action === "bulk-move") && (
        <MoveDialog
          tree={tree}
          onClose={() => setAction("")}
          onSave={(target) =>
            perform(async () => {
              for (const id of action === "bulk-move"
                ? checked
                : paper
                  ? [paper.id]
                  : [])
                await api(
                  "/api/paper/" + encodeURIComponent(id) + "/move",
                  "PUT",
                  { target_category_id: target },
                );
            })
          }
        />
      )}
      {category && ["category", "create-category"].includes(action) && (
        <CategoryDialog
          category={category}
          tree={tree}
          creating={action === "create-category"}
          onClose={() => setAction("")}
          onSaved={() => {
            refreshTree();
            onChanged();
            setAction("");
          }}
        />
      )}
    </div>
  );
}
function MoveDialog({
  tree,
  onClose,
  onSave,
}: {
  tree: Category;
  onClose: () => void;
  onSave: (id: string) => Promise<unknown>;
}) {
  const [id, setId] = useState("root");
  return (
    <Modal title="移动到分类" onClose={onClose}>
      <Field label="目标分类">
        <select value={id} onChange={(e) => setId(e.target.value)}>
          <CategoryOptions tree={tree} />
        </select>
      </Field>
      <button className="primary" onClick={() => onSave(id)}>
        移动
      </button>
    </Modal>
  );
}
function EditPaper({
  paper,
  onClose,
  onSaved,
}: {
  paper: Paper;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState({
      title: paper.title,
      authors: paper.authors,
      year: paper.year,
      abstract: paper.abstract,
      github: paper.github || "",
      homepage: paper.homepage || "",
      notes: paper.notes || "",
      affiliation: paper.affiliation || "",
      journal: paper.journal || "",
    }),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <Modal title="编辑论文信息" onClose={onClose} wide>
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
          title: "标题",
          authors: "作者",
          year: "年份",
          abstract: "摘要",
          github: "代码仓库",
          homepage: "项目主页",
          notes: "文献备注",
          affiliation: "作者机构",
          journal: "期刊 / 会议",
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
function CategoryDialog({
  category,
  tree,
  creating,
  onClose,
  onSaved,
}: {
  category: Category;
  tree: Category;
  creating: boolean;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(creating ? "" : category.name),
    [parent, setParent] = useState(creating ? category.id : "root"),
    [error, setError] = useState(""),
    [confirm, setConfirm] = useState(false);
  const [color, setColor] = useState(category.color || "#168272");
  async function run(fn: () => Promise<unknown>) {
    try {
      await fn();
      onSaved();
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <Modal title={creating ? "新建分类" : "管理分类"} onClose={onClose}>
      <Field label="分类名称">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
        />
      </Field>
      <Field label="上级分类">
        <select value={parent} onChange={(e) => setParent(e.target.value)}>
          <CategoryOptions
            tree={tree}
            exclude={creating ? undefined : category.id}
          />
        </select>
      </Field>
      <Status error={error} />
      {!creating && category.id !== "root" && (
        <Field label="分类颜色">
          <input
            type="color"
            value={/^#[0-9a-f]{6}$/i.test(color) ? color : "#168272"}
            onChange={(e) => setColor(e.target.value)}
          />
          <button
            onClick={() =>
              run(() =>
                api(
                  "/api/categories/" +
                    encodeURIComponent(category.id) +
                    "/color",
                  "PUT",
                  { color },
                ),
              )
            }
          >
            保存颜色
          </button>
        </Field>
      )}
      <div className="action-row">
        <button
          className="primary"
          disabled={!creating && category.id === "root"}
          onClick={() =>
            run(() =>
              api(
                "/api/categories" +
                  (creating ? "" : "/" + encodeURIComponent(category.id)),
                creating ? "POST" : "PUT",
                creating ? { name, parent_id: parent } : { name },
              ),
            )
          }
        >
          {creating ? "创建" : "重命名"}
        </button>
        {!creating && category.id !== "root" && (
          <>
            <button
              onClick={() =>
                run(() =>
                  api(
                    "/api/categories/" +
                      encodeURIComponent(category.id) +
                      "/move",
                    "PUT",
                    { target_parent_id: parent },
                  ),
                )
              }
            >
              移动
            </button>
            <button
              onClick={() =>
                run(() =>
                  api(
                    "/api/categories/" +
                      encodeURIComponent(category.id) +
                      "/pin",
                    "PUT",
                    { pinned: !category.pinned },
                  ),
                )
              }
            >
              {category.pinned ? "取消置顶" : "置顶"}
            </button>
            <button className="danger" onClick={() => setConfirm(true)}>
              删除
            </button>
          </>
        )}
      </div>
      {!creating && (
        <a
          className="text-button"
          href={
            "/api/categories/" +
            encodeURIComponent(category.id) +
            "/export-bibtex"
          }
          download
        >
          导出 BibTeX
        </a>
      )}
      {confirm && (
        <Confirm
          title="删除分类"
          detail="分类中的文献可能一同被删除，请确认无需保留。"
          onClose={() => setConfirm(false)}
          onConfirm={() =>
            run(() =>
              api(
                "/api/categories/" + encodeURIComponent(category.id),
                "DELETE",
              ),
            )
          }
        />
      )}
    </Modal>
  );
}
