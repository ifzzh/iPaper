import { useEffect, useState, useRef } from "react";
import { Folder, Plus, Settings2, X } from "lucide-react";
import { api, useResource, Modal, Field, Status } from "./ui";
import { errorText } from "./api";

export type Topic = { id: string; name: string; parent_id: string | null; revision: number; count: number; manual?: boolean; definition_id?: string | null; sort_order?: number };
type Catalog = { redirects?: Record<string,string|null>; deleted?: Topic[]; topics: Topic[]; unorganized: number; total: number; operations: { id: string; kind: string; undone: boolean }[] };
const empty: Catalog = { topics: [], unorganized: 0, total: 0, operations: [] };
function ordered(topics: Topic[], parent: string | null = null, depth = 0): { topic: Topic; depth: number }[] {
  if (depth > 12) return [];
  return topics.filter(t => t.parent_id === parent).sort((a,b) => (a.sort_order || 0)-(b.sort_order || 0) || a.id.localeCompare(b.id)).flatMap(topic => [{ topic, depth }, ...ordered(topics, topic.id, depth + 1)]);
}

export function TopicSidebar({ filter, onFilter, changed, collapsed = [], onCollapsed = () => {} }: { filter: string; onFilter: (s: string) => void; changed: number; collapsed?: string[]; onCollapsed?: (ids: string[]) => void }) {
  const data = useResource<Catalog>("/api/topics", empty);
  const [manage, setManage] = useState(false);
  const selected = filter.startsWith("topic:") ? filter.slice(6).split(",") : [];
  function toggle(id: string) {const next = selected.includes(id) ? selected.filter(t => t !== id) : [...selected, id]; onFilter(next.length ? "topic:" + next.join(",") : "all");}
  // Refresh when an action reports a change. The first run is skipped: the hook
  // already fetches on mount, and the extra call doubled every initial load.
  const firstChange = useRef(true);
  useEffect(() => {
    if (firstChange.current) {
      firstChange.current = false;
      return;
    }
    data.refresh();
  }, [changed]);
  useEffect(() => {
    if(!selected.length) return;
    const redirects=data.data.redirects || {};
    const next=Array.from(new Set(selected.flatMap(id => id in redirects ? redirects[id] ? [redirects[id]!] : [] : [id])));
    if(next.join()!==selected.join()) onFilter(next.length ? 'topic:'+next.join(',') : 'all');
  }, [data.data]);
  useEffect(() => {
    // Background organisation can add topics, but a 5s poll of an always-mounted
    // sidebar piled requests up and flashed the blocking spinner. Poll slowly,
    // only while the tab is visible and never while a request is in flight.
    const timer = setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (data.loading) return;
      data.refresh();
    }, 30000);
    return () => clearInterval(timer);
  }, [data.loading]);
  function visible(t: Topic) {let parent=t.parent_id; for(let i=0;parent && i<12;i++){if(collapsed.includes(parent)) return false; parent=data.data.topics.find(n=>n.id===parent)?.parent_id || null;}return true;}
  return <>
    <div className="section-label">研究主题 {filter !== "all" && <button onClick={() => onFilter("all")}>清除筛选</button>} <button className="icon-button" aria-label="管理主题" onClick={() => setManage(true)}><Settings2 size={15}/></button></div>
    <Status loading={data.loading && !data.loaded} error={data.error} retry={data.refresh}/>
    <nav className="category-tree" aria-label="研究主题">
      <div className={"category-row" + (filter === "unorganized" ? " active" : "")}><button onClick={() => onFilter("unorganized")}><Folder size={16}/><span>待整理</span><small>{data.data.unorganized}</small></button></div>
      {ordered(data.data.topics).filter(({topic})=>visible(topic)).map(({ topic, depth }) => <div key={topic.id} className={"category-row topic-row depth-" + Math.min(depth, 4) + (selected.includes(topic.id) ? " active" : "")}>{data.data.topics.some(n=>n.parent_id===topic.id) && <button className="topic-collapse" aria-label={(collapsed.includes(topic.id)?"展开主题 ":"折叠主题 ")+topic.name} onClick={()=>onCollapsed(collapsed.includes(topic.id)?collapsed.filter(id=>id!==topic.id):[...collapsed,topic.id])}>{collapsed.includes(topic.id)?"▸":"▾"}</button>}<input type="checkbox" aria-label={"组合主题 " + topic.name} checked={selected.includes(topic.id)} onChange={() => toggle(topic.id)}/><button onClick={() => onFilter("topic:" + topic.id)}><Folder size={16}/><span>{topic.name}</span><small>{topic.count}</small></button></div>)}
      {data.loaded && !data.data.topics.length && <p className="muted">尚无研究主题。整理已入库论文后会形成有依据的方向，也可手动创建。</p>}
    </nav>
    {manage && <TopicManager onClose={() => { setManage(false); data.refresh(); }}/ >}
  </>;
}

