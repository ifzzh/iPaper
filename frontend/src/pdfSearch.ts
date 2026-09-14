import type { PDFDocumentProxy } from "pdfjs-dist/legacy/build/pdf.mjs";
import type { TextItem, Match } from "./readingSearch";
export type PageSearch = {
  page: number;
  count: number;
  characters: number;
  error?: string;
};
export class PdfSearch {
  private worker = new Worker(
    new URL("./readingSearch.worker.ts", import.meta.url),
    { type: "module" },
  );
  private cache = new Map<number, TextItem[]>();
  private bytes = 0;
  private readers = new Set<ReadableStreamDefaultReader<any>>();
  private dead = false;
  private id = 0;
  private pending = new Map<
    number,
    { resolve: (v: any) => void; reject: (e: Error) => void }
  >();
  constructor(
    private doc: PDFDocumentProxy,
    readonly query: string,
    readonly sensitive: boolean,
  ) {
    this.worker.onmessage = (e) => {
      const p = this.pending.get(e.data.id);
      this.pending.delete(e.data.id);
      e.data.error ? p?.reject(new Error(e.data.error)) : p?.resolve(e.data);
    };
    this.worker.onerror = () => this.destroy();
  }
  async items(page: number) {
    if (this.dead) throw new DOMException("Aborted", "AbortError");
    const old = this.cache.get(page);
    if (old) {
      this.cache.delete(page);
      this.cache.set(page, old);
      return old;
    }
    const pdf = await this.doc.getPage(page);
    if (this.dead) throw new DOMException("Aborted", "AbortError");
    const reader = pdf.streamTextContent().getReader();
    this.readers.add(reader);
    const items: TextItem[] = [];
    let bytes = 0;
    try {
      for (;;) {
        const { value, done } = await reader.read();
        if (this.dead) throw new DOMException("Aborted", "AbortError");
        if (done) break;
        for (const item of value.items) {
          if (!("str" in item)) continue;
          bytes += item.str.length * 26 + 64;
          if (bytes > 8 * 1024 * 1024) throw new Error("page_text_limit");
          items.push({ str: item.str, hasEOL: item.hasEOL });
        }
      }
    } finally {
      this.readers.delete(reader);
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
    while (this.cache.size >= 16 || this.bytes + bytes > 8 * 1024 * 1024) {
      const [key, value] = this.cache.entries().next().value!;
      this.cache.delete(key);
      this.bytes -= value.reduce((n, i) => n + i.str.length * 26 + 64, 0);
    }
    this.cache.set(page, items);
    this.bytes += bytes;
    return items;
  }
  async matches(
    page: number,
    skip = 0,
    limit = 50,
  ): Promise<{ count: number; matches: Match[]; characters: number }> {
    const items = await this.items(page),
      id = ++this.id;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.worker.postMessage({
        id,
        items,
        query: this.query,
        sensitive: this.sensitive,
        skip,
        limit,
      });
    });
  }
  async scan(update: (pages: PageSearch[]) => void) {
    const pages: PageSearch[] = [];
    for (let page = 1; page <= this.doc.numPages && !this.dead; page++) {
      try {
        const value = await this.matches(page, 0, 0);
        pages.push({ page, count: value.count, characters: value.characters });
      } catch (e) {
        if (this.dead) return;
        pages.push({
          page,
          count: 0,
          characters: 0,
          error: e instanceof Error ? e.message : "extraction_failed",
        });
      }
      if (!this.dead) update([...pages]);
    }
  }
  destroy() {
    this.dead = true;
    this.worker.terminate();
    for (const reader of this.readers) void reader.cancel().catch(() => {});
    this.readers.clear();
    this.cache.clear();
    this.bytes = 0;
    for (const p of this.pending.values())
      p.reject(new DOMException("Aborted", "AbortError"));
    this.pending.clear();
  }
}
/** Use the actual PDF.js text divs/characters; no guessed word rectangles. */
export function highlightMatch(
  surface: HTMLElement,
  divs: HTMLElement[],
  match: Match | null,
) {
  surface.querySelectorAll(".pdf-search-highlight").forEach((e) => e.remove());
  if (!match) return true;
  function endpoint(index: number, offset: number): [Node, number] | null {
    const div = divs[index];
    if (!div) return null;
    const walker = document.createTreeWalker(div, NodeFilter.SHOW_TEXT);
    let node: Node | null;
    while ((node = walker.nextNode())) {
      const length = node.textContent?.length || 0;
      if (offset <= length) return [node, offset];
      offset -= length;
    }
    return null;
  }
  const start = endpoint(match.startItem, match.startOffset),
    end = endpoint(match.endItem, match.endOffset);
  if (!start || !end) return false;
  try {
    const range = document.createRange();
    range.setStart(...start);
    range.setEnd(...end);
    const box = surface.getBoundingClientRect();
    let count = 0;
    for (const rect of range.getClientRects()) {
      if (!rect.width || !rect.height) continue;
      const marker = document.createElement("div");
      marker.className = "pdf-search-highlight";
      marker.setAttribute("aria-label", "搜索匹配");
      marker.style.left = rect.left - box.left + "px";
      marker.style.top = rect.top - box.top + "px";
      marker.style.width = rect.width + "px";
      marker.style.height = rect.height + "px";
      surface.append(marker);
      count++;
    }
    return count > 0;
  } catch {
    return false;
  }
}
