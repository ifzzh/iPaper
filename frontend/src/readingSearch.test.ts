import { describe, it, expect } from "vitest";
import { findMatches, normalizeItems } from "./readingSearch";
describe("exact normalized PDF search", () => {
  it("maps ligatures, soft hyphens, line breaks and astral UTF-16 offsets", () => {
    const items = [
      { str: "😀 Ofﬁce soft\u00adhyphen", hasEOL: true },
      { str: "   NEXT line" },
    ];
    const office = findMatches(items, "office").matches[0];
    expect([office.startItem, office.startOffset, office.endOffset]).toEqual([
      0, 3, 8,
    ]);
    const wrapped = findMatches(items, "softhyphen NEXT").matches[0];
    expect(wrapped.startItem).toBe(0);
    expect(wrapped.endItem).toBe(1);
    expect(wrapped.endOffset).toBe(7);
    expect(findMatches(items, "OFFICE", true).count).toBe(0);
    expect(findMatches(items, "Ofﬁce", true).count).toBe(1);
  });
  it("returns exact counts independently from bounded requested match pages", () => {
    const items = [{ str: "match ".repeat(5000) }];
    const result = findMatches(items, "match", false, 4999, 1);
    expect(result.count).toBe(5000);
    expect(result.matches).toHaveLength(1);
    expect(result.matches[0].startOffset).toBe(4999 * 6);
    expect(findMatches(items, "   ").count).toBe(0);
    expect(normalizeItems([{ str: "a\n\t b" }]).text).toBe("a b");
  });
});