export function TopicManager({ onClose }: { onClose: () => void }) {
  const catalog = useResource<Catalog>("/api/topics", empty);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const [target, setTarget] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState("");
  const definitions = useResource<{id: string; name: string}[]>("/api/topics/definitions", []);
  const settings = useResource<{automatic: boolean}>("/api/topics/settings", {automatic: true});
  const [definition, setDefinition] = useState("");
  const [position, setPosition] = useState(0);
  const [expansion, setExpansion] = useState<any>(null);
  const topic = catalog.data.topics.find(t => t.id === selected);
  useEffect(() => { setName(topic?.name || ""); setParent(topic?.parent_id || ""); setDefinition(topic?.definition_id || ""); setPosition(topic?.sort_order || 0); }, [selected, topic?.revision]);
  async function perform(operation: () => Promise<unknown>) {
    setBusy(true); setFailure("");
    try { await operation(); catalog.refresh(); } catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  function edit(action: string, extra = {}) { return api("/api/topics/" + selected, "PATCH", { action, revision: topic?.revision, ...extra }); }
  return <Modal title="管理研究主题" onClose={() => {if(!busy) onClose();}}>
    <Status loading={busy || catalog.loading} error={failure || catalog.error} retry={catalog.refresh}/>
    <label><input type="checkbox" checked={settings.data.automatic} disabled={busy} onChange={e => perform(async () => {await api("/api/topics/settings", "PUT", {automatic: e.target.checked}); settings.refresh();})}/>新入库或已采用内容变化后自动整理</label>
    <button disabled={busy} onClick={() => perform(async () => setExpansion(await api("/api/topics/expand/preview", "POST", {})))}>检查可扩展方向</button>
    {expansion && <section><p>{expansion.definitions.length ? "新增或关联方向：" + expansion.definitions.map((d: any) => d.name).join("、") : "没有需要新增的可靠方向。"}</p>{!!expansion.definitions.length && <button disabled={busy} onClick={() => perform(async () => {await api("/api/topics/expand", "POST", {revision: expansion.revision, definitionIds: expansion.definitions.map((d: any) => d.id)}); setExpansion(null);})}>确认扩展体系</button>}</section>}
    <Field label="主题"><select value={selected} onChange={e => setSelected(e.target.value)}><option value="">创建新主题</option>{ordered(catalog.data.topics).map(({ topic: t, depth }) => <option key={t.id} value={t.id}>{"　".repeat(depth)}{t.name}（{t.count} 篇）</option>)}</select></Field>
    <Field label="名称"><input maxLength={64} value={name} onChange={e => setName(e.target.value)}/></Field>
    <Field label="上级主题"><select value={parent} onChange={e => setParent(e.target.value)}><option value="">一级主题</option>{catalog.data.topics.filter(t => t.id !== selected).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
    <div className="actions"><button disabled={busy || !name.trim()} onClick={() => perform(() => selected ? edit("rename", {name}) : api("/api/topics", "POST", { name, parentId: parent || null }))}>{selected ? "保存名称" : "创建主题"}</button>{selected && <button disabled={busy} onClick={() => perform(() => edit("reparent", {parentId: parent || null}))}>调整上级</button>}</div>
    {topic && <>
      <div className="actions"><a href={"/api/topics/"+topic.id+"/export"} download>下载主题 BibTeX</a><button disabled={busy} onClick={() => perform(async () => {const result=await api<{text:string}>("/api/topics/"+topic.id+"/export?format=arxiv"); await navigator.clipboard.writeText(result.text);})}>复制 arXiv 链接</button></div>
      <Field label="排列顺序"><input type="number" min={0} max={1000} value={position} onChange={e => setPosition(Number(e.target.value))}/></Field><button disabled={busy} onClick={() => perform(() => edit('order',{position}))}>保存顺序</button>
      <Field label="自动整理方向"><select disabled={busy} value={definition} onChange={e => setDefinition(e.target.value)}><option value="">仅人工维护</option>{definitions.data.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>
      <button disabled={busy} onClick={() => perform(() => edit("bind", {definitionId: definition || null}))}>保存方向绑定</button>
      <Field label="合并到"><select value={target} onChange={e => setTarget(e.target.value)}><option value="">选择保留的主题</option>{catalog.data.topics.filter(t => t.id !== selected).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select></Field>
      <button disabled={busy || !target} onClick={() => perform(async () => { await edit("merge", {targetId: target, targetRevision: catalog.data.topics.find(t => t.id === target)?.revision}); setSelected(target); })}>合并主题</button>
      <p className="muted">删除主题会保留论文，子主题上移一级。近期操作可以撤销；若随后有新修改，系统会拒绝覆盖。</p>
      <button className="danger" disabled={busy} onClick={() => perform(async () => { await edit("delete"); setSelected(""); })}>删除“{topic.name}”主题</button>
    </>}
    {!!catalog.data.deleted?.length && <details><summary>已删除的主题</summary>{catalog.data.deleted.map(t => <div key={t.id}>{t.name}<button disabled={busy} onClick={() => perform(() => api('/api/topics/'+t.id,'PATCH',{action:'restore',revision:t.revision}))}>恢复主题</button></div>)}<p>恢复主题不自动恢复已移除的论文归属。</p></details>}
    {catalog.data.operations.find(o => !o.undone) && <button disabled={busy} onClick={() => perform(() => api("/api/topics/operations/" + catalog.data.operations.find(o => !o.undone)!.id + "/undo", "POST", {}))}>撤销最近的主题操作</button>}
  </Modal>;
}

export function PaperTopics({ paperId, onChanged }: { paperId: string; onChanged: () => void }) {
  const catalog = useResource<Catalog>("/api/topics", empty);
  const state = useResource<{ revision: number; topics: Topic[] }>("/api/paper/" + encodeURIComponent(paperId) + "/topics", { revision: 0, topics: [] });
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => { setFailure(""); setMessage(""); }, [paperId]);
  async function edit(action: string, topicId?: string) {
    setBusy(true); setFailure("");
    try { await api("/api/paper/" + encodeURIComponent(paperId) + "/topics", "PATCH", { action, topicId, revision: state.data.revision }); state.refresh(); onChanged(); }
    catch (e) { setFailure(errorText(e)); } finally { setBusy(false); }
  }
  return <section aria-label="论文研究主题"><h4>研究主题</h4><Status loading={state.loading || busy} error={failure || state.error} retry={state.refresh}/>
    <div className="keyword-chips">{state.data.topics.map(topic => <span className="keyword-chip" key={topic.id}>{topic.name}<button className="icon-button" disabled={busy} aria-label={"移出主题 " + topic.name} onClick={() => edit("remove", topic.id)}><X size={12}/></button></span>)}</div>
    <select aria-label="加入研究主题" value="" disabled={busy} onChange={e => { if(e.target.value) void edit("add", e.target.value); }}><option value="">加入主题…</option>{catalog.data.topics.filter(t => !state.data.topics.some(p => p.id === t.id)).map(t => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
    <button disabled={busy} onClick={async () => { setBusy(true); setFailure(""); try { await api("/api/topics/jobs", "POST", { paperIds: [paperId] }); setMessage("主题整理已提交，可在任务中心查看进度。"); } catch(e) {setFailure(errorText(e));} finally {setBusy(false);} }}>整理主题</button>
    <button disabled={busy || !state.data.topics.length} onClick={() => edit("clear")}>清空主题</button>
    <button disabled={busy} onClick={() => edit("reset")}>重置自动归类保护</button>
    {message && <p role="status">{message}</p>}
  </section>;
}

export function TopicBatchDialog({ selection, onClose, onChanged }: { selection: Record<string, unknown>; onClose: () => void; onChanged: () => void }) {
  const catalog = useResource<Catalog>("/api/topics", empty);
  type Preview = {count: number; paperIds: string[]; revisions: Record<string, number>};
  const [preview, setPreview] = useState<Preview | null>(null);
  const [topicId, setTopicId] = useState("");
  const [failure, setFailure] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const attempt = useRef<{key: string; requestId: string} | null>(null);
  async function refreshPreview() {setFailure(""); try {setPreview(await api<Preview>("/api/topics/preview", "POST", selection)); attempt.current = null;} catch(e) {setFailure(errorText(e));}}
  useEffect(() => { let active = true; api<Preview>("/api/topics/preview", "POST", selection).then(p => {if(active) setPreview(p);}).catch(e => {if(active) setFailure(errorText(e));}); return () => {active = false;}; }, [selection]);
  async function execute(action: string) {
    if(!preview || busy) return;
    setBusy(true); setFailure("");
    try {
      if(action === "organize") { await api("/api/topics/jobs", "POST", {paperIds: preview.paperIds}); setMessage("已提交 " + preview.count + " 篇论文的本地主题整理；关闭此窗口不取消任务。"); }
      else {
        const payload = {paperIds: preview.paperIds, revisions: preview.revisions, action, topicId};
        const key = JSON.stringify(payload);
        if(attempt.current?.key !== key) attempt.current = {key, requestId: Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2,"0")).join("")};
        await api("/api/topics/batch", "POST", {...payload, requestId: attempt.current.requestId});
        setMessage("已保存 " + preview.count + " 篇论文的主题调整。");
        await refreshPreview();
      }
      onChanged();
    } catch(e) {setFailure(errorText(e) + "。若响应未确认，可重试同一操作；修订冲突时请重新核对。");} finally {setBusy(false);}
  }
  return <Modal title="整理论文主题" onClose={() => {if(!busy) onClose();}}>
    <Status loading={busy || (!preview && !failure)} error={failure}/>
    <p>本次固定选择 {preview?.count ?? "…"} 篇已入库论文。主题操作不移动文件。</p>
    {busy && <p role="status">正在保存，请等待确认后关闭。人工调整整体提交，冲突时不保存部分结果。</p>}
    <button disabled={busy || !preview} onClick={() => execute("organize")}>本地自动整理</button>
    <Field label="主题"><select disabled={busy} value={topicId} onChange={e => setTopicId(e.target.value)}><option value="">选择主题</option>{ordered(catalog.data.topics).map(({topic, depth}) => <option key={topic.id} value={topic.id}>{"　".repeat(depth)}{topic.name}</option>)}</select></Field>
    <div className="actions"><button disabled={busy || !preview || !topicId} onClick={() => execute("add")}>加入主题</button><button disabled={busy || !preview || !topicId} onClick={() => execute("remove")}>移出该主题分支</button><button disabled={busy} onClick={refreshPreview}>重新核对</button></div>
    {message && <p role="status">{message}</p>}
  </Modal>;
}

export function TopicTask({ id }: {id: string}) {
  const [offset, setOffset] = useState(0);
  const task = useResource<any>("/api/topics/jobs/" + encodeURIComponent(id) + "?offset=" + offset, null);
  const [failure, setFailure] = useState("");
  const names: Record<string,string> = { queued: "排队中", running: "整理中", completed: "已完成", reused: "已复用", unorganized: "待整理", partial: "部分完成", cancelled: "已取消", failed: "失败", stale: "内容已变化", deleted: "论文已删除" };
  useEffect(() => { const timer = setInterval(task.refresh, 2500); return () => clearInterval(timer); }, [id, offset]);
  async function perform(action: string) {try { await api("/api/topics/jobs/" + id + "/" + action, "POST", {}); task.refresh(); }catch(e) {setFailure(errorText(e));}}
  return <><Status loading={task.loading} error={failure || task.error} retry={task.refresh}/>{task.data && <>
    <p>本地主题整理 · {names[task.data.status]} · 共 {task.data.total} 篇</p>
    <div className="keyword-counts">{Object.entries(task.data.counts).map(([s, n]) => <span key={s}>{names[s] || "未知状态"}：{String(n)}</span>)}</div>
    <button disabled={task.data.status !== "running"} onClick={() => perform("cancel")}>取消未完成任务</button>
    <button disabled={task.data.status === "running" || !["failed", "stale", "cancelled"].some(s => task.data.counts[s])} onClick={() => perform("retry")}>重试失败或取消项</button>
    <ul>{task.data.items.map((item: any) => <li key={item.id}>{item.title || "论文已不可用"} · {names[item.status] || "未知状态"}{item.error && <small> · {item.status === "stale" ? "内容或人工整理已变化，请重新核对后重试" : item.status === "failed" ? "本次整理未保存，请重试；原有归属保留" : "未完成项目可在核对后重试"}</small>}</li>)}</ul>
    <button disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 50))}>上一页</button><button disabled={task.data.nextOffset === null} onClick={() => setOffset(task.data.nextOffset)}>下一页</button>
  </>}</>;
}
