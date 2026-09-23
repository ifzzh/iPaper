# 阅读高亮、批注与单篇笔记

本轮（Web 1.13.0）把阅读器扩展为可回访的笔记工作面：选中文字保存高亮或批注、在右侧工作面查看与回访、维护每篇论文一份 Markdown 主笔记、把已持久化的 AI 回答存入笔记，并导出 Markdown。**不新增任何模型 / MinerU / OCR / 翻译调用**：保存回答只复制已有内容，高亮与笔记全部是本地持久记录。

## 1. 用户可见能力

| 能力 | 入口 | 说明 |
|---|---|---|
| 高亮 / 批注 | 在原文 PDF、版式译文或结构正文中选中文字 → 选区工具条「高亮/批注」 | 四种淡色（淡紫/灰蓝/雾粉/暖黄）、可留空只保存高亮、批注可修改与删除；删除后侧栏提供「撤销」 |
| 批注侧栏 | 阅读器右侧「批注」页签 | 按阅读顺序列出摘录、自己的批注、文档类型与页码/块来源；可按原文/版式译文、结构内容、仅批注筛选与搜索；点击条目定位来源并显示详情 |
| 单篇笔记 | 右侧「笔记」页签 | 每篇论文一份主笔记，Markdown 编辑/预览、自动保存与状态提示（保存中/已保存/保存失败），可插入摘录 |
| 保存已有回答 | 问答气泡中助手消息的「存入本篇笔记」 | 只复制已持久化的回答与当时的来源标签；正在生成或保存失败的回合不可用；重复点击幂等 |
| 导出 | 笔记面板「导出 Markdown」 | `GET /api/paper/<id>/reading/note/export.md`，含标题、作者、arXiv/DOI、导出时间、笔记正文、摘录与批注、来源记录；可用 `?annotations=0` 只导出笔记正文 |

阅读界面本身不变：浅色为默认、白/极浅灰大面 + 小面积蓝紫，论文内容颜色保持原样；高亮只是叠加的淡色层。

## 2. 数据与服务边界

新增四张表（`ipaper/processing/notes_schema.py`，纯增量、可空，旧版本可安全读写）：

- `reading_annotations`：`id, owner_id, paper_id, document_id, result_id, kind, color, excerpt, comment, anchor_json, context_json, revision, deleted_at, created_at, updated_at`
- `reading_notes`：`owner_id, paper_id, markdown, revision, created_at, updated_at`（每人每篇一份）
- `reading_note_entries`：插入的摘录/回答来源记录（`kind, annotation_id, message_id, content_json, note_revision, dedupe_key`）
- `reading_note_conflicts`：并发编辑时保存的对方草稿

规则与上限：

- 所有读写 owner 隔离并在写入前校验论文、文档与结果归属；沿用现有认证、CSRF 与限流，跨 owner 一律 404/403。
- 尺寸：摘录 ≤ 8,000 字符、批注 ≤ 2,000 字符、笔记 ≤ 256 KiB（PUT 路由局部放宽请求体上限），每篇论文有效批注 ≤ 2,000 条，列表分页（默认 50，最大 200）。
- 论文文件、哈希与译文**不被修改**；批注与笔记只引用既有 document/result，不复制全篇论文。
- 备份：新表位于同一 SQLite 文件内，现有数据库备份与一致性检查自然包含；无需额外的附件目录。

## 3. 来源定位与版本

- **四类内容各自独立**：原文 PDF、BabelDOC 版式译文、结构原文、结构译文。批注分别绑定到自己的 `document_id`（原文 **或** 译文受控文件，绝不拿原文 PDF 哈希代替译文）、必要时绑定 `result_id`，以及结构侧的块/字段/文字范围与译文修订。创建时后端会核验文档归属、结果与文档的对应关系、块是否存在、字段是否存在、范围是否越界、以及译文修订是否真的是该块当前的修订（字符串 UUID 身份，整数身份仍可读）。
- **PDF（原文与版式译文各自独立）**：保存页号与**归一化行矩形**（相对页面 0–1，且按页面旋转反算到未旋转坐标系），多行逐行记录，不覆盖段间空白或相邻栏。缩放、旋转、适合宽度、窗口变化与虚拟页面卸载重渲染后位置仍然正确。
- **结构内容**：绘制前用**完整摘录**与当前文本逐字比较（不是只比开头），并确认译文修订未变化；任一不符就不绘制，并提示来源已变化。重解析、重译、替换源文件后都不会在错误的新版本上画旧标记。
- **降级**：扫描件或没有可靠文字层时，阅读器工具栏提供**页级记录**入口（页码 + 记录文字，明确说明不含精准选区、不触发 OCR）；服务端也接受无矩形的页级记录。
- 每条记录带 `context_json`（文档类型与标签、页数、文档哈希、结果修订、译文修订、块顺序、文本长度与哈希、保存时间）。读取时计算 `stale`/`canNavigate`/`notice`/`contentKind`/`sourcePage`/`sourceBlock`/`sourceOrder`：文件被替换 → 保留摘录但**不自动跳转**并说明原因；块消失、范围不再匹配或译文修订变化 → 同样保留摘录、只给块级来源与说明。

