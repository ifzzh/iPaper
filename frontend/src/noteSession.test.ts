import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import { NoteSession, clearNoteSessions, getNoteSession, hasNoteDraft } from "./noteSession";
import type { NotePayload } from "./PaperNotes";

function record(markdown = "baseline", revision = "r0"): NotePayload {
  return { paperId: "paper", markdown, revision, exists: true, updatedAt: null, entries: [], conflicts: [] };
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function fixture() {
  let note = record(), revision = 0;
  const request = vi.fn(async (path: string, method = "GET", body?: unknown): Promise<{ note: NotePayload }> => {
    const data = body as any;
    if (method === "PUT") {
      if (data.revision !== note.revision) {
        note.conflicts.push({ id: "conflict", markdown: data.markdown, baseRevision: data.revision,
          currentRevision: note.revision!, createdAt: "" });
        throw new ApiError(409, "note_revision_conflict");
      }
      note = { ...note, markdown: data.markdown, revision: `saved-${++revision}` };
    } else if (method === "POST") {
      if (data.revision !== note.revision) throw new ApiError(409, "note_revision_conflict");
      note = { ...note, markdown: data.choice === "draft" ? data.markdown : note.markdown,
        revision: `decision-${++revision}`, conflicts: [] };
    }
    return { note: structuredClone(note) };
  });
  const session = new NoteSession("paper", request);
  return { session, request, remote: () => note, writeRemote: (text: string) => { note = record(text, `remote-${++revision}`); } };
}
let active: NoteSession[] = [];
beforeEach(() => { vi.useFakeTimers(); });
afterEach(() => {
  active.forEach(session => session.dispose()); active = [];
  clearNoteSessions(); vi.useRealTimers();
});
async function ready() {
  const f = fixture(); active.push(f.session); await f.session.load(); return f;
}
async function conflicted() {
  const f = await ready();
  f.writeRemote("REMOTE"); f.session.edit("LOCAL"); await f.session.flush();
  expect(f.session.getSnapshot().status).toBe("conflict"); return f;
}

describe("a note session outlives the editor", () => {
  it("does not autosave through an unresolved conflict after detaching and reattaching", async () => {
    const { session, remote, request } = await conflicted();
    const detach = session.subscribe(() => {}); detach();
    session.edit("LOCAL latest");
    session.subscribe(() => {}); await session.load();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(remote().markdown).toBe("REMOTE");
    expect(session.getSnapshot()).toMatchObject({ markdown: "LOCAL latest", status: "conflict" });
    expect(request.mock.calls.filter(call => call[1] === "PUT")).toHaveLength(1);
  });
  for (const choice of ["draft", "current"] as const) it(`preserves new input while the ${choice} decision is pending`, async () => {
    const { session, request, remote } = await conflicted();
    const transport = request.getMockImplementation()!;
    const gate = deferred<{ note: NotePayload }>();
    let committed!: { note: NotePayload };
    request.mockImplementation(async (...args) => {
      const value = await transport(...args);
      if (args[1] === "POST") { committed = value; return gate.promise; }
      return value;
    });
    const decision = session.decide(choice);
    await vi.advanceTimersByTimeAsync(0);
    session.edit("NEW INPUT after decision\n\n- 中文要点");
    gate.resolve(committed); await decision;
    await vi.advanceTimersByTimeAsync(0);
    expect(session.getSnapshot().markdown).toBe("NEW INPUT after decision\n\n- 中文要点");
    expect(remote().markdown).toBe(session.getSnapshot().markdown);
    expect(session.getSnapshot().status).toBe("saved");
  });
  it("rebinds a refused decision without issuing an ordinary overwrite", async () => {
    const { session, request, remote, writeRemote } = await conflicted();
    writeRemote("UNSEEN THIRD VERSION");
    await session.decide("draft");
    expect(remote().markdown).toBe("UNSEEN THIRD VERSION");
    expect(session.getSnapshot()).toMatchObject({ status: "conflict", markdown: "LOCAL",
      conflict: { theirs: "UNSEEN THIRD VERSION", revision: remote().revision } });
    expect(request.mock.calls.filter(call => call[1] === "PUT")).toHaveLength(1);
    await session.decide("draft");
    expect(remote().markdown).toBe("LOCAL");
  });
  it("preserves a conflict if its decision fails offline", async () => {
    const { session, request, remote } = await conflicted();
    request.mockRejectedValueOnce(new TypeError("offline"));
    await session.decide("current");
    expect(session.getSnapshot()).toMatchObject({ markdown: "LOCAL", status: "conflict" });
    expect(remote().markdown).toBe("REMOTE");
    await vi.advanceTimersByTimeAsync(30_000);
    expect(session.getSnapshot().markdown).toBe("LOCAL");
  });
  it("keeps new typing when a successful PUT response arrives after the editor detached", async () => {
    const { session, request, remote } = await ready();
    const transport = request.getMockImplementation()!;
    const gate = deferred<{ note: NotePayload }>();
    let first = true, committed!: { note: NotePayload };
    request.mockImplementation(async (...args) => {
      const value = await transport(...args);
      if (args[1] === "PUT" && first) { first = false; committed = value; return gate.promise; }
      return value;
    });
    session.edit("A"); const saving = session.flush();
    await vi.advanceTimersByTimeAsync(0);
    const detach = session.subscribe(() => {}); session.edit("B"); detach();
    gate.resolve(committed); await saving; await vi.advanceTimersByTimeAsync(0);
    expect(remote().markdown).toBe("B");
    expect(session.getSnapshot()).toMatchObject({ markdown: "B", status: "saved" });
  });
  it("reconciles a lost response before saving the newer text", async () => {
    const { session, request, remote } = await ready();
    const transport = request.getMockImplementation()!;
    let first = true;
    request.mockImplementation(async (...args) => {
      const value = await transport(...args);
      if (args[1] === "PUT" && first) { first = false; session.edit("B"); throw new TypeError("lost response"); }
      return value;
    });
    session.edit("A"); await session.flush(); await vi.advanceTimersByTimeAsync(0);
    expect(remote().markdown).toBe("B");
    expect(session.getSnapshot().status).toBe("saved");
  });
  it("does not claim a reverted edit is saved while a different PUT is still pending", async () => {
    const { session, request, remote } = await ready();
    const transport = request.getMockImplementation()!;
    const gate = deferred<{ note: NotePayload }>();
    let response!: { note: NotePayload };
    request.mockImplementationOnce(async (...args) => {
      response = await transport(...args); return gate.promise;
    });
    session.edit("B"); const saving = session.flush();
    await vi.advanceTimersByTimeAsync(0);
    session.edit("baseline");
    expect(session.getSnapshot().status).toBe("dirty");
    expect(remote().markdown).toBe("B");
    gate.resolve(response); await saving; await vi.advanceTimersByTimeAsync(0);
    expect(remote().markdown).toBe("baseline");
    expect(session.getSnapshot().status).toBe("saved");
  });
  it("restores the selected persisted conflict copy when there is no newer local edit", async () => {
    const { session, remote } = await ready();
    remote().conflicts.push({ id: "archived", markdown: "Recover this older side",
      baseRevision: "old", currentRevision: "r0", createdAt: "" });
    await session.load();
    await session.decide("draft", "archived");
    expect(remote().markdown).toBe("Recover this older side");
    expect(session.getSnapshot()).toMatchObject({ markdown: "Recover this older side", status: "saved" });
  });
  for (const seconds of [2, 8, 60]) it(`honours ${seconds}s Retry-After while input, blur and manual retry are combined`, async () => {
    const { session, request, remote } = await ready();
    const transport = request.getMockImplementation()!;
    let puts = 0;
    request.mockImplementation(async (...args) => {
      if (args[1] === "PUT" && ++puts === 1) throw new ApiError(429, "rate_limited", seconds);
      return transport(...args);
    });
    session.edit("A"); await session.flush();
    session.edit("B"); await session.flush(); await session.retry();
    await vi.advanceTimersByTimeAsync(seconds * 1000 - 1);
    expect(puts).toBe(1); expect(remote().markdown).toBe("baseline");
    await vi.advanceTimersByTimeAsync(1);
    expect(puts).toBe(2); expect(remote().markdown).toBe("B");
    expect(session.getSnapshot().status).toBe("saved");
  });
  it("limits automatic offline retries and keeps the latest draft", async () => {
    const { session, request } = await ready();
    request.mockRejectedValue(new TypeError("offline"));
    session.edit("offline draft"); await session.flush();
    await vi.advanceTimersByTimeAsync(60_000);
    const attempts = request.mock.calls.length;
    await vi.advanceTimersByTimeAsync(60_000);
    expect(request.mock.calls).toHaveLength(attempts);
    expect(session.getSnapshot()).toMatchObject({ markdown: "offline draft", status: "failed" });
    expect(attempts).toBeLessThan(12);
  });
  it("does not resurrect private text after session disposal", async () => {
    const { session, request } = await ready();
    const gate = deferred<{ note: NotePayload }>();
    request.mockImplementationOnce(() => gate.promise);
    session.edit("private text"); const saving = session.flush(); session.dispose();
    gate.resolve({ note: record("private text", "late") }); await saving;
    expect(session.getSnapshot().markdown).toBe(""); expect(session.getSnapshot().note).toBeNull();
  });
  it("ignores an old GET arriving after an explicit excerpt append", async () => {
    const { session, request } = await ready();
    const gate = deferred<{ note: NotePayload }>();
    request.mockImplementationOnce(() => gate.promise);
    const reading = session.load();
    session.accept(record("baseline plus new excerpt", "r1"));
    gate.resolve({ note: record() }); await reading;
    expect(session.getSnapshot()).toMatchObject({ markdown: "baseline plus new excerpt",
      status: "saved", note: { revision: "r1" } });
  });
  it("isolates owners and retains an intentionally emptied draft", () => {
    const a = getNoteSession("alice", "paper"), b = getNoteSession("bob", "paper");
    a.accept(record()); b.accept(record("bob")); a.edit("");
    expect(hasNoteDraft("alice", "paper")).toBe(true);
    expect(b.getSnapshot().markdown).toBe("bob");
    clearNoteSessions("alice");
    expect(hasNoteDraft("alice", "paper")).toBe(false);
    expect(b.getSnapshot().markdown).toBe("bob");
  });
});
