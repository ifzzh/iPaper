import { useState, type CSSProperties } from "react";
import {
  ArrowDownLeft,
  ArrowUpRight,
  ArrowRight,
  BookOpen,
  CalendarDays,
  Clock3,
  FileText,
  FolderOpen,
  Layers3,
  Library,
  Rss,
  Search,
} from "lucide-react";
import type { Paper, User } from "./api";
import type { Topic } from "./Topics";
import { ReadingActivity, type ActivityPayload } from "./ReadingActivity";
import { useResource, Status, dateText, APP_TIME_ZONE } from "./ui";
import {
  chartCeiling,
  elapsedDays,
  minutesText,
  readingPapers,
} from "./home-data";
import "./home.css";

type Props = {
  user: User;
  items: Paper[];
  tabs: string[];
  preferences: {
    activePaper?: string | null;
    tabDocuments?: Record<string, string>;
  };
  loading: boolean;
  error: string;
  onRead: (paper: Paper) => void;
  onLibrary: (filter?: string) => void;
  onDaily: () => void;
  onImport: () => void;
  onRefresh: () => void;
};

function PaperResume({
  paper,
  featured = false,
  translated,
  onRead,
  resume,
}: {
  paper: Paper;
  featured?: boolean;
  translated: boolean;
  onRead: (paper: Paper) => void;
  resume: boolean;
}) {
  const position = useResource<Record<string, { page: number }> | null>(
    resume
      ? `/api/paper/${encodeURIComponent(paper.id)}/reading-position`
      : null,
    null,
  );
  const saved = position.data?.[translated ? "translated" : "original"];
  return (
    <article className={featured ? "home-resume-feature" : "home-resume-row"}>
      {featured && (
        <div className="home-paper-art" aria-hidden="true">
          <div className="home-paper-sheet back" />
          <div className="home-paper-sheet front">
            <span className="home-paper-mark">
              <BookOpen size={18} /> iPaper
            </span>
            <i />
            <i />
            <i />
            <div className="home-paper-diagram">
              <span />
              <span />
              <span />
            </div>
            <i />
            <i />
          </div>
        </div>
      )}
      {!featured && (
        <span className="home-document-icon">
          <FileText size={19} />
        </span>
      )}
      <div className="home-resume-copy">
        {featured && (
          <span className="home-kicker">
            {resume ? "接着上次的思路" : "从第一篇开始"}
          </span>
        )}
        <h3 title={paper.title}>{paper.title}</h3>
        <p className="home-paper-byline" title={paper.authors}>
          {[paper.authors || "作者信息待补全", paper.year]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <div className="home-resume-meta">
          <span>{translated ? "版式译文" : "原文 PDF"}</span>
          <span>
            {saved
              ? `上次读至第 ${saved.page} 页`
              : resume
                ? "已打开的论文"
                : "已在文献库"}
          </span>
        </div>
        {featured && (
          <button
            className="primary home-read-button"
            onClick={() => onRead(paper)}
          >
            <BookOpen size={16} />
            {resume ? "继续阅读" : "开始阅读"}
            <ArrowRight size={16} />
          </button>
        )}
      </div>
      {!featured && (
        <button
          className="icon-button"
          title={`阅读 ${paper.title}`}
          aria-label={`阅读 ${paper.title}`}
          onClick={() => onRead(paper)}
        >
          <ArrowUpRight size={20} />
        </button>
      )}
    </article>
  );
}

function ReadingTrend({
  data,
  loading,
  error,
  retry,
}: {
  data: ActivityPayload | null;
  loading: boolean;
  error: string;
  retry: () => void;
}) {
  const [range, setRange] = useState(7);
  const [selected, setSelected] = useState("");
  const days = elapsedDays(data?.days || [], range);
  const total = days.reduce((sum, day) => sum + day.minutes, 0);
  const ceiling = chartCeiling(days);
  const focused = days.find((day) => day.date === selected);
  return (
    <section
      className="home-panel home-trend"
      aria-labelledby="home-trend-title"
    >
      <header className="home-section-head">
        <h2 id="home-trend-title">阅读节奏</h2>
        <div className="home-range" role="group" aria-label="阅读趋势范围">
          {[7, 30].map((count) => (
            <button
              key={count}
              aria-pressed={count === range}
              onClick={() => {
                setRange(count);
                setSelected("");
              }}
            >
              {count} 天
            </button>
          ))}
        </div>
      </header>
      <Status error={error} loading={loading && !data} retry={retry} />
      <div className="home-trend-value">
        <strong>
          {data ? Math.round(total).toLocaleString("zh-CN") : "—"}
        </strong>
        <span>
          分钟
          <span className="home-trend-caption">近 {range} 天的有效阅读</span>
        </span>
      </div>
      {data && (
        <>
          <div className="home-chart" aria-label={`近 ${range} 天阅读趋势`}>
            <div className="home-chart-axis" aria-hidden="true">
              <span>{ceiling}</span>
              <span>{ceiling / 2}</span>
              <span>0</span>
            </div>
            <div className="home-chart-plot">
              <div className="home-chart-lines" aria-hidden="true">
                <i />
                <i />
                <i />
              </div>
              <div className="home-chart-bars">
                {days.map((day) => (
                  <button
                    key={day.date}
                    className={
                      (day.today ? "today " : "") +
                      (day.date === selected ? "selected" : "")
                    }
                    aria-label={`${dateText(day.date)}，${minutesText(day.minutes)}`}
                    title={`${dateText(day.date)} · ${minutesText(day.minutes)}`}
                    onFocus={() => setSelected(day.date)}
                    onClick={() => setSelected(day.date)}
                    onMouseEnter={() => setSelected(day.date)}
                  >
                    <span
                      style={{ height: `${(day.minutes / ceiling) * 100}%` }}
                    />
                  </button>
                ))}
              </div>
            </div>
            <div className="home-chart-dates" aria-hidden="true">
              <span>{days[0]?.date.slice(5).replace("-", "/")}</span>
              <span>
                {days[Math.floor(days.length / 2)]?.date
                  .slice(5)
                  .replace("-", "/")}
              </span>
              <span>今天</span>
            </div>
          </div>
          <div className="home-chart-note" aria-live="polite">
            <span className="home-chart-dot" />
            {focused
              ? `${dateText(focused.date)} · ${minutesText(focused.minutes)}`
              : total
                ? `日均 ${minutesText(total / days.length)}`
                : "开始阅读，记录会在这里慢慢积累"}
          </div>
        </>
      )}
    </section>
  );
}

export function Home({
  user,
  items,
  tabs,
  preferences,
  loading,
  error,
  onRead,
  onLibrary,
  onDaily,
  onImport,
  onRefresh,
}: Props) {
  const activity = useResource<ActivityPayload | null>(
    "/api/settings/reading-activity?weeks=53",
    null,
  );
  const topics = useResource<{ topics: Topic[]; total: number } | null>(
    "/api/topics",
    null,
  );
  const daily = useResource<{ dates: string[]; enabled: boolean } | null>(
    "/api/daily-arxiv/dates",
    null,
  );
  const opened = readingPapers(items, tabs, preferences.activePaper);
  const suggestions = opened.length ? opened : items.slice(0, 3);
  const topTopics = (topics.data?.topics || [])
    .filter((topic) => !topic.parent_id && topic.count > 0)
    .sort((a, b) => b.count - a.count || a.name.localeCompare(b.name, "zh-CN"))
    .slice(0, 5);
  const today = activity.data?.days.find((day) => day.today)?.date;
  const now = new Date();
  const dateLabel = new Intl.DateTimeFormat("zh-CN", {
    timeZone: APP_TIME_ZONE,
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(now);
  const latest = daily.data?.dates.slice().sort().at(-1);
  const stats = [
    {
      label: "我的文献",
      value:
        (error && !items.length) || (loading && !items.length)
          ? "—"
          : items.length,
      unit: "篇",
      note: "每一篇，都是新的线索",
      Icon: Library,
      className: "collection",
    },
    {
      label: "本周阅读",
      value: activity.data
        ? Math.round(activity.data.summary.weekMinutes)
        : "—",
      unit: "分钟",
      note: "从本周一开始累计",
      Icon: Clock3,
      className: "minutes",
    },
    {
      label: "阅读足迹",
      value: activity.data ? activity.data.summary.readingDays : "—",
      unit: "天",
      note: "过去一年，有阅读的日子",
      Icon: CalendarDays,
      className: "days",
    },
    {
      label: "已有译文",
      value:
        (error && !items.length) || (loading && !items.length)
          ? "—"
          : items.filter((p) => p.translated).length,
      unit: "篇",
      note: "随时切换，双语对照",
      Icon: Layers3,
      className: "translations",
    },
  ];
  return (
    <main className="home-page" aria-labelledby="home-title">
      <div className="home-layout">
        <header className="home-heading">
          <div>
            <span className="home-kicker">YOUR RESEARCH, IN FOCUS</span>
            <h1 id="home-title">
              研究工作台<span className="home-heading-dot">.</span>
            </h1>
            <p>欢迎回来，{user.username}。从上次的思路，继续向前。</p>
          </div>
          <div className="home-date">
            <CalendarDays size={16} />
            <div>
              <time dateTime={today}>{dateLabel}</time>
              <small>北京时间 · UTC+8</small>
            </div>
          </div>
        </header>
        <div className="home-stats" aria-label="研究概览">
          {stats.map(({ label, value, unit, note, Icon, className }) => (
            <section className={`home-stat ${className}`} key={label}>
              <div className="home-stat-label">
                <span>{label}</span>
                <Icon size={19} />
              </div>
              <p className="home-stat-value">
                <strong>
                  {typeof value === "number"
                    ? value.toLocaleString("zh-CN")
                    : value}
                </strong>
                <span>{unit}</span>
              </p>
              <small>{note}</small>
            </section>
          ))}
        </div>
        <div className="home-middle">
          <section
            className="home-continue"
            aria-labelledby="home-continue-title"
          >
            <header className="home-section-head">
              <h2 id="home-continue-title">
                {opened.length ? "继续阅读" : "开始阅读"}
              </h2>
              <button className="home-text-link" onClick={() => onLibrary()}>
                查看文献库
                <ArrowUpRight size={15} />
              </button>
            </header>
            <Status
              loading={loading && !items.length}
              error={error}
              retry={onRefresh}
            />
            {suggestions[0] && (
              <PaperResume
                paper={suggestions[0]}
                featured
                translated={
                  !!suggestions[0].translated &&
                  preferences.tabDocuments?.[suggestions[0].id] === "translated"
                }
                resume={opened.length > 0}
                onRead={onRead}
              />
            )}
            {suggestions.length > 1 && (
              <div className="home-resume-list">
                {suggestions.slice(1).map((paper) => (
                  <PaperResume
                    key={paper.id}
                    paper={paper}
                    translated={
                      !!paper.translated &&
                      preferences.tabDocuments?.[paper.id] === "translated"
                    }
                    resume={opened.length > 0}
                    onRead={onRead}
                  />
                ))}
              </div>
            )}
            {!loading && !error && !items.length && (
              <div className="home-empty">
                <div className="home-empty-icon">
                  <BookOpen size={34} />
                </div>
                <h3>为新的想法，留一个位置</h3>
                <p>导入第一篇论文，开始阅读、翻译与探索。</p>
                <button className="primary" onClick={onImport}>
                  导入第一篇文献
                  <ArrowRight size={16} />
                </button>
              </div>
            )}
          </section>
          <ReadingTrend
            data={activity.data}
            error={activity.error}
            loading={activity.loading}
            retry={activity.refresh}
          />
        </div>
        <div className="home-calendar">
          <ReadingActivity
            source={activity}
            onRead={(id) => {
              const paper = items.find((p) => p.id === id);
              if (paper) onRead(paper);
            }}
            availablePaperIds={items.map((p) => p.id)}
          />
        </div>
        <div className="home-bottom">
          <section
            className="home-panel home-topics"
            aria-labelledby="home-topics-title"
          >
            <header className="home-section-head">
              <h2 id="home-topics-title">研究版图</h2>
              <span className="home-section-caption">按一级主题</span>
            </header>
            <Status
              error={topics.error}
              loading={topics.loading && !topics.loaded}
              retry={topics.refresh}
            />
            <div className="home-topic-list">
              {topTopics.map((topic, index) => (
                <button
                  className="home-topic"
                  key={topic.id}
                  onClick={() => onLibrary(`topic:${topic.id}`)}
                >
                  <span className={`home-topic-symbol tone-${index % 3}`}>
                    <FolderOpen size={17} />
                  </span>
                  <span className="home-topic-content">
                    <span className="home-topic-name">
                      {topic.name}
                      <small>{topic.count} 篇</small>
                    </span>
                    <span className="home-topic-track">
                      <i
                        style={
                          {
                            "--topic-fraction": `${Math.min(100, (topic.count / Math.max(1, topics.data?.total || items.length)) * 100)}%`,
                          } as CSSProperties
                        }
                      />
                    </span>
                  </span>
                  <ArrowUpRight size={15} />
                </button>
              ))}
            </div>
            {topics.loaded && !topTopics.length && (
              <p className="home-subtle-empty">
                研究方向会在整理文献后出现在这里。
              </p>
            )}
            <p className="home-panel-footnote">
              一篇论文可以属于多个研究主题。
              <button className="home-text-link" onClick={() => onLibrary()}>
                浏览全部
                <ArrowRight size={14} />
              </button>
            </p>
          </section>
          <section className="home-daily" aria-labelledby="home-daily-title">
            <div className="home-daily-art" aria-hidden="true">
              <span />
              <span />
              <Rss size={38} />
            </div>
            <span className="home-kicker">A LITTLE DISCOVERY, EVERY DAY</span>
            <h2 id="home-daily-title">
              让新的发现，
              <br />
              自然发生。
            </h2>
            <p>在 Daily arXiv，找到下一篇值得读的论文。</p>
            <Status error={daily.error} retry={daily.refresh} />
            <div className="home-daily-footer">
              <span>
                {daily.loading && !daily.loaded
                  ? "正在读取更新…"
                  : daily.data?.enabled === false
                    ? "尚未开启订阅"
                    : latest
                      ? `最近一期 · ${dateText(latest)}`
                      : "等待第一期论文"}
              </span>
              <button onClick={onDaily}>
                探索 Daily
                <ArrowUpRight size={16} />
              </button>
            </div>
          </section>
        </div>
        <footer className="home-footer">
          <span>iPaper · 留下阅读的轨迹，连接研究的线索。</span>
          <button className="home-text-link" onClick={() => onLibrary()}>
            <Search size={14} />
            在文献库中查找
            <ArrowDownLeft size={14} />
          </button>
        </footer>
      </div>
    </main>
  );
}
