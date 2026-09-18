# iPaper UI 视觉规范

生效日期：2026-09-17。适用 iPaper 统一产品界面（React + TypeScript）。本文是**活动规范**，取代 2026-09-16 版本中"灰紫压暗、以压缩密度为目标、绿色可作成功色"的表述。

## 1. 设计方向与来源

参考图保存在私有目录 `.devnotes/ui-phase1-references/`（原像素、未裁剪，来源与 SHA-256 见其 `sources.json`）。主要依据：

| 参考 | 采用的结构/视觉特点 | 对应区域 |
| --- | --- | --- |
| 第 9 张「国际」 | 白色占主导的三栏构图、安静的导航分组、局部强调、不同层级内容块的间距 | 全局框架、文献库、详情侧栏 |
| 第 3 张「科技」 | 明亮工作区 + 独立聊天面板；外部深色属于展示画布 | 阅读正文与 AI 问答 |
| 第 11 张「简洁」 | 几乎全白、黑色标题、蓝紫重点、靠对齐与留白分组的列表 | 列表节奏、书目信息、工具栏 |

参考图与色卡只提供结构与层级参照，不作为运行素材；不引入原图中的青绿、金融图表或营销内容；论文 PDF 与图表颜色保持原样。

**配色边界**：界面不得使用绿色（含成功等状态色）；主色是清晰的靛紫，只出现在关键动作与小面积选中态；蓝与雾粉少量辅助。**浅色是主设计与验收场景**，深色是显式阅读偏好。

## 2. 主题策略

- 产品**浅色优先**：无显式偏好时默认浅色；`templates/workbench.html` 声明 `<meta name="color-scheme" content="light">`，`:root` 声明 `color-scheme: light`。
- **不跟随系统自动深色**：`style.css` 不包含 `@media (prefers-color-scheme: dark)` 令牌块；也避免浏览器自动深色改写浅色页面。
- 深色只在 `html[data-theme="dark"]` 下生效，并由用户在界面显式切换；切换会通过既有工作区状态保存，服务端已保存的 `light`/`dark` 原样保留。
- **懒加载样式隔离**：PDF.js 的 `pdf_viewer.css` 也会声明 `:root { color-scheme: light dark }`，会盖掉普通 `:root` 规则。`style.css` 因此用更高优先级的 `:root:not([data-theme])`、`html:not([data-theme])`、`html[data-theme="light"]` 与 `html[data-theme="dark"]` 显式声明 `color-scheme`，阅读器 chunk 加载前后计算样式都跟随用户选择；PDF 页面与图表原色不变。
- 截图/证据脚本必须通过**真实主题入口**操作并断言 `data-theme` 与计算底色，结束后恢复账号原有偏好，禁止直接改 DOM 属性伪造浅色。

## 3. 设计令牌

令牌在 `frontend/src/style.css` 的 `:root` 与 `html[data-theme="dark"]` 两处维护；页面只引用令牌，不写颜色字面量。

### 3.1 颜色（浅色）

| 令牌 | 值 | 用途 |
| --- | --- | --- |
| `--bg` | `#f7f8fa` | 页面底（极浅冷灰） |
| `--surface` / `--surface-2` | `#ffffff` / `#fcfcfd` | 面板、内容面 |
| `--subtle` / `--hover` | `#f3f4f7` / `#f5f6f9` | 次级底、悬停 |
| `--divider` / `--border` / `--border-strong` | `#eef0f4` / `#e6e8ec` / `#d6d9e0` | 细分组线、控件边界 |
| `--canvas` | `#eef0f4` | PDF 阅读区外壳 |
| `--text` / `--text-strong` / `--muted` / `--faint` | `#15171c` / `#0b0d11` / `#5f6672` / `#98a0ad` | 文字层级 |
| `--accent` / `--accent-hover` | `#5757d1` / `#4646bb` | 主操作、选中、焦点 |
| `--accent-bg`/`--accent-soft` / `--accent-border` | `#efeffc` / `#cfcff2` | 选中底、选中边 |
| `--blue` / `--blue-bg` | `#3f6fe4` / `#eef3fe` | 次级强调、信息 |
| `--pink` / `--pink-bg` | `#e7b7be` / `#fdf3f4` | 少量点缀 |
| `--success` / `-bg` / `-border` | `#2f5fd0` / `#eef2fd` / `#c9d8f7` | 成功（**非绿**，配图标+文字） |
| `--warning` / `-bg` / `-border` | `#b45309` / `#fdf4e7` / `#f0d8ae` | 警示 |
| `--danger` / `-bg` / `-border` | `#c2352f` / `#fdeeed` / `#f2cbc8` | 失败、删除 |
| `--info` / `-bg` / `-border` | `#4b6b8f` / `#eef2f7` / `#cdd9e6` | 信息 |
| `--highlight` 等 | 琥珀系 | PDF/结构搜索高亮 |
| `--shadow-sm/-md/--shadow` | 中性黑透明 | 只用于浮层（菜单、弹窗、抽屉） |

