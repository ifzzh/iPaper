import type React from "react";
import { useMemo, useState } from "react";
import { CalendarDays, ChevronRight } from "lucide-react";
import { dateText, useResource, Status, APP_TIME_ZONE_LABEL } from "./ui";
import { calendarWindow } from "./home-data";

export type ActivityDay = {
  date: string;
  minutes: number;
  papers: number;
  papersKnown: boolean;
  legacy: boolean;
  future: boolean;
  today: boolean;
};

export type ActivityPayload = {
  timezone: string;
  generatedAt: string;
  range: { start: string; end: string; weeks: number };
  summary: { totalMinutes: number; weekMinutes: number; readingDays: number };
  days: ActivityDay[];
};

/** Intensity buckets; the legend states them so colour is never the only cue. */
function levelOf(minutes: number) {
  if (minutes < 0.1) return 0;
  if (minutes < 15) return 1;
  if (minutes < 30) return 2;
  if (minutes < 60) return 3;
  return 4;
}

function label(day: ActivityDay) {
  const parts = [`${dateText(day.date)}`, `${Math.round(day.minutes)} 分钟`];
  if (day.minutes > 0) {
    parts.push(day.papersKnown ? `${day.papers} 篇论文` : "篇数未记录");
  }
  if (day.legacy) parts.push("历史仅记录总时长");
  if (day.future) parts.push("未来日期");
  return parts.join(" · ");
}

