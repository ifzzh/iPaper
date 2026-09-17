import "vite/modulepreload-polyfill";
import { createRoot } from "react-dom/client";
import { useEffect, useRef, useState, lazy, Suspense } from "react";
import {
  Library as LibraryIcon,
  Rss,
  Settings as SettingsIcon,
  ListTodo,
  Plus,
  X,
  Sun,
  Moon,
  LogOut,
  Upload,
  BookOpen,
  ChevronLeft,
  Menu,
} from "lucide-react";
import { Auth } from "./Auth";
import { Library, type Category } from "./Library";
import { ImportDialog, Tasks, type LocalTask } from "./Transfers";
import { api, Status, Markdown, useResource } from "./ui";
import { papers, paperFrom, type Paper, type User, errorText } from "./api";
import "./style.css";
const Settings = lazy(() =>
  import("./Settings").then((m) => ({ default: m.Settings })),
);
const Daily = lazy(() => import("./Daily").then((m) => ({ default: m.Daily })));
const Reader = lazy(() =>
  import("./Understanding").then((m) => ({ default: m.PaperWorkspace })),
);
type View = "library" | "reader" | "daily" | "settings" | "tasks" | "analysis";
const route = () => {
  const p = new URLSearchParams(location.search);
  return {
    view: (["reader", "daily", "settings", "tasks", "analysis"].includes(
      p.get("view") || "",
    )
      ? p.get("view")
      : "library") as View,
    paper: p.get("paper") || "",
    translated: p.get("document") === "translated",
  };
};
function App() {
  const [user, setUser] = useState<User | null>(null),
    [checking, setChecking] = useState(true),
    [items, setItems] = useState<Paper[]>([]),
    [loading, setLoading] = useState(false),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [locationState, setLocation] = useState(route),
    [tabs, setTabs] = useState<string[]>([]),
    [theme, setTheme] = useState("light"),
    [importing, setImporting] = useState(false),
    [filter, setFilter] = useState("all"),
    [revision, setRevision] = useState(0),
    [stateReady, setStateReady] = useState(false),
    [taskRefs, setTaskRefs] = useState<LocalTask[]>([]),
    [logoutFailed, setLogoutFailed] = useState(false),
    [layoutRevision, setLayoutRevision] = useState(0),
    [stateRevision, setStateRevision] = useState(0),
    [sidebarOpen, setSidebarOpen] = useState(false);
  const logoutBlocked = useRef(false),
    authCheck = useRef<AbortController | null>(null),
    saveQueue = useRef(Promise.resolve()),
    saveController = useRef(new AbortController());
  const controller = useRef<AbortController | null>(null),
    epoch = useRef(0),
    drafts = useRef(new Map<string, string>()),
    identity = useRef<User | null>(null),
    preferences = useRef<any>({}),
    channel = useRef<BroadcastChannel | null>(null),
    shell = useRef<HTMLDivElement>(null);
  const allowed = !!user && !user.must_change_password;
  const categories = useResource<Category>(
    allowed ? "/api/library/navigation" : null,
    {
      id: "root",
      name: "Root",
      children: [],
    },
  );
  const detail = useResource<any>(
    allowed && locationState.paper
      ? "/api/paper/" + encodeURIComponent(locationState.paper)
      : null,
    null,
  );
  const selectedPaper =
    items.find((p) => p.id === locationState.paper) ||
    (detail.data?.id === locationState.paper ? paperFrom(detail.data) : null);
  function navigate(
    view: View,
    paper = locationState.paper,
    translated = locationState.translated,
    replace = false,
    section = "models",
  ) {
    const p = new URLSearchParams();
    if (view !== "library") p.set("view", view);
    if (paper) p.set("paper", paper);
    if (view === "settings") p.set("section", section);
    if (view === "reader")
      p.set("document", translated ? "translated" : "original");
    if (view === "reader" && paper)
      updatePreferences({
        tabDocuments: {
          ...preferences.current.tabDocuments,
          [paper]: translated ? "translated" : "original",
        },
      });
    history[replace ? "replaceState" : "pushState"](
      {},
      "",
      "/" + (p.size ? "?" + p : ""),
    );
    setLocation({ view, paper, translated });
  }
  function updatePreferences(value: Record<string, unknown>) {
    preferences.current = { ...preferences.current, ...value };
    setLayoutRevision((n) => n + 1);
  }
  function clear() {
    authCheck.current?.abort();
    saveController.current.abort();
    saveController.current = new AbortController();
    epoch.current++;
    preferences.current = {};
    setTheme("light");
    controller.current?.abort();
    setUser(null);
    identity.current = null;
    setItems([]);
    setTabs([]);
    setTaskRefs([]);
    setStateReady(false);
    detail.setData(null);
    categories.setData({ id: "root", name: "Root", children: [] });
    drafts.current.clear();
    setImporting(false);
  }
  async function check() {
    authCheck.current?.abort();
    const c = new AbortController();
    authCheck.current = c;
    try {
      const s = await api("/api/auth/session", "GET", undefined, c.signal);
      if (c.signal.aborted || logoutBlocked.current) return;
      if (!s.authenticated) {
        clear();
        return;
      }
      if (identity.current && identity.current.id !== s.user.id) clear();
      identity.current = s.user;
      setUser(s.user);
    } catch (e) {
      if (!c.signal.aborted) setError("无法检查登录状态，请重新加载。");
    } finally {
      setChecking(false);
    }
  }
  useEffect(() => {
    void check();
    const expired = () => {
        clear();
        setNotice("会话已失效，请重新登录。");
      },
      focus = () => {
        if (!logoutBlocked.current) void check();
      };
    window.addEventListener("ipaper-session-expired", expired);
    window.addEventListener("focus", focus);
    const pop = () => setLocation(route());
    window.addEventListener("popstate", pop);
    if ("BroadcastChannel" in window) {
      channel.current = new BroadcastChannel("ipaper-auth");
      channel.current.onmessage = () => {
        logoutBlocked.current = true;
        clear();
        setNotice("账号状态已在其他标签页改变，请重新登录。");
      };
    }
    return () => {
      controller.current?.abort();
      channel.current?.close();
      window.removeEventListener("ipaper-session-expired", expired);
      window.removeEventListener("focus", focus);
      window.removeEventListener("popstate", pop);
    };
  }, []);
  async function logout() {
    logoutBlocked.current = true;
    clear();
    channel.current?.postMessage("logout");
    setLogoutFailed(false);
    try {
      await api("/api/auth/session", "DELETE");
      setNotice("已退出登录。");
    } catch (e) {
      setLogoutFailed(true);
      setNotice("本地内容已清空，但服务端退出未成功，请重试退出。");
    }
  }
  useEffect(() => {
    if (!allowed) return;
    const c = new AbortController();
    controller.current = c;
    const seq = ++epoch.current;
    setLoading(true);
    setError("");
    papers(c.signal)
      .then((data) => {
        if (seq === epoch.current) setItems(data);
      })
      .catch((e) => {
        if (!c.signal.aborted) setError(errorText(e));
      })
      .finally(() => {
        if (!c.signal.aborted) setLoading(false);
      });
    return () => c.abort();
  }, [user?.id, allowed, revision]);
  useEffect(() => {
    if (!allowed) return;
    const c = new AbortController();
    api("/api/workspace/state", "GET", undefined, c.signal)
      .then((s) => {
        if (c.signal.aborted) return;
        preferences.current = s;
        setTabs(s.tabs || []);
        setTheme(s.theme === "dark" ? "dark" : "light");
        setTaskRefs(s.taskRefs || []);
        setStateReady(true);
      })
      .catch((e) => {
        if (!c.signal.aborted) {
          setNotice("工作区偏好暂时无法加载，稍后可重试。");
          setStateReady(false);
        }
      });
    return () => c.abort();
  }, [user?.id, allowed, stateRevision]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    return () => {};
  }, [theme]);
  useEffect(() => {
    if (!sidebarOpen) return;
    const close = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [sidebarOpen]);
  useEffect(() => {
    if (!allowed || !stateReady) return;
    const owner = user!.id,
      signal = saveController.current.signal;
    const t = setTimeout(() => {
      const value = {
        ...preferences.current,
        tabs,
        activePaper: tabs.includes(locationState.paper)
          ? locationState.paper
          : null,
        theme,
        taskRefs,
        readerResults: Object.fromEntries(
          Object.entries(preferences.current.readerResults || {}).filter(
            ([id]) => tabs.includes(id),
          ),
        ),
        tabDocuments: Object.fromEntries(
          Object.entries(preferences.current.tabDocuments || {}).filter(
            ([id]) => tabs.includes(id),
          ),
        ),
      };
      saveQueue.current = saveQueue.current.then(async () => {
        if (signal.aborted || identity.current?.id !== owner) return;
        try {
          await api("/api/workspace/state", "PUT", value, signal);
        } catch (e) {
          if (!signal.aborted) setNotice("工作区偏好保存失败，请稍后重试。");
        }
      });
    }, 400);
    return () => clearTimeout(t);
  }, [
    tabs,
    theme,
    locationState.paper,
    taskRefs,
    stateReady,
    user?.id,
    layoutRevision,
  ]);
  useEffect(() => {
    if (
      stateReady &&
      ["reader", "analysis"].includes(locationState.view) &&
      selectedPaper
    )
      setTabs((v) =>
        v.includes(selectedPaper.id) ? v : [...v.slice(-19), selectedPaper.id],
      );
  }, [stateReady, locationState.view, selectedPaper?.id]);
  function read(p: Paper) {
    if (!tabs.includes(p.id)) {
      if (tabs.length >= 20) {
        setNotice("最多打开20篇论文，请先关闭不用的标签。");
        return;
      }
      setTabs((v) => [...v, p.id]);
    }
    navigate(
      "reader",
      p.id,
      preferences.current.tabDocuments?.[p.id] === "translated",
    );
  }
  function closeTab(id: string) {
    const rest = tabs.filter((v) => v !== id);
    setTabs(rest);
    if (locationState.paper === id) {
      if (rest.length) {
        const next = rest[rest.length - 1];
        navigate(
          "reader",
          next,
          preferences.current.tabDocuments?.[next] === "translated",
        );
      } else navigate("library", "");
    }
  }
  function task(t: LocalTask) {
    setTaskRefs((v) => [t, ...v.filter((x) => x.id !== t.id)].slice(0, 50));
    navigate("tasks");
    setRevision((n) => n + 1);
  }
  const changed = () => {
    setRevision((n) => n + 1);
    categories.refresh();
    detail.refresh();
  };
  if (checking)
    return (
      <div className="startup">
        <span className="brand-mark">P</span>
        <Status loading />
      </div>
    );
  if (!allowed)
    return (
      <>
        {error && (
          <div className="auth-notice">
            <Status error={error} retry={() => void check()} />
          </div>
        )}
        {notice && (
          <div className="auth-notice" role="status">
            {notice}
            {logoutFailed && <button onClick={logout}>重试退出</button>}
          </div>
        )}
        <Auth
          user={user}
          onAuthenticated={(u) => {
            authCheck.current?.abort();
            identity.current = u;
            setUser(u);
            setNotice("");
            setError("");
            setLogoutFailed(false);
            logoutBlocked.current = false;
          }}
          onLogout={logout}
        />
      </>
    );
  const nav = [
    ["library", "文献库", LibraryIcon],
    ["daily", "Daily arXiv", Rss],
    ["tasks", "任务中心", ListTodo],
    ["settings", "设置", SettingsIcon],
  ] as const;
  return (
    <div className="app-shell" ref={shell}>
      <aside
        className={"app-sidebar" + (sidebarOpen ? " open" : "")}
        aria-label="应用导航"
      >
        <div className="sidebar-brand">
          <button
            className="brand-mark"
            aria-label="iPaper 文献库"
            onClick={() => {
              navigate("library", "");
              setSidebarOpen(false);
            }}
          >
            i
          </button>
          <div className="brand-text">
            <strong>iPaper</strong>
            <small>论文与研究工作台</small>
          </div>
        </div>
        <nav className="sidebar-nav" aria-label="主导航">
          {nav.map(([id, label, Icon]) => (
            <button
              key={id}
              className={
                locationState.view === id ||
                (["reader", "analysis"].includes(locationState.view) &&
                  id === "library")
                  ? "active"
                  : ""
              }
              onClick={() => {
                navigate(id);
                setSidebarOpen(false);
              }}
            >
              <Icon size={18} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <BookOpen size={16} />
          <div>
            本地优先，安全存储
            <small>{items.length} 篇文献</small>
          </div>
        </div>
      </aside>
      {sidebarOpen && (
        <button
          className="sidebar-scrim"
          aria-label="关闭导航"
          onClick={() => setSidebarOpen(false)}
        />
      )}
      <div className="app-main">
        <header className="app-header">
          <button
            className="icon-button sidebar-toggle"
            aria-label="打开导航"
            title="打开导航"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu size={18} />
          </button>
          <button
            className="brand"
            aria-label="iPaper 文献库"
            onClick={() => navigate("library", "")}
          >
            iPaper
          </button>
          <div className="workspace-tabs" role="tablist" aria-label="打开的论文">
            {tabs.map((id) => (
              <div
                className={
                  "paper-tab " +
                  (["reader", "analysis"].includes(locationState.view) &&
                  id === locationState.paper
                    ? "active"
                    : "")
                }
                key={id}
              >
                <button
                  role="tab"
                  aria-selected={
                    ["reader", "analysis"].includes(locationState.view) &&
                    id === locationState.paper
                  }
                  onClick={() => {
                    navigate(
                      "reader",
                      id,
                      preferences.current.tabDocuments?.[id] === "translated",
                    );
                    setSidebarOpen(false);
                  }}
                  title={items.find((p) => p.id === id)?.title}
                >
                  <span>
                    {items.find((p) => p.id === id)?.title || "正在加载论文…"}
                  </span>
                </button>
                <button
                  className="icon-button"
                  aria-label={
                    "关闭论文标签 " +
                    (items.find((p) => p.id === id)?.title || id)
                  }
                  onClick={() => closeTab(id)}
                >
                  <X size={13} />
                </button>
              </div>
            ))}
          </div>
          <div className="header-actions">
            <button className="primary" onClick={() => setImporting(true)}>
              <Upload size={15} />
              <span>导入文献</span>
            </button>
            <button
              className="icon-button"
              aria-label="切换浅深主题"
              title="切换浅深主题"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
            </button>
            <button
              onClick={() =>
                navigate(
                  "settings",
                  locationState.paper,
                  false,
                  false,
                  "account",
                )
              }
              title="账号设置"
            >
              {user.username}
            </button>
            <button
              className="icon-button"
              aria-label="退出登录"
              title="退出登录"
              onClick={logout}
            >
              <LogOut size={17} />
            </button>
          </div>
        </header>
        {notice && (
          <div className="global-notice" role="status">
            {notice}
            {!stateReady && allowed && (
              <button onClick={() => setStateRevision((n) => n + 1)}>
                重新加载偏好
              </button>
            )}
            <button
              className="icon-button"
              aria-label="关闭提示"
              onClick={() => setNotice("")}
            >
              <X size={14} />
            </button>
          </div>
        )}
        <div className="app-content">
          <Suspense fallback={<Status loading />}>
            {locationState.view === "library" && (
              <Library
                preferences={preferences.current}
                onPreferences={updatePreferences}
                items={items}
                selected={locationState.paper}
                onSelect={(id) => navigate("library", id)}
                onRead={read}
                onChanged={changed}
                onImport={() => setImporting(true)}
                onTasks={(t) => (t ? task(t) : navigate("tasks"))}
                onAnalysis={(p) => {
                  setTabs((v) => (v.includes(p.id) ? v : [...v, p.id]));
                  navigate("analysis", p.id);
                }}
                loading={loading}
                error={
                  error ||
                  (locationState.paper && !selectedPaper ? detail.error : "")
                }
                tree={categories.data}
                refreshTree={categories.refresh}
                filter={filter}
                setFilter={setFilter}
              />
            )}
            {["reader", "analysis"].includes(locationState.view) &&
              (selectedPaper ? (
                <Suspense fallback={<Status loading />}>
                  <Reader
                    initialView={
                      locationState.view === "analysis" ? "analysis" : "reader"
                    }
                    preferences={preferences.current}
                    onPreferences={updatePreferences}
                    key={selectedPaper.id}
                    paper={selectedPaper}
                    translated={locationState.translated}
                    onVersion={(v) => navigate("reader", selectedPaper.id, v)}
                    onClose={() => navigate("library")}
                    onExpired={clear}
                    drafts={drafts.current}
                  />
                </Suspense>
              ) : (
                <div className="empty-state">
                  <Status
                    error={detail.error || error}
                    loading={loading || detail.loading}
                    retry={changed}
                  />
                  <p>请选择有效的文献。</p>
                  <button onClick={() => navigate("library", "")}>
                    返回文献库
                  </button>
                </div>
              ))}
            {locationState.view === "daily" && (
              <Daily
                onRead={(id) => {
                  setTabs((v) => (v.includes(id) ? v : [...v, id]));
                  navigate(
                    "reader",
                    id,
                    preferences.current.tabDocuments?.[id] === "translated",
                  );
                  changed();
                }}
                onSettings={() =>
                  navigate(
                    "settings",
                    locationState.paper,
                    false,
                    false,
                    "daily",
                  )
                }
                onChanged={changed}
              />
            )}
            {locationState.view === "settings" && (
              <Settings
                initialSection={
                  new URLSearchParams(location.search).get("section") ||
                  "models"
                }
                user={user}
                onChanged={changed}
              />
            )}
            {locationState.view === "tasks" && (
              <Tasks
                localTasks={taskRefs}
                onTask={task}
                onRead={(id) => {
                  setTabs((v) => (v.includes(id) ? v : [...v, id]));
                  navigate(
                    "reader",
                    id,
                    preferences.current.tabDocuments?.[id] === "translated",
                  );
                  changed();
                }}
              />
            )}
          </Suspense>
        </div>
      </div>
      {importing && (
        <ImportDialog
          tree={categories.data}
          onClose={() => {
            setImporting(false);
            changed();
          }}
          onTask={task}
        />
      )}
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
