import { useState } from "react";
import { api, useResource, Status, Field, Confirm, dateText } from "./ui";
import { errorText } from "./api";

export function QualitySettings({
  value,
  onChange,
}: {
  value: any;
  onChange: (v: any) => void;
}) {
  const q = value.qualityConfig || {};
  return (
    <section className="settings-card">
      <h3>机构与筛选标准</h3>
      <p className="muted">
        机构分级用于现有 Daily 筛选规则，可按研究方向调整。
      </p>
      <div className="form-grid">
        {["S", "A", "B", "C"].map((tier) => (
          <Field key={tier} label={tier + " 级机构（每行一个）"}>
            <textarea
              rows={5}
              value={(q.institutionTiers?.[tier] || []).join("\n")}
              onChange={(e) =>
                onChange({
                  ...value,
                  qualityConfig: {
                    ...q,
                    institutionTiers: {
                      ...q.institutionTiers,
                      [tier]: e.target.value.split("\n"),
                    },
                  },
                })
              }
            />
          </Field>
        ))}
      </div>
      {Object.entries(q.strategies || {}).map(([key, v]: any) => (
        <details key={key}>
          <summary>
            {(
              { strict: "严格", balanced: "均衡", discovery: "探索" } as Record<
                string,
                string
              >
            )[key] || key}
            策略
          </summary>
          <div className="form-grid">
            <Field label="每分类最多论文">
              <input
                type="number"
                min={1}
                max={500}
                value={v.maxPapersPerCategory || 1}
                onChange={(e) =>
                  onChange({
                    ...value,
                    qualityConfig: {
                      ...q,
                      strategies: {
                        ...q.strategies,
                        [key]: {
                          ...v,
                          maxPapersPerCategory: Number(e.target.value),
                        },
                      },
                    },
                  })
                }
              />
            </Field>
            <Field label="最低机构级别">
              <select
                value={v.minInstitutionTier || "B"}
                onChange={(e) =>
                  onChange({
                    ...value,
                    qualityConfig: {
                      ...q,
                      strategies: {
                        ...q.strategies,
                        [key]: { ...v, minInstitutionTier: e.target.value },
                      },
                    },
                  })
                }
              >
                {["S", "A", "B", "C"].map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
          </div>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={!!v.allowUnknownInstitutions}
              onChange={(e) =>
                onChange({
                  ...value,
                  qualityConfig: {
                    ...q,
                    strategies: {
                      ...q.strategies,
                      [key]: {
                        ...v,
                        allowUnknownInstitutions: e.target.checked,
                      },
                    },
                  },
                })
              }
            />
            允许未知机构
          </label>
        </details>
      ))}
    </section>
  );
}

export function Institutions() {
  const resource = useResource<any>("/api/custom-institutions", {
    institutions: [],
  });
  const [name, setName] = useState(""),
    [variants, setVariants] = useState(""),
    [error, setError] = useState(""),
    [deleting, setDeleting] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <section className="settings-card">
      <h3>机构名称归一化</h3>
      <p className="muted">
        将同一机构的不同名称合并为缩写。这部分修改即时保存。
      </p>
      <Status error={error || resource.error} />
      {resource.data.institutions.map((v: any) => (
        <div className="admin-row" key={v.abbreviation}>
          <strong>{v.abbreviation}</strong>
          <span>{v.variants.join(" · ")}</span>
          <button
            onClick={() => {
              setName(v.abbreviation);
              setVariants(v.variants.join("\n"));
            }}
          >
            编辑
          </button>
          <button onClick={() => setDeleting(v.abbreviation)}>删除</button>
        </div>
      ))}
      <Field label="机构缩写">
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="机构全称与别名（每行一个）">
        <textarea
          value={variants}
          onChange={(e) => setVariants(e.target.value)}
          rows={4}
        />
      </Field>
      <button
        disabled={busy || !name.trim() || !variants.trim()}
        onClick={async () => {
          setBusy(true);
          setError("");
          try {
            await api("/api/custom-institutions", "POST", {
              abbreviation: name.trim(),
              variants: variants.split("\n").filter(Boolean),
            });
            resource.refresh();
            setName("");
            setVariants("");
          } catch (e) {
            setError(errorText(e));
          } finally {
            setBusy(false);
          }
        }}
      >
        保存机构
      </button>
      {deleting && (
        <Confirm
          title="删除机构映射"
          detail={"删除 " + deleting + " 的自定义映射。"}
          onClose={() => setDeleting("")}
          onConfirm={async () => {
            await api(
              "/api/custom-institutions/" + encodeURIComponent(deleting),
              "DELETE",
            );
            resource.refresh();
          }}
        />
      )}
    </section>
  );
}

