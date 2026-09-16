# Deploying iPaper / 部署 iPaper

iPaper uses ipaper deployment identifiers and IPAPER_ environment variables. The current repository Compose includes maintainer-specific host paths: it is a configuration reference, not a universal one-command installer.

iPaper 使用 ipaper 运行标识和 IPAPER_ 环境变量。仓库 Compose 含维护者机器的路径，部署到其他机器前必须调整；已有部署按改名迁移记录切换路径，保留数据和密钥。

## 1. Choose a release / 选择版本

Use a published [GitHub Release](https://github.com/ifzzh/iPaper/releases), read its deployment notes and pin its complete component matrix. The source checkout may describe an unpublished next version. Do not assume that all three components share the application version.

选择已发布 Release，按其说明固定完整组件版本和 digest。开发分支可能已经使用下一版本号，不能因此认定镜像已发布。

| Component | Repository |
| --- | --- |
| Web | `ifzzh520/ipaper` |
| Translation Worker | `ifzzh520/ipaper-translation-worker` |
| Document Worker | `ifzzh520/ipaper-document-worker` |

Production uses `repository:version@sha256:…` references. Worker versions are reused when unchanged; Workers do not use `latest`. The source matrix is in [release-components.json](../../docker/release-components.json).

1.6.0 upgrades only Web; both Workers remain on verified 1.2.0 digests. Keyword tables are additive. Capture a new stopped release backup before upgrading, retain the current database on rollback, and stop new keyword jobs before starting an older Web. See [the keyword release](../releases/v1.6.0.md) and [backup procedure](structured-backup.md).

1.6.0 仅更新 Web。回退保留新增标签、别名、自动排除和当前用户数据；不要使用旧数据库覆盖升级后的修改。Web latest 仅在正式阅读及重启验收后更新。

## 2. Prepare storage and configuration / 准备存储与配置

Copy the release's Compose and environment example into a deployment directory outside the source checkout. Review every bind mount and secret path before starting services.

- Keep SQLite, paper assets, translation staging and backups in dedicated locations outside the source tree. Preserve existing user UUID layouts on upgrades.
- Copy [the environment example](../../.env.example) for a new installation; do not overwrite an existing environment file.
- Keep production authentication in `local` mode. Set secure cookies for HTTPS. Workers have internal ports only; Web binds to `127.0.0.1:7191` by default. Provide HTTPS through your existing reverse proxy when needed.
- On first installation, create **two separate random Worker tokens** and a **32-byte settings encryption key** using a secure random generator. Use the Compose secret paths. Never regenerate these files during an ordinary upgrade.
- Ensure the Web UID 10001, translation UID 10002 and document UID 10003 can access only their required files through shared GID 1001. Secret/config files must be readable by the required container user; runtime SQLite and data directories must be writable. Do not solve permission errors with world-writable permissions.
- Keep Document Worker on its internal network without library/database mounts. Keep Translation Worker staging separate from persistent paper storage.

中文要点：修改宿主路径、准备独立数据库与论文目录、配置本地账号认证、生成并保护两个 Worker token 和设置加密主密钥。升级时保留现有密钥与权限，不向 Worker 挂载论文库或数据库。

## 3. Create the first administrator / 初始化管理员

For a **fresh database only**, after the paths, permissions and pinned images are ready, run from the deployment directory:

```bash
docker compose config --quiet
docker compose pull
docker compose run --rm --no-deps ipaper \
  python -m ipaper.auth_cli --db /app/db/ipaper.db \
  create-admin --username admin
```

The password is entered through a hidden interactive prompt; never put it in an argument or environment variable. The first login requires a password change. Existing installations keep their accounts and follow the release-specific migration instructions instead.

仅新安装执行管理员初始化。密码通过隐藏交互输入，首次登录需要改密；已有部署不能通过重建数据库来升级。

## 4. Start and verify / 启动与验证

```bash
docker compose up -d
docker compose ps
curl --fail http://127.0.0.1:7191/healthz
curl --fail http://127.0.0.1:7191/readyz
```

Open `http://localhost:7191` through the intended local/HTTPS access path. Verify login, a real PDF, existing translations and chat history. Configure AI providers and MinerU through the application's settings; credentials stay server-side. AI features may send selected document content to the configured services and may incur provider charges.

验证登录、真实 PDF、已有译文和聊天记录；仅健康检查通过不等于产品功能验收完成。AI 接口在网页设置中配置，密钥不下发浏览器。

## 5. Upgrade and roll back / 升级与回退

Back up SQLite consistently, paper assets, configuration and the settings key. Keep key backups protected separately. Review active jobs before stopping services, follow the target release's migration procedure and retain the previously verified component digests.

A feature rollback restores images/configuration, not an old database over new user data. For 1.2.0, preserve the additive processing tables and immutable artifacts; the older image ignores them until a later upgrade. Follow the [structured backup and rollback guide](structured-backup.md). Branding/path migration is a separate historical operation.

历史迁移说明见 [upgrade-history.md](upgrade-history.md)，具体操作仍以目标 Release 和当前实际配置为准。

## Renamed installations / 改名升级

Use [the migration guide](ipaper-migration.md) for existing installations. Never start a renamed Compose project against automatically created empty volumes.


## 1.7.0 主题分类

1.7.0 仅发布 Web；Workers1.2.0 不重建。部署前重新检查主题/关键词/元数据、processing、导入和 Daily 活动项。创建本轮停写备份，回退基线为 Web1.6.0，不能复用1.6发布时恢复1.5的脚本。固定摘要部署并实际浏览、阅读和重启验收后才更新 Web latest。

正式1.7.0组件摘要与验收状态见[发布记录](../releases/v1.7.0.md)。本轮升级后日常备份已核验包含主题、排除及任务表；派生搜索索引可因正常Daily更新，不能与不可变论文内容混为一类。
