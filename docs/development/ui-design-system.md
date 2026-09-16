# iPaper UI 视觉规范

生效日期：2026-09-16。适用 iPaper 统一产品界面（React + TypeScript）。本文记录第一部分实际采用的视觉变量、公共组件、布局与响应式规则，以及设计来源和配色边界。

## 1. 设计来源与配色边界

参考图保存在私有目录 `.devnotes/ui-phase1-references/`（原像素、未裁剪，来源与 SHA-256 见其 `sources.json`）：

| 参考 | 采用的结构/视觉特点 | 对应区域 |
| --- | --- | --- |
| 第 9 张「国际」 | 导航分组、主次分区、强调色集中使用 | 全局框架、文献库、详情侧栏 |
| 第 3 张「科技」 | 主工作区与右侧对话区并列、工具分组紧凑 | 阅读正文 + AI 问答 |
| 第 11 张「简洁」 | 中性底色、清晰字阶、克制边界、可扫描的信息 | 论文列表、书目信息、工具栏 |
| 用户莫兰迪色卡 | 雾粉、中性灰、灰蓝；排除绿色部分 | 背景、强调色与辅助色方向 |

参考图与色卡只提供结构与层级参照，不作为产品运行素材，不热链外站字体、图片或脚本，也没有引入参考图中的金融图表、统计或人物图片。

**配色边界（用户 2026-09-16 确认）**：主色调不能是绿色。青绿不再承担主按钮、选中态、焦点、页签、链接或导航激活；界面背景不再做绿色染色。绿色只允许作为克制的**状态色**（成功），且必须同时有文字或图标，不能成为页面视觉主调。当前实现以莫兰迪灰紫为主色、灰蓝与雾粉为辅助。

## 2. 设计令牌

令牌在 `frontend/src/style.css` 顶部的 `:root`、`html[data-theme="dark"]` 与 `prefers-color-scheme` 回退三处同步维护；页面与组件只引用令牌，不再各自维护颜色字面量。

### 2.1 颜色

| 令牌 | 浅色 | 深色 | 用途 |
| --- | --- | --- | --- |
| `--bg` | `#f2f1ef` | `#17171a` | 页面底 |
| `--surface` / `--surface-2` | `#ffffff` / `#faf9f8` | `#1e1e22` / `#1a1a1e` | 面板、卡片 |
| `--subtle` / `--hover` | `#f7f6f4` / `#f0eef3` | `#242429` / `#2a2a31` | 次级面板、悬停 |
| `--border` / `--border-strong` | `#e3e2e5` / `#cecdd4` | `#34343c` / `#45454f` | 边界 |
| `--rail` | `#edece9` | `#141417` | 左侧导航条 |
| `--text` / `--muted` / `--faint` | `#26262b` / `#6e6e77` / `#9a9aa3` | `#e8e7eb` / `#a3a2ac` / `#7c7b86` | 文字层级 |
| `--accent` / `--accent-hover` | `#5a5f8c` / `#4a4f78` | `#a9acd6` / `#c0c3e6` | 主色（灰紫，非绿） |
| `--accent-bg` / `--accent-border` / `--accent-contrast` | `#ecebf4` / `#c7c6de` / `#ffffff` | `#2a2a3a` / `#4a4a63` / `#1b1b22` | 激活底、选中边、主色上的文字 |
| `--secondary` / `--secondary-bg`（雾粉） | `#e4cece` / `#f7efef` | `#e4cece` / `#3a3033` | 少量点缀 |
| `--blue` / `--blue-bg`（梦幻蓝系） | `#7c8aa8` / `#eef1f6` | `#9aa6c4` / `#232833` | 次级强调、信息 |
| `--success` / `-bg` / `-border` | `#3f7a5c` / `#eaf3ed` / `#bcd8c8` | `#79b394` / `#1f2e26` / `#35543f` | 仅状态，配图标+文字 |
| `--warning` / `-bg` / `-border` | `#a8792b` / `#f8f1e3` / `#e4cfa4` | `#d3b06b` / `#33291a` / `#5c4a24` | 警示 |
| `--danger` / `-bg` / `-border` | `#a8555c` / `#f8ecec` / `#e4bfc1` | `#e09095` / `#3a2426` / `#63383c` | 失败、删除 |
| `--info` / `-bg` / `-border` | `#4e7a9b` / `#edf2f6` / `#c2d3e0` | `#8fb2cc` / `#1f2a33` / `#38505f` | 信息 |
| `--highlight` / `-border` / `-strong` / `-soft` | 琥珀系 | 琥珀系 | PDF/结构搜索高亮 |
| `--shadow-sm/-md/-shadow` | 中性黑透明 | 中性黑透明 | 层级阴影 |
| `--scrim` | `#1c1c225c` | `#000000a6` | 弹窗遮罩 |

### 2.2 字阶、间距与尺寸

