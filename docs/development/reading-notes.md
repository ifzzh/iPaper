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

- **PDF（原文与版式译文各自独立）**：保存页号与**归一化行矩形**（相对页面 0–1，且按页面旋转反算到未旋转坐标系），多行逐行记录，不覆盖段间空白或相邻栏。缩放、旋转、适合宽度、窗口变化与虚拟页面卸载重渲染后位置仍然正确。版式译文有自己的 `document_id`/哈希与页码，不套用原文坐标。
- **结构内容**：保存块、字段（`text`/`caption`/`cell:r:c`）、文字范围与（译文侧）译文修订。不同译文版本或重新解析后，**先按存下的摘录校验当前文本**，不匹配则不画高亮并提示来源已变化，绝不跳到新内容的相似段落。
- **降级**：扫描件或没有可靠文字层时保存为**页级记录**（`kind=page_note`，无矩形），明确不再声称有精确文字高亮；不自动 OCR。
- 每条记录都带 `context_json`（文档类型、页数、文档哈希、结果修订、保存时间）；读取时计算 `stale`/`canNavigate`/`notice`：源文件被替换 → 保留摘录但**不自动跳转**并说明原因；结果失效同理。

## 4. 自动保存、冲突与离线

- 编辑采用 1.2 秒防抖 + 修订校验：只有服务端确认后才显示「已保存」；失败时内容保留在编辑区并显示原因，恢复后可重试。
- 两个标签/设备并发编辑：后到的保存不会覆盖先到的内容，服务端把来稿保存为**冲突草稿**并返回 `note_revision_conflict`；界面提示并让用户明确选择「保留服务器版本」或「保留我的草稿」，两者都不会丢。
- 摘录/回答插入在同一事务内读取-追加-写回，并校验调用方看到的笔记修订；过期修订返回 409，不会吞掉并发加入的内容。
- 未保存内容不会因为切换页签而写进别的论文：保存请求只针对当前 `paper_id`，切换论文会重新加载（编辑中的内容在切换前会先落盘）。
- 浏览器不保存可被他人读取的笔记草稿；服务端记录按 owner 隔离。

## 5. 接口

```
GET    /api/paper/<id>/reading/annotations[?documentId=&kind=&cursor=&limit=]
POST   /api/paper/<id>/reading/annotations
PUT    /api/paper/<id>/reading/annotations/<annotation_id>   # {revision, comment?, color?, excerpt?, restore?}
DELETE /api/paper/<id>/reading/annotations/<annotation_id>   # {revision}
GET    /api/paper/<id>/reading/annotations/deleted
GET    /api/paper/<id>/reading/note
PUT    /api/paper/<id>/reading/note                          # {markdown, revision}
POST   /api/paper/<id>/reading/note/excerpts                 # {annotationId, revision}
POST   /api/paper/<id>/reading/note/answers                  # {sessionId, messageIndex|messageKey}
POST   /api/paper/<id>/reading/note/conflicts/<id>           # {choice: current|draft}
GET    /api/paper/<id>/reading/note/export.md[?annotations=0]
```

错误码：`invalid_anchor` / `invalid_excerpt_too_large` / `invalid_comment_too_large` / `invalid_color` / `invalid_annotation_kind` / `annotation_not_found` / `annotation_revision_conflict` / `annotation_limit_reached` / `invalid_note_markdown_too_large` / `note_revision_conflict` / `answer_not_found` / `answer_not_assistant` / `answer_not_persisted`（流式或未成功保存的回合）/ `conflict_not_found`。

## 6. 明确不在本轮范围

全库富文本工作区、双链与知识图谱、跨论文 RAG、自动生成笔记、把批注写回 PDF 或导出带批注 PDF、笔记版本历史（只保留一份冲突草稿与可撤销的删除）。已有阅读时长、热力图与阅读位置继续复用。