深色在 `html[data-theme="dark"]` 中给出对应值：`--bg #16171b`、`--surface #1d1e23`、`--text #eceef2`、`--accent #a6a8f0`、`--canvas #141519`，语义色取浅色对应值的提亮版，同样不含绿色。

### 3.2 字阶、间距与尺寸

- 字号：`--fs-2xs 12 --fs-xs/sm 13 --fs-base 14 --fs-md 15 --fs-lg 17 --fs-xl 20 --fs-2xl 22`；不再把 10–11px 当作常态。
- 行高：`--lh-tight 1.35 --lh-base 1.55 --lh-loose 1.75`。
- 间距：`--sp-1..7 = 4/8/12/16/20/24/32`。
- 圆角：`--r-xs 4 --r-sm 6 --r-md 10 --r-lg 14 --r-pill 999`。
- 控件：`--control-h 32 --control-h-sm 28 --control-h-lg 36`。
- 布局：`--header-h 52 --tab-h 36 --sidebar-w 248 --detail-w 380 --chat-w 380 --thumbnail-width 136`。
- 字体栈：Inter → system-ui → PingFang SC / Hiragino Sans GB / Microsoft YaHei / Noto Sans CJK SC / Source Han Sans SC，实际核对中英文回退。

## 4. 结构

### 全局框架
一条 `.app-sidebar`（248px，白底、右侧 `--divider`）承载品牌、主导航、**研究主题浏览**（`.sidebar-topics`）与底部说明。主题树通过 `createPortal` 渲染进这条侧栏，关键词与组合筛选仍放在筛选弹窗，所以日常按主题浏览不必先打开弹窗，也不恢复第二层导航列。`.app-header`（52px）左侧是论文页签（`role="tab"`、`.paper-tab`），右侧是唯一的导入入口、主题、账号与退出；文献库页头只保留标题与数量，不再重复放一个同等权重的导入按钮。≤1100px 侧栏变为抽屉（顶栏菜单按钮、遮罩与 Escape 关闭）。

### 文献库
`页头 → 范围标签 → 一条工具栏 → 已选条件 → 列表 → 分页`：
- `.page-heading`：真实页面标题（`--fs-2xl`）与数量；导入只保留顶栏的全局入口，页头不再重复主操作。
- `.scope-tabs`：全部文献／收藏／Reading List，下划线选中态，不使用第二层导航。
- `.list-toolbar`：搜索框（聚焦有强调描边）、`筛选` 按钮（带条件计数）、排序、刷新。
- `.applied-filters`：仅在存在筛选时出现，条件可逐条移除；批量操作放在选中后才出现的 `.batch-toolbar`。
- `.paper-row`：**无边框行**——标题（15px/600，两行截断＋全文提示）、作者·年份（可展开全部作者）、最多 3 个纯文字标签、资产状态；悬停 `--hover`，选中 `--accent-soft` ＋ 2px 左强调条；复选框与收藏在悬停/聚焦/选中或触屏下才显示。
- `.paper-details`：白底、`--divider` 左边框；大标题、作者、强调主操作、细分隔线分组（处理入口、摘要、主题、标签、文献信息、来源/BibTeX），低频动作进溢出菜单。
- `.pagination`：`flex` + `gap 12px` + `padding 10px 24px` + 上边 `--divider` 分隔；「选择当前页」用 `margin-right: auto` 单独靠左，范围与页码为不换行的弱化文字，翻页按钮 ≥28px 点击区；窄屏减小字号与间距但不堆叠。

### 阅读器
顶部只有一条 `.reader-toolbar`（52px 起）：阅读导航、搜索、`.mode-switch`（AI 概览／深度解读／阅读正文）、文档标题、版本（含"版式结果版本"）、页码、缩放、旋转、问答；正文区使用 `--canvas` 外壳，页面只加 `--shadow-sm`。左阅读导航为紧凑行（虚拟机行高一致）；分析视图复用同一条顶栏与模式切换。

工具栏规则：`select/input/图标按钮` 一律 `flex: 0 0 auto` 并保留可用最小宽度（页码 52px、`/ N` 不换行、下拉最大宽度按断点收敛到 260/200/168px），**标题先让出空间**（`flex: 1 1 120px` 并省略号），`flex-wrap: wrap` 允许低频控件整体换行。绝不能把"版式译文""适合宽度"等真实标签压成单字。

### 问答侧栏
`.chat-head`：标题＋消息数、会话下拉、`.menu-wrap/.menu-list` 溢出菜单（新会话／刷新历史／删除当前会话）。消息：助手为白底排版（正文 `--fs-base`、行高 1.7），用户为 `--subtle` 圆角气泡；来源为小型文字按钮。输入：`.compose-box` 整块带边框容器，内联发送/停止，引用卡在上方，底部只保留一行提示。

## 5. 阅读活动热力图

