# PaperQuay 与 iPaper 统一界面的接入记录

参照固定为 [WangQrkkk/PaperQuay](https://github.com/WangQrkkk/PaperQuay/tree/1d65fdbfe0eb7ef33c57cf8b9d87b6afdb5f06bf)，版本 0.1.25，提交 `1d65fdbfe0eb7ef33c57cf8b9d87b6afdb5f06bf`。本地参照目录被 Git 忽略，不进入 Docker 上下文。

## 复用判断

已阅读上游 LICENSE、主题、文献库、标签、ReaderWorkspace、PdfViewer、AssistantSidebar 和 PDF 文件源适配；对照 `docs/assets/main.png`、`agent.png`。上游为 AGPL-3.0-only。iPaper 根 LICENSE 为 CC BY-NC 4.0，Python classifier 仍标为 MIT；classifier 不能替代授权文本。

直接搬入上游 React 组件会形成需要核查的组合代码。上游 AGPL 第 5、10、13 节涉及整体许可、不得增加限制和网络使用的源码提供；现有 [CC BY-NC 第 2 节](https://creativecommons.org/licenses/by-nc/4.0/legalcode.en)则限制为非商业使用。未发现覆盖双方相关权利人的额外授权，不能通过分目录或只修改一个许可证声明就确认该组合可以发布。因此本次没有复制上游组件源码、CSS、图标或截图进发行包，也没有修改项目许可证。以下采用独立代码重现其布局、信息组织与操作流程；这是一项具体的复用决策，不是对现有项目所有许可问题的法律结论。将来直接代码复用仍须另行明确授权路径。

## 逐项映射

| 上游参照 | 本地实现与适配 | 后端与浏览器差异 |
|---|---|---|
| `src/app/index.css`、`literatureUi.tsx` | `frontend/src/style.css`、`ui.tsx`，独立 CSS token、细边框、中性浅深主题、青绿色强调、中文控件 | 系统字体与自托管 CSS；不引入上游 Electron 主题状态 |
| `LiteratureCategorySidebar.tsx` | `Library.tsx`，功能栏、分类、收藏、Reading List，初始分类栏 248px、可调整宽度 | `/api/categories` 及现有分类 CRUD；owner 虚拟根，资产 ID 不变 |
| `LiteraturePaperList.tsx` | 紧凑标题/作者/年份/状态、搜索排序、单击预览、双击/按钮阅读、批量操作 | 现有全量列表 API，客户端每页 50 条；不宣称服务端分页 |
| `LiteraturePaperDetails.tsx` | 420px 初始详情栏、摘要/状态/元数据、原文译文、任务动作 | 复用论文详情、编辑、收藏、Reading List、翻译、分析接口 |
| `components/tabs/TabBar.tsx` | `main.tsx`，论文标签、关闭与切换，服务端保存工作区偏好 | 不复用 Electron 工作区数据库；owner 设置保存，不新增表 |
| `ReaderWorkspace.tsx`、`PdfViewerToolbar.tsx` | `Reader.tsx`，填满剩余高度的阅读区、紧凑工具栏、折叠缩略图、可调整问答侧栏 | 同源授权 URL，原文/译文分别定位；无桌面自定义协议和磁盘路径 |
| `PdfViewer.tsx`、PDF document source | 独立 PDF.js 按可见范围渲染与生命周期；复用官方 PDF.js 包 | matching legacy display/worker 修复 Edge 139；资源自托管，保留严格脚本 CSP |
| `AssistantSidebar.tsx`、聊天呈现 | `Chat.tsx`，历史/多会话、流式回答、引用卡片、选择文字后确认发送 | 保留首行会话 JSON + 原始文本流；服务端模型配置；停止接收不等于服务端取消 |
| 桌面文件选择、IPC、设置 | `Transfers.tsx`、`Settings.tsx`，浏览器文件选择/拖放、现有 HTTP API | 不暴露主机路径、密钥、Electron IPC 或未实现工具入口 |
| 桌面无对应的多用户登录与 Daily | `Auth.tsx`、`Daily.tsx`、`DiscoverySettings.tsx`，沿用同一设计体系 | 保留现有 iPaper 身份、Daily、机构配置与用户资产 |

上表是源码接入记录。视觉、交互和正式部署是否通过，必须以本次验收记录为准，不能以组件名或截图数量代替。

## 发行依赖

实际依赖版本及来源见 `frontend/DEPENDENCIES.json`，完整性来自 npm lockfile。React、React DOM、Scheduler、Vite 使用 MIT；PDF.js 使用 Apache-2.0；Lucide React 使用 ISC；Marked 使用 MIT；DOMPurify 为 MPL-2.0 OR Apache-2.0（本次按 Apache-2.0 路径使用）。运行依赖原始许可证随 `THIRD_PARTY_NOTICES.txt` 自托管；字体/CMap 的上游声明随发行资源保留。没有将 PaperQuay 当作上述独立 npm 包的授权来源。


## 1.2.0 双翻译与来源交互

仍固定上述提交；未复制 AGPL 源码，未改变许可判断。新运行依赖 KaTeX 0.18.7 为 MIT，自托管声明随前端产物保留。以下是本次独立实现的具体映射，发布验收另行记录：

| 上游参考 | iPaper 实现 | 必要适配 |
|---|---|---|
| `features/blocks/BlockViewer.tsx`、`blockViewerContent` | `StructuredReader.tsx`、`MathFormula.tsx`、`structured.css` | 原文/译文/双语按块排列、来源操作、可变高度窗口化和表格/公式；游标 API 代替桌面整份 JSON/路径 |
| `features/reader/ReaderWorkspace.tsx` | 现有 Reader 外层内容选择与可折叠原文对照 | 同源授权文档版本；不假设 BabelDOC 与原 PDF 的页码对应 |
| `readerTranslation.ts`、`useDocumentTranslation.ts` | `processing/translation.py`、`pipeline.py`、`Processing.tsx` | 增量结果与重译交互；模型在服务端执行，持久预算/幂等/取消，浏览器没有密钥或直接出站请求 |
| `readerTranslationCache.ts` | `processing/store.py` 的索引、哈希和不可变译文版本 | 上游本地缓存路径改为 owner、源文件、解析版本和配置指纹；原子发布与过期检测代替覆盖单一 JSON |
| 块选择与引用跳转 | `processing/sources.py`、`Chat.tsx` | 源文字范围在服务端核实；只有本次上下文映射允许产生回答引用，历史保持可追溯 |

公式使用原生 MathML；没有引入上游笔记、Agent、Electron IPC 或桌面文件权限。具体模型和坐标约束见 [双翻译契约](dual-translation.md)。

## 1.3.0 单篇理解适配

固定 PaperQuay `1d65fdbfe0eb7ef33c57cf8b9d87b6afdb5f06bf` 的 `SummaryPanel`、summary source/agent context 流程作为分区、信息密度、来源和更新状态参考。`frontend/src/Understanding.tsx` 在现有论文标签中独立实现六部分概览、长篇解读、共用问答和来源往返；Electron summary IPC、文件系统、桌面数据库和模型密钥均未搬入。

后端由 `ipaper/processing/understanding*.py` 接现有 interpret 与原文快照，概览/长解读分别保存。长解读沿用 PaperPilot 原有图文详细解释意图，未以 SummaryPanel 的速读卡替代。来源、许可及独立实现策略不变；没有更改项目许可证，也没有以不同目录名宣称 AGPL 与现有许可自动兼容。细节见 [单篇理解说明](single-paper-understanding.md)。
