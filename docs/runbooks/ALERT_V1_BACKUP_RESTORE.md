# ALERT_V1_BACKUP_RESTORE — V1 告警体系备份与恢复 Runbook

> 适用范围：告警体系 V2 · 里程碑 0（M0）交付的 V1 快照/恢复工具。
> 目标：可校验、可演练的 V1 快照；生产事故时可回滚；演练不得影响现用服务。
> 关联：`scripts/ops/alert_backup.sh`、`scripts/ops/alert_restore.sh`、
> `docs/architecture/ALERT_V2_ROADMAP.md`。

## 0. 前置条件与拓扑

- 仓库根：`/Users/zhangzheyu/market-replay`（脚本通过相对路径自动定位）。
- PostgreSQL 16.14 运行于 Docker 容器 `market-replay-postgres-1`
  （可用环境变量 `ALERT_PG_CONTAINER` 覆盖；默认库 `marketreplay`，用户
  `marketuser`，可用 `POSTGRES_USER`/`POSTGRES_DB` 覆盖）。
- 主机工具：`/usr/bin/tar`（bsdtar）、`/usr/bin/shasum`、`docker`。
- 备份目录：`reports/alert-backups/`（已被 `.gitignore` 忽略，永不提交）。

### 快照内容

| 文件 | 内容 |
|---|---|
| `source.tgz` | backend/app、backend/tests、frontend/{app,components,lib,types}、backend/Dockerfile、frontend/Dockerfile、backend/requirements{,-dev}.txt、frontend/package{,-lock}.json、docker-compose.yml、.env.example；不含 .env/行情数据/缓存 |
| `alerts.pgdump` | users、watchlists、alerts、notification_channels、monitor_plans、alert_events 六表（PostgreSQL custom format） |
| `row_counts.tsv` | 六表行数（快照时刻） |
| `metadata.json` | backup_id、HEAD、git 摘要、工具版本、表行数 |
| `MANIFEST.sha256` | 全部文件 SHA-256 |

权限：目录 700，文件 600（含渠道信息的 `alerts.pgdump` 为 600）。日志不打印
webhook/token。

## 1. 创建快照（create）

```bash
# 默认目录 reports/alert-backups/<UTC时间>（如 20260812T141847Z）
scripts/ops/alert_backup.sh create

# 指定目录（例如每周例行快照）
scripts/ops/alert_backup.sh create reports/alert-backups/2026W33
```

输出：5 个文件 + 六表行数列表。完成后立即执行一次 verify。

> 约定：**任何源码改动前**先 create；生产回滚前自动生成 rescue backup
> （见 §4），无需手工 create。

## 2. 校验快照（verify）

```bash
scripts/ops/alert_backup.sh verify reports/alert-backups/20260812T141847Z
```

校验内容：
1. 5 个必需文件存在；
2. 目录权限 700、`alerts.pgdump` 权限 600（不满足仅告警）；
3. `shasum -a 256 -c MANIFEST.sha256`（**任一文件被篡改即失败**，退出码非 0）；
4. `pg_restore --list` 可解析且含六张表；
5. `metadata.json` 与 `row_counts.tsv` 行数一致（各 6 条）。

演练篡改：把 `row_counts.tsv` 或任意文件改一个字节后 verify 必须失败。

## 3. 恢复演练（drill）— 安全，可反复执行

```bash
scripts/ops/alert_restore.sh drill reports/alert-backups/20260812T141847Z
```

流程：
1. 先对备份执行 verify（不通过即中止）；
2. 源码解压到 `mktemp` 临时目录（不触碰工作区）；
3. 创建唯一命名临时库 `alert_drill_<UTC时间>_<pid>`；
4. `pg_restore --no-owner --no-privileges` 恢复六表；
5. 逐表对比 `row_counts.tsv` 与恢复库行数（不一致即失败）；
6. 对比演练前后现用库六表行数（必须不变）；
7. 无论成败，结束后删除临时库与临时目录（trap 兜底）。

约束：不得停止/修改现用服务；不得创建重复临时库（唯一命名）。

## 4. 生产回滚（restore）— 需要显式确认

```bash
scripts/ops/alert_restore.sh restore reports/alert-backups/20260812T141847Z
```

流程：
1. 先对备份执行 verify；
2. 提示输入 backup-id（即目录名，如 `20260812T141847Z`）或 `abort`；
   输入不符立即中止，不做任何修改；
3. **先自动生成 rescue backup**（`reports/alert-backups/rescue-<UTC时间>/`，
   保存当前现场，回滚失败可再回滚）；
