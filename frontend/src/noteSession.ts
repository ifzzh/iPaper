import { ApiError, request } from "./api";
import type { NotePayload } from "./PaperNotes";

export type NoteConflict = {
  id: string | null;
  theirs: string;
  revision: string | null;
};
export type NoteState = {
  note: NotePayload | null;
  markdown: string;
  status: "idle" | "dirty" | "saving" | "saved" | "failed" | "conflict";
  conflict: NoteConflict | null;
  busy: boolean;
  message: string;
  retryAt: number;
};
type Transport = (path: string, method?: string, body?: unknown) => Promise<{ note: NotePayload }>;

/** One request queue per owner/paper, independent of any mounted editor. */
export class NoteSession {
  private state: NoteState = {
    note: null, markdown: "", status: "idle", conflict: null,
    busy: false, message: "", retryAt: 0,
  };
  private listeners = new Set<() => void>();
  private base: string | null = null;
  private confirmed = "";
  private version = 0;
  private operation = 0;
  private retries = 0;
  private disposed = false;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private loading: Promise<NotePayload | null> | null = null;
  private uncertain: { markdown: string } | null = null;
  private controller = new AbortController();
  private path: string;
  private transport: Transport;

  constructor(readonly paperId: string, transport?: Transport) {
    this.path = `/api/paper/${encodeURIComponent(paperId)}/reading/note`;
    this.transport = transport || ((path, method = "GET", body) =>
      request(path, this.controller.signal, method, body) as Promise<{ note: NotePayload }>);
  }
  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };
  get unsaved() {
    return this.state.markdown !== this.confirmed || !!this.state.conflict || this.state.busy;
  }
  get disposable() { return !this.listeners.size && !this.unsaved; }
  private update(value: Partial<NoteState>) {
    if (this.disposed) return;
    this.state = { ...this.state, ...value };
    this.listeners.forEach(listener => listener());
  }
  private cancelTimer() {
    if (this.timer) clearTimeout(this.timer);
    this.timer = undefined;
  }
  private schedule(delay = 1200) {
    this.cancelTimer();
    if (this.disposed || this.state.conflict) return;
    // A deadline is authoritative for typing, blur, retries and remounts alike.
    const wait = Math.max(delay, this.state.retryAt - Date.now(), 0);
    this.timer = setTimeout(() => { this.timer = undefined; void this.flush(); }, Math.min(wait, 60_000));
  }
  private merge(note: NotePayload): NotePayload {
    return { ...note, entries: note.entries || this.state.note?.entries || [],
      conflicts: note.conflicts || this.state.note?.conflicts || [] };
  }
  private openConflict(note: NotePayload, preferredId = this.state.conflict?.id) {
    this.cancelTimer();
    const pending = (note.conflicts || []).filter(row => !row.currentRevision.startsWith("resolved:"));
    const row = pending.find(row => row.id === preferredId) ||
      pending.find(row => row.markdown === this.state.markdown);
    // Keep the confirmed base unchanged. The displayed remote revision belongs
    // to the decision, never to an ordinary autosave.
    this.update({ note: this.merge(note), conflict: { id: row?.id || null,
      theirs: note.markdown, revision: note.revision }, status: "conflict",
      message: "这篇笔记已在其他页面修改，请选择要保留的版本。" });
  }
  private confirm(note: NotePayload, replaceText = false) {
    this.base = note.revision;
    this.confirmed = note.markdown;
    const markdown = replaceText ? note.markdown : this.state.markdown;
    this.retries = 0;
    this.uncertain = null;
    this.update({ note: this.merge(note), markdown, conflict: null, retryAt: 0, message: "",
      status: markdown === note.markdown ? (note.revision ? "saved" : "idle") : "dirty" });
  }
  /** Accept an explicit append/answer response, or a fresh read, without losing edits. */
  accept = (note: NotePayload) => {
    if (this.disposed || note.paperId !== this.paperId || this.state.busy) return;
    // An explicit append/answer can finish while an older GET is in flight.
    // Once its response is accepted, that read must not restore the old copy.
    this.operation += 1;
    if (this.state.conflict) { this.openConflict(note); return; }
    if (!this.state.note || this.state.markdown === this.confirmed) this.confirm(note, true);
    else if (note.markdown === this.state.markdown) this.confirm(note);
    else if (note.revision !== this.base) this.openConflict(note);
    else this.update({ note: this.merge(note) });
  };
  load = (): Promise<NotePayload | null> => {
    if (this.disposed || this.state.busy) return Promise.resolve(this.state.note);
    if (this.loading) return this.loading;
    const operation = this.operation;
    this.loading = this.transport(this.path).then(({ note }) => {
      if (!this.disposed && operation === this.operation) this.accept(note);
      return this.disposed ? null : this.state.note;
    }).catch(() => {
      if (!this.disposed && operation === this.operation)
        this.update({ message: "笔记暂时无法读取，请重试；本地内容仍保留。" });
      return null;
    }).finally(() => { this.loading = null; });
    return this.loading;
  };
  edit = (markdown: string) => {
    if (this.disposed || !this.state.note) return;
    this.version += 1;
    this.update({ markdown, status: this.state.conflict ? "conflict" :
      this.state.retryAt > Date.now() ? "failed" :
      !this.state.busy && markdown === this.confirmed ? "saved" : "dirty" });
    if (!this.state.busy) this.schedule();
  };
  private async reconcile() {
    const { note } = await this.transport(this.path);
    if (this.disposed) return false;
    if (note.markdown === this.state.markdown || note.markdown === this.uncertain?.markdown) {
      this.confirm(note);
      return true;
    }
    if (note.revision !== this.base) { this.openConflict(note); return false; }
    return true;
  }
  flush = async () => {
    if (this.disposed || this.state.busy || this.state.conflict || !this.state.note) return;
    this.cancelTimer();
    if (this.state.retryAt > Date.now()) { this.schedule(0); return; }
    if (this.state.markdown === this.confirmed && !this.uncertain) return;
    this.operation += 1;
    this.update({ busy: true, status: "saving", message: "" });
    try {
      if (this.uncertain && !(await this.reconcile())) return;
      if (this.disposed || this.state.markdown === this.confirmed) return;
      const markdown = this.state.markdown;
      this.uncertain = { markdown };
      const { note } = await this.transport(this.path, "PUT", { markdown, revision: this.base });
      if (this.disposed) return;
      this.confirm(note); // Only confirms this response; newer text stays dirty.
    } catch (error) {
      if (this.disposed) return;
      if (error instanceof ApiError && error.status === 429) {
        const seconds = error.retryAfter && error.retryAfter > 0 ? error.retryAfter : 60;
        this.uncertain = null; // This request was explicitly rejected.
        this.update({ status: "failed", retryAt: Date.now() + seconds * 1000,
          message: `服务器要求约 ${Math.ceil(seconds)} 秒后再保存；内容仍保留在编辑区。` });
        this.schedule(0);
      } else if (error instanceof ApiError && error.status === 409) {
        // Freeze immediately, even if the following GET is temporarily offline.
        this.cancelTimer();
        this.update({ status: "conflict", conflict: { id: null, theirs: this.state.note?.markdown || "", revision: null },
          message: "保存冲突，正在核对服务器版本。" });
        try {
          const { note } = await this.transport(this.path);
          if (!this.disposed) this.openConflict(note);
        } catch { this.update({ message: "服务器版本暂时无法读取，请重试核对；草稿已保留。" }); }
      } else {
        try { if (await this.reconcile()) {
          if (this.state.markdown === this.confirmed || !this.uncertain) return;
        } else return; } catch { /* Leave unknown results for the next reconciliation. */ }
        this.update({ status: "failed", message: "保存失败，内容仍保留在编辑区；可重试保存。" });
        if (this.retries < 3) this.schedule([3000, 8000, 20000][this.retries++]);
      }
    } finally {
      this.update({ busy: false });
      // Never recurse: a waiting/failed/conflicted session has its own wake-up.
      if (!this.disposed && this.state.status === "dirty") this.schedule(0);
    }
  };
  retry = async () => {
    if (this.disposed || this.state.busy) return;
    if (this.state.conflict) { await this.load(); return; }
    this.retries = 0;
    if (!this.uncertain) this.uncertain = { markdown: this.confirmed };
    await this.flush();
  };
  decide = async (choice: "current" | "draft", conflictId?: string) => {
    if (this.disposed || this.state.busy || this.state.retryAt > Date.now()) return null;
    const row = this.state.note?.conflicts?.find(row => row.id === conflictId);
    const pending = this.state.conflict || (row && this.state.note ? {
      id: row.id, theirs: this.state.note.markdown, revision: this.state.note.revision,
    } : null);
    if (!pending) return null;
    const version = this.version;
    const markdown = !this.state.conflict && this.state.markdown === this.confirmed && row
      ? row.markdown : this.state.markdown;
    this.cancelTimer();
    this.operation += 1;
    this.update({ busy: true, conflict: pending, status: "conflict" });
    try {
      let id = pending.id;
      if (!id) {
        // A divergence discovered by GET has no conflict row yet. Preserve the
        // local side with the old base before an explicit decision can replace
        // the remote side. A moving remote still requires a new decision.
        try { await this.transport(this.path, "PUT", { markdown, revision: this.base }); }
        catch (error) { if (!(error instanceof ApiError && error.status === 409)) throw error; }
        const fresh = (await this.transport(this.path)).note;
        if (this.disposed) return null;
        if (fresh.revision !== pending.revision) { this.openConflict(fresh); return null; }
        id = fresh.conflicts.find(row => row.markdown === markdown && !row.currentRevision.startsWith("resolved:"))?.id || null;
        if (!id) { this.openConflict(fresh); return null; }
      }
      const { note } = await this.transport(`${this.path}/conflicts/${encodeURIComponent(id)}`, "POST",
        { choice, revision: pending.revision, ...(choice === "draft" ? { markdown } : {}) });
      if (this.disposed) return null;
      // The choice covers the text shown when clicked, not later typing.
      this.confirm(note, this.version === version);
      return note;
    } catch (error) {
      if (this.disposed) return null;
      if (error instanceof ApiError && error.status === 409) {
        try { this.openConflict((await this.transport(this.path)).note); }
        catch { /* Keep the original conflict, never fall back to an ordinary PUT. */ }
      }
      this.update({ status: "conflict", message: "决定尚未生效，请核对后重新选择；最新草稿仍保留。",
        ...(error instanceof ApiError && error.status === 429 ? {
          retryAt: Date.now() + (error.retryAfter || 60) * 1000,
        } : {}) });
      return null;
    } finally {
      this.update({ busy: false });
      if (!this.disposed && this.state.status === "dirty") this.schedule(0);
    }
  };
  discard = () => {
    if (this.state.busy || !this.state.note) return;
    this.cancelTimer();
    this.confirm(this.state.note, true);
  };
  dispose() {
    this.disposed = true;
    this.cancelTimer();
    this.controller.abort();
    this.listeners.clear();
    this.confirmed = "";
    this.uncertain = null;
    this.base = null;
    this.state = { ...this.state, markdown: "", note: null, conflict: null };
  }
}

const sessions = new Map<string, NoteSession>();
export function getNoteSession(owner: string, paper: string) {
  const key = `${owner}::${paper}`;
  let session = sessions.get(key);
  if (!session) {
    // Bound cached, confirmed copies; never evict unsaved work to meet a limit.
    for (const [oldKey, old] of sessions) {
      if (sessions.size < 32) break;
      if (old.disposable) { old.dispose(); sessions.delete(oldKey); }
    }
    session = new NoteSession(paper);
    sessions.set(key, session);
  }
  return session;
}
export function clearNoteSessions(owner?: string) {
  for (const [key, session] of sessions) {
    if (!owner || key.startsWith(`${owner}::`)) { session.dispose(); sessions.delete(key); }
  }
}
export function hasNoteDraft(owner: string, paper: string) {
  return sessions.get(`${owner}::${paper}`)?.unsaved || false;
}
if (typeof window !== "undefined") window.addEventListener("beforeunload", event => {
  if ([...sessions.values()].some(session => session.unsaved)) {
    event.preventDefault(); event.returnValue = "";
  }
});
