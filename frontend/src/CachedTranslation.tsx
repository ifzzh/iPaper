import { useEffect, useState, useRef } from "react";
import { api, Status } from "./ui";
import { errorText } from "./api";
import { languageLabels, type ProcessingResult } from "./Processing";
import type { Identity, Heading } from "./ReadingTools";
export function CachedTranslation({
  paperId,
  document,
  point,
  results,
  selectedId,
  onSelected,
  onClose,
  onAsk,
}: {
  paperId: string;
  document: Identity;
  point: { page: number; x: number; y: number };
  results: ProcessingResult[];
  selectedId?: string;
  onSelected: (id: string) => void;
  onClose: () => void;
  onAsk: (v: any) => void;
}) {
  const options = results.filter(
    (r) =>
      r.documentId === document.id &&
      r.sourceHash === document.sha256 &&
      r.kind === "structured_translation",
  );
  const selected =
    options.find((r) => r.id === selectedId) ||
    (options.length === 1 ? options[0] : undefined);
  const [candidates, setCandidates] = useState<Heading[]>([]),
    [block, setBlock] = useState<any>(null),
    [error, setError] = useState(""),
    [loading, setLoading] = useState(false),
    [checked, setChecked] = useState(false);
  const selectionKey = useRef(selected?.id);
  selectionKey.current = selected?.id;
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", close);
    return () => {
      live.current = false;
      window.removeEventListener("keydown", close);
    };
  }, []);
  useEffect(() => {
    if (selected && selected.id !== selectedId) onSelected(selected.id);
  }, [selected?.id]);
  useEffect(() => {
    setCandidates([]);
    setError("");
    setBlock(null);
    setChecked(false);
    if (!selected) return;
    const c = new AbortController();
    setLoading(true);
    void (async () => {
      let after: number | null = -1;
      const hits: Heading[] = [];
      while (after !== null) {
        const r: any = await api(
          `/api/results/${selected.id}/navigation?page=${point.page}&after=${after}`,
          "GET",
          undefined,
          c.signal,
        );
        if (r.stale || r.sourceHash !== document.sha256)
          throw new Error("source_expired");
        for (const item of r.items)
          if (
            item.precision === "region" &&
            item.regions.some(
              (region: any) =>
                region.page === point.page &&
                point.x >= Math.min(region.rect[0], region.rect[2]) &&
                point.x <= Math.max(region.rect[0], region.rect[2]) &&
                point.y >= Math.min(region.rect[1], region.rect[3]) &&
                point.y <= Math.max(region.rect[1], region.rect[3]),
            )
          )
            hits.push(item);
        after = r.nextCursor;
      }
      if (c.signal.aborted) return;
      setCandidates(hits);
      setChecked(true);
      if (hits.length === 1) {
        const r = await api(
          `/api/results/${selected.id}/blocks/${hits[0].blockId}`,
          "GET",
          undefined,
          c.signal,
        );
        if (!c.signal.aborted) setBlock(r.block);
      }
    })()
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setLoading(false);
      });
    return () => c.abort();
  }, [selected?.id, point, document.id]);
  // A blank page click with no trustworthy hit should preserve normal reading.
  if (!options.length || (checked && !candidates.length && !error)) return null;
  async function ask() {
    try {
      const source = await api(`/api/paper/${paperId}/sources`, "POST", {
        resultId: selected!.id,
        blockId: block.id,
        start: 0,
        end: Math.min(block.text.length, 6000),
      });
      onAsk({
        sourceId: source.source.id,
        text: source.source.text,
        page: source.source.page,
      });
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <section className="cached-translation" aria-label="段落已有译文">
      <header>
        <strong>段落已有译文</strong>
        <button onClick={onClose} aria-label="关闭段落译文">
          关闭
        </button>
      </header>
      {options.length > 1 && (
        <label>
          译文版本
          <select
            value={selected?.id || ""}
            onChange={(e) => onSelected(e.target.value)}
          >
            <option value="">请选择译文版本</option>
            {options.map((r) => (
              <option key={r.id} value={r.id}>
                {languageLabels[r.targetLanguage]} · {r.model} ·{" "}
                {r.createdAt.slice(0, 10)}
              </option>
            ))}
          </select>
        </label>
      )}
      <Status error={error} loading={loading} />
      {selected && (
        <p className="muted">
          {languageLabels[selected.targetLanguage]} · {selected.model} ·
          读取已有结果
        </p>
      )}
      {candidates.length > 1 && !block && (
        <>
          <p>此处对应多个结构块，请选择：</p>
          {candidates.map((item) => (
            <button
              key={item.blockId}
              onClick={() =>
                void api(`/api/results/${selected!.id}/blocks/${item.blockId}`)
                  .then((r) => {
                    if (live.current && selectionKey.current === selected?.id)
                      setBlock(r.block);
                  })
                  .catch((e) => setError(errorText(e)))
              }
            >
              {item.title || "图表内容"}
            </button>
          ))}
        </>
      )}
      {block && (
        <>
          <p className="selection-original">{block.text || block.caption}</p>
          {block.translation?.content ? (
            <>
              <p className="selection-result">
                {block.translation.content.text ||
                  block.translation.content.caption ||
                  "此块包含表格译文，请在结构阅读中查看。"}
              </p>
              {block.translation.status === "failed" && (
                <p>最新翻译失败，显示上次成功译文。</p>
              )}
            </>
          ) : (
            <p>
              {block.translation?.status === "failed"
                ? "此块翻译失败。"
                : "此块尚未翻译。"}
              未创建新任务。
            </p>
          )}
          {block.text && <button onClick={() => void ask()}>带来源提问</button>}
        </>
      )}
    </section>
  );
}