- 字号：`--fs-2xs:11 --fs-xs:12 --fs-sm:13 --fs-base:14 --fs-md:15 --fs-lg:16 --fs-xl:20 --fs-2xl:23`。
- 行高：`--lh-tight:1.3 --lh-base:1.55 --lh-loose:1.75`。
- 间距：`--sp-1..7 = 4/6/8/12/16/24/32`。
- 圆角：`--r-xs:4 --r-sm:6 --r-md:10 --r-lg:14 --r-pill:999`。
- 控件：`--control-h:32 --control-h-sm:27`。
- 布局：`--header-h:50 --tab-h:36 --rail-w:56 --panel-w:236 --detail-w:384 --chat-w:380 --thumbnail-width:136`。

正文按 `--fs-base`/`--fs-md`（14–15px）校准，论文列表标题 `--fs-md`（15px），次要信息 `--fs-xs`/`--fs-2xs`（12/11px）；长标题用两行截断并保留 `title` 全文，不靠缩小字号塞入。

## 3. 公共组件

| 组件 | 规范 |
| --- | --- |
| 按钮 `.primary` / `.primary.dark` | 主色底 + `--accent-contrast` 文字；悬停 `--accent-hover` |
| 图标按钮 `.icon-button` | ≥30px 点击区、`--r-sm` 圆角、必带 `aria-label` 与 `title` |
| 文本按钮 `.text-button` | 主色文字、无边框、`--fs-sm` |
| `.danger` / `--danger` | 危险动作，与常用动作分开收纳 |
| 输入 / 下拉 | 统一边界、`--r-sm`、统一聚焦环 |
| 徽标 `.badge`（neutral/success/warning/danger/info） | 状态同时有文字，不单靠颜色；`--r-pill`、`--fs-2xs` |
| 筛选片 `.filter-chip` / `.keyword-chip` | 灰紫强调底 + 强调文字，可单独移除 |
| 弹窗 `dialog.modal` | 统一头/体/脚、`--r-lg`、遮罩 `--scrim`；Escape 关闭、焦点返回由 `ui.tsx` 的 `Modal` 保证 |
| 列表行 | 文献库 `.paper-row`、目录 `.tool-window.compact .tool-window-row`、书签 `.bookmark-row` 均使用紧凑行高与一致悬停/选中态 |
| 状态 | `Status` 组件统一加载/失败/重试；`.notice`、`.empty-state` 使用语义色 |

所有交互控件都覆盖正常、悬停、聚焦、选中、禁用态；焦点环使用 `--accent`；`prefers-reduced-motion` 下关闭过渡与动画（`style.css` 末尾）。

## 4. 布局与响应式

### 全局框架
左图标导航条（`--rail-w`，≤640 变为底部标签栏）+ 顶部功能栏（`--header-h`）+ 论文页签（`--tab-h`）+ 内容区。页签激活态用 `--accent-bg` 底、`--accent` 文字与顶部细条；导航各级只保留一个导入入口（顶部功能栏），空状态提供上下文入口。

### 文献库
`图书馆工作台` 三列：导航/主题（`--panel-w`，可拖动）+ 论文列表（`minmax(300px,1fr)`）+ 详情侧栏（`--detail-w`，可拖动）。未选中论文时桌面端收起详情列（`@media (min-width:901px)` 且无 `has-selection`），把空间让给列表。≤900 隐藏详情、选中时单列显示详情并隐藏列表；≤640 变为单区域 + 抽屉（主题筛选用 `Modal`）。

论文条目固定为单行到三行信息（标题 → 作者·年份 → 状态/标签），目标约 80px 高，1440 桌面一屏可显示 9 条以上。

### 阅读器
顶部工具栏压缩到 46px，按 `[导航/搜索] | [标题·文档版本] | [页码] | [缩放/旋转] | [问答]` 分组（`--tab`/`.toolbar-separator`）；正文区只调整外壳，不改 PDF 页面颜色与坐标。左阅读导航的目录使用紧凑行（`WindowList compact`，34px 行高，与虚拟化行高一致）。AI 侧栏宽度由 `--chat-w`/偏好控制，最小 300、最大 640。≤900 收窄侧栏并隐藏缩略图；≤640 单区域 + 抽屉，隐藏旋转与分隔符。

PDF/译文/结构内容的颜色、字号和坐标由内容决定，界面主题只影响外壳，不做全局反色或整体缩放。

## 5. 维护约定

- 新增颜色必须先进令牌，不在组件里写颜色字面量；`grep -nE '#[0-9a-fA-F]{3,8}' frontend/src/*.css` 应只匹配令牌定义行。
- 不新增品牌皮肤或主题编辑器；浅色与深色分别调校明度与对比度。
- 布局与密度改动用真实/合成长标题、多作者、多标签、缺失摘要与长聊天核对，并运行 `frontend/unified-tests/ui-design.spec.ts` 的主色与密度审计。
- 验收证据保存在 `.devnotes/ui-phase1-acceptance/`，交付记录见 `.devnotes/ipaper-ui-phase1-delivery.md`。