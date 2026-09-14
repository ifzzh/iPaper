import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import "./reading-tools.css";
const allowed =
  /^(?:\/api\/results\/[a-f0-9-]+\/assets\/[a-f0-9]{64}|\/api\/paper\/[^/]+\/(?:analysis\/image(?:\?|$)|understanding-assets\/[a-f0-9]{64})|\/api\/understanding\/[a-f0-9-]+\/assets\/[a-f0-9]{64}|\/static\/images\/)/;
/** Only clones already sanitized, authorized rendered content. */
export function MediaBoundary({
  children,
  onSource,
}: {
  children: ReactNode;
  onSource?: () => void;
}) {
  const root = useRef<HTMLDivElement>(null),
    [target, setTarget] = useState<Element | null>(null);
  useEffect(() => {
    const host = root.current!;
    const refresh = () => {
      for (const button of host.querySelectorAll<HTMLButtonElement>(
        "button[data-media-open]",
      ))
        if (
          !button.previousElementSibling?.matches(
            "img,table,math,.formula-mathml,.structure-formula",
          )
        )
          button.remove();
      for (const media of host.querySelectorAll<HTMLElement>(
        "img,table,.formula-mathml,.structure-formula,math[display=block]",
      )) {
        if (
          media.closest("[role=dialog],dialog") ||
          media.parentElement?.closest(
            "table,math,.formula-mathml,.structure-formula",
          )
        )
          continue;
        if (
          media.matches("img") &&
          !allowed.test(media.getAttribute("src") || "")
        )
          continue;
        if (media.nextElementSibling?.matches("[data-media-open]")) continue;
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.mediaOpen = "true";
        button.className = "media-open-button";
        button.textContent = media.matches("img")
          ? "放大图片"
          : media.matches("table")
            ? "展开表格"
            : "放大公式";
        media.after(button);
      }
    };
    const observer = new MutationObserver(refresh);
    observer.observe(host, { childList: true, subtree: true });
    refresh();
    return () => {
      observer.disconnect();
      host.querySelectorAll("[data-media-open]").forEach((n) => n.remove());
    };
  }, []);
  return (
    <div
      ref={root}
      className="media-boundary"
      onClick={(e) => {
        const button = (e.target as Element).closest("[data-media-open]");
        if (button && root.current?.contains(button)) {
          e.preventDefault();
          setTarget(button.previousElementSibling);
        }
      }}
    >
      {children}
      {target && (
        <MediaViewer
          target={target}
          onClose={() => setTarget(null)}
          onSource={onSource}
        />
      )}
    </div>
  );
}
function MediaViewer({
  target,
  onClose,
  onSource,
}: {
  target: Element;
  onClose: () => void;
  onSource?: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null),
    stage = useRef<HTMLDivElement>(null),
    content = useRef<HTMLDivElement>(null),
    [scale, setScale] = useState(1);
  const drag = useRef<{
    x: number;
    y: number;
    left: number;
    top: number;
  } | null>(null);
  const image = target.matches("img"),
    caption = image
      ? target.getAttribute("alt")
      : target
          .closest("article,figure")
          ?.querySelector(".structure-caption,figcaption")?.textContent;
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current?.showModal();
    return () => {
      dialog.current?.close();
      if (previous instanceof HTMLElement && previous.isConnected)
        previous.focus({ preventScroll: true });
    };
  }, []);
  useLayoutEffect(() => {
    const copy = target.cloneNode(true) as Element;
    copy.querySelectorAll("button,a,script,iframe").forEach((n) => n.remove());
    for (const n of [copy, ...copy.querySelectorAll("*")]) {
      n.removeAttribute("id");
      n.removeAttribute("tabindex");
    }
    content.current?.replaceChildren(copy);
    return () => content.current?.replaceChildren();
  }, [target]);
  useLayoutEffect(() => {
    if (content.current) content.current.style.zoom = String(scale);
  }, [scale]);
  function fit() {
    const img = content.current?.querySelector("img");
    if (img && stage.current)
      setScale(
        Math.min(
          1,
          (stage.current.clientWidth - 40) / img.naturalWidth,
          (stage.current.clientHeight - 40) / img.naturalHeight,
        ),
      );
    else setScale(1);
  }
  useEffect(() => {
    const img = content.current?.querySelector("img");
    if (img) {
      if (img.complete) fit();
      else img.addEventListener("load", fit, { once: true });
    }
    return () => img?.removeEventListener("load", fit);
  }, [target]);
  return (
    <dialog
      className="modal wide media-viewer"
      ref={dialog}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
    >
      <header>
        <h2>
          {image
            ? "图片查看"
            : target.matches("table")
              ? "表格查看"
              : "公式查看"}
        </h2>
        <button aria-label="关闭图表查看" onClick={onClose}>
          关闭
        </button>
      </header>
      <div className="modal-body">
        <div className="media-viewer-controls">
          <button onClick={fit}>适合窗口</button>
          <button
            aria-label="缩小图表"
            onClick={() => setScale((s) => Math.max(0.1, s / 1.25))}
          >
            −
          </button>
          <span>{Math.round(scale * 100)}%</span>
          <button
            aria-label="放大图表"
            onClick={() => setScale((s) => Math.min(6, s * 1.25))}
          >
            ＋
          </button>
        </div>
        <div
          ref={stage}
          className={"media-viewer-stage" + (image ? " image-stage" : "")}
          tabIndex={0}
          aria-label="图表，可滚动或拖动查看"
          onPointerDown={(e) => {
            if (!image) return;
            drag.current = {
              x: e.clientX,
              y: e.clientY,
              left: e.currentTarget.scrollLeft,
              top: e.currentTarget.scrollTop,
            };
            e.currentTarget.setPointerCapture(e.pointerId);
          }}
          onPointerMove={(e) => {
            if (drag.current) {
              e.currentTarget.scrollLeft =
                drag.current.left + drag.current.x - e.clientX;
              e.currentTarget.scrollTop =
                drag.current.top + drag.current.y - e.clientY;
            }
          }}
          onPointerUp={() => (drag.current = null)}
          onPointerCancel={() => (drag.current = null)}
        >
          <div className="media-viewer-content" ref={content} />
        </div>
        {caption && <p>{caption}</p>}
        {onSource && (
          <button
            onClick={() => {
              onClose();
              onSource();
            }}
          >
            查看原文来源
          </button>
        )}
        <p className="muted">
          当前论文中已有的{image ? "图片资产" : "内容"}
          ；未发送图像给模型。关闭后返回原阅读位置。
        </p>
      </div>
    </dialog>
  );
}
