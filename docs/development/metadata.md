# 论文入库识别与书目信息

1.5.0 在统一文献库提供“补全信息”、修订编辑、完整作者、字段来源、BibTeX 和重复提示。只处理书目，不增加标签分类或模型任务。文件通过 Document Worker 安全检查后即可阅读；首页文本与内嵌信息随入库保存，联网补全使用独立持久队列。

## 使用

在论文详情点击编辑图标修改书目。作者可以逐位添加、排序或标为团体；不能可靠拆分的历史作者字符串保留原文。实际编辑过的字段（包括清空）受到保护。两个页面同时编辑会返回修订冲突，草稿保留，需要明确重新载入核对。

“补全信息”只填未保护的缺失字段；新上传的文件名／内嵌标题属于临时线索，只有核实身份后才能替换显示标题。来源不明的存量非空字段保守保留。来源面板可查看候选，用户逐项采用会成为人工选择。保存和补全不会改论文 UUID、文件名、路径或任何内容绑定。

批量选择可以选择当前页或全部匹配论文；创建任务时固定 owner 隔离的完整名单。任务中心显示分页明细、进度、取消与失败重试。Daily 未入库候选不属于全库补全范围，也不会因此下载 PDF。重复提示只提供打开和忽略，不合并或删除文件；同文件、同标识与相关版本分别说明。

详情可复制或下载当前 BibTeX；分类 BibTeX 使用同一生成器。人工修改后的引用立即按当前书目生成，作者摘要不会被 AI 结果覆盖。

## 数据与兼容

新增 bibliography、bibliography_revisions、bibliography_inspections、bibliography_candidates、metadata_batches/items/events、metadata_http_cache/provider_limits/index_events 和 metadata_duplicate_dismissals。所有书目正文、证据及检查点在 SQLite 内，不增加资产目录或常驻容器。主库和任务登记在同一事务，失败回滚。主库成功后再发布内存与搜索索引；索引失败保留 outbox，重启只重新索引，不再请求供应商。

旧论文字段是当前规范书目的兼容投影。后台整对象保存不能覆盖投影，阅读时长通过锁内增量更新。旧 `PUT /api/paper/<id>` 走相同人工保护层；`POST .../refresh-metadata` 返回真实补全任务，已移除自动重命名。回退后再次升级比较旧投影与当前旧字段，保留旧版本期间的修改及备注，不让扩展表的旧快照覆盖新数据。

每篇内容保留完整作者、机构、标识符、来源链接、发表信息及分开的预印本／版本／发表日期。历史原始 BibTeX 不覆盖，下载使用当前字段派生。线上最新 arXiv 版本不能证明现有文件版本；未能从下载标识或首页核实时单独记录，不替换 PDF。

## 供应商与身份

- arXiv：精确 ID／版本，至少 3 秒全局间隔。关联 DOI 仅在书目身份一致时补充 Crossref 的发表字段；不换掉预印本版本的作者／摘要。
- Crossref：已知 DOI 精确读取，否则多候选检索。响应头可进一步降低保守速率。
- DBLP：无 DOI 的计算机论文多候选检索，结合作者、标题、年份核对，不直接取第一条。来源不可用时可以继续其他既定来源。
- OpenReview：仅在明确 forum 来源时读取公开 Note，区分投稿与明确录用记录，匿名作者留空；不获取作者当前个人机构。

只发送标识符、必要标题和作者词，不上传 PDF。客户端固定官方 HTTPS origin，校验 DNS，拒绝非预期重定向、HTML 和超过 1 MiB 的响应；禁用隐含代理环境读取。可由部署显式设置 `IPAPER_METADATA_PROXY`，默认直连；arXiv 仍支持原有独立代理。透明 DNS fake-IP 仅沿用已明确启用的 `IPAPER_AI_PROXY_FAKE_IP_RANGES`，不能借此放行局域网、IP 字面量或任意来源。

每篇最多 8 次书目 GET，单请求连接 5 秒／读取 15 秒并限制响应读取时长；暂时错误最多两次退避重试，计入预算。429 持久化等待，403 与无法识别的内容分别失败。全局两个执行槽，每用户一个执行中明细。成功缓存 30 天，无版本 arXiv 和无匹配 24 小时；每用户最多 1,000 个响应缓存，过期读取不会因打开页面而联网。取消停止领取新请求，当前只读请求有界结束；重启恢复未完成项，不重跑完成项。

## API

- `GET/PATCH /api/paper/<id>/metadata`：字段、来源、修订、当前任务；PATCH 使用 `{revision, fields}`。
- `POST /api/paper/<id>/metadata/adopt`：核对候选并采用指定字段，需当前修订及源文件指纹一致。
- `POST /api/metadata/preview`、`POST/GET /api/metadata/jobs`：显式论文名单或服务端筛选；查询批次、取消、重试使用 `/jobs/<id>`、`/cancel`、`/retry`。
- `GET /api/paper/<id>/bibtex[?download=1]`：当前书目引用。
- `GET /api/paper/<id>/duplicates`、`POST .../duplicates/dismiss`：owner 隔离且带签名版本的提示。

写入需要会话、CSRF、owner 与大小校验；不返回服务端路径。元数据资产和下载均 `private, no-store`。

## 验证与限制

固定身份样本为 30 个明确标注的合成边界案例，并非 30 篇真实供应商验收论文。真实书目核对由 `scripts/validate_metadata_sources.py --live --output <private-directory>` 独立执行；默认离线、每来源至多一次，无模型／MinerU／OCR。

当前网络实测 Crossref 的 Deep learning 标题、三位作者和 DOI 通过，缓存复读零请求；arXiv 限流／超时、DBLP 返回防机器人 HTML、OpenReview 403，不能描述为三者已真实通过。客户端保持真实错误与手动重试，不绕过访问限制。需要人工确认的论文继续可读；缺失机构、匿名作者和无法确认的发表信息留空。

备份与回退见 [运维说明](../operations/structured-backup.md)，本轮部署与正式回填以 Release 的最终证据为准。
