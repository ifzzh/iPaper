# 统一产品的验收范围

本轮已由 1.1.3 完成发布、固定摘要部署及正式浏览器验收，结果见 [发布记录](../releases/v1.1.3.md)。1.1.0 保留为未部署候选；1.1.1 的稳定阅读位置验收失败后已回退。历史测试通过不替代最终镜像及正式服务的实际验收。

| 能力 | 实现/回归证据 | 验收边界 |
|---|---|---|
| 统一首页、登录、首次改密、失效与退出 | `tests/test_workbench.py`、`frontend/unified-tests/session.spec.ts`、`account.spec.ts` | 历史 URL 进入同一应用；跨标签清空内容，服务端退出失败不伪称已注销 |
| 独立研究首页与文献库 | `frontend/src/home-data.test.ts`、`frontend/unified-tests/home.spec.ts` | `/` 默认首页；旧论文链接与显式文献库可用；活动面板不占用列表；按真实 UTC+8 数据绘图、空态与错误不伪造数据；继续阅读恢复位置、主题跳转可筛选；手机不横向溢出 |
| 冷启动文献库与 owner 隔离 | `tests/test_workbench_cold_start.py`、`tests/test_workspace_state.py`，候选镜像真实 Flask 冷库检查 | 不依赖先访问旧首页；列表每页50 DOM，API仍返回全量 |
| 分类、收藏、Reading List、元数据 | `frontend/unified-tests/product.spec.ts`、`tests/test_category_transaction.py` 分类原子保存/虚拟根回归 | 合成用户执行修改；不改写生产论文 |
| 上传与导入 | `upload.spec.ts`、`import.spec.ts`、`tests/test_import_contract.py`、独立 Document Worker HTTP 验收 | Zotero目标字段、SSE、任务owner、有界后台身份；不放宽归档校验 |
| Daily、任务、设置与管理员 | `product.spec.ts`、`daily-states.spec.ts`、`account.spec.ts`、`task-history.spec.ts`、`tests/test_daily_asset_identity.py`、`tests/test_daily_asset_processor.py`、`tests/test_daily_scheduler_state.py` | 操作在统一界面，不嵌入旧页；Daily 的 PDF 与封面状态分别显示，缺图可只重生成封面，失败不提前标已读，调度状态可区分线程/启用/本轮/暂停/空闲；变更测试使用合成数据，生产不新建付费任务 |
| 阅读高亮、批注与单篇笔记 | `tests/test_paper_notes.py`（25 项）、`tests/test_translation_budget.py`（缓存子单元发布与时间保护）、`frontend/src/noteSession.test.ts`（16 项）、`frontend/unified-tests/notes.spec.ts`（21 项）及 `notes-reliability.spec.ts`（8 项，独立合成结构化夹具） | 自动保存与编辑版本绑定（慢响应不覆盖新输入）、失败草稿跨面板恢复/重试/离开提醒、冲突绑定所见修订且双方保留、四类内容（原文 PDF/版式译文/结构原文/结构译文）创建→列表→标记→刷新→回访、批注编辑改色与删除撤销、回答与摘录按身份判重、来源完整回访与分页、页级记录、失效版本安全降级、缩放/旋转后几何对应 |
| PDF、缩略图、版本与标签 | `session.spec.ts`、`visual.spec.ts`、`position-layout.spec.ts` | 实际 AutoSci 原文/已有译文22页；页码必须与可见页面一致，稳定后保存并在新会话恢复 |
| 聊天、历史与选区 | `stream.spec.ts`、`product.spec.ts`、`tests/p0_live_chat.py` | 增量UTF-8首行JSON+文本；只进行1次授权真实问答，其余假模型；停止接收不等于服务端取消 |
| 失败、竞争与安全内容 | `recovery.spec.ts`、流适配单元及恶意载荷回归 | 位置读取失败不覆盖旧值；失效身份清空；Markdown清洗，外部图片不自动加载 |
| 样式、响应布局与资源 | `visual.spec.ts`，1920/1440/390浅深截图 | 严格脚本及样式CSP；动态定位走CSSOM；实际文字/画面/可见区域均核对 |

完整 Docker 非集成、安全扫描、SBOM、远端 CI 和固定摘要运行检查是额外发布门禁，不能代替上述产品体验。正式论文截图、文本、文件指纹、账号会话及受限备份路径只保存在私有开发证据中；GitHub Release 只保留平台自动生成的源码归档，不再附带 SBOM、扫描报告或验证 JSON，脱敏结论、组件摘要与有效漏洞例外写入 Release Notes 正文。

统一浏览器套件通过 `cd frontend && npm run test:e2e` 运行。默认分成三个顺序执行的 Playwright 分片，每片重新创建临时数据库和测试服务，避免多个用例累积触发同一账号 15 分钟内 5 次登录的真实限流；不修改应用鉴权阈值。传入文件名或配置参数时按指定范围运行。首页使用独立的合成账号/文件，避免改变其它用例依赖的文献数、主题或阅读历史。

PaperQuay 来源、许可限制与独立组件适配见 [接入记录](paperquay-adaptation.md)。高级 Agent、块级翻译、笔记、RAG、综述和图谱在同一产品内继续建设，不在本轮提供占位入口。
