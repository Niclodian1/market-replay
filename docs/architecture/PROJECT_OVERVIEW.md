# Market Replay - 行情复盘助手

> 更新：2026-08-22。早期版本概览与优化建议已归档进 git 历史（4 月版），
> 本版反映告警体系 V2（M0-M3B）落地后的现状。

## 项目简介

Market Replay 是一款面向个人交易者的全栈行情复盘、量化回测与告警平台。
在原有「图表 / 复盘 / 回测 / 告警」闭环之上，2026 年 8 月完成了告警体系 V2 重构：
统一市场状态引擎（MarketState）、多周期融合决策、缠论信号接入、
影子运行时（shadow runtime）与结果标注体系。

架构文档索引见 `docs/architecture/`：

| 文档 | 内容 |
|---|---|
| ALERT_V2_ROADMAP.md | V2 里程碑总路线（M0-M3 状态） |
| MARKET_STATE_V1.md | 统一市场状态模型（趋势/波动/K线质量/量能） |
| MULTI_TIMEFRAME_V1.md | 周/日/4H 角色融合与门控 |
| OUTCOME_LABELING_V1.md / M3_SHADOW_RUNTIME_V1.md | 影子事件标注与运行时 |
| MTF_BTC_BACKTEST_V1.md | 4H 入场位置 A/B 对照事件研究 |

## 技术架构

```
┌────────────────────────────────────────────────────────────┐
│                 Frontend (Next.js 14 App Router)            │
│  TypeScript · Tailwind · Zustand · React Query v5           │
│  lightweight-charts · 缠论叠加/统计 · MA 策略组件组           │
├────────────────────────────────────────────────────────────┤
│                 Backend (FastAPI + SQLAlchemy 2 async)      │
│  market_state(统一状态) → multi_timeframe(融合)              │
│  monitor_engine(V1 告警) / m3_shadow_runtime(V2 影子)        │
│  chanlun_service(fenxing 管线) · daily_report_* 日报         │
│  strategy_backtest_engine · matrix_backtest · signal_detectors│
│  APScheduler 调度 · Discord/飞书/邮件/站内通知                │
├────────────────────────────────────────────────────────────┤
│        PostgreSQL 16 (Alembic 迁移) · Docker Compose         │
├────────────────────────────────────────────────────────────┤
│  数据源: Binance · Hyperliquid(WS) · Yahoo · Dukascopy ·     │
│         Twelve Data · akshare(A股/宏观)                      │
└────────────────────────────────────────────────────────────┘
```

### 因果性契约（全系统硬约束）

时刻 t 的任何状态/信号只允许依赖 `candles[:t+1]`；摆动点需右侧确认
（`idx + pivot <= t`）；信号只记在确认 bar，禁止回写历史。
由 `tests/test_asof_causality.py`、逐前缀一致性测试与 golden digest 保护。

## 后端核心模块（app/core/）

| 模块 | 职责 |
|---|---|
| `market_state.py` + `market_state_adapters.py` | 统一 MarketState 引擎；旧 regime/trend/日报口径经适配层收口，golden parity 测试锁定行为 |
| `multi_timeframe.py` | 周(Context)/日(Setup)/4H(Trigger) 角色融合，产出带完整审计的 FusionDecision |
| `monitor_engine.py` + `signals/monitor_signals.py` | 生产 V1 告警（ma/macd/structure），只消费最新已完成 bar |
| `m3_shadow_runtime.py` | V2 影子事件：decision → label → 完成态状态机（DB CHECK 强约束） |
| `outcome_labeling.py` / `mtf_historical_backtest.py` | 影子结果三重标注、多周期历史回测研究 |
| `chanlun_service.py` / `triangle.py` / `divergence_detectors.py` | 缠论信号（依赖仓库根 `fenxing/` 管线）、三角收敛、背驰检测 |
| `daily_report_{analysis,sources,service,delivery}.py` | 每日多市场日报（生成→渲染→邮件/飞书投递），运维手册 docs/runbooks/DAILY_EMAIL_REPORT.md |
| `strategy_backtest_engine.py` / `backtest_engine.py` | 策略回测（快慢双路径等价）与通用回测 |
| `signal_detectors.py` / `signals/strategy_detectors.py` | 信号检测原语与策略探测器 |
| `notification_channels.py` / `discord_webhook.py` / `feishu_webhook.py` | 多渠道通知路由与投递状态机 |

数据源 provider 在 `app/providers/`（binance/hyperliquid/yahoo_finance/
dukascopy/local_crypto），行情源路由规则见 `tests/test_market_source_routing.py`。

## 前端主要页面

- `/chart/[symbol]`：蜡烛图 + MA/RSI/MACD + 缠论 overlay（画线、对数坐标）
- `/replay/[symbol]`：逐根 K 线复盘播放器 + 笔记
- `/alerts`、`/watchlist`、首页看板（指数/加密/恐慌指数/宏观倒计时）
- `/ma-strategies*`、`/strategy-lab`、`/strategy-overview`：MA 策略族与策略实验台
- `/macro`：宏观日历

前端算法库 `lib/chanlunStats.ts`、`lib/chanlunStrategy.ts` 与后端/fenxing 口径
保持一致，由 `frontend/tests/`（node:test）守护。

## 数据模型（13 表）

`market_candles, users, watchlists, replay_notes, alerts, chanlun_signals,
notification_channels, monitor_plans, alert_events, daily_reports,
m3_shadow_runtime, m3_shadow_events, hot_product_snapshots`

schema 由 Alembic 管理（基线迁移 `0001_baseline`）；
`DB_AUTO_DDL=false` 可切换为纯迁移模式。流程见
`docs/runbooks/DATABASE_MIGRATIONS.md`。

## API 概览（前缀 /api）

health · market · indicators · watchlist · replay/notes · alerts · monitoring ·
auth/login · backtest · chanlun · analysis · macro · strategy-lab ·
ma-strategies(-3x/-c/-unified)

## 工程设施

- **测试**：后端 pytest 650+ 用例（因果性/golden parity/契约测试），
  前端 node:test 23 用例；全部单元化，CI 无需真实 DB/网络
- **Lint**：ruff（Python，零告警）+ ESLint next/core-web-vitals
- **CI**：`.github/workflows/ci.yml` — backend(ruff+pytest) + frontend(tsc+lint+test)
- **pre-commit**：空白/大文件/私钥检测 + ruff --fix + 前端 tsc
- **依赖**：`requirements.lock.txt` 锁定构建；升级改 requirements.txt 后重新解析
- **备份/恢复**：`scripts/ops/alert_backup.sh` / `alert_restore.sh`
  （verify/drill/原子事务恢复），runbook 见 docs/runbooks/

## 部署

```bash
docker compose up -d          # postgres + backend(8000) + frontend(3000)
# API 文档: http://localhost:8000/docs
```

镜像为生产命令（uvicorn 无 --reload / npm build+start）；
compose 中以 command 覆盖保留开发热重载。

## 已知债务与方向

- 三套旧状态口径已收口到 MarketState 适配层，monitor 尚未迁移到融合决策
  （M3C 对照报告与人工切换待做）
- `app/core/` 存在 8 个 800+ 行模块（最大 m3_shadow_runtime 1522 行），
  拆分需配合 golden 测试单独排期
- `fenxing/` 为缠论运行时依赖（已入库），长期应重构为正式 Python 包
- scripts/backtests 下六个同族 MA 回测脚本待参数化合并