- **数据口径**：按 UTC+8 业务日展示**有效阅读分钟数**，论文数为同日有记录且当前用户可访问的 paper 去重；只有历史总分钟数的日期显示分钟并注明"篇数未记录"。合并规则取逐条事件与旧按日汇总中的**较大值**（不是求和），因此两套写入不会重复计账，历史总量也不会被降低。规则实现在 `ipaper/reading_activity.py`，路由只负责取数。
- **颜色**：`--heat-empty` 表示无记录，`--heat-1..4` 为浅紫到柔和紫（浅色 `#f1f2f5 / #eeeaf8 / #ded6f1 / #c9bce8 / #b19ddc`，深色单独调校）；颜色之外必须有可读数值与图例（0 / <15 / <30 / <60 / ≥60 分钟），不使用满屏深紫。
- **结构**：周为列、周一至周日为行，默认近 12 周、可展开近一年；补齐日历空格，未来日期用斜纹与"有记录日"区分，今天有 `--accent` 细边框。窄屏在组件内部横向滚动，整页不得横向溢出。
- **交互**：悬停、键盘聚焦与触屏点按给出同一详情（日期、分钟、篇数）；点击日期列出当天论文并可跳回阅读入口。
- **位置**：文献库主区可折叠区（`.library-activity`，状态保存在工作区偏好 `readingActivityOpen`）与设置页复用同一组件；不得挤占阅读器正文，也不新增只有统计的首页。

## 6. 时间与业务日期

- **绝对时刻**：存储与 API 使用带 `Z` 的 UTC ISO 字符串或 epoch 秒；排序、超时、重试与过期一律按真实经过时间，不做"加八小时"。
- **业务日期**：阅读归日、Daily 的"今天"与日期窗口使用 `Asia/Shanghai`（`ipaper.timeutil`）；跨北京时间的午夜按 `split_interval_by_app_day` 拆到两天。
- **展示**：前端 `dateText` / `timestampText` 统一用 `Intl.DateTimeFormat` 指定 `timeZone: 'Asia/Shanghai'`，界面在时间旁标注"北京时间（UTC+8）"；日期型书目字段（如 `2026-09-18`）保持日期语义，不先当 UTC 午夜再换算。
- **历史值**：旧的无时区时间由运行在 UTC 的容器写入，读取时按 UTC 解释后换算显示，不批量平移、不改写历史行。
- **上游公告**：arXiv 批次按 `America/New_York` 的 14:00 截止与 20:00 公告、跳过周末计算（`arxiv_announce_instant`），不用手工夏令时或固定偏移伪造公告日；原始 published/updated 保持不变。
- **日志**：应用日志经 `ipaper.logging_setup` 以 `+08:00` 渲染；HTTP Date、外部平台原始 UTC 元数据与 Docker 引擎时间保持规范语义。

## 7. Daily 资产状态

列表必须按真实资产状态显示，且与文件一致：

| 状态 | 含义 | 界面 |
| --- | --- | --- |
| `ready` | 本地 PDF 存在 | 「PDF 已就绪」 |
| `candidate` / `queued` / `downloading` / `validating` / `retry_wait` | 尚未获取或正在重试 | 「PDF 待获取 / 排队中 / 获取中 / 校验中」+「重新获取」 |
| `missing` | 记录说 ready 但文件不存在（已按 owner 重新排队） | 「文件缺失，已重新获取」 |
| `failed` | 重试耗尽 | 「PDF 获取失败」+「重试获取 PDF」 |
| 无候选行 | 仅元数据 | 「仅元数据」 |

- 论文行与候选行的 arXiv 身份匹配忽略版本后缀与旧式斜杠形式（`normalize_arxiv_id`），否则会出现"记录 ready、文件为空"的静默不一致。
- 缩略图 GET 只读既有资产，不隐式触发下载；无封面时返回 `thumbnail_pending` 并 `Cache-Control: no-store`，成功时 `private, max-age=300` 且 ETag 取自真实文件修订。缓存键包含 owner，禁止跨用户共享封面。
- 前端在论文/日期/封面版本或资产状态变化、以及手动重试时重置失败状态，待获取状态做有界自动重试（最多两次），不做无限重试。

## 8. 维护约定

- 新增颜色必须先进令牌；`grep -nE '#[0-9a-fA-F]{3,8}' frontend/src/*.css` 只应命中令牌定义行。
- 不新增品牌皮肤或主题编辑器；浅色与深色分别调校。
- 保留下述测试钩子：`.paper-row`、`.paper-details`、`.paper-tab`、`.category-tree`、`role="tab"`。
- 一屏密度用**行与列表滚动视口的矩形交集**度量（完整/部分可见分别记录），不得用 `.paper-row:visible` 总数，也不以压缩字号与行高作为目标。
- 证据与验收：`.devnotes/ui-phase1-rework/`；交付记录 `.devnotes/ipaper-ui-phase1-rework-delivery.md`，收尾记录 `.devnotes/ipaper-ui-1.8.1-closeout.md`。
- 抓取脚本必须在 **`finally` 中、浏览器与会话仍有效时**恢复账号主题偏好并核对：显式 `light`/`dark` 写回原值，`system` 保留为 `system`，无偏好则删除该键；若当前值不是本次脚本写入的值（并发用户改动）则跳过并记录。密度必须在列表内容与布局稳定后、与截图同一状态下测量，论文库中测到 `total=0` 视为未就绪而不是有效结果；结构阅读与问答场景要断言到对应组件，且截图侧车记录版本、提交与镜像 revision。