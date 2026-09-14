/** Exact normalized text matching, with UTF-16 offsets into PDF text items. */
export type TextItem = { str: string; hasEOL?: boolean };
export type Match = {
  startItem: number;
  startOffset: number;
  endItem: number;
  endOffset: number;
  before: string;
  text: string;
  after: string;
};
export function normalizeItems(items: TextItem[], sensitive = false) {
  let text = "";
  const item: number[] = [],
    start: number[] = [],
    end: number[] = [];
  items.forEach((value, index) => {
    let offset = 0;
    for (const original of value.str + (value.hasEOL ? "\n" : "")) {
      const at = offset;
      offset += original.length;
      if (original === "\u00ad") continue;
      let normalized = original.normalize("NFKC");
      if (!sensitive) normalized = normalized.toLowerCase();
      for (const char of normalized) {
        const part = /\s/u.test(char) ? " " : char;
        if (part === " " && text.endsWith(" ")) continue;
        text += part;
        for (let n = 0; n < part.length; n++) {
          item.push(index);
          start.push(Math.min(at, value.str.length));
          end.push(Math.min(offset, value.str.length));
        }
      }
    }
  });
  return {
    text,
    item: Int32Array.from(item),
    start: Int32Array.from(start),
    end: Int32Array.from(end),
  };
}
export function findMatches(
  items: TextItem[],
  query: string,
  sensitive = false,
  skip = 0,
  limit = 50,
) {
  const normalized = normalizeItems(items, sensitive),
    needle = normalizeItems([{ str: query }], sensitive).text.trim();
  let count = 0,
    position = 0;
  const matches: Match[] = [];
  if (needle)
    while ((position = normalized.text.indexOf(needle, position)) !== -1) {
      const last = position + needle.length - 1;
      if (count >= skip && matches.length < limit)
        matches.push({
          startItem: normalized.item[position],
          startOffset: normalized.start[position],
          endItem: normalized.item[last],
          endOffset: normalized.end[last],
          before: normalized.text.slice(Math.max(0, position - 45), position),
          text: normalized.text.slice(position, last + 1),
          after: normalized.text.slice(last + 1, last + 71),
        });
      count++;
      position = last + 1;
    }
  return { count, matches, characters: normalized.text.trim().length };
}
