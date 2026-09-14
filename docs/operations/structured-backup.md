# 结构产物备份与功能版本回退

1.2.0 的 SQLite 索引引用版本化正文、图片和译文文件。仅复制数据库不构成完整的新功能备份。现有论文树、配置和原主密钥同样需要保护；更换或丢失主密钥会使既有和独立模型凭据无法解密。

## 日常备份

在运行源码的 Python 环境执行：

```sh
python scripts/backup_processing.py \
  --database /srv/ifserver-fast/ipaper/runtime/db/ipaper.db \
  --papers-root /mnt/raid1/projects/ipaper/data/papers \
  --destination /mnt/raid1/backups/ipaper/structured/UNIQUE_TIMESTAMP
```

目标必须尚不存在。程序创建 SQLite 一致性快照、检查完整性，再复制该快照引用的不可变产物，逐文件验证 SHA-256。备份 lease 防止同期清理；完成清单为 `manifest.json`。存在 `INCOMPLETE` 或缺少清单时不能恢复。目录 0700、文件 0600，不输出凭据。此命令不能替代原文、历史 PDF、配置与主密钥的常规备份。

## 发布前完整备份

先核对各用户的分析、翻译、Daily、导入和结构任务空闲，再优雅停止三个服务；禁止 `down -v`。使用 `scripts/prepare_release_backup.py` 指定实际 deployment/database/papers/destination/regctl 路径。它要求所有写入容器已停止，检查 Compose 与运行镜像匹配且固定 digest，以及 `deploy_document_jobs` external 卷绑定。

备份包包含数据库和不可变产物快照、完整论文树及哈希清单、受限配置/密钥副本、组件摘要和独立回退程序。Registry 仍通过维护账号已有的 Docker 凭据流程认证；包中不保存 Registry token。回退工具及 Registry 工具都复制到持久备份目录，不能只留在 `/tmp`。

## 功能版本回退

1.2.0 发布时的历史回退基线为 1.1.4；1.3.0 的回退基线为本次切换前的 1.2.0 兼容组件组合。每次以新备份中的实际摘要为准，不运行品牌改名时期的迁移回退脚本，也不改回 PaperPilot 路径。

```sh
sudo -n python3 /PERSISTENT_BACKUP/rollback.py --backup /PERSISTENT_BACKUP
sudo -n python3 /PERSISTENT_BACKUP/rollback.py --backup /PERSISTENT_BACKUP --apply
```

第一条只校验并显示摘要；第二条停止三个服务、恢复该备份记录的配置与固定镜像、检查健康/ready，并回退新 Web 仓库的 `latest` 到同一已验证摘要。不会删除新表、不可变产物、用户论文，也不会用备份覆盖当前数据库。切换期间不允许两套 Web 同时写库；正在处理的供应商请求不承诺取消或退费。

恢复后实际核对原文、已有译文、账号和历史。再次升级会重新识别新表、来源、独立凭据和旧镜像期间新增的数据。旧版本不显示结构结果，不代表它们被删除。灾难恢复应在全新隔离目录恢复数据库、匹配的产物和原主密钥，先验证哈希及容器 UID/GID 权限，再安排正式切换；不能直接把旧快照覆盖到仍在写入的服务。

## 受控验收产物提升

`scripts/promote_processing_acceptance.py` 仅用于操作员把本轮隔离验收的已成功结果提升到匹配论文；不是面向用户的导入格式或公共 API。默认 dry-run，源和目标写入均须停止。它校验验收回执、owner/论文、原文 SHA、配置与凭据修订、所有产物哈希和目标配额，再以只新增事务登记解析、译文版本、已完成任务及已核验会话。已有用户、论文、设置和密钥表不复制、不覆盖；UUID 冲突直接拒绝。失败不留下可见结果。

验收目录含实际论文和模型回答，只能放入受限私有存储；不上传到 Release。真实凭据仅由验收进程内存持有，产物提升不搬运任何密钥或凭据密文。

