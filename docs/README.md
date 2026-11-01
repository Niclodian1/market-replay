# 文档索引（单一事实源）

> 先读顺序：`README.md`（怎么跑）→ `STATUS.md`（现在到哪）→ 本页（细节在哪）。
> 每个链接标注「最后校对」日期；改动架构/流程时同步更新这里与 `STATUS.md`。

## 1. 状态与总览

| 文档 | 内容 | 最后校对 |
| --- | --- | --- |
| [STATUS.md](../STATUS.md) | 当前里程碑、阻塞、下一步、最近一次验收命令与结果 | 2026-10-22 |
| [architecture/PROJECT_OVERVIEW.md](architecture/PROJECT_OVERVIEW.md) | 系统全貌（数据链 / 后端域 / 前端页 / 缠论两条链） | 2026-10-23 |
| [refactor/ARCHITECTURE_DESIGN_2026-10-22.md](refactor/ARCHITECTURE_DESIGN_2026-10-22.md) | 重构设计（P0–P7 验收清单，全部 ✅） | 2026-10-22 |
| [refactor/REPO_AUDIT_2026-10-22.md](refactor/REPO_AUDIT_2026-10-22.md) | 仓库盘点与分区方案 | 2026-10-22 |
| [refactor/IMPLEMENTATION_2026-10-22.md](refactor/IMPLEMENTATION_2026-10-22.md) | 逐轮落地记录（§1–§30，含缠论因果性审计、参数实验室） | 2026-10-23 |

## 2. 运行手册（runbooks）

| 文档 | 内容 | 最后校对 |
| --- | --- | --- |
| [runbooks/DATABASE_MIGRATIONS.md](runbooks/DATABASE_MIGRATIONS.md) | Alembic 迁移 / `DB_AUTO_DDL` 取舍 | 2026-10-22 |
| [runbooks/ALERT_V1_BACKUP_RESTORE.md](runbooks/ALERT_V1_BACKUP_RESTORE.md) | 告警库备份与恢复 | 2026-08-16 |
| [runbooks/DAILY_EMAIL_REPORT.md](runbooks/DAILY_EMAIL_REPORT.md) | 每日邮件报告 | 2026-08-16 |
| [runbooks/BROWSER_VERIFICATION.md](runbooks/BROWSER_VERIFICATION.md) | 浏览器端到端验证（30 项断言，含 PWA/离线/练习交互） | 2026-10-22 |

## 3. 架构专题

| 文档 | 内容 | 最后校对 |
| --- | --- | --- |
| [architecture/MULTI_TIMEFRAME_V1.md](architecture/MULTI_TIMEFRAME_V1.md) | 多周期级别联动 | 2026-08-16 |
| [architecture/MARKET_STATE_V1.md](architecture/MARKET_STATE_V1.md) | 市场状态机 | 2026-08-16 |
| [architecture/ALERT_V2_ROADMAP.md](architecture/ALERT_V2_ROADMAP.md) | 告警 V2 路线 | 2026-08-16 |
| [architecture/M3_SHADOW_RUNTIME_V1.md](architecture/M3_SHADOW_RUNTIME_V1.md) | M3B 影子运行时 | 2026-08-16 |
| [architecture/OUTCOME_LABELING_V1.md](architecture/OUTCOME_LABELING_V1.md) | 结果标注（Outcome）口径 | 2026-08-16 |
| [architecture/MTF_BTC_BACKTEST_V1.md](architecture/MTF_BTC_BACKTEST_V1.md) | MTF-BTC 对照回测 | 2026-08-16 |

## 4. 策略与实验

| 文档 | 内容 | 最后校对 |
| --- | --- | --- |
| [strategies/MA策略.md](strategies/MA策略.md)、[strategies/MA_BREAKOUT_OPTIMIZED.md](strategies/MA_BREAKOUT_OPTIMIZED.md) | 均线策略口径 | 2026-08-16 |
| [strategies/分段加仓策略.md](strategies/分段加仓策略.md) | 分段加仓 | 2026-08-16 |
| [../labs/README.md](../labs/README.md) | 实验三态与登记表用法 | 2026-10-22 |
| [../labs/experiments/registry.yaml](../labs/experiments/registry.yaml) | 实验登记表（唯一事实源） | 2026-10-22 |

## 5. 归档

| 文档 | 说明 |
| --- | --- |
| [archive/PROGRESS_2026H1.md](archive/PROGRESS_2026H1.md) | 2026 H1 追加式进度记录（1594 行，已冻结） |
| [notes/](notes/) | 早期笔记（含 `zhibiao.md` 指标草稿） |
