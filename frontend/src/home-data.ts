import type { Paper } from "./api";
import type { ActivityDay, ActivityPayload } from "./ReadingActivity";

export function readingPapers(
  items: Paper[],
  tabs: string[],
  active?: string | null,
) {
  const byId = new Map(items.map((p) => [p.id, p]));
  const ids = [
    ...new Set([
      ...(active && tabs.includes(active) ? [active] : []),
      ...tabs.slice().reverse(),
    ]),
  ];
  return ids.flatMap((id) => (byId.has(id) ? [byId.get(id)!] : [])).slice(0, 3);
}

export function elapsedDays(days: ActivityDay[], count: number) {
  return days.filter((day) => !day.future).slice(-count);
}

export function calendarWindow(
  data: ActivityPayload | null,
  weeks: number,
): ActivityPayload | null {
  if (!data || weeks >= data.range.weeks) return data;
  const days = data.days.slice(-weeks * 7);
  return {
    ...data,
    range: { ...data.range, start: days[0]?.date || data.range.start, weeks },
    summary: {
      ...data.summary,
      totalMinutes:
        Math.round(days.reduce((sum, day) => sum + day.minutes, 0) * 10) / 10,
      readingDays: days.filter((day) => day.minutes > 0 && !day.future).length,
    },
    days,
  };
}

export function minutesText(minutes: number) {
  if (minutes > 0 && minutes < 1) return "不足 1 分钟";
  return `${Math.round(minutes).toLocaleString("zh-CN")} 分钟`;
}

export function chartCeiling(days: ActivityDay[]) {
  const maximum = Math.max(0, ...days.map((day) => day.minutes));
  const step = maximum > 120 ? 60 : maximum > 30 ? 30 : 10;
  return Math.max(step, Math.ceil(maximum / step) * step);
}