## 1.2.0 本机运维记录

本机日常入口仍为 `/mnt/raid1/projects/ipaper/automation/backup-ipaper.sh`，现已使用上述 SQLite + 不可变产物逻辑并实际生成 50 文件匹配快照。原备份自动化入口和调度保持，原脚本随发布前备份保存。

本次发布回退基线目录为 `/mnt/raid1/backups/ipaper/releases/1.2.0-20260913T011653Z`，其 `rollback.py` 独立可执行，默认 dry-run。它恢复 Web/Translation/Document 1.1.4 的独立仓库摘要及配置、回退 Web latest，继续使用当前数据库和产物。完整数据/配置备份及工具文件均受限；不要使用早期品牌迁移回退程序。

一次性产物提升容器需要读取私有验收目录并给新文件设置 Web 所有者。本次仅该无网络、非驻留容器增加 `DAC_OVERRIDE`/`CHOWN`，正式三服务仍非 root、`cap_drop: ALL`。正式数据库含容器内 `/data/papers` 绝对引用，提升应使用相同容器挂载映射，不能将宿主路径直接套用。

## 1.3.0 单篇分析的新增备份范围

1.3.0 的升级基线是已部署的 1.2.0。备份逻辑增加 `understanding_artifacts` 清单中的正文快照、授权图片、分析修订、分段检查点和离线导出；证据和问答范围索引随 SQLite 一致性快照保存。没有新增加密用途或密钥表，概览与解读引用现有 interpret 凭据。

发布前必须重新记录当时三个组件和活动任务，不使用上述历史 1.2.0 发布目录去回退本阶段。回退继续保留当前数据库的理解索引及文件；1.2.0 不识别这些表但可以继续读写原有功能。详细本轮备份路径在完成正式切换后写入新 Release 与交付记录。

1.3.0 使用 `scripts/promote_understanding_acceptance.py` 单独提升已批准且通过真实验收的概览、长解读、证据、任务和两次问答。默认只校验，要求目标论文及现有解析版本完全匹配、凭据修订不变、没有既有分析头或活动任务；不复制账户、配置、密钥或旧解析，不覆盖已有结果。先完成一致性备份和停写，再执行明确的 `--apply`；私有样本不得附到 Release。


### 1.3.0 本次已执行的备份与回退入口

正式切换前备份：`/mnt/raid1/backups/ipaper/releases/1.3.0-20260914T015657Z`。已校验313篇论文、669个原有文件、50个原有不可变产物；只新增16个理解产物索引及34个文件，没有覆盖旧论文、聊天、设置或密钥。升级后的日常备份实测包含84个不可变文件，含新概览、长解读、引用图片和导出。

```sh
sudo -n python3 /mnt/raid1/backups/ipaper/releases/1.3.0-20260914T015657Z/rollback.py \
  --backup /mnt/raid1/backups/ipaper/releases/1.3.0-20260914T015657Z
```

默认dry-run已通过；确认运维回退时追加`--apply`。它恢复本次Web 1.2.0及两个Worker 1.2.0固定摘要、配置和Web latest，保留当前数据库、新分析产物及升级后新增用户内容。不能运行1.2.0发布目录中的旧回退脚本（其基线是1.1.4）。

一次性提升使用发布归档中的`scripts/`只读挂载至无网络操作容器；脚本不进入常驻Web运行镜像。设置密钥不挂给提升容器；验收样本中的钥匙仅是隔离夹具钥匙，真实interpret凭据没有写入样本或提升到正式环境。


运维轮询注意：既有`GET /api/import/zotero/status`归入导入/上传限流桶，不能作为高频健康探针。发布前只做必要的一次导入状态确认；等待Daily时只读Daily进度，遇到429按返回等待，不将限流当作“没有任务”。本次高频等待脚本曾触及该旧限流，未改限流策略；后续记录结合最后一次空闲导入状态和期间没有新的已受理导入请求。
