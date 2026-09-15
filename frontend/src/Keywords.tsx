import { useEffect, useState } from "react";
import { Plus, X, Tags, Sparkles, Undo2, Pencil, Trash2 } from "lucide-react";
import { api, useResource, Modal, Field, Status, dateText } from "./ui";
import { errorText } from "./api";
import "./keywords.css";

export type Tag = {
  id: string;
  name: string;
  revision?: number;
  aliases?: string[];
  count?: number;
  manual?: boolean;
  status?: string;
};
const statuses: Record<string, string> = {
  pending: "待整理",
  ready: "已整理",
  needs_content: "内容不足，待整理",
  queued: "排队中",
  running: "整理中",
  completed: "已完成",
  reused: "已复用",
  failed: "失败",
  stale: "内容已变化",
  cancelled: "已取消",
  interrupted: "结果未确认",
  deleted: "论文已删除",
  partial: "部分完成",
};

export function TagChips({
  tags,
  onSelect,
  compact = false,
}: {
  tags: Tag[];
  onSelect: (t: Tag) => void;
  compact?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div
      className="keyword-chips"
      onDoubleClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {(compact && !expanded ? tags.slice(0, 3) : tags).map((t) => (
        <button
          className="keyword-chip"
          key={t.id}
          title={"筛选：" + t.name}
          onClick={(e) => {
            e.stopPropagation();
            onSelect(t);
          }}
        >
          {t.name}
        </button>
      ))}
      {compact && tags.length > 3 && (
        <button
          className="keyword-more"
          onClick={(e) => {
            e.stopPropagation();
            setExpanded(!expanded);
          }}
        >
          {expanded ? "收起" : `+${tags.length - 3} 更多`}
        </button>
      )}
    </div>
  );
}

function Undo({ id, onDone }: { id: string; onDone: () => void }) {
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <div className="keyword-undo" role="status">
      <span>标签已更新</span>
      <button
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await api(
              "/api/tags/operations/" + encodeURIComponent(id) + "/undo",
              "POST",
              {},
            );
            onDone();
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        <Undo2 size={14} />
        撤销
      </button>
      <Status error={error} />
    </div>
  );
}

export function PaperKeywords({
  id,
  onFilter,
  onChanged,
  onTasks,
}: {
  id: string;
  onFilter: (t: Tag) => void;
  onChanged: () => void;
  onTasks: () => void;
}) {
  const resource = useResource<any>(
    "/api/paper/" + encodeURIComponent(id) + "/tags",
    { tags: [], revision: 1, status: "pending" },
  );
  const [name, setName] = useState(""),
    [editing, setEditing] = useState(false),
    [failure, setFailure] = useState(""),
    [busy, setBusy] = useState(false),
    [operation, setOperation] = useState(""),
    [generate, setGenerate] = useState(false);
  useEffect(() => {
    const timer = setInterval(resource.refresh, 4000);
    return () => clearInterval(timer);
  }, [id]);
  async function edit(action: string, tagId?: string) {
    if (busy) return;
    setBusy(true);
    setFailure("");
    try {
      const result = await api(
        "/api/paper/" + encodeURIComponent(id) + "/tags",
        "PATCH",
        {
          action,
          revision: resource.data.revision,
          ...(tagId ? { tagId } : {}),
          ...(action === "add" ? { name } : {}),
        },
      );
      resource.setData(result);
      setOperation(result.operationId || "");
      setName("");
      onChanged();
    } catch (e) {
      setFailure(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="detail-section keyword-section">
      <div className="keyword-heading">
        <span className="section-label">关键词标签</span>
        <button
          className="text-button"
          aria-label="编辑论文标签"
          onClick={() => setEditing(!editing)}
        >
          <Pencil size={14} />
          {editing ? "完成" : "编辑"}
        </button>
      </div>
      <TagChips tags={resource.data.tags} onSelect={onFilter} />
      {!resource.data.tags.length && (
        <p className="muted">
          {resource.data.status === "needs_content"
            ? "可用内容不足，可以手动添加标签。"
            : "暂无标签，整理时使用现有标题、摘要及原文。"}
        </p>
      )}
      {editing && (
        <div className="keyword-edit">
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void edit("add");
            }}
          >
            <input
              aria-label="新标签名称"
              placeholder="添加技术词或个人工作标签"
              maxLength={64}
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
            <button aria-label="添加论文标签" disabled={busy || !name.trim()}>
              <Plus size={16} />
            </button>
          </form>
          {resource.data.tags.map((t: Tag) => (
            <div className="keyword-edit-row" key={t.id}>
              <span>{t.name}</span>
              {!t.manual && (
                <button disabled={busy} onClick={() => edit("confirm", t.id)}>
                  确认保留
                </button>
              )}
              <button
                disabled={busy}
                aria-label={"从本篇移除 " + t.name}
                onClick={() => edit("remove", t.id)}
              >
                <X size={14} />
              </button>
            </div>
          ))}
          {!!resource.data.excludedCount && (
            <button
              className="text-button"
              disabled={busy}
              onClick={() => edit("reset")}
            >
              重置 {resource.data.excludedCount} 项移除纠正
            </button>
          )}
        </div>
      )}
      <div className="keyword-actions">
        <button onClick={() => setGenerate(true)}>
          <Sparkles size={14} />
          整理标签
        </button>
        <small>
          {statuses[resource.data.task?.status] ||
            statuses[resource.data.status]}
        </small>
      </div>
      <Status
        error={failure || resource.error}
        retry={() => {
          setFailure("");
          resource.refresh();
        }}
      />
      {operation && (
        <Undo
          id={operation}
          onDone={() => {
            setOperation("");
            resource.refresh();
            onChanged();
          }}
        />
      )}
      {generate && (
        <KeywordBatchDialog
          selection={{ paperIds: [id] }}
          onClose={() => setGenerate(false)}
          onChanged={() => {
            resource.refresh();
            onChanged();
          }}
          onCreated={() => {
            setGenerate(false);
            resource.refresh();
            onTasks();
          }}
        />
      )}
    </section>
  );
}

