# iPaper

**自托管 AI 论文与研究工作台**
发现论文，深入阅读，连接知识。

[English](README.md) · 中文 · [发布记录](https://github.com/ifzzh/iPaper/releases) · [部署指南](docs/operations/deployment.md) · [许可证](LICENSE)

iPaper 从 PaperPilot 持续开发而来，正在将 PaperQuay 的阅读工作台设计与相关交互适配到统一的 Web 产品中。我们希望在自己的服务器上，把论文发现、文献管理、阅读、翻译和研究积累连接起来。

**产品与技术标识统一为 iPaper / ipaper。** 使用三个独立镜像仓库及 `IPAPER_` 环境变量。已有安装按迁移指南升级；持久化标识和旧环境变量读取保留兼容。


[1.3.0](https://github.com/ifzzh/iPaper/releases/tag/v1.3.0) 已发布并通过正式部署验收，提供独立 AI 概览、图文深度解读、共同原文来源、局部/整篇问答，以及 Markdown、图片包和浏览器打印导出。生成须明确操作，读取已有结果不调用模型。该版本仅更新 Web，两个 Worker 复用已验证的 1.2.0 摘要。范围与限制见 [单篇理解说明](docs/development/single-paper-understanding.md)。

[1.4.0](https://github.com/ifzzh/iPaper/releases/tag/v1.4.0) 提供全文搜索、章节目录、版本化书签、PDF 段落缓存译文、明确触发的划词翻译及图表放大。搜索和缓存阅读不调用模型或自动 OCR。范围与限制见[日常阅读工具](docs/development/reading-tools.md)。兼容组合为 Web 1.4.0＋两个 Worker 1.2.0，部署使用 Release 中的最终摘要。

## 一个连续的研究流程

**Daily arXiv 发现 → 加入文献库 → 阅读与翻译 → 论文问答 → 笔记与研究整理。**

已有服务提供文献管理、BabelDOC 翻译、MinerU 解析与分析、单篇论文问答、Daily arXiv 和本地账号。统一文献库、连续 PDF 阅读器、阅读位置恢复和侧栏问答已交付。1.2.0 已发布双翻译与可信来源定位；笔记、文献操作 Agent、跨论文检索、综述和图谱按阶段建设。

开发分支中的版本号和页面不等于已发布能力。安装前请查看目标 [Release](https://github.com/ifzzh/iPaper/releases) 的功能范围、已知问题和验证记录；README 不将规划中的功能视为已完成。

## 现有能力与集成方向

| 领域 | 已实现 | 后续建设 |
| --- | --- | --- |
| 论文发现 | Daily arXiv、研究主题与筛选、候选和 PDF 资产处理 | 持续完善发现流程 |
| 文献管理 | PaperQuay 风格分类/列表/详情；上传、搜索、收藏、Reading List、Zotero RDF 导入 | 文献操作 Agent |
| 阅读与理解 | 连续 PDF、缩略图、标签、独立阅读位置、选区提问、有效来源跳转及侧栏历史问答 | 关联笔记与批注 |
| 翻译 | BabelDOC 纯译文/双语 PDF、独立结构化双语内容；有界任务、缓存与单块重译 | 持续完善语言与版式支持 |
| 数据与账号 | 本地账号、用户范围隔离、服务端模型配置、持久任务 | 继续复用，保留已有论文和聊天记录 |
| 研究积累 | 独立 AI 概览、图文长解读、带来源的局部/整篇问答与离线分析导出 | 笔记、Agent 文献操作、RAG、综述与图谱 |

当前使用统一正式界面，不要求用户在“新版/旧版”间选择。旧版本可能仍有实验入口；这属于历史过渡形态，不是产品目标。

## 两种翻译方式

[1.2.0 正式版](https://github.com/ifzzh/iPaper/releases/tag/v1.2.0) 在同一阅读器提供两种流程。详见[实现与能力边界](docs/development/dual-translation.md)。

| 方式 | 处理流程与结果 | 适用场景 |
| --- | --- | --- |
| **版式翻译 · BabelDOC** | 生成译文或双语 PDF，尽量保留原文版式 | 连续阅读、下载、打印 |
| **结构化翻译 · MinerU + 大模型** | MinerU 解析文本、公式和表格，再由模型翻译结构块 | 段落对照、来源定位、块级重译与问答 |

MinerU 负责解析，不是翻译引擎。同一论文的两种结果独立保存；阅读器选择原始 PDF、已有版式译文或结构化译文。切换已有结果不自动创建翻译任务，尚未生成的结果需要明确操作。

解析按每段最多 200 页处理，仍受文档和存储总限额约束。仅已验证坐标路径提供区域高亮，其他情况降为页级或明确无法定位。不会自动重译已有 PDF。结构化翻译使用独立服务端模型配置，生成前显示范围和预算。新数据使用增量表及不可变文件；升级前必须[同时备份数据库和产物](docs/operations/structured-backup.md)。

## 自托管部署

基础部署由三个容器组成，前端由 Web 服务提供，不需要单独的前端容器：

| 服务 | 职责 | Docker Hub 仓库 |
| --- | --- | --- |
| Web | 页面、API、账号、文献库、AI 调用与任务编排 | [ifzzh520/ipaper](https://hub.docker.com/r/ifzzh520/ipaper) |
| Translation Worker | BabelDOC 翻译 | [ifzzh520/ipaper-translation-worker](https://hub.docker.com/r/ifzzh520/ipaper-translation-worker) |
| Document Worker | 受控 PDF、归档与解析产物处理 | [ifzzh520/ipaper-document-worker](https://hub.docker.com/r/ifzzh520/ipaper-document-worker) |

1. 选择已发布版本，查看其三个组件的兼容矩阵。
2. 按[部署指南](docs/operations/deployment.md)准备 Compose、存储目录、本地管理员和密钥文件。
3. 固定版本及 digest，启动后验证登录、真实 PDF 和已有数据。

Web 默认端口为 **7191**。Worker 不开放宿主端口；未变化的 Worker 复用已验证版本，不随每次应用发布重新构建。当前源码的目标组合见 [release-components.json](docker/release-components.json)，正式部署以所选 Release 的矩阵为准。

本地存储不代表全部计算离线：AI 翻译、解析或问答可能向配置的服务发送文档内容，并产生对应服务费用。请根据需要选择服务和处理范围。

## 项目来源

感谢上游项目及其贡献者。iPaper 是在既有工作上的持续开发，不将上游功能和设计表述为全部原创。

- **[PaperPilot](https://github.com/flyflypeng/PaperPilot)**：本项目的直接代码基础，提供文献管理、Daily arXiv、BabelDOC 翻译及论文分析等基础能力。
- **[PaperQuay](https://github.com/WangQrkkk/PaperQuay)**：阅读工作台、文献库布局、笔记与 Agent 工作流的重要参考。当前适配记录采用独立实现重现相关布局与交互，不代表已将其全部源码或功能合并。具体对应关系见[适配记录](docs/development/paperquay-adaptation.md)。
- **[Resophy](https://github.com/Mountchicken/Resophy)**：PaperPilot README 所注明的更早项目来源；保留这一来源链与致谢。

核心依赖包括 [BabelDOC](https://github.com/funstory-ai/BabelDOC)、[MinerU](https://github.com/opendatalab/MinerU)、PDF.js、React 和 Flask。依赖项目按各自许可证使用，不意味着上游对本项目背书。

## 后续方向

- 批注与关联笔记。
- 可查看计划和结果的文献操作 Agent。
- 跨论文与笔记检索、综述写作和知识图谱。

以真实功能和界面验收推进，不通过占位入口或演示数据宣称完成。版本细节见 [Release 文档](docs/releases)，前端开发入口见 [frontend/README.md](frontend/README.md)。

## 许可证与贡献

当前仓库的 [LICENSE](LICENSE) 为 **CC BY-NC 4.0**；项目元数据中仍有 MIT classifier 的历史不一致，不能据此认定仓库采用 MIT。PaperQuay 标注 **AGPL-3.0-only**，代码复用范围与相应声明应单独核对，品牌改名不会改变现有授权条件。

欢迎通过 [Issues](https://github.com/ifzzh/iPaper/issues) 反馈可复现问题和功能需求。请提供应用与组件版本、操作步骤和脱敏错误信息，不上传密钥、账号凭据或私人论文。提交改动时注明参考来源，并同步必要的测试和文档。
