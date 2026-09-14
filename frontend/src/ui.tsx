import { MediaBoundary } from "./MediaViewer";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { X, LoaderCircle, AlertCircle } from "lucide-react";
import DOMPurify from "dompurify";
import { marked } from "marked";
import katex from "katex";
const renderMath = (value: string, display: boolean) => {
  try {
    return katex.renderToString(value, {
      output: "mathml",
      displayMode: display,
      throwOnError: true,
      trust: false,
      strict: "error",
      maxExpand: 1000,
      maxSize: 20,
    });
  } catch {
    return value.replace(
      /[&<>]/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!,
    );
  }
};
marked.use({
  renderer: {
    html(token) {
      return token.text.replace(
        /[&<>]/g,
        (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!,
      );
    },
  },
  extensions: [
    {
      name: "ipaperBlockMath",
      level: "block",
      start: (source) => source.indexOf("$$"),
      tokenizer(source) {
        const m = /^\$\$([^]*?)\$\$(?:\n|$)/.exec(source);
        if (m && m[0].length <= 32000)
          return { type: "ipaperBlockMath", raw: m[0], text: m[1] };
      },
      renderer: (token) => renderMath(token.text, true),
    },
    {
      name: "ipaperInlineMath",
      level: "inline",
      start: (source) => source.indexOf("$"),
      tokenizer(source) {
        const m = /^\$([^$\n]+)\$/.exec(source);
        if (m && m[0].length <= 32000)
          return { type: "ipaperInlineMath", raw: m[0], text: m[1] };
      },
      renderer: (token) => renderMath(token.text, false),
    },
  ],
});
import { request, errorText, csrfHeaders, ApiError } from "./api";
export const api = <T = any,>(
  path: string,
  method = "GET",
  body?: unknown,
  signal?: AbortSignal,
  keepalive = false,
) => request(path, signal, method, body, keepalive) as Promise<T>;
export async function upload(
  path: string,
  body: FormData,
  signal?: AbortSignal,
) {
  const r = await fetch(path, {
    method: "POST",
    body,
    signal,
    credentials: "same-origin",
    headers: csrfHeaders(),
  });
  if (r.status === 401)
    window.dispatchEvent(new Event("ipaper-session-expired"));
  let data: any;
  try {
    data = await r.json();
  } catch {
    throw new ApiError(r.status, "invalid_response");
  }
  if (!r.ok || data.error || data.success === false)
    throw new ApiError(r.status, data.error || "request_failed");
  return data;
}
export function useResource<T>(path: string | null, initial: T) {
  const [data, setData] = useState(initial),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [revision, refresh] = useState(0);
  useEffect(() => {
    if (!path) return;
    const c = new AbortController();
    setLoading(true);
    setError("");
    api<T>(path, "GET", undefined, c.signal)
      .then((value) => {
        if (!c.signal.aborted) setData(value);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setLoading(false);
      });
    return () => c.abort();
  }, [path, revision]);
  return {
    data,
    setData,
    error,
    loading,
    refresh: () => refresh((n) => n + 1),
  };
}
export function Status({
  error,
  loading,
  retry,
}: {
  error?: string;
  loading?: boolean;
  retry?: () => void;
}) {
  return error ? (
    <div role="alert" className="notice error">
      <AlertCircle size={16} />
      {error}
      {retry && <button onClick={retry}>重试</button>}
    </div>
  ) : loading ? (
    <div className="loading" role="status">
      <LoaderCircle size={18} className="spin" />
      正在加载…
    </div>
  ) : null;
}
export function Modal({
  title,
  children,
  onClose,
  wide = false,
  className = "",
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  wide?: boolean;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const old = document.activeElement;
    ref.current?.showModal();
    return () => {
      ref.current?.close();
      if (old instanceof HTMLElement && old.isConnected)
        old.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className={(wide ? "modal wide" : "modal") + " " + className}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2>{title}</h2>
        <button className="icon-button" aria-label="关闭" onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <div className="modal-body">{children}</div>
    </dialog>
  );
}
export function Markdown({
  text,
  sources,
  onSource,
}: {
  text: string;
  sources?: Record<string, { sourceId: string }>;
  onSource?: (id: string) => void;
}) {
  const html = DOMPurify.sanitize(
    marked.parse(text, { async: false }) as string,
    {
      FORBID_TAGS: ["style", "iframe", "form", "video", "audio", "object"],
      FORBID_ATTR: ["style"],
      ALLOW_DATA_ATTR: false,
    },
  );
  const template = document.createElement("template");
  template.innerHTML = html;
  for (const img of template.content.querySelectorAll("img")) {
    const src = img.getAttribute("src") || "";
    if (
      !/^(?:\/api\/paper\/[^/]+\/analysis\/image(?:\?|$)|\/api\/paper\/[^/]+\/understanding-assets\/[a-f0-9]{64}$|\/api\/understanding\/[a-f0-9-]+\/assets\/[a-f0-9]{64}$|\/static\/images\/)/.test(
        src,
      )
    )
      img.remove();
    else {
      img.loading = "lazy";
      img.decoding = "async";
    }
  }
  for (const a of template.content.querySelectorAll("a")) {
    const href = a.getAttribute("href") || "";
    if (!/^(https?:\/\/|\/(?!\/)|#)/i.test(href)) a.removeAttribute("href");
    else {
      a.target = "_blank";
      a.rel = "noopener noreferrer";
    }
  }
  if (sources && onSource) {
    const walker = document.createTreeWalker(
      template.content,
      NodeFilter.SHOW_TEXT,
    );
    const nodes: Text[] = [];
    while (walker.nextNode()) nodes.push(walker.currentNode as Text);
    for (const node of nodes) {
      if (node.parentElement?.closest("code,pre,a,math")) continue;
      const value = node.textContent || "";
      if (!/\[S[1-9][0-9]{0,5}\]/.test(value)) continue;
      const fragment = document.createDocumentFragment();
      let offset = 0;
      for (const m of value.matchAll(/\[(S[1-9][0-9]{0,5})\]/g)) {
        fragment.append(value.slice(offset, m.index));
        if (sources[m[1]]) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "evidence-link";
          button.dataset.sourceId = sources[m[1]].sourceId;
          button.textContent = m[0];
          button.setAttribute("aria-label", "查看来源 " + m[1]);
          fragment.append(button);
        } else fragment.append(m[0]);
        offset = m.index + m[0].length;
      }
      fragment.append(value.slice(offset));
      node.replaceWith(fragment);
    }
  }
  return (
    <MediaBoundary>
      <div
        className="markdown"
        onClick={(e) => {
          const button = (e.target as HTMLElement).closest<HTMLButtonElement>(
            "button[data-source-id]",
          );
          if (
            button?.dataset.sourceId &&
            Object.values(sources || {}).some(
              (s) => s.sourceId === button.dataset.sourceId,
            )
          )
            onSource?.(button.dataset.sourceId);
        }}
        dangerouslySetInnerHTML={{ __html: template.innerHTML }}
      />
    </MediaBoundary>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function dateText(value: string) {
  if (!value) return "—";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value;
  const d = new Date(value);
  if (Number.isNaN(d.getTime()))
    return /^\d{4}-\d{2}-\d{2}/.exec(value)?.[0] || "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
export function timestampText(value: string) {
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${dateText(value)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
export function Confirm({
  title,
  detail,
  onConfirm,
  onClose,
}: {
  title: string;
  detail: string;
  onConfirm: () => Promise<unknown>;
  onClose: () => void;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  return (
    <Modal title={title} onClose={onClose}>
      <p>{detail}</p>
      <Status error={error} />
      <footer>
        <button onClick={onClose}>取消</button>
        <button
          className="danger"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await onConfirm();
              onClose();
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "正在处理…" : "确认"}
        </button>
      </footer>
    </Modal>
  );
}