export function KeywordSettings() {
  const r = useResource<any>("/api/keywords/settings", null);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <section className="settings-card">
      <h3>关键词整理</h3>
      <p>新论文入库后在后台使用现有本地内容提取标签，不调用模型。</p>
      {r.data && (
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={r.data.automatic}
            disabled={busy}
            onChange={async (e) => {
              setBusy(true);
              try {
                r.setData(
                  await api("/api/keywords/settings", "PUT", {
                    automatic: e.target.checked,
                  }),
                );
                setError("");
              } catch (x) {
                setError(errorText(x));
              } finally {
                setBusy(false);
              }
            }}
          />
          自动整理新入库与内容更新后的论文
        </label>
      )}
      <small>关闭后保留已有标签。模型增强始终需要明确提交。</small>
      <Status error={error || r.error} />
    </section>
  );
}

export function TagManager({
  onClose,
  onChanged,
  onMerged,
}: {
  onClose: () => void;
  onChanged: () => void;
  onMerged: (from: string, to?: Tag) => void;
}) {
  const [query, setQuery] = useState(""),
    [deleted, setDeleted] = useState(false),
    [selected, setSelected] = useState<Tag | null>(null),
    [name, setName] = useState(""),
    [aliases, setAliases] = useState(""),
    [target, setTarget] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [operation, setOperation] = useState(""),
    [confirm, setConfirm] = useState(false);
  const r = useResource<{ tags: Tag[] }>(
    "/api/tags?deleted=" + (deleted ? "1" : "0"),
    { tags: [] },
  );
  const history = useResource<any>("/api/tags/operations", { operations: [] });
  const visible = r.data.tags.filter((t) =>
    [t.name, ...(t.aliases || [])]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  function choose(t: Tag) {
    setSelected(t);
    setName(t.name);
    setAliases((t.aliases || []).join("\n"));
    setTarget("");
    setError("");
    setConfirm(false);
  }
  async function edit(action: string) {
    if (!selected || busy) return;
    setBusy(true);
    setError("");
    try {
      const to = r.data.tags.find((t) => t.id === target);
      const result = await api(
        "/api/tags/" + encodeURIComponent(selected.id),
        "PATCH",
        {
          action,
          revision: selected.revision,
          ...(action === "rename" ? { name } : {}),
          ...(action === "aliases"
            ? {
                aliases: aliases
                  .split("\n")
                  .map((v) => v.trim())
                  .filter(Boolean),
              }
            : {}),
          ...(action === "merge"
            ? { targetId: target, targetRevision: to?.revision }
            : {}),
        },
      );
      setOperation(result.operationId || "");
      if (action === "merge" || action === "delete")
        onMerged(selected.id, action === "merge" ? to : undefined);
      setSelected(null);
      r.refresh();
      history.refresh();
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="标签管理" onClose={onClose} wide>
      <div className="tag-manager-toolbar">
        <input
          aria-label="搜索标签和别名"
          placeholder="搜索标签和别名"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button
          onClick={() => {
            setDeleted(!deleted);
            setSelected(null);
          }}
        >
          {deleted ? "查看使用中的标签" : "已删除标签"}
        </button>
      </div>
      <div className="tag-manager-layout">
        <div className="tag-catalog" aria-label="标签列表">
          {visible.map((t) => (
            <button
              className={selected?.id === t.id ? "selected" : ""}
              key={t.id}
              onClick={() => choose(t)}
            >
              <span>{t.name}</span>
              <small>{t.count} 篇</small>
            </button>
          ))}
          {!visible.length && (
            <p className="muted">
              {deleted ? "没有已删除标签" : "没有匹配标签"}
            </p>
          )}
        </div>
        <div className="tag-editor">
          {selected ? (
            <>
              <Field label="标签名称">
                <input
                  maxLength={64}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
              {deleted ? (
                <>
                  <p>
                    恢复标签名称后，可以重新添加关联。恢复原操作中的关联请使用下方撤销。
                  </p>
                  <button disabled={busy} onClick={() => edit("restore")}>
                    恢复标签
                  </button>
                </>
              ) : (
                <>
                  <button
                    disabled={busy || !name.trim()}
                    onClick={() => edit("rename")}
                  >
                    保存名称
                  </button>
                  <Field label="别名（每行一个）">
                    <textarea
                      rows={4}
                      value={aliases}
                      onChange={(e) => setAliases(e.target.value)}
                    />
                  </Field>
                  <button disabled={busy} onClick={() => edit("aliases")}>
                    保存别名
                  </button>
                  <Field label="合并到">
                    <select
                      value={target}
                      onChange={(e) => setTarget(e.target.value)}
                    >
                      <option value="">选择同义标签</option>
                      {r.data.tags
                        .filter((t) => t.id !== selected.id)
                        .map((t) => (
                          <option key={t.id} value={t.id}>
                            {t.name}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <button
                    disabled={busy || !target}
                    onClick={() => edit("merge")}
                  >
                    合并关联与别名
                  </button>
                  <div className="tag-danger-zone">
                    {confirm ? (
                      <>
                        <p>
                          从全库移除“{selected.name}”，影响 {selected.count}{" "}
                          篇论文。论文和文件保留。
                        </p>
                        <button
                          className="danger"
                          disabled={busy}
                          onClick={() => edit("delete")}
                        >
                          确认删除全库标签
                        </button>
                      </>
                    ) : (
                      <button
                        className="text-button danger"
                        onClick={() => setConfirm(true)}
                      >
                        <Trash2 size={14} />
                        删除全库标签
                      </button>
                    )}
                  </div>
                </>
              )}
            </>
          ) : (
            <p className="muted">
              选择标签以修改名称、维护别名或合并。相近概念不一定同义。
            </p>
          )}
          <Status
            error={error || r.error}
            retry={() => {
              r.refresh();
              setSelected(null);
              setError("");
            }}
          />
        </div>
      </div>
      {operation && (
        <Undo
          id={operation}
          onDone={() => {
            setOperation("");
            r.refresh();
            history.refresh();
            onChanged();
          }}
        />
      )}
      <details className="keyword-history">
        <summary>最近操作与撤销</summary>
        {history.data.operations.map((op: any) => (
          <div key={op.id}>
            <span>
              {dateText(op.created_at)} ·{" "}
              {(
                {
                  rename: "改名",
                  merge: "合并",
                  delete: "删除",
                  aliases: "别名",
                  restore: "恢复",
                  paper_add: "添加",
                  paper_remove: "移除",
                  paper_clear: "清空",
                  paper_confirm: "确认",
                  paper_reset: "重置",
                } as any
              )[op.kind] || "标签编辑"}
            </span>
            <button
              disabled={op.undone === 1 || busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api(
                    "/api/tags/operations/" + op.id + "/undo",
                    "POST",
                    {},
                  );
                  setSelected(null);
                  r.refresh();
                  history.refresh();
                  onChanged();
                } catch (e) {
                  setError(errorText(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              {op.undone ? "已撤销" : "撤销"}
            </button>
          </div>
        ))}
      </details>
    </Modal>
  );
}

export function KeywordBatchDialog({
  selection,
  onClose,
  onCreated,
  onChanged,
  initialMethod = "local",
}: {
  selection: Record<string, unknown>;
  initialMethod?: string;
  onClose: () => void;
  onCreated: () => void;
  onChanged: () => void;
}) {
  const [action, setAction] = useState("generate"),
    [method, setMethod] = useState(initialMethod),
    [inputScope, setInputScope] = useState("available"),
    [preview, setPreview] = useState<any>(null),
    [name, setName] = useState(""),
    [tagId, setTagId] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [operation, setOperation] = useState("");
  const tags = useResource<{ tags: Tag[] }>("/api/tags", { tags: [] });
  useEffect(() => {
    const c = new AbortController();
    setPreview(null);
    setError("");
    api(
      "/api/keywords/preview",
      "POST",
      {
        ...selection,
        inputScope,
        method: action === "generate" ? method : "local",
      },
      c.signal,
    )
      .then((x) => {
        if (!c.signal.aborted) setPreview(x);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      });
    return () => c.abort();
  }, [selection, method, action, inputScope]);
  async function submit() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      if (action === "generate") {
        await api("/api/keywords/jobs", "POST", {
          ...selection,
          method,
          inputScope,
          ...(method === "model" ? { previewKey: preview.previewKey } : {}),
        });
        onCreated();
      } else {
        const result = await api("/api/tags/batch", "POST", {
          ...selection,
          action,
          revisions: preview.revisions,
          ...(action === "add" ? { name } : {}),
          ...(action === "remove" ? { tagId } : {}),
        });
        setOperation(result.operationId || "");
        onChanged();
        setPreview(null);
      }
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="整理关键词标签" onClose={onClose}>
      <Field label="操作">
        <select
          value={action}
          onChange={(e) => {
            setAction(e.target.value);
            setOperation("");
          }}
        >
          <option value="generate">自动提取标签</option>
          <option value="add">批量添加标签</option>
          <option value="remove">从所选论文移除标签</option>
          <option value="clear">清空所选论文标签</option>
        </select>
      </Field>
      {action === "generate" && (
        <Field label="提取方式">
          <select value={method} onChange={(e) => setMethod(e.target.value)}>
            <option value="local">本地整理 · 不调用模型</option>
            <option value="model">模型增强 · 明确提交后调用</option>
          </select>
        </Field>
      )}
      {action === "generate" && method === "model" && (
        <Field label="增强使用内容">
          <select
            value={inputScope}
            onChange={(e) => setInputScope(e.target.value)}
          >
            <option value="available">已确认信息，必要时使用已有原文</option>
            <option value="metadata">仅已确认标题和作者摘要</option>
          </select>
        </Field>
      )}
      {action === "add" && (
        <Field label="标签名称">
          <input
            maxLength={64}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </Field>
      )}
      {action === "remove" && (
        <Field label="要移除的标签">
          <select value={tagId} onChange={(e) => setTagId(e.target.value)}>
            <option value="">选择标签</option>
            {tags.data.tags.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </Field>
      )}
      {preview && (
        <div className="keyword-preview">
          <strong>已固定 {preview.count} 篇论文</strong>
          {action === "generate" ? (
            method === "model" ? (
              <>
                <p>模型：{preview.model}</p>
                <p>
                  最多 {preview.requests} 次请求，输入上限{" "}
                  {preview.maximumInputTokens}、输出上限{" "}
                  {preview.maximumOutputTokens} token；每次最多 120
                  秒，不自动重试。
                </p>
                {preview.items?.some((i: any) => i.overBudget && !i.cached) && (
                  <p className="error">
                    部分论文超出单次输入预算，本批不能提交。请缩小使用内容、选择其他论文，或改用本地整理。
                  </p>
                )}
                <p>已成功处理的相同内容将复用缓存。</p>
              </>
            ) : (
              <p>
                使用当前已确认信息和已有原文，不请求书目网站、模型、MinerU 或
                OCR。
              </p>
            )
          ) : (
            <p>人工操作受保护。移除或清空后，后台不会自动添加回来。</p>
          )}
        </div>
      )}
      <Status error={error} loading={!preview && !error && !operation} />
      {operation && (
        <Undo
          id={operation}
          onDone={() => {
            setOperation("");
            onChanged();
            onClose();
          }}
        />
      )}
      <footer>
        <button onClick={onClose}>关闭</button>
        <button
          className="primary"
          disabled={
            !preview ||
            busy ||
            (action === "add" && !name.trim()) ||
            (action === "remove" && !tagId) ||
            preview?.items?.some((i: any) => i.overBudget && !i.cached)
          }
          onClick={submit}
        >
          {busy
            ? "提交中…"
            : action === "generate"
              ? "开始整理"
              : "应用到所选论文"}
        </button>
      </footer>
    </Modal>
  );
}

export function KeywordTask({ id }: { id: string }) {
  const r = useResource<any>(
    "/api/keywords/jobs/" + encodeURIComponent(id),
    null,
  );
  const [after, setAfter] = useState(0),
    [error, setError] = useState(""),
    [retrySelection, setRetrySelection] = useState<Record<
      string,
      unknown
    > | null>(null);
  const details = useResource<any>(
    "/api/keywords/jobs/" + encodeURIComponent(id) + "?after=" + after,
    null,
  );
  useEffect(() => {
    const timer = setInterval(() => {
      r.refresh();
      details.refresh();
    }, 2500);
    return () => clearInterval(timer);
  }, [id, after]);
  return (
    <>
      <Status error={error || r.error} />
      {r.data && (
        <>
          <p>
            {r.data.method === "model" ? "模型增强" : "本地整理"} ·{" "}
            {statuses[r.data.status]} · {r.data.completed} / {r.data.total}
          </p>
          <div className="keyword-counts">
            {Object.entries(r.data.counts).map(([s, n]) => (
              <span key={s}>
                {statuses[s] || s}：{String(n)}
              </span>
            ))}
          </div>
          <div className="action-row">
            <button
              disabled={r.data.status !== "running"}
              onClick={async () => {
                try {
                  await api("/api/keywords/jobs/" + id + "/cancel", "POST", {});
                  r.refresh();
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              取消未完成任务
            </button>
            <button
              disabled={r.data.status === "running"}
              onClick={async () => {
                try {
                  if (r.data.method === "model") {
                    const all = [];
                    let cursor = 0;
                    do {
                      const p = await api(
                        "/api/keywords/jobs/" +
                          id +
                          "?after=" +
                          cursor +
                          "&limit=100",
                      );
                      all.push(...p.items);
                      cursor = p.next;
                    } while (cursor !== null);
                    setRetrySelection({
                      paperIds: all
                        .filter((i: any) =>
                          [
                            "failed",
                            "stale",
                            "interrupted",
                            "cancelled",
                          ].includes(i.status),
                        )
                        .map((i: any) => i.paper_id),
                    });
                  } else {
                    await api(
                      "/api/keywords/jobs/" + id + "/retry",
                      "POST",
                      {},
                    );
                    r.refresh();
                  }
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              重试未完成项
            </button>
          </div>
          <small>
            取消会停止后续请求。已发送的模型请求可能仍由供应商处理。
          </small>
        </>
      )}
      {details.data && (
        <>
          <div className="keyword-task-items">
            {details.data.items.map((i: any) => (
              <div key={i.id}>
                <span>{i.title || "已删除论文"}</span>
                <strong>{statuses[i.status]}</strong>
                <small>{i.requests} 次模型请求</small>
                {i.error && <p>{errorText({ code: i.error })}</p>}
              </div>
            ))}
          </div>
          <div className="pagination">
            <button
              disabled={!after}
              onClick={() => setAfter(Math.max(0, after - 50))}
            >
              上一页
            </button>
            <button
              disabled={details.data.next === null}
              onClick={() => setAfter(details.data.next)}
            >
              下一页
            </button>
          </div>
        </>
      )}
      {retrySelection && (
        <KeywordBatchDialog
          initialMethod="model"
          selection={retrySelection}
          onClose={() => setRetrySelection(null)}
          onCreated={() => {
            setRetrySelection(null);
            r.refresh();
          }}
          onChanged={r.refresh}
        />
      )}
    </>
  );
}