## 4. 自动保存、冲突与离线

本节的会话级保存与统一回访实现位于当前 `dev`，尚未发布部署；正式 1.13.6 的行为不能据此视为已经修复。

**保存队列随笔记会话保留。** `noteSession.ts` 按 `owner + paper` 保留最新正文、已确认正文与基准修订、未决冲突及所见修订、在途请求和重试截止时间。编辑器只是同一会话的订阅者；问答/批注/笔记切换、侧栏收起、原译文与论文切换都不会重建保存队列或清空冲突。没有待决冲突时，1.2 秒防抖、离焦和手动重试共用串行队列，同一会话只发一个写请求。

**响应只确认它所提交的文本。** 旧 PUT 返回后推进已确认基准，但保留期间输入的最新正文并接着保存。较早成功、失败、未知结果都不能用请求快照覆盖编辑器。服务器确认的正文与当前正文一致且没有未决请求/冲突才显示「已保存」。即使在较早请求未结束时把正文改回原样，也要等该请求核对完毕，不能提前宣称已保存。

**失败草稿有明确生命周期。** 未提交文本存在会话内存，不写 localStorage 或日志；组件卸载后仍保留，恢复网络后可继续保存。通用故障最多自动重试 3 次（3/8/20 秒），同时提供手动重试。结果未知时先 GET 对账：服务器已持有本次提交即可确认；远端修订已前进则进入冲突。关闭页面时有未保存内容会触发离开提醒；这不等于跨浏览器重启的草稿持久化。退出登录/会话失效会中止请求并清空该账号的会话，迟到响应不能恢复私人文本。已确认且无订阅的旧会话可回收，未保存内容不会为了缓存上限被驱逐。

**冲突必须显式决定。** 409 与对账发现的远端变更都冻结普通保存。切换视图、重新读取或重新挂载时，不能把远端最新修订当成本地草稿的已确认基准而自动覆盖服务器。面板提示与编辑器按钮共用同一个决定入口，始终携带用户看见的修订；重复点击不会重复发送。

- 「保留服务器版本」仅在点击后没有继续输入时用服务器正文替换编辑区；期间有较新输入则保留最新稿，并在该决定确认后的基准上继续保存。
- 「保留我的草稿」提交点击时的最新正文；决定响应返回时若又有新输入，先确认已提交版本，再保存新输入。恢复一条持久冲突副本时使用用户选中的副本，不会拿当前服务器正文冒充它。
- 失败、未知结果或过期决定仍保持冲突，不能退回普通 PUT 绕过选择。409 后刷新所见修订，再由用户重新决定；两侧副本沿用服务端既有保护。

**冲突针对所见修订。** 保存遇到 409 时，界面立即重新拉取笔记，展示**服务器当前版本与我的草稿**并给出「保留服务器版本 / 保留我的草稿」按钮（笔记页签和批注面板入口都能操作）。解决请求必须携带用户当时看到的 `revision`：若期间又有第三次写入，服务端拒绝该过期决定（409 `stale_decision`），把未被看过的当前内容保存为**新的冲突副本**，绝不覆盖；选择保留草稿时，被替换的服务器版本也会另存为冲突副本。冲突记录**不会被删除**：应用过的会标记为 `resolved:<choice>`，未决的继续显示在冲突提示里，因此不会出现"声称两边都保留却删掉唯一副本"。

**写入限流与退避。** 笔记自动保存以及同一流程中的批注、摘录、回答复制与冲突决定使用**独立的 owner 级写入桶** `note_writes`（**120 次 / 60 秒**，`app.py`），不再消耗通用 `mutation`（60 次/小时）额度：1.2 秒防抖下连续输入最坏约每分钟数十次保存，10 秒一次的节奏可连续保存而不会被拒。桶仍然有界：远超人类输入速度的爆发（例如每秒 2.5 次）会返回 429 并带 `Retry-After`（≤60 秒）。前端**读取并遵循** `Retry-After`：显示还需等待多久、保留最新草稿、期间合并继续输入，等待到期后由单一计时器唤醒，保存**最新**文本；等待分支不会在 `finally` 同步递归重入。手动重试、离焦、面板切换同样遵循等待时间，固定 3/8/20 秒退避不会在长窗口里继续轰击。畸形或缺失的 `Retry-After` 采用有界后备（≤60 秒）。模型、上传、鉴权等无关入口的阈值不变。

