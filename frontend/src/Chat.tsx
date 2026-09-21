import { useEffect, useRef, useState } from "react";
import { errorText, isSessionError, request } from "./api";
import { postChat, readChatStream } from "./chat-stream";
import { api, Markdown, Confirm, useResource } from "./ui";
import { AcademicText } from "./MathFormula";
import {
  MessageSquare,
  X,
  ArrowDown,
  Send,
  Plus,
  RefreshCw,
  MoreHorizontal,
  Trash2,
  Square,
} from "lucide-react";
export type Excerpt = {
  text: string;
  page?: number;
  document: string;
  title: string;
  sourceId?: string;
  locationLabel?: string;
};

type Message = {
  role: "user" | "assistant";
  content: string;
  sources?: { label: string; sourceId: string }[];
  scope?: {
    mode: string;
    usedUnits?: number;
    availableUnits?: number;
    selectionLimited?: boolean;
    imageInput?: boolean;
  };
};
type Session = { id: string; title: string };
function messagesFrom(value: unknown): Message[] {
  if (!Array.isArray(value)) throw new Error("invalid history");
  return value
    .filter(
      (v): v is Message =>
        v &&
        ["user", "assistant"].includes(v.role) &&
        typeof v.content === "string",
    )
    .map((v) => ({
      role: v.role,
      content: v.content,
      scope: v.scope,
      sources: Array.isArray(v.sources)
        ? v.sources.filter(
            (s) =>
              /^S[1-9][0-9]{0,5}$/.test(s.label) &&
              typeof s.sourceId === "string",
          )
        : [],
    }));
}
export function Chat({
  paperId,
  onExpired,
  initialSession = "",
  onSessionChange,
  excerpt,
  onClearExcerpt,
  drafts,
  onSource,
  prepareSources,
}: {
  paperId: string;
  onExpired: () => void;
  initialSession?: string;
  onSessionChange?: (id: string) => void;
  excerpt?: Excerpt | null;
  onClearExcerpt?: () => void;
  drafts?: Map<string, string>;
  onSource?: (id: string) => void;
  prepareSources?: (signal: AbortSignal) => Promise<string[]>;
}) {
  const [sessions, setSessions] = useState<Session[]>([]),
    [id, setId] = useState(initialSession);
  const [scope, setScope] = useState<"paper" | "local">("paper"),
    [allowPartial, setAllowPartial] = useState(false);
  const content = useResource<any>(
    `/api/paper/${encodeURIComponent(paperId)}/content`,
    {},
  );
  useEffect(() => {
    if (excerpt) setScope("local");
  }, [excerpt]);
  const [messages, setMessages] = useState<Message[]>([]),
    [draft, setDraft] = useState(
      drafts?.get(paperId + "|" + initialSession) || "",
    );
  const [pending, setPending] = useState(""),
    [notice, setNotice] = useState("正在加载聊天历史…");
  const [busy, setBusy] = useState(false),
    [loading, setLoading] = useState(false),
    [saveStates, setSaveStates] = useState<Record<number, "saving" | "saved" | "failed">>({}),
    [saveError, setSaveError] = useState("");
  const epoch = useRef(0),
    controller = useRef<AbortController | null>(null);
  const stream = useRef<AbortController | null>(null),
    sending = useRef(false);
  const messageHost = useRef<HTMLDivElement>(null),
    follow = useRef(true),
    scrollPositions = useRef(new Map<string, number>()),
    restoreScroll = useRef<number | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [menu, setMenu] = useState(false);
  const [away, setAway] = useState(false);
  const lastTurn = useRef("");
  function rememberSession(sessionId: string) {
    onSessionChange?.(sessionId);
    void request(
      `/api/paper/${encodeURIComponent(paperId)}/understanding-position`,
      undefined,
      "PUT",
      { sessionId: sessionId || null },
      true,
    ).catch(() => {
      setNotice("会话已保留，但会话选择保存失败，可从历史列表重新打开。");
    });
  }
  useEffect(() => {
    if (
      restoreScroll.current !== null &&
      messageHost.current &&
      messages.length
    ) {
      messageHost.current.scrollTop = restoreScroll.current;
      restoreScroll.current = null;
    } else if (follow.current && messageHost.current)
      messageHost.current.scrollTop = messageHost.current.scrollHeight;
    else setAway(true);
  }, [messages, pending]);
  useEffect(() => {
    if (initialSession && initialSession !== id) void choose(initialSession);
  }, [initialSession]);
  function editDraft(value: string) {
    setDraft(value);
    drafts?.set(paperId + "|" + id, value);
  }
  function begin() {
    controller.current?.abort();
    stream.current?.abort();
    sending.current = false;
    setBusy(false);
    const c = new AbortController();
    controller.current = c;
    return { c, seq: ++epoch.current };
  }
  function valid(seq: number) {
    return seq === epoch.current;
  }
  function expire() {
    epoch.current++;
    controller.current?.abort();
    stream.current?.abort();
    onExpired();
  }
  function fail(e: unknown) {
    if (isSessionError(e)) expire();
    else setNotice(errorText(e));
  }
  async function list(signal: AbortSignal, seq: number) {
    const data = (await request(
      `/api/paper/chat/sessions?paper_id=${encodeURIComponent(paperId)}`,
      signal,
    )) as { sessions: Session[] };
    if (!Array.isArray(data.sessions)) throw new Error("invalid sessions");
    if (valid(seq))
      setSessions(
        data.sessions
          .filter(
            (s) => typeof s.id === "string" && typeof s.title === "string",
          )
          .map((s) => ({ id: s.id, title: s.title })),
      );
  }
  async function history(sessionId: string, signal: AbortSignal) {
    const data = (await request(
      `/api/paper/chat/session?paper_id=${encodeURIComponent(paperId)}&session_id=${encodeURIComponent(sessionId)}`,
      signal,
    )) as { session: { messages: unknown } };
    return messagesFrom(data.session.messages);
  }
  async function choose(sessionId: string) {
    if (messageHost.current)
      scrollPositions.current.set(id, messageHost.current.scrollTop);
    restoreScroll.current = scrollPositions.current.get(sessionId) ?? null;
    follow.current = restoreScroll.current === null;
    const { c, seq } = begin();
    setId(sessionId);
    setMessages([]);
    setPending("");
    setDraft(drafts?.get(paperId + "|" + sessionId) || "");
    rememberSession(sessionId);
    setLoading(true);
    setNotice("");
    try {
      await list(c.signal, seq);
      if (sessionId) {
        const result = await history(sessionId, c.signal);
        if (valid(seq)) setMessages(result);
      }
    } catch (e) {
      if (valid(seq) && !c.signal.aborted) fail(e);
    } finally {
      if (valid(seq)) setLoading(false);
    }
  }
  async function refresh() {
    const { c, seq } = begin();
    setLoading(true);
    setNotice("");
    setPending("");
    try {
      await list(c.signal, seq);
      if (id) {
        const result = await history(id, c.signal);
        if (valid(seq)) setMessages(result);
      }
    } catch (e) {
      if (valid(seq) && !c.signal.aborted) fail(e);
    } finally {
      if (valid(seq)) setLoading(false);
    }
  }
  useEffect(() => {
    const c = new AbortController();
    void request(
      `/api/paper/${encodeURIComponent(paperId)}/understanding-position`,
      c.signal,
    )
      .then((v: any) => {
        if (!c.signal.aborted) {
          const chosen = v.sessionId ?? initialSession;
          if (chosen) void choose(chosen);
          else void refresh();
        }
      })
      .catch(() => {
        if (!c.signal.aborted) void refresh();
      });
    return () => {
      c.abort();
      epoch.current++;
      controller.current?.abort();
      stream.current?.abort();
    };
  }, [paperId]);
  async function send() {
    if (sending.current || loading || !draft.trim()) return;
    if (scope === "local" && !excerpt?.sourceId && !prepareSources) {
      setNotice("请先选择文字或一个结构段落，再进行局部问答。");
      return;
    }
    if (
      scope === "paper" &&
      (!content.data.available ||
        (!content.data.coverage?.complete && !allowPartial))
    ) {
      setNotice("请先核对解析正文及实际覆盖范围。");
      return;
    }
    const { c, seq } = begin();
    sending.current = true;
    setBusy(true);
    setNotice("");
    setPending("");
    const userMessage: Message = {
      role: "user",
      content:
        (excerpt && scope === "local"
          ? `引用《${excerpt.title}》${excerpt.locationLabel || `${excerpt.document === "translated" ? "译文" : "原文"}${excerpt.page ? `第 ${excerpt.page} 页` : "选区（页码未确认）"}`}：\n> ${excerpt.text.replace(/\n/g, "\n> ")}\n\n`
          : "") + draft.trim(),
    };
    onClearExcerpt?.();
    const original = messages;
    let sessionId = id,
      answer = "",
      stopped = false;
    editDraft("");
    follow.current = true;
    setAway(false);
    setMessages([...original, userMessage]);
    const sc = new AbortController();
    stream.current = sc;
    try {
      const sourceIds =
        scope === "local"
          ? excerpt?.sourceId
            ? [excerpt.sourceId]
            : await prepareSources?.(sc.signal)
          : undefined;
      if (sc.signal.aborted || !valid(seq))
        throw new DOMException("Aborted", "AbortError");
      lastTurn.current = crypto.randomUUID();
      const response = await postChat(
        {
          paper_id: paperId,
          messages: [...original, userMessage],
          scope,
          request_id: lastTurn.current,
          ...(scope === "paper"
            ? {
                content_version: content.data.version,
                allow_partial: allowPartial,
              }
            : {}),
          ...(id ? { session_id: id } : {}),
          ...(sourceIds?.length ? { source_ids: sourceIds } : {}),
        },
        sc.signal,
      );
      await readChatStream(
        response,
        sc.signal,
        (next) => {
          sessionId = next;
          if (valid(seq)) {
            setId(next);
            rememberSession(next);
          }
        },
        (text) => {
          answer += text;
          if (valid(seq)) setPending(answer);
        },
      );
    } catch (e) {
      stopped = sc.signal.aborted;
      if (valid(seq) && !stopped) {
        if (isSessionError(e)) {
          expire();
          return;
        }
        setNotice("请求或连接异常；不会自动重发。");
      }
    } finally {
      if (valid(seq)) {
        try {
          await list(c.signal, seq);
          if (sessionId) {
            const saved = await history(sessionId, c.signal);
            if (valid(seq)) {
              const confirmed =
                saved.length >= original.length + 2 &&
                saved[original.length]?.role === "user" &&
                saved[original.length]?.content === userMessage.content &&
                saved[original.length + 1]?.role === "assistant" &&
                saved[original.length + 1]?.content === answer;
              setMessages(saved);
              if (confirmed) {
                setPending("");
                setNotice(
                  stopped
                    ? "已停止接收；历史中已有保存的回答。"
                    : "已读取服务端保存的历史。",
                );
              } else {
                const turn: any = lastTurn.current
                  ? await request(
                      `/api/paper/chat/turns/${lastTurn.current}`,
                      c.signal,
                    ).catch(() => null)
                  : null;
                setNotice(
                  stopped
                    ? "已停止接收，服务端可能继续处理；回答未确认保存。可稍后刷新历史。"
                    : turn?.errorCode
                      ? `回答未保存：${errorText({ code: turn.errorCode })} 不会自动重发。`
                      : "回答未确认保存，可刷新历史核对；不会自动重发。",
                );
              }
            }
          } else if (valid(seq))
            setNotice(
              stopped
                ? "已停止接收，服务端可能继续处理；尚未获得会话编号，请刷新历史核对。"
                : "未获得会话编号，请刷新历史核对；不会自动重发。",
            );
        } catch (e) {
          if (valid(seq) && !c.signal.aborted) {
            if (isSessionError(e)) expire();
            else setNotice("历史核对失败，回答未确认保存。请刷新历史。");
          }
        }
        if (valid(seq)) {
          sending.current = false;
          setBusy(false);
        }
      }
    }
  }
  return (
    <aside className="chat-panel" aria-label="论文问答">
      <div className="chat-head">
        <div className="chat-head-title">
          <h2>论文问答</h2>
          <small>{messages.length ? `${messages.length} 条消息` : "新会话"}</small>
        </div>
        <label className="chat-session">
          <span className="sr-only">聊天会话</span>
          <select
            aria-label="聊天会话"
            value={id}
            onChange={(e) => void choose(e.target.value)}
          >
            <option value="">新会话</option>
            {sessions.map((s) => (
              <option key={s.id} value={s.id}>
                {s.title}
              </option>
            ))}
          </select>
        </label>
        <div
          className="menu-wrap"
          onKeyDown={(e) => {
            if (e.key === "Escape") setMenu(false);
          }}
        >
          <button
            className="icon-button"
            aria-label="会话操作"
            aria-expanded={menu}
            onClick={() => setMenu((v) => !v)}
          >
            <MoreHorizontal size={16} />
          </button>
          {menu && (
            <div className="menu-list" role="menu">
              <button
                role="menuitem"
                onClick={() => {
                  setMenu(false);
                  void choose("");
                }}
              >
                <Plus size={15} />
                新会话
              </button>
              <button
                role="menuitem"
                disabled={busy || loading}
                onClick={() => {
                  setMenu(false);
                  void refresh();
                }}
              >
                <RefreshCw size={15} />
                刷新历史
              </button>
              {id && (
                <button
                  role="menuitem"
                  className="danger"
                  disabled={busy || loading}
                  onClick={() => {
                    setMenu(false);
                    setDeleting(true);
                  }}
                >
                  <Trash2 size={15} />
                  删除当前会话
                </button>
              )}
            </div>
          )}
        </div>
      </div>
      <div
        className="chat-messages"
        ref={messageHost}
        onScroll={() => {
          const el = messageHost.current!;
          follow.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 80;
          if (follow.current) setAway(false);
        }}
      >
        {messages.map((m, i) => (
          <div key={i} className={`message ${m.role}`}>
            <strong>{m.role === "user" ? "你" : "助手"}</strong>
            <Markdown
              text={m.content}
              sources={Object.fromEntries(
                (m.sources || []).map((s) => [
                  s.label,
                  { sourceId: s.sourceId },
                ]),
              )}
              onSource={onSource}
            />
            {m.role === "assistant" && (
              <div className="message-actions">
                <button
                  type="button"
                  disabled={saveStates[i] === "saving"}
                  onClick={async () => {
                    setSaveStates((v) => ({ ...v, [i]: "saving" }));
                    try {
                      // Copies the already-persisted answer; no new model call.
                      await api(
                        `/api/paper/${encodeURIComponent(paperId)}/reading/note/answers`,
                        "POST",
                        { sessionId: id || undefined, messageIndex: i },
                      );
                      setSaveStates((v) => ({ ...v, [i]: "saved" }));
                      // The note panel refetches so the inserted answer is visible
                      // without a reload (no polling, no model call).
                      window.dispatchEvent(
                        new CustomEvent("paper-notes:refresh", { detail: { paperId } }),
                      );
                    } catch (e) {
                      setSaveStates((v) => ({ ...v, [i]: "failed" }));
                      setSaveError(errorText(e));
                    }
                  }}
                >
                  {saveStates[i] === "saving"
                    ? "正在存入…"
                    : saveStates[i] === "saved"
                      ? "已存入本篇笔记"
                      : "存入本篇笔记"}
                </button>
                {saveStates[i] === "failed" && <span>保存失败，可重试</span>}
              </div>
            )}
            {m.scope && (
              <p className="message-scope">
                {m.scope.mode === "local" ? "局部问答" : "整篇论文问答"} ·
                实际使用 {m.scope.usedUnits ?? "—"}
                {m.scope.availableUnits
                  ? ` / ${m.scope.availableUnits}`
                  : ""}{" "}
                个正文单元
                {m.scope.selectionLimited ? " · 按问题选择的证据" : ""} ·
                未发送图片像素
              </p>
            )}
            {!!m.sources?.length && (
              <nav className="source-links" aria-label="回答来源">
                {m.sources.map((s) => (
                  <button key={s.label} onClick={() => onSource?.(s.sourceId)}>
                    [{s.label}] 查看来源
                  </button>
                ))}
              </nav>
            )}
          </div>
        ))}
        {pending && (
          <div className="message assistant">
            <strong>{busy ? "正在接收" : "未确认保存的内容"}</strong>
            <Markdown text={pending} />
          </div>
        )}
        {!messages.length && !pending && (
          <div className="empty-state">
            <MessageSquare size={30} />
            <h3>与论文对话</h3>
            <p>询问方法、结果或局限，也可以选择 PDF 中的文字提问。</p>
          </div>
        )}
      </div>
      {away && (
        <button
          className="follow-latest"
          onClick={() => {
            follow.current = true;
            setAway(false);
            messageHost.current?.scrollTo({
              top: messageHost.current.scrollHeight,
              behavior: "smooth",
            });
          }}
        >
          <ArrowDown size={14} />
          回到最新
        </button>
      )}
      <p className="chat-notice" role="status">
        {notice}
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void send();
        }}
      >
        {excerpt && (
          <div className="excerpt-card">
            <div>
              <span>
                引用 ·{" "}
                {excerpt.locationLabel ||
                  `${excerpt.document === "translated" ? "译文" : "原文"}${excerpt.page ? `第 ${excerpt.page} 页` : "选区（页码未确认）"}`}
              </span>
              <button
                type="button"
                aria-label="移除引用"
                onClick={onClearExcerpt}
              >
                <X size={14} />
              </button>
            </div>
            <p>
              <AcademicText text={excerpt.text} />
            </p>
            {excerpt.sourceId && (
              <small>
                本次将使用已核实的选区及相邻结构段落；回答中只有匹配来源的编号可以跳转。
              </small>
            )}
          </div>
        )}
        <div className="scope-control" aria-label="问答范围">
          <button
            type="button"
            disabled={busy}
            className={scope === "paper" ? "active" : ""}
            onClick={() => {
              setScope("paper");
              onClearExcerpt?.();
            }}
          >
            整篇论文
          </button>
          <button
            type="button"
            disabled={busy}
            className={scope === "local" ? "active" : ""}
            onClick={() => setScope("local")}
          >
            选区／当前段落
          </button>
        </div>
        {scope === "paper" && (
          <div className="scope-notice">
            {content.loading ? (
              "正在确认解析正文…"
            ) : content.data.available ? (
              <>
                <span>
                  {content.data.coverage?.complete
                    ? `完整解析原文，共 ${content.data.coverage.totalPages} 页`
                    : "可用正文的完整性未确认"}
                  。译文完成数量不影响问答；长文按问题选择证据，最多两次模型请求。
                </span>
                {!content.data.coverage?.complete && (
                  <label>
                    <input
                      type="checkbox"
                      checked={allowPartial}
                      onChange={(e) => setAllowPartial(e.target.checked)}
                    />
                    仅使用当前可用正文，理解覆盖范围有限。
                  </label>
                )}
              </>
            ) : (
              <span>
                当前没有可用正文。请先在概览页明确创建解析任务；不会自动解析。
              </span>
            )}
          </div>
        )}
        {scope === "local" && prepareSources && !excerpt && (
          <small>本次仅使用当前结构块和相邻段落。</small>
        )}
        <label className="sr-only" htmlFor="question">
          你的问题
        </label>
        <div className="compose-box">
          <textarea
            id="question"
            placeholder="询问方法、结果或局限…"
            value={draft}
            disabled={busy || loading}
            onChange={(e) => editDraft(e.target.value)}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !e.shiftKey &&
                !e.nativeEvent.isComposing
              ) {
                e.preventDefault();
                void send();
              }
            }}
            rows={3}
          />
          <div className="compose-actions">
            {busy && (
              <button
                type="button"
                className="ghost"
                onClick={() => stream.current?.abort()}
              >
                <Square size={13} />
                停止接收
              </button>
            )}
            <button
              className="primary send-button"
              disabled={busy || loading || !draft.trim()}
            >
              <Send size={14} />
              发送
            </button>
          </div>
        </div>
        <p className="footnote">
          停止接收或切换会话不会确认服务端取消。
        </p>
      </form>
      {deleting && (
        <Confirm
          title="删除当前会话"
          detail="该会话的问答历史将被删除。"
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            await request(
              `/api/paper/chat/session?paper_id=${encodeURIComponent(paperId)}&session_id=${encodeURIComponent(id)}`,
              undefined,
              "DELETE",
            );
            drafts?.delete(paperId + "|" + id);
            setSessions((v) => v.filter((s) => s.id !== id));
            await choose("");
          }}
        />
      )}
    </aside>
  );
}