export function ReadingActivity({
  compact = false,
  source,
  onRead,
  availablePaperIds,
}: {
  compact?: boolean;
  source?: {
    data: ActivityPayload | null;
    loading: boolean;
    error: string;
    refresh: () => void;
  };
  onRead?: (id: string) => void;
  availablePaperIds?: string[];
}) {
  // A full year is the default so the calendar reads like GitHub's graph and
  // fills the content column; 12 weeks stays one click away.
  const [weeks, setWeeks] = useState(53);
  const [selected, setSelected] = useState<string>("");
  const resource = useResource<ActivityPayload | null>(
    source ? null : `/api/settings/reading-activity?weeks=${weeks}`,
    null,
  );
  const detail = useResource<any>(
    selected ? `/api/settings/reading-activity/papers?date=${selected}` : null,
    null,
  );
  const data = source ? calendarWindow(source.data, weeks) : resource.data;
  const state = source || resource;
  const selectedDay = data?.days.find((day) => day.date === selected);
  // A late response from the previously selected date must not be labelled
  // with the new date while its request is still pending.
  const selectedDetail = detail.data?.date === selected ? detail.data : null;

  const columns = useMemo(() => {
    const days = data?.days || [];
    const result: ActivityDay[][] = [];
    for (let index = 0; index < days.length; index += 7) {
      result.push(days.slice(index, index + 7));
    }
    return result;
  }, [data]);

  const monthLabels = useMemo(() => {
    let last = "";
    return columns.map((column) => {
      const month = column[0]?.date.slice(0, 7) || "";
      if (month && month !== last) {
        last = month;
        return `${Number(month.slice(5, 7))}月`;
      }
      return "";
    });
  }, [columns]);

  // GitHub shows every other weekday label: Mon, Wed, Fri.
  const weekdayLabels = ["一", "", "三", "", "五", "", ""];

  const summary = data?.summary;

  return (
    <section className={"reading-activity" + (compact ? " compact" : "")}>
      <header className="reading-activity-head">
        <div>
          <h3>
            <CalendarDays size={16} /> 阅读活动
          </h3>
          <p className="muted">
            {source
              ? "每一天的专注，都在这里留下印记。北京时间（UTC+8）。"
              : `仅统计活动、可见且正文已加载的阅读区；日期与时间为${APP_TIME_ZONE_LABEL}。`}
          </p>
        </div>
        <div
          className="reading-activity-range"
          role="group"
          aria-label="时间范围"
        >
          <button
            className={weeks === 12 ? "active" : ""}
            aria-pressed={weeks === 12}
            onClick={() => setWeeks(12)}
          >
            近 12 周
          </button>
          <button
            className={weeks === 53 ? "active" : ""}
            aria-pressed={weeks === 53}
            onClick={() => setWeeks(53)}
          >
            近一年
          </button>
        </div>
      </header>

      <Status
        error={state.error}
        loading={state.loading && !data}
        retry={state.refresh}
      />

      {summary && (
        <p className="reading-activity-summary">
          <span>
            累计 <strong>{Math.round(summary.totalMinutes)}</strong> 分钟
          </span>
          <span>
            本周 <strong>{Math.round(summary.weekMinutes)}</strong> 分钟
          </span>
          <span>
            阅读 <strong>{summary.readingDays}</strong> 天
          </span>
        </p>
      )}

      {data && (
        <div className="reading-activity-scroll">
          <div
            className="reading-activity-grid-wrap"
            style={{ "--heat-weeks": columns.length } as React.CSSProperties}
          >
            <div className="reading-activity-months" aria-hidden="true">
              {monthLabels.map((text, index) =>
                text ? (
                  <span key={index} style={{ gridColumn: index + 1 }}>
                    {text}
                  </span>
                ) : null,
              )}
            </div>
            <div className="reading-activity-body">
              <div className="reading-activity-weekdays" aria-hidden="true">
                {weekdayLabels.map((day, index) => (
                  <span key={index}>{day}</span>
                ))}
              </div>
              <div
                className="reading-activity-grid"
                role="grid"
                aria-label={`近 ${weeks === 53 ? "一年" : "12 周"}每日有效阅读分钟数`}
              >
                {columns.map((column, columnIndex) => (
                  <div className="reading-activity-column" key={columnIndex}>
                    {column.map((day) => (
                      <button
                        key={day.date}
                        type="button"
                        role="gridcell"
                        className={[
                          "heat-cell",
                          `level-${levelOf(day.minutes)}`,
                          day.today ? "today" : "",
                          day.future ? "future" : "",
                          selected === day.date ? "selected" : "",
                        ]
                          .filter(Boolean)
                          .join(" ")}
                        aria-label={label(day)}
                        title={label(day)}
                        disabled={day.future}
                        onClick={() => setSelected(day.date)}
                        onFocus={() => setSelected(day.date)}
                      />
                    ))}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      <div className="reading-activity-legend">
        <small>0 / &lt;15 / &lt;30 / &lt;60 / ≥60 分钟</small>
        <span>少</span>
        {[0, 1, 2, 3, 4].map((level) => (
          <i
            key={level}
            className={`heat-cell level-${level}`}
            aria-hidden="true"
          />
        ))}
        <span>多</span>
      </div>

      {selected && (
        <div className="reading-activity-detail">
          <header>
            <strong>{dateText(selected)}</strong>
            <button
              className="text-button"
              onClick={() => setSelected("")}
              aria-label="关闭日期详情"
            >
              关闭
            </button>
          </header>
          {detail.loading && !selectedDetail && (
            <p className="muted">正在读取当天论文…</p>
          )}
          {detail.error && <p role="alert">{detail.error}</p>}
          {selectedDetail?.papers?.length ? (
            <ul>
              {selectedDetail.papers.map((paper: any) => (
                <li key={paper.paper_id}>
                  {onRead && availablePaperIds?.includes(paper.paper_id) ? (
                    <button
                      className="home-text-link"
                      onClick={() => onRead(paper.paper_id)}
                    >
                      {paper.title || paper.arxiv_id || paper.paper_id}
                      <ChevronRight size={13} />
                    </button>
                  ) : (
                    <a href={`/?paper=${encodeURIComponent(paper.paper_id)}`}>
                      {paper.title || paper.arxiv_id || paper.paper_id}
                      <ChevronRight size={13} />
                    </a>
                  )}
                </li>
              ))}
            </ul>
          ) : selectedDetail ? (
            <p className="muted">
              {selectedDay?.minutes
                ? "这一天有累计时长，暂无可回看的逐篇记录。"
                : "这一天还没有阅读记录。"}
            </p>
          ) : null}
        </div>
      )}
    </section>
  );
}