4. **源码同步恢复**：解压 `source.tgz` 并按白名单路径同步（rsync --delete）：
   - 目录级同步（删除快照后新增的源文件）: backend/app、backend/tests、
     frontend/{app,components,lib,types}
   - 文件级覆盖: backend/Dockerfile、frontend/Dockerfile、requirements/package/lock、
     docker-compose.yml、.env.example
5. **数据恢复（原子事务，兼容真实 schema）**：`_restore_db` 把「摘除备份外
   引用六表的外键 → pg_restore 导入六表 → 重建外键」放入**单个 SQL 事务**：
   - 先动态找出备份外引用六表的外键（如 replay_notes→users），生成摘除/重建 SQL；
   - `pg_restore --clean --if-exists --no-owner --no-privileges -f -` 输出 SQL 文本；
   - 以 `BEGIN; 摘除; <恢复 SQL>; SET search_path; 重建; COMMIT;` 管道交给 psql
     （`ON_ERROR_STOP=1`）执行；
   - **任一环节失败 → 整个事务回滚 → 现场自动复原**（例如现场数据与快照
     不一致导致重建外键失败时，不会留下「已恢复一半」的破坏状态）；
6. 恢复后逐表核对行数与快照一致；
7. 提示重启 backend 使其加载源码：
   `docker compose restart backend`。

> ⚠ 生产回滚仅覆盖快照白名单路径与六张告警表；`.env` 与行情数据不在快照内，
> 回滚不会触碰。生产 restore 需人工在变更窗口执行并确认；失败时事务自动回滚，
> 现场恢复原状，可直接重试或改走其他处置（rescue backup 保留）。

## 4.1 生产同构恢复测试（验收项）

```bash
scripts/ops/test_restore_schema.sh [<backup-dir>]
```

构造「生产同构库」（六表 + replay_notes(FK→users) + 引用行），复用与生产
restore 相同的 `_restore_db` 流程，断言：六表行数与快照一致、replay_notes
数据保留、外键重建且生效；并覆盖**失败自动回滚**（快照后新增用户及引用
新用户的 replay_notes → `_restore_db` 失败 → 现场完全复原：users/replay_notes
行数与 FK 均不变）；源码同步删除快照外新增文件。全部操作在唯一命名临时库/沙箱
中进行，结束即清理，可反复执行。旧实现在此场景下失败（pg_restore 5 errors
exit 1；失败后遗留 users=1/孤儿笔记/FK=0），故该测试是生产回滚可用性与
原子性的回归闸门。

## 5. 故障排查

| 现象 | 可能原因 | 处理 |
|---|---|---|
| verify 报 SHA-256 失败 | 文件被篡改/损坏/复制中断 | 检查来源；用另一个快照或重新 create；勿用损坏快照回滚 |
| drill 报行数不一致 | pgdump 与 row_counts 采集时刻不同步（活库写入） | 重新 create；确认无并发写入后重试 |
| pg_restore 报错 | 备份损坏 / 容器未启动 | 先 verify；`docker ps` 确认 postgres 容器 |
| 临时库残留 | drill 中断 | `docker exec market-replay-postgres-1 dropdb -U marketuser --if-exists <库名>` |
| 容器名不符 | 部署环境不同 | 设置 `ALERT_PG_CONTAINER` 环境变量 |

## 6. 演练记录

| 日期 (UTC) | 操作 | 快照 | 结果 |
|---|---|---|---|
| 2026-08-12 | create + verify | 20260812T141847Z | 通过 |
| 2026-08-12 | drill（六表行数对比） | 20260812T141847Z | 成功，临时资源已清理，现用库未变 |
| 2026-08-12 | 篡改 row_counts.tsv 后 verify | 20260812T141847Z（副本） | 失败（预期） |
| 2026-08-13 | 生产同构恢复测试 test_restore_schema.sh | 20260812T141847Z | 全部 PASS（六表/数据/FK 重建/源码同步） |
| 2026-08-13 | 失败自动回滚场景（快照后新增用户+孤儿引用） | 20260812T141847Z | PASS：_restore_db 失败且现场完全复原（users=2/replay=2/FK 存在生效） |
| 2026-08-13 | restore 确认中止路径（输入 abort） | 20260812T141847Z | 立即中止，未生成 rescue，未改动任何内容 |
| 2026-08-13 | verify 严格化：权限/backup_id/行数不一致 | 20260812T141847Z（副本） | 分别失败（预期） |

> 后续每次例行快照/演练请追加一行到本表。