**摘录从笔记回访来源。** 笔记「来源」列表中的每条摘录都带有 `annotationId`，可点击「回到来源」；界面按 owner/paper 通过该批注的真实受控来源定位（原文 PDF / 版式译文 / 结构原文 / 结构译文各自的页、块与译文修订），由同一个上层入口读取并核验批注，再选择确切的文档/结构结果与语言侧。PDF 等对应文件加载并渲染目标页，结构内容等目标块实际加载后才显示「已定位」；不会只跳当前 PDF 的同页号，也不会整页刷新而丢掉未保存稿。异步加载完成不再次抢占用户已切换的面板。找不到对应结果时提示重试，不猜测最近版本。批注已删除时仍保留摘录并明确提示"来源批注已被删除，定位仅供参考"；来源版本失效时按既有规则降级。已保存 AI 回答的来源按钮与 Markdown 导出保持不变。

**插入的回答与摘录按身份判重。** 正文里带有不可见的身份标记（`<!-- ipaper:excerpt:<annotationId> -->` / `<!-- ipaper:answer:<hash> -->`），判重基于 `(owner, paper, dedupe_key)` 数据库记录与该标记，而不是正文子串：多行、列表、公式回答重复保存都返回 200 且只保留一份来源记录；同一摘录在正文被手动删除后再插入会恢复正文片段但不新增来源记录；相同文本但不同来源不会互相误判。导出时会剥离这些标记。

## 5. 接口

```
GET    /api/paper/<id>/reading/annotations[?documentId=&kind=&cursor=&limit=]  # 分页，nextCursor 为字符串
POST   /api/paper/<id>/reading/annotations
PUT    /api/paper/<id>/reading/annotations/<annotation_id>   # {revision, comment?, color?, excerpt?, restore?}
DELETE /api/paper/<id>/reading/annotations/<annotation_id>   # {revision}
GET    /api/paper/<id>/reading/annotations/deleted
GET    /api/paper/<id>/reading/note
PUT    /api/paper/<id>/reading/note                          # {markdown, revision}
POST   /api/paper/<id>/reading/note/excerpts                 # {annotationId, revision}
POST   /api/paper/<id>/reading/note/answers                  # {sessionId, messageIndex|messageKey}
POST   /api/paper/<id>/reading/note/conflicts/<id>           # {choice: current|draft, revision}（必填）
GET    /api/paper/<id>/reading/note/export.md[?annotations=0]
```

列表默认每页 100 条（最大 200），返回 `nextCursor`、`total` 与每条记录的 `orderKey`（文档 → 原文/译文 → 块顺序 → 页 → 创建时间），界面据此按文档内阅读顺序展示并在需要时「加载更多」，因此超过 200 条的批注依然可访问、可搜索、可回访。

错误码：`invalid_anchor` / `invalid_excerpt_too_large` / `invalid_comment_too_large` / `invalid_color` / `invalid_annotation_kind` / `annotation_not_found` / `annotation_revision_conflict` / `annotation_limit_reached` / `invalid_note_markdown_too_large` / `note_revision_conflict` / `answer_not_found` / `answer_not_assistant` / `answer_not_persisted`（流式或未成功保存的回合）/ `conflict_not_found`。

## 6. 明确不在本轮范围

全库富文本工作区、双链与知识图谱、跨论文 RAG、自动生成笔记、把批注写回 PDF 或导出带批注 PDF、完整笔记版本历史（当前保留冲突副本与可撤销的删除）。已有阅读时长、热力图与阅读位置继续复用。

## 7. 回归检查

- `frontend/src/noteSession.test.ts`：会话卸载与恢复、迟到/未知响应、冲突决定后新输入、失败决定、账号隔离、2/8/60 秒退避与有界自动重试。
- `frontend/unified-tests/notes-reliability.spec.ts`：原 N1–N4 复现及两种决定的两个入口；PDF 与结构两侧回访、未保存稿跨视图保留。
- 既有 `notes.spec.ts` 由 `playwright.notes.config.ts` 执行，新增组合回归由 `playwright.notes-reliability.config.ts` 单独执行；两次各自启动全新合成库，避免共享账号的来源、偏好和限流窗口干扰。使用合成文件、临时数据库与假供应商，不访问正式笔记或真实模型。既有摘录回访测试按本轮创建的 annotation 身份选择来源，结构测试明确选择对应结果与语言侧。

```sh
cd frontend
npx playwright test --config playwright.notes.config.ts
npx playwright test --config playwright.notes-reliability.config.ts
```
