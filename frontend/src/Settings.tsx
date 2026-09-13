import { StructuredSettings } from "./Processing";
import { UnderstandingSettings } from "./Understanding";
import {
  QualitySettings,
  Institutions,
  ReadingHistory,
} from "./DiscoverySettings";
import { useEffect, useState } from "react";
import {
  Settings as SettingsIcon,
  UserRound,
  KeyRound,
  Rss,
  ShieldCheck,
  Save,
  Plus,
  Trash2,
} from "lucide-react";
import { api, useResource, Field, Status, Modal, Confirm } from "./ui";
import { type User, errorText } from "./api";
export function Settings({
  user,
  onChanged,
  initialSection = "models",
}: {
  user: User;
  onChanged: () => void;
  initialSection?: string;
}) {
  const [tab, setTab] = useState(() => {
      const value = initialSection;
      return ["models", "daily", "account", "admin"].includes(value || "")
        ? value!
        : "models";
    }),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [busy, setBusy] = useState(false);
  const models = useResource<any>(
      tab === "models" ? "/api/settings/agentic" : null,
      null,
    ),
    profile = useResource<any>(
      tab === "account" ? "/api/settings/user" : null,
      null,
    ),
    daily = useResource<any>(
      tab === "daily" ? "/api/settings/daily-arxiv" : null,
      null,
    );
  const [current, setCurrent] = useState(""),
    [next, setNext] = useState(""),
    [deleteSecret, setDeleteSecret] = useState("");
  const [testing, setTesting] = useState("");
  useEffect(() => {
    if (["models", "daily", "account", "admin"].includes(initialSection))
      setTab(initialSection);
  }, [initialSection]);
  async function save() {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (tab === "models") {
        const data = models.data;
        const llmConfigs = Object.fromEntries(
          Object.entries(data.llmConfigs).map(([key, v]: any) => [
            key,
            {
              llmModel: v.llmModel,
              llmBaseUrl: v.llmBaseUrl,
              ...(v.llmApiKey ? { llmApiKey: v.llmApiKey } : {}),
            },
          ]),
        );
        await api("/api/settings/agentic", "POST", {
          llmConfigs,
          mineruUseApi: data.mineruUseApi,
          mineruServerUrl: data.mineruServerUrl,
          ...(data.mineruApiToken
            ? { mineruApiToken: data.mineruApiToken }
            : {}),
        });
        models.refresh();
      } else if (tab === "account")
        await api("/api/settings/user", "POST", profile.data);
      else if (tab === "daily")
        await api("/api/settings/daily-arxiv", "POST", daily.data);
      setNotice("设置已保存。");
      onChanged();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }
  function modelChange(scene: string, key: string, value: string) {
    models.setData((d: any) => ({
      ...d,
      llmConfigs: {
        ...d.llmConfigs,
        [scene]: { ...d.llmConfigs[scene], [key]: value },
      },
    }));
  }
  return (
    <section className="settings-page">
      <aside className="settings-nav">
        <h1>设置</h1>
        {[
          ["models", "模型与解析", KeyRound],
          ["daily", "Daily arXiv", Rss],
          ["account", "个人账号", UserRound],
          ...(user.role === "admin"
            ? [["admin", "用户与服务管理", ShieldCheck]]
            : []),
        ].map(([id, label, Icon]: any) => (
          <button
            className={tab === id ? "active" : ""}
            onClick={() => {
              setTab(id);
              const url = new URL(location.href);
              url.searchParams.set("section", id);
              history.replaceState({}, "", url);
              setError("");
              setNotice("");
            }}
            key={id}
          >
            <Icon size={17} />
            {label}
          </button>
        ))}
      </aside>
      <div className="settings-content">
        <header className="page-heading">
          <div>
            <span className="eyebrow">PREFERENCES</span>
            <h2>
              {tab === "models"
                ? "模型与解析服务"
                : tab === "daily"
                  ? "论文发现偏好"
                  : tab === "account"
                    ? "个人账号"
                    : "用户与服务管理"}
            </h2>
          </div>
          {tab !== "admin" && (
            <button
              className="primary"
              disabled={
                busy ||
                (
                  {
                    models: models.loading,
                    daily: daily.loading,
                    account: profile.loading,
                  } as any
                )[tab]
              }
              onClick={save}
            >
              <Save size={16} />
              保存设置
            </button>
          )}
        </header>
        <Status
          error={error || models.error || daily.error || profile.error}
          loading={models.loading || daily.loading || profile.loading}
        />
        {notice && (
          <p className="notice" role="status">
            {notice}
          </p>
        )}
        {tab === "models" && models.data && (
          <>
            <p className="muted">
              密钥只写入服务端。输入留空保留现有密钥，不会在浏览器回显。
            </p>
            <StructuredSettings />
            <UnderstandingSettings />
            {Object.entries({
              interpret: "论文问答与分析",
              translate: "BabelDOC 版式翻译",
              dailyArxiv: "Daily arXiv 筛选与摘要",
            }).map(([scene, label]) => (
              <section className="settings-card" key={scene}>
                <h3>{label}</h3>
                <div className="form-grid">
                  <Field label="模型名称">
                    <input
                      value={models.data.llmConfigs?.[scene]?.llmModel || ""}
                      onChange={(e) =>
                        modelChange(scene, "llmModel", e.target.value)
                      }
                    />
                  </Field>
                  <Field label="服务地址">
                    <input
                      value={models.data.llmConfigs?.[scene]?.llmBaseUrl || ""}
                      onChange={(e) =>
                        modelChange(scene, "llmBaseUrl", e.target.value)
                      }
                      placeholder="https://…/v1"
                    />
                  </Field>
                </div>
                <Field
                  label={
                    "API 密钥 · " +
                    (models.data.llmConfigs?.[scene]?.llmApiKeyConfigured
                      ? "已配置"
                      : "未配置")
                  }
                >
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={models.data.llmConfigs?.[scene]?.llmApiKey || ""}
                    placeholder="留空保留现有密钥"
                    onChange={(e) =>
                      modelChange(scene, "llmApiKey", e.target.value)
                    }
                  />
                </Field>
                <button onClick={() => setTesting(scene)}>测试连接</button>
                {models.data.llmConfigs?.[scene]?.llmApiKeyConfigured && (
                  <button
                    className="text-button danger"
                    onClick={() => setDeleteSecret(scene)}
                  >
                    删除该密钥
                  </button>
                )}
              </section>
            ))}
            <section className="settings-card">
              <h3>MinerU 解析</h3>
              <Field label="解析方式">
                <select
                  value={models.data.mineruUseApi ? "cloud" : "local"}
                  onChange={(e) =>
                    models.setData((d: any) => ({
                      ...d,
                      mineruUseApi: e.target.value === "cloud",
                    }))
                  }
                >
                  <option value="cloud">MinerU 云 API</option>
                  <option value="local">自托管 MinerU 服务</option>
                </select>
              </Field>
              {models.data.mineruUseApi ? (
                <Field
                  label={
                    "云 API 密钥 · " +
                    (models.data.mineruApiTokenConfigured ? "已配置" : "未配置")
                  }
                >
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={models.data.mineruApiToken || ""}
                    onChange={(e) =>
                      models.setData((d: any) => ({
                        ...d,
                        mineruApiToken: e.target.value,
                      }))
                    }
                    placeholder="留空保留现有密钥"
                  />
                </Field>
              ) : (
                <Field label="MinerU 服务地址">
                  <input
                    value={models.data.mineruServerUrl || ""}
                    onChange={(e) =>
                      models.setData((d: any) => ({
                        ...d,
                        mineruServerUrl: e.target.value,
                      }))
                    }
                  />
                </Field>
              )}
              <button
                onClick={() =>
                  setTesting(models.data.mineruUseApi ? "mineru-api" : "mineru")
                }
              >
                测试解析服务
              </button>
              {models.data.mineruApiTokenConfigured && (
                <button
                  className="text-button danger"
                  onClick={() => setDeleteSecret("mineru")}
                >
                  删除 MinerU 密钥
                </button>
              )}
            </section>
          </>
        )}
        {tab === "daily" && daily.data && (
          <>
            <DailySettings value={daily.data} onChange={daily.setData} />
            <QualitySettings value={daily.data} onChange={daily.setData} />
            <Institutions />
          </>
        )}
        {tab === "account" && profile.data && (
          <>
            <ReadingHistory />
            <section className="settings-card">
              <h3>个人资料</h3>
              <Field label="显示名称">
                <input
                  value={profile.data.name || ""}
                  onChange={(e) =>
                    profile.setData((d: any) => ({
                      ...d,
                      name: e.target.value,
                    }))
                  }
                />
              </Field>
              <Field label="AI 输出语言">
                <select
                  value={profile.data.aiLanguage || "zh"}
                  onChange={(e) =>
                    profile.setData((d: any) => ({
                      ...d,
                      aiLanguage: e.target.value,
                    }))
                  }
                >
                  <option value="zh">简体中文</option>
                  <option value="en">英语</option>
                </select>
              </Field>
              <Field label="头像">
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={async (e) => {
                    if (!e.target.files?.[0]) return;
                    const file = e.target.files[0];
                    try {
                      if (file.size > 2 * 1024 * 1024)
                        throw new Error("avatar_too_large");
                      const avatarData = await new Promise<string>(
                        (resolve, reject) => {
                          const reader = new FileReader();
                          reader.onload = () => resolve(String(reader.result));
                          reader.onerror = reject;
                          reader.readAsDataURL(file);
                        },
                      );
                      await api("/api/settings/avatar", "POST", { avatarData });
                      setNotice("头像已更新。");
                    } catch (e) {
                      setError(errorText(e));
                    }
                  }}
                />
              </Field>
            </section>
            <section className="settings-card">
              <h3>修改密码</h3>
              <Field label="当前密码">
                <input
                  type="password"
                  autoComplete="current-password"
                  value={current}
                  onChange={(e) => setCurrent(e.target.value)}
                />
              </Field>
              <Field label="新密码">
                <input
                  type="password"
                  autoComplete="new-password"
                  minLength={8}
                  maxLength={128}
                  value={next}
                  onChange={(e) => setNext(e.target.value)}
                />
              </Field>
              <button
                onClick={async () => {
                  try {
                    await api("/api/auth/change-password", "POST", {
                      current_password: current,
                      new_password: next,
                    });
                    setCurrent("");
                    setNext("");
                    setNotice("密码已更新。");
                  } catch (e) {
                    setError(errorText(e));
                  }
                }}
              >
                更新密码
              </button>
            </section>
          </>
        )}
        {tab === "admin" && <Admin />}
        {testing && (
          <Confirm
            title="测试服务连接"
            detail="将向当前填写的服务发送验证请求；模型测试可能产生费用。不会自动重试。"
            onClose={() => setTesting("")}
            onConfirm={async () => {
              if (testing === "mineru-api")
                await api("/api/settings/test/mineru-api", "POST", {
                  apiToken: models.data.mineruApiToken || "",
                });
              else if (testing === "mineru")
                await api("/api/settings/test/mineru", "POST", {
                  mineruServerUrl: models.data.mineruServerUrl || "",
                });
              else {
                const v = models.data.llmConfigs?.[testing] || {};
                await api("/api/settings/test/llm", "POST", {
                  llmConfigType: testing,
                  llmModel: v.llmModel || "",
                  llmBaseUrl: v.llmBaseUrl || "",
                  llmApiKey: v.llmApiKey || "",
                });
              }
              setNotice("服务连接测试通过。");
            }}
          />
        )}
        {deleteSecret && (
          <Confirm
            title="删除服务密钥"
            detail="删除后相应服务将不可用，直到重新配置。"
            onClose={() => setDeleteSecret("")}
            onConfirm={async () => {
              await api(
                "/api/settings/agentic/secrets/" + deleteSecret,
                "DELETE",
              );
              models.refresh();
            }}
          />
        )}
      </div>
    </section>
  );
}
function DailySettings({
  value: v,
  onChange,
}: {
  value: any;
  onChange: (v: any) => void;
}) {
  const set = (key: string, value: any) => onChange({ ...v, [key]: value });
  return (
    <>
      <section className="settings-card">
        <h3>发现与更新</h3>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={!!v.enabled}
            onChange={(e) => set("enabled", e.target.checked)}
          />
          启用 Daily arXiv
        </label>
        <div className="form-grid">
          {Object.entries({
            checkIntervalMinutes: "检查间隔（分钟）",
            retentionDays: "保留天数",
            maxDailyPapers: "每日论文数量",
            maxNewPapersPerCategoryPerFetch: "每分类新增上限",
            replacementCandidateLimit: "候选替换上限",
            maxKeywords: "关键词数量",
          }).map(([key, label]) => (
            <Field key={key} label={label}>
              <input
                type="number"
                min={1}
                value={v[key] ?? 1}
                onChange={(e) => set(key, Number(e.target.value))}
              />
            </Field>
          ))}
        </div>
        <Field label="arXiv 分类（逗号分隔）">
          <input
            value={(v.categories || []).join(", ")}
            onChange={(e) =>
              set(
                "categories",
                e.target.value
                  .split(/[,，]/)
                  .map((x) => x.trim())
                  .filter(Boolean),
              )
            }
          />
        </Field>
        <Field label="关键词（每行一个）">
          <textarea
            rows={5}
            value={(v.keywordList || []).join("\n")}
            onChange={(e) => set("keywordList", e.target.value.split("\n"))}
          />
        </Field>
        <Field label="筛选策略">
          <select
            value={v.qualityConfig?.strategy || "balanced"}
            onChange={(e) =>
              set("qualityConfig", {
                ...v.qualityConfig,
                strategy: e.target.value,
              })
            }
          >
            {Object.keys(v.qualityConfig?.strategies || { balanced: {} }).map(
              (key) => (
                <option key={key} value={key}>
                  {(
                    {
                      balanced: "均衡",
                      strict: "严格",
                      broad: "广泛",
                      discovery: "探索",
                      exploratory: "探索",
                    } as any
                  )[key] ||
                    v.qualityConfig.strategies[key]?.label ||
                    key}
                </option>
              ),
            )}
          </select>
        </Field>
      </section>
      <section className="settings-card">
        <h3>研究兴趣</h3>
        <label className="checkbox-field">
          <input
            type="checkbox"
            checked={!!v.topicFilteringEnabled}
            onChange={(e) => set("topicFilteringEnabled", e.target.checked)}
          />
          按研究兴趣筛选
        </label>
        {(v.researchTopics || []).map((topic: any, i: number) => (
          <div className="topic-editor" key={topic.id}>
            <div className="form-grid">
              <Field label="主题名称">
                <input
                  value={topic.name || ""}
                  onChange={(e) =>
                    set(
                      "researchTopics",
                      v.researchTopics.map((t: any, n: number) =>
                        n === i ? { ...t, name: e.target.value } : t,
                      ),
                    )
                  }
                />
              </Field>
              <Field label="每日配额">
                <input
                  type="number"
                  min={1}
                  max={24}
                  value={topic.quota || 1}
                  onChange={(e) =>
                    set(
                      "researchTopics",
                      v.researchTopics.map((t: any, n: number) =>
                        n === i ? { ...t, quota: Number(e.target.value) } : t,
                      ),
                    )
                  }
                />
              </Field>
            </div>
            <Field label="召回词组（逗号分隔）">
              <textarea
                value={(topic.phrases || []).join(", ")}
                onChange={(e) =>
                  set(
                    "researchTopics",
                    v.researchTopics.map((t: any, n: number) =>
                      n === i
                        ? {
                            ...t,
                            phrases: e.target.value
                              .split(/[,，\n]/)
                              .map((s) => s.trim()),
                          }
                        : t,
                    ),
                  )
                }
              />
            </Field>
            <button
              className="text-button danger"
              onClick={() =>
                set(
                  "researchTopics",
                  v.researchTopics.filter((_: any, n: number) => n !== i),
                )
              }
            >
              删除主题
            </button>
          </div>
        ))}
        <button
          onClick={() =>
            set("researchTopics", [
              ...(v.researchTopics || []),
              {
                id: crypto.randomUUID(),
                name: "新研究主题",
                quota: 1,
                phrases: [],
              },
            ])
          }
        >
          <Plus size={15} />
          添加主题
        </button>
      </section>
      <details className="settings-card">
        <summary>高级筛选设置</summary>
        <Field label="机构识别提示词">
          <textarea
            rows={8}
            value={v.affiliationPrompt || ""}
            onChange={(e) => set("affiliationPrompt", e.target.value)}
          />
        </Field>
        {v.categoryRatios &&
          Object.keys(v.categoryRatios).map((key) => (
            <Field key={key} label={key + " 分类比例"}>
              <input
                type="number"
                min={0}
                value={v.categoryRatios[key]}
                onChange={(e) =>
                  set("categoryRatios", {
                    ...v.categoryRatios,
                    [key]: Number(e.target.value),
                  })
                }
              />
            </Field>
          ))}
      </details>
    </>
  );
}
function Admin() {
  const users = useResource<any>("/api/admin/users", { users: [] }),
    invites = useResource<any>("/api/admin/invites", {}),
    providers = useResource<any>("/api/admin/ai-providers", { providers: [] });
  const [secret, setSecret] = useState(""),
    [error, setError] = useState(""),
    [url, setUrl] = useState(""),
    [name, setName] = useState("");
  async function run(fn: () => Promise<any>) {
    try {
      const r = await fn();
      if (r.invite_code || r.reset_code)
        setSecret(r.invite_code || r.reset_code);
      users.refresh();
      invites.refresh();
      providers.refresh();
    } catch (e) {
      setError(errorText(e));
    }
  }
  return (
    <>
      <Status error={error || users.error || providers.error} />
      <section className="settings-card">
        <h3>用户管理</h3>
        {users.data.users.map((u: any) => (
          <div className="admin-row" key={u.id}>
            <strong>{u.username}</strong>
            <select
              aria-label={u.username + "角色"}
              value={u.role}
              onChange={(e) =>
                run(() =>
                  api("/api/admin/users/" + u.id, "PATCH", {
                    role: e.target.value,
                  }),
                )
              }
            >
              <option value="user">用户</option>
              <option value="admin">管理员</option>
            </select>
            <button
              onClick={() =>
                run(() =>
                  api("/api/admin/users/" + u.id, "PATCH", {
                    status: u.status === "active" ? "disabled" : "active",
                  }),
                )
              }
            >
              {u.status === "active" ? "停用" : "启用"}
            </button>
            <button
              onClick={() =>
                run(() =>
                  api(
                    "/api/admin/users/" + u.id + "/password-reset",
                    "POST",
                    {},
                  ),
                )
              }
            >
              生成重置码
            </button>
          </div>
        ))}
        <button
          onClick={() => run(() => api("/api/admin/invites", "POST", {}))}
        >
          <Plus size={16} />
          生成邀请码
        </button>
        {(invites.data.invites || []).map((v: any) => (
          <div className="admin-row" key={v.id}>
            <span>邀请码 · {v.id.slice(0, 8)}</span>
            <span>
              {v.used_at ? "已使用" : v.revoked_at ? "已撤销" : "有效"}
            </span>
            <button
              onClick={() =>
                run(() => api("/api/admin/invites/" + v.id, "DELETE"))
              }
            >
              撤销
            </button>
          </div>
        ))}
      </section>
      <section className="settings-card">
        <h3>允许的模型服务</h3>
        <p className="muted">
          只有批准的服务地址能被模型配置使用。密钥在各账号设置中单独保存。
        </p>
        {providers.data.providers.map((p: any) => (
          <div className="admin-row" key={p.id}>
            <div>
              <strong>{p.name || "模型服务"}</strong>
              <small>{p.origin}</small>
            </div>
            <button
              onClick={() =>
                run(() =>
                  api("/api/admin/ai-providers/" + p.id, "PATCH", {
                    enabled: !p.enabled,
                  }),
                )
              }
            >
              {p.enabled ? "停用" : "启用"}
            </button>
            <button
              onClick={() =>
                run(() => api("/api/admin/ai-providers/" + p.id, "DELETE"))
              }
            >
              删除
            </button>
          </div>
        ))}
        <Field label="服务名称">
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="服务地址">
          <input
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…"
          />
        </Field>
        <button
          onClick={() =>
            run(() => api("/api/admin/ai-providers", "POST", { url, name }))
          }
        >
          批准服务
        </button>
      </section>
      {secret && (
        <Modal title="仅显示一次" onClose={() => setSecret("")}>
          <p>请安全交给对应用户，关闭后不再显示。</p>
          <output className="one-time-code">{secret}</output>
          <button
            onClick={() => {
              void navigator.clipboard.writeText(secret);
            }}
          >
            复制
          </button>
        </Modal>
      )}
    </>
  );
}
