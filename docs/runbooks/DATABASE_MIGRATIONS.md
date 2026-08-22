# 数据库迁移 Runbook（Alembic）

> 生效日期：2026-08-22。此前 schema 由 `main.py` lifespan 里的
> `create_all` + 手写 `ALTER TABLE ... IF NOT EXISTS` 管理，现已引入 Alembic。

## 现状与双轨期

`config.DB_AUTO_DDL = true`（默认）时维持旧行为：启动自动建表补列。
设置 `DB_AUTO_DDL=false` 后，schema 完全由 Alembic 管理，启动不再改库。

## 迁移文件位置

- 配置：`backend/alembic.ini`
- 环境：`backend/alembic/env.py`（URL 取自 `settings.DATABASE_URL`，
  `+asyncpg` 自动转 `+psycopg2` 同步驱动）
- 版本：`backend/alembic/versions/`
  - `0001_baseline`：全量建表（直接 `Base.metadata.create_all`，与模型零偏差）

## 既有生产库接入（一次性）

生产库已有全部表，**不要**执行 upgrade，只需打标记：

```bash
cd backend
alembic stamp head          # 写入 alembic_version=0001_baseline
```

## 新环境部署

```bash
cd backend
export DB_AUTO_DDL=false
alembic upgrade head        # 空库建出 13 张表
```

## 日常改表流程

1. 修改 `app/models/models.py`
2. 自动生成迁移并人工审查：

```bash
cd backend
alembic revision --autogenerate -m "add xxx"
# 审查 versions/ 下新文件——autogenerate 不识别 CHECK 约束变化与部分默认值
alembic upgrade head
```

3. 生产变更窗口：先备份（`scripts/ops/alert_backup.sh create`），再 upgrade，
   失败用 `alembic downgrade -1` 或恢复备份。
4. 提交迁移文件与模型改动到同一个 commit。

## 注意事项

- 模型含 PG 专有 CHECK（如 m3_shadow_events 的 `~` 正则），SQLite 无法建表；
  本地快速验证可用 `alembic upgrade head --sql`（离线生成 SQL，不连库）。
- 关闭 DB_AUTO_DDL 前，确认目标库已 `stamp head` 且后续一律走迁移，
  否则新增表/列不会自动出现。
