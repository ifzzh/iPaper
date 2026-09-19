import { describe, expect, it } from "vitest";
import { workspaceRoute } from "./routes";
import {
  calendarWindow,
  chartCeiling,
  elapsedDays,
  readingPapers,
} from "./home-data";
import { paperFrom } from "./api";
import type { ActivityDay, ActivityPayload } from "./ReadingActivity";

describe("home routing and reading continuity", () => {
  it("opens home at the root while preserving explicit library and legacy paper links", () => {
    expect(workspaceRoute("").view).toBe("home");
    expect(workspaceRoute("?view=library").view).toBe("library");
    expect(workspaceRoute("?paper=a%2Fb")).toMatchObject({
      view: "library",
      paper: "a/b",
    });
    expect(
      workspaceRoute("?view=reader&paper=a&document=translated"),
    ).toMatchObject({ view: "reader", paper: "a", translated: true });
    expect(workspaceRoute("?view=home&paper=a")).toMatchObject({
      view: "home",
      paper: "",
    });
  });
  it("uses the saved active tab first, excludes unavailable papers and never duplicates a card", () => {
    const papers = ["a", "b", "c", "d"].map((id) => paperFrom({ id }));
    expect(
      readingPapers(papers, ["a", "b", "missing", "c", "d"], "b").map(
        (p) => p.id,
      ),
    ).toEqual(["b", "d", "c"]);
    expect(readingPapers(papers, [], "b")).toEqual([]);
    expect(readingPapers([], ["a"], "a")).toEqual([]);
  });
});

describe("reading statistics use the server's UTC+8 calendar", () => {
  const days: ActivityDay[] = Array.from({ length: 371 }, (_, index) => ({
    date: new Date(Date.UTC(2025, 8, 15 + index)).toISOString().slice(0, 10),
    minutes: index < 369 ? 3 : 0,
    papers: 1,
    papersKnown: true,
    legacy: false,
    future: index > 368,
    today: index === 368,
  }));
  const data: ActivityPayload = {
    timezone: "Asia/Shanghai",
    generatedAt: "2026-09-18T22:00:00Z",
    range: { start: days[0].date, end: days.at(-1)!.date, weeks: 53 },
    summary: { totalMinutes: 1107, weekMinutes: 15, readingDays: 369 },
    days,
  };
  it("does not count future calendar padding as days in the trend", () => {
    const week = elapsedDays(days, 7);
    expect(week).toHaveLength(7);
    expect(week.at(-1)?.today).toBe(true);
    expect(week.reduce((sum, day) => sum + day.minutes, 0)).toBe(21);
    expect(chartCeiling([])).toBe(10);
    expect(chartCeiling([{ ...days[0], minutes: 61 }])).toBe(90);
  });
  it("reuses the annual response for 12 weeks and recomputes range totals without mutating it", () => {
    const short = calendarWindow(data, 12)!;
    expect(short.days).toHaveLength(84);
    expect(short.summary).toEqual({
      totalMinutes: 246,
      weekMinutes: 15,
      readingDays: 82,
    });
    expect(data.summary.readingDays).toBe(369);
    expect(calendarWindow(data, 53)).toBe(data);
    expect(calendarWindow(null, 12)).toBeNull();
  });
});
