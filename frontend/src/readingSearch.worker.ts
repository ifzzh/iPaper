import { findMatches } from "./readingSearch";
self.onmessage = (e) => {
  const { id, items, query, sensitive, skip, limit } = e.data;
  try {
    self.postMessage({
      id,
      ...findMatches(items, query, sensitive, skip, limit),
    });
  } catch {
    self.postMessage({ id, error: "page_text_limit" });
  }
};
