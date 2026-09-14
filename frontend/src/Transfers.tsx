import { MetadataTask } from "./Metadata";
import { ProcessingTaskDetails, processingNames } from "./Processing";
import { useEffect, useState } from "react";
import {
  Upload,
  FileText,
  Link,
  Download,
  RefreshCw,
  CheckCircle,
  Clock,
} from "lucide-react";
import { api, upload, Modal, Field, Status, useResource } from "./ui";
import { errorText } from "./api";
import { CategoryOptions, type Category } from "./Library";
export type LocalTask = {
  id: string;
  kind: "upload" | "import" | "export" | "analysis";
  label: string;
};
export function ImportDialog({
  tree,
  onClose,
  onTask,
}: {
  tree: Category;
  onClose: () => void;
  onTask: (t: LocalTask) => void;
}) {
  const [kind, setKind] = useState("pdf"),
    [files, setFiles] = useState<File[]>([]),
    [target, setTarget] = useState("reading_list_temp"),
    [url, setUrl] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (kind === "arxiv") {
        const r = await api("/api/upload/arxiv", "POST", {
          arxiv_url: /^https?:/.test(url)
            ? url
            : "https://arxiv.org/abs/" + url,
          category_id: target,
          use_temp_dir: target === "reading_list_temp",
        });
        if (r.task_id)
          onTask({ id: r.task_id, kind: "upload", label: "arXiv 导入" });
      } else {
        for (const file of files) {
          const data = new FormData();
          data.set("file", file);
          data.set(
            kind === "zotero" ? "target_category_id" : "category_id",
            target,
          );
          const r = await upload(
            kind === "pdf"
              ? "/api/upload"
              : kind === "zotero"
                ? "/api/import/zotero"
                : "/api/import/from-export",
            data,
          );
          if (r.task_id)
            onTask({
              id: r.task_id,
              kind: kind === "pdf" ? "upload" : "import",
              label: file.name,
            });
        }
      }
      onClose();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="导入文献" onClose={onClose} wide>
      <div className="segmented">
        {[
          ["pdf", "PDF 文件"],
          ["arxiv", "arXiv 链接"],
          ["zotero", "Zotero RDF"],
          ["backup", "元数据备份"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={kind === id ? "active" : ""}
            onClick={() => {
              setKind(id);
              setFiles([]);
              if (id === "zotero") setTarget("root");
            }}
            disabled={busy}
          >
            {label}
          </button>
        ))}
      </div>
      <form onSubmit={submit}>
        {kind === "arxiv" ? (
          <Field label="arXiv 链接或编号">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              required
              placeholder="https://arxiv.org/abs/…"
            />
          </Field>
        ) : (
          <label
            className="drop-zone"
            onDragOver={(e) => e.preventDefault()}
            onDrop={(e) => {
              e.preventDefault();
              if (!busy) setFiles(Array.from(e.dataTransfer.files));
            }}
          >
            <Upload size={34} />
            <strong>选择文件，或拖放到这里</strong>
            <span>
              {kind === "pdf"
                ? "支持多个 PDF，依次导入"
                : kind === "zotero"
                  ? "选择从 Zotero 导出的 RDF 文件"
                  : "选择 iPaper 元数据 ZIP 备份"}
            </span>
            <input
              type="file"
              aria-label="导入文件"
              accept={
                kind === "pdf" ? ".pdf" : kind === "zotero" ? ".rdf" : ".zip"
              }
              multiple={kind === "pdf"}
              onChange={(e) => setFiles(Array.from(e.target.files || []))}
            />
          </label>
        )}
        {files.length > 0 && (
          <ul className="file-list">
            {files.map((f, i) => (
              <li key={i}>
                <FileText size={16} />
                {f.name}
                <small>{Math.ceil(f.size / 1024)} KB</small>
              </li>
            ))}
          </ul>
        )}
        {kind !== "backup" && (
          <Field label="存入">
            <select value={target} onChange={(e) => setTarget(e.target.value)}>
              {kind !== "zotero" && (
                <option value="reading_list_temp">Reading List</option>
              )}
              <CategoryOptions tree={tree} />
            </select>
          </Field>
        )}
        {kind === "backup" && (
          <p className="muted">
            按备份中的分类结构恢复元数据；不会搬入备份中的服务器路径。
          </p>
        )}
        <p className="muted">导入后可整理、阅读文献，并按需启动翻译或问答。</p>
        <Status error={error} />
        <footer>
          <button type="button" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            className="primary"
            disabled={busy || (kind !== "arxiv" && !files.length)}
          >
            {busy ? "正在提交…" : "开始导入"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
const names: Record<string, string> = {
  ...processingNames,
  queued: "排队中",
  pending: "等待中",
  running: "处理中",
  validating: "正在校验",
  starting: "准备导入",
  cancelling: "正在取消",
  dispatching: "正在分配",
  completed: "已完成",
  failed: "失败",
  error: "失败",
  cancelled: "已取消",
  paused: "已暂停",
  recovering: "恢复中",
};
export function Tasks({
  localTasks,
  onRead,
  onTask,
}: {
  localTasks: LocalTask[];
  onRead: (id: string) => void;
  onTask: (t: LocalTask) => void;
}) {
  const translations = useResource<any>("/api/translations", { tasks: [] }),
    metadata = useResource<any>("/api/metadata/jobs", {jobs:[]}),
    structured = useResource<any>("/api/processing/jobs", { jobs: [] }),
    analysis = useResource<any>("/api/paper/analyze/active", { tasks: [] }),
    [selected, setSelected] = useState<any>(null),
    [error, setError] = useState("");
  useEffect(() => {
    const timer = setInterval(() => {
      translations.refresh();
      structured.refresh();
      metadata.refresh();
      analysis.refresh();
    }, 5000);
    return () => clearInterval(timer);
  }, []);
  const tasks = [
    ...metadata.data.jobs.map((j:any)=>({...j,kind:"metadata",task_id:j.id,title:`论文信息补全 · ${j.completed} / ${j.total}`})),
    ...structured.data.jobs.map((j: any) => ({
      ...j,
      kind: "structure",
      task_id: j.id,
      paper_id: j.paperId,
      title:
        (
          {
            overview: "AI 概览",
            interpretation: "深度解读",
            analysis_export: "分析导出",
          } as any
        )[j.kind] || "结构解析与翻译",
    })),
    ...(translations.data.tasks || []).map((t: any) => ({
      ...t,
      kind: "translation",
      task_id: t.job_id,
    })),
    ...(analysis.data.tasks || []).map((t: any) => ({
      ...t,
      kind: "analysis",
    })),
    ...localTasks
      .filter(
        (t) =>
          t.kind !== "analysis" ||
          !(analysis.data.tasks || []).some((a: any) => a.task_id === t.id),
      )
      .map((t) => ({ ...t, task_id: t.id, status: "", title: t.label })),
  ].filter(
    (t, index, all) =>
      all.findIndex((v) => v.kind === t.kind && v.task_id === t.task_id) ===
      index,
  );
  return (
    <section className="utility-page">
      <header className="page-heading">
        <div>
          <span className="eyebrow">DOCUMENT PIPELINE</span>
          <h1>任务中心</h1>
          <p>查看文献处理进度、历史结果和日志。</p>
        </div>
        <button
          onClick={() => {
            translations.refresh();
            analysis.refresh();
          }}
        >
          <RefreshCw size={16} />
          刷新
        </button>
        <button
          onClick={async () => {
            try {
              const r = await api("/api/export/start", "POST", {});
              onTask({
                id: r.task_id,
                kind: "export",
                label: "导出文献元数据",
              });
            } catch (e) {
              setError(errorText(e));
            }
          }}
        >
          <Download size={16} />
          导出元数据
        </button>
      </header>
      <Status error={error || translations.error || analysis.error || metadata.error} />
      <div className="task-list">
        {tasks.map((t: any) => (
          <button
            className="task-card"
            key={t.kind + t.task_id}
            onClick={() => setSelected(t)}
          >
            <Clock size={20} />
            <span>
              <strong>
                {t.title ||
                  t.paper_title ||
                  t.label ||
                  ({ translation: "全文翻译", analysis: "解析与分析" } as any)[
                    t.kind
                  ] ||
                  "文献处理"}
              </strong>
              <small>
                {names[t.status] || "查看进度"} · {t.task_id.slice(0, 8)}
              </small>
            </span>
            <span
              className={"badge " + (t.status === "completed" ? "success" : "")}
            >
              {names[t.status] || "查看"}
            </span>
          </button>
        ))}
      </div>
      {!tasks.length && (
        <div className="empty-state">
          <CheckCircle size={38} />
          <h3>暂无处理任务</h3>
          <p>从文献详情开始翻译或分析，进度将在这里显示。</p>
        </div>
      )}
      {selected?.kind === "structure" && (
        <Modal title="结构处理任务" onClose={() => setSelected(null)} wide>
          <ProcessingTaskDetails id={selected.task_id} />
          <button
            onClick={() => {
              onRead(selected.paper_id);
              setSelected(null);
            }}
          >
            阅读处理结果
          </button>
        </Modal>
      )}
      {selected?.kind === "metadata" && <Modal title="论文信息补全" onClose={()=>setSelected(null)} wide><MetadataTask id={selected.task_id}/></Modal>}
      {selected && !["structure","metadata"].includes(selected.kind) && (
        <TaskDetails
          task={selected}
          onClose={() => {
            setSelected(null);
            translations.refresh();
            analysis.refresh();
          }}
          onRead={onRead}
        />
      )}
    </section>
  );
}
function TaskDetails({
  task,
  onClose,
  onRead,
}: {
  task: any;
  onClose: () => void;
  onRead: (id: string) => void;
}) {
  const id = encodeURIComponent(task.task_id),
    kind = task.kind;
  const path =
    kind === "translation"
      ? "/api/translations/" + id
      : kind === "analysis"
        ? "/api/paper/analyze/" + id + "/logs"
        : kind === "export"
          ? "/api/export/status/" + id
          : kind === "upload"
            ? "/api/upload/" + id
            : null;
  const jsonState = useResource<any>(path, {}),
    importState = useImportProgress(kind === "import" ? id : null),
    state = kind === "import" ? importState : jsonState,
    [error, setError] = useState("");
  useEffect(() => {
    if (kind === "import") return;
    const t = setInterval(state.refresh, 2000);
    return () => clearInterval(t);
  }, [kind]);
  async function act(action: string) {
    try {
      if (kind === "translation")
        await api(
          "/api/translations/" + id + (action === "cancel" ? "" : "/" + action),
          action === "cancel" ? "DELETE" : "POST",
          {},
        );
      else if (kind === "upload") await api("/api/upload/" + id, "DELETE");
      else
        await api(
          kind === "analysis"
            ? "/api/paper/analyze/" + id + "/cancel"
            : kind === "export"
              ? "/api/export/cancel/" + id
              : "/api/import/zotero/cancel/" + id,
          "POST",
          {},
        );
      state.refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  const d = state.data.task || state.data;
  const status = d.status || "";
  return (
    <Modal title="任务详情" onClose={onClose} wide>
      <Status error={error || state.error} loading={state.loading} />
      <p>
        <span className="badge">{names[status] || "正在查询"}</span>{" "}
        {d.step || ""}
      </p>
      <progress
        max={100}
        value={typeof d.progress === "number" ? d.progress : 0}
      />
      {kind === "import" && (
        <p>
          {names[d.status] || "等待导入进度"} · {Number(d.current) || 0}/
          {Number(d.total) || 0}，已导入 {Number(d.success_count) || 0} 篇
        </p>
      )}
      <div className="task-log">
        {(d.logs || d.events || []).map((line: any, i: number) => (
          <p key={i}>
            {typeof line === "string"
              ? line
              : line.message || line.text || line.event || ""}
          </p>
        ))}
        {d.error && (
          <p role="alert">
            {typeof d.error === "string" ? d.error : "任务处理失败"}
          </p>
        )}
      </div>
      <div className="action-row">
        {!["completed", "failed", "error", "cancelled"].includes(status) && (
          <button onClick={() => act("cancel")}>取消任务</button>
        )}
        {kind === "translation" && (
          <>
            {["running", "queued"].includes(status) && (
              <button onClick={() => act("pause")}>暂停</button>
            )}
            {status === "paused" && (
              <button onClick={() => act("resume")}>继续</button>
            )}
            {status === "failed" && (
              <button onClick={() => act("retry")}>重试</button>
            )}
          </>
        )}
        {status === "completed" && kind === "export" && (
          <a className="button primary" href={"/api/export/download/" + id}>
            下载元数据
          </a>
        )}
        {(d.paper_id || d.paper?.id || task.paper_id) && (
          <button
            onClick={() => {
              onRead(d.paper_id || d.paper?.id || task.paper_id);
              onClose();
            }}
          >
            打开论文
          </button>
        )}
        <button onClick={state.refresh}>刷新进度</button>
      </div>
    </Modal>
  );
}

function useImportProgress(id: string | null) {
  const [data, setData] = useState<any>({}),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [revision, setRevision] = useState(0);
  useEffect(() => {
    setData({});
    setError("");
    if (!id) return;
    setLoading(true);
    const source = new EventSource("/api/import/zotero/progress/" + id, {
      withCredentials: true,
    });
    let active = true;
    source.onmessage = (event) => {
      if (!active) return;
      try {
        if (event.data.length > 65536) throw Error();
        const value = JSON.parse(event.data);
        if (!value || typeof value.status !== "string") throw Error();
        setData(value);
        setLoading(false);
        if (["completed", "error", "cancelled"].includes(value.status))
          source.close();
      } catch {
        source.close();
        setError("导入进度格式异常，请刷新重试。");
        setLoading(false);
      }
    };
    source.onerror = () => {
      if (!active) return;
      source.close();
      setLoading(false);
      setError("导入进度连接中断，请刷新重试。");
      void api("/api/auth/session")
        .then((s) => {
          if (active && !s.authenticated)
            window.dispatchEvent(new Event("ipaper-session-expired"));
        })
        .catch(() => {});
    };
    return () => {
      active = false;
      source.close();
    };
  }, [id, revision]);
  return { data, error, loading, refresh: () => setRevision((n) => n + 1) };
}
