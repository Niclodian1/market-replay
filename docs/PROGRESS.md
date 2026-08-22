# PROGRESS — 告警体系 V2 · 里程碑 0（M0：备份与因果性）

> 本文件由执行者持续更新，记录每个任务的改动、命令、结果、决策、已知债务与下一步。
> 完成进度: [x] = 已完成并验证。

## 0. 基线取证（Task 0）

- 时间（UTC）: 2026-08-12T14:19:00Z
- 分支/HEAD: `refactor/strategy-spine` @ `965d1501db98e351956326d5eb066afb9ff539b1`
- 工作区状态（`git status --short`，184 行）: 103 个 tracked 变化、49 个删除、80 个既有 untracked + 1 个本里程碑新增 `scripts/ops/alert_backup.sh`（共 81 个 untracked）。均为领导资产，未清理/未暂存/未提交。
- 工具版本:
  - Python 3.11.8（`/usr/local/bin/python3`），pytest 运行正常
  - PostgreSQL 16.14（容器 `market-replay-postgres-1`，`pg_dump`/`pg_restore`/`psql` 均在容器内）
  - tar=/usr/bin/tar（bsdtar 3.5.3），shasum=/usr/bin/shasum 6.02，docker 29.6.1
- 数据库行数（实测）: users=1, watchlists=7, alerts=0, notification_channels=2, monitor_plans=1, alert_events=943
  - ⚠️ 与领导文档中的 942 差 1：生产库为活库，`alert_events` 在两次测量间新增 1 条，属正常漂移；快照以实测为准（943）。
- 基线测试（`cd backend && pytest -q tests/test_monitor_signals.py tests/test_monitoring_contract.py tests/test_new_architecture_golden.py tests/test_daily_report_analysis.py`）: **71 passed** ✅
- 全量后端测试（排除预先存在的收集错误 `tests/test_ma_strategy_scope.py`，其 `from scripts.backtests...` 导入失败与本次无关）: **179 passed**（回归基准）
- `cd frontend && npx tsc --noEmit`: 通过（领导基线已实测；本里程碑末尾复验）
- 前缀一致性探测（确定性 OHLC，`make_candles(n=500)`，逐前缀比较“在 i 时刻用 candles[:i+1] 算出的值” vs “全序列在 i 的值”）:
  - `trend_classifier.classify`: **大量不一致**（n=120 时 38/56，n=500 时 212/436）→ 未来 pivot 改写历史 regime 确认
  - `TrendStrategyDetector.signal_array`: 不一致（n=500 时 ≥1）
  - `MomentumStrategyDetector.signal_array`: 0 不一致（本序列；仍纳入回归保护）
  - `ReversalStrategyDetector.signal_array`: 0 不一致（本序列；本序列未触发回填路径，改用手工构造触发场景验证）
  - 注：领导给出的 33/55、4/450、6/450 为另一组探测口径/序列的数值；本里程碑以“逐前缀完整历史数组一致性 = 0”为验收标准，口径记录于 `test_asof_causality.py`。
- 代码事实确认:
  - `monitor_engine.py` + `signals/monitor_signals.py`: 生产告警只消费 **最后一根已完成 bar**（`index = len(candles)-1`），无回填，因果干净；monitor 的 detector 集合 = {ma, macd, structure}。
  - `trend_classifier.py`: `classify()` 一次性预计算全序列摆动点，`_swing_score` 取“最后两个摆动高/低点”未做确认过滤 → 未来 pivot 泄漏（死代码，app 内无引用，仅回测/测试层可能使用）。
  - `regime_classifier.py`: `precompute_regime_arrays` 的 caution 数组全 False（“回测时由策略类型决定，这里先不填”）→ 快速路径固化 caution=False。
  - `strategy_detectors.py`: Trend 用摆动点未检查 `confirmed_at <= i`；Reversal 在确认 bar `i` 检测到新低/新高 + 收回后向历史 `i_eff` 回填信号；Momentum 无已知泄漏。
  - 日报 `daily_report_analysis.py`: 只使用最新收盘 bar，无泄漏。

## 1. Task 1 — V1 可恢复快照 ✅

### 1.1 源码改动前的原始快照（先备份，后一切源码编辑）

- 命令: `scripts/ops/alert_backup.sh create`
- 结果: `reports/alert-backups/20260812T141847Z/`
  - `source.tgz`（422040 B）: backend/app、backend/tests、frontend/{app,components,lib,types}、backend/Dockerfile、frontend/Dockerfile、backend/requirements{,-dev}.txt、frontend/package{,-lock}.json、docker-compose.yml、.env.example；排除 .env、__pycache__、*.pyc、node_modules、.next、*.parquet 等。
  - `alerts.pgdump`（225840 B）: 六表 custom format。
  - `row_counts.tsv`: users=1 watchlists=7 alerts=0 notification_channels=2 monitor_plans=1 alert_events=943
  - `metadata.json`: backup_id、HEAD=965d150…、git 摘要、工具版本、表行数。
  - `MANIFEST.sha256`: 四个文件 SHA-256。
- 权限: 目录 700，全部文件 600。
- `reports/` 在 `.gitignore`（第 34 行 `reports/`）→ 备份不会进入 Git。

### 1.2 备份工具

- 新增 `scripts/ops/alert_backup.sh`: `create`（默认目录 reports/alert-backups/<UTC时间>）/ `verify`（文件存在 + 权限 + SHA-256 + pg_restore 清单含六表 + metadata 行数一致性）。
- `alert_backup.sh verify reports/alert-backups/20260812T141847Z`: **通过** ✅

### 1.3 恢复工具

- 新增 `scripts/ops/alert_restore.sh`: `drill <backup-dir>`（源码解到 mktemp、恢复六表到唯一命名临时库、逐表对比 row_counts.tsv、成功后删临时库、校验现用库行数不变）/ `restore <backup-dir>`（要求 backup-id 显式确认，先自动生成 rescue backup，再恢复源码 + 六表）。
- `alert_restore.sh drill reports/alert-backups/20260812T141847Z`: **成功** ✅ 六表行数一致，临时库已删除，现用库行数不变（alert_events=943 前后一致）。
- 篡改测试: 对快照副本篡改 `row_counts.tsv` / `source.tgz` 后 `verify` 均失败（SHA-256 不符）✅
- 敏感信息: 脚本日志不打印 webhook/token（不 cat pgdump/渠道配置）；含渠道信息的 `alerts.pgdump` 权限 600。

### 1.4 Runbook

- 新增 `docs/runbooks/ALERT_V1_BACKUP_RESTORE.md`: 完整操作手册（前置条件、create/verify/drill/restore 四步、生产回滚流程、故障排查、演练记录表）。

## 2. Task 2 — 消除未来数据污染 ✅

### 2.1 统一 as-of 契约（已写入 `test_asof_causality.py` 文档字符串）

- 时刻 t 的状态/信号只能依赖 `candles[:t+1]`。
- 中心摆动点（pivot 窗口中心）只有经过右侧 pivot 根（`idx + pivot <= t`）后才可使用。
- 信号只记在确认/决策 bar，禁止回写历史 bar。

### 2.2 修复清单

- `backend/app/core/trend_classifier.py`: `classify_single` 增加 pivot 参数，摆动点按 `idx + pivot <= i` 过滤（`_swing_score` 只吃已确认摆动点）；`classify` 传入 pivot=10。最后一根 bar 的结论不变（全序列摆动点均在其右侧 pivot 之前），中间 bar 不再被未来 pivot 改写。
- `backend/app/core/signals/strategy_detectors.py`:
  - `TrendStrategyDetector`: 趋势线子信号使用摆动点时过滤 `idx + pivot <= i`（pivot=3）。
  - `ReversalStrategyDetector`: 不再向历史 `i_eff` 回填；在确认 bar `i` 发出信号（背离/形态条件仍以收回 bar `i_eff` 为参考，全部 ≤ i，因果成立）。
  - `MomentumStrategyDetector`: 未改动，纳入回归保护。
- `backend/app/core/strategy_backtest_engine.py`: `_collect_signals_fast` 把 `regime_permits` 返回的真实 `caution` 传给 `build_signal_order`（不再固定 False）。

### 2.3 新增测试

- `backend/tests/test_asof_causality.py`（18 个用例，全部通过）:
  - `test_prefix_consistency_full_history_array`（12 个参数组合：4 组件 × 3 序列）:
    对确定性 OHLC 序列逐前缀比较完整历史数组，断言前缀不一致数 = 0；
    追加任何未来 K 线都不得改变已产生的结果。实测 4 组件全部 0 不一致
    （探测前 trend_classifier 38/56（n=120）、212/436（n=500））。
  - Reversal 定向测试（构造“新低 + 提前收回”场景）: 信号必须落在确认 bar 99，
    不得回填到收回 bar 98；确认 bar 之前的前缀不得出现任何信号。
    已验证旧实现回填到 98、新实现在 99（可复现区分）。
  - Trend 定向测试（构造“未确认摆动点”场景）: 摆动点 idx+pivot>i 时不得触发；
    旧实现在 105 触发（泄漏）、新实现仅在 107（已确认）触发；另用
    _touches_line 探针断言每次趋势线拟合所用摆动点全部已确认。
  - `test_fast_and_slow_paths_produce_same_regime_caution`: 快慢路径每个触发
    bar 的 (strategy, direction, regime_id, regime_caution) 完全一致；
    覆盖了 caution=0.5 的真实降仓订单（trend 9 笔 + reversal 33 笔）。
- `backend/tests/test_alert_v1_characterization.py`（12 个用例，全部通过）:
  锁定 monitor detector 集合 {ma, macd, structure}、事件字段结构、
  payload 可序列化、只报告最新已完成 bar、dedupe 唯一键语义（重复返回 None）、
  通知路由（绑定渠道 + 用户活跃渠道去重排序）、投递状态机（sent/failed+
  +5min 重试/in_app_only）——证明本次修复未改变生产告警契约。

### 2.4 测试结果

- 新增 `test_asof_causality.py`: 18 passed（trend classifier / Trend / Momentum /
  Reversal 前缀不一致 = 0）✅
- 新增 `test_alert_v1_characterization.py`: 12 passed ✅
- 原 71 项基线测试: **71 passed** ✅
- 全量后端测试（排除既有收集错误 test_ma_strategy_scope.py）: 209 passed
  （基线 179 + 新增 30，无回退）✅

## 3. Task 3 — 冻结后续路线 ✅

- 新增 `docs/architecture/ALERT_V2_ROADMAP.md`: M1（单一 MarketState）、M2（周/日/4H 融合）、M3（历史标注、影子运行 30 天 + 100 事件、对照报告后人工切换）验收门槛与延后事项；指标窗口/阈值/降级/版本号/解释/审计字段可配置可回放（M0 不实现）；明确“可能有多大级别行情”只能表达为历史条件分布 + 时间窗口，禁止确定性预测；所有判断只用已收盘已确认 K 线。
- 本里程碑未实现 V2 功能、未接入 Pinbar、未做参数寻优、未改 API/schema/告警文案/去重键/发送渠道、未做股票适配。

## 4. 最终校验

- `alert_backup.sh verify`: 通过；篡改后必失败（已实测）✅
- `alert_restore.sh drill`: 成功；临时资源清理；现用库行数不变 ✅
- 原 71 项基线测试: 通过 ✅
- 新增因果测试: 四组件前缀不一致 = 0 ✅
- 快慢回测路径 regime_caution 等价 ✅
- V1 characterization: 全绿 ✅
- `cd frontend && npx tsc --noEmit`: 通过 ✅
- `git status --short`: 除白名单文件与 reports 下忽略备份外无新增变化 ✅

## 4.1 验收整改（2026-08-13，验收方退回项修复）

验收结论：核心代码正确，但生产回滚不可用。已修复：

1. **生产 restore 兼容真实 schema（原失败根因）**：`pg_restore --clean` 在六表被
   备份外表引用时失败（实测 `replay_notes_user_id_fkey` 依赖 `users`，5 errors，
   exit 1）。修复：`alert_restore.sh` 新增可复用 `_restore_db`，恢复前动态找出
   「备份外引用六表的外键」→ 保存 DDL → 摘除 → `pg_restore --clean` 导入 →
   重建外键（数据不一致时重建失败并保留 DDL，rescue 可回滚）。
2. **源码恢复改为同步语义**：新增 `_sync_source`（rsync --delete 按白名单目录
   同步 + 文件覆盖），恢复时删除快照后新增的源文件（原实现只覆盖不删除）。
3. **verify 严格化**：`alert_backup.sh` 权限不符由警告改为失败（目录 700、
   全部文件 600）；metadata.json 的 backup_id 与目录名比对；metadata 与
   row_counts.tsv 逐表行数真实比对（不再只看条数）。
4. **新增生产同构恢复测试** `scripts/ops/test_restore_schema.sh`：构造
   「六表 + replay_notes(FK→users) + 引用行」的同构库，复用 `_restore_db`
   执行与生产 restore 相同流程，断言六表行数一致、replay_notes 数据保留、
   FK 重建且生效（非法引用被拒）、源码同步删除新增文件。实测全部 PASS；
   旧实现在同构库上复现失败（5 errors exit=1），证明测试覆盖真实缺陷。
5. **安全性验证**：restore 确认失败（输入不符）→ 立即中止且不生成 rescue；
   演练/同构测试均在唯一命名临时库进行，生产库未触碰；未执行生产 restore。
6. **回归**：101 项（71 基线 + 30 新增）+ 全量 209 passed；前端 tsc 通过；
   drill / verify / 篡改检测（内容、权限、backup_id、行数不一致）全部按预期
   失败或通过。

## 4.2 二次验收整改（2026-08-13，原子性修复）

复验反例：快照后新增用户及 replay_notes（引用新用户）时，回滚返回 1，并留下
users=1、孤儿笔记 1 条、外键 0 个——根因是「摘外键→恢复→重建外键」在事务外
执行，重建失败时无法自动复原。已修复：

1. **原子事务**：`_restore_db` 改为把「摘除备份外外键 → pg_restore 导入六表 →
   重建外键」全部放入**单个 SQL 事务**（pg_restore 输出 SQL 文本，与 psql
   组成 `BEGIN; 摘除; <恢复 SQL>; SET search_path; 重建; COMMIT;` 管道，
   `ON_ERROR_STOP=1`）。任一环节失败 → 整体回滚 → 现场自动复原，不留半成品。
2. **细节处理**：pg_restore 输出的 `set_config('search_path','',false)` 会清空
   search_path，重建语句（含 `REFERENCES users(id)`）在事务内显式恢复
   search_path 并以 schema 限定名执行；外键名/表名用 `format('%I.%I')` 防注入。
3. **失败回滚测试**：`test_restore_schema.sh` 新增失败场景——同构库上新增
   用户 2 并在 replay_notes 中引用（快照后现场），执行 `_restore_db` 必须失败，
   断言现场完全复原（users=2、replay_notes=2、FK 存在且生效）。实测通过；
   反例复现的「users=1/孤儿 1 条/FK 0 个」状态不再出现。
4. **行为调整**：`_restore_db` 失败改为返回非 0（不再直接 die），由调用方决定；
   `cmd_restore` 失败时报错并提示现场已复原、rescue 保留；测试可捕获失败。
5. **回归**：同构成功场景 + 失败回滚场景 + 源码同步全部 PASS；101 项 + 全量
   209 passed；tsc 通过；drill/verify/restore-abort 正常；无残留临时库。

已知说明：生产 restore 仍需人工在变更窗口执行并确认；失败时事务自动回滚，
现场恢复原状，可直接重试或改走其他处置（rescue backup 保留）。

## 5. 已知债务 / 下一步

- 债务: 三套状态口径（trend_classifier 5 态 / regime_classifier 6 态 / 日报 stage）仍分裂，M1 收口；`alert_events` 行数在本次会话从 943 增长到 946（活库漂移，monitor 正常写入）；`test_ma_strategy_scope.py` 的收集错误（`scripts` 不在 sys.path）为既有问题，未越界修改；M0 快照已锁定 943 时刻的现场。
- 下一步: M1 单一 MarketState（趋势方向/强度 + 波动率 contraction→transition→expansion + 质量分层），适配器逐步收口三套旧状态。
- 未阻塞项: 无（若遇连续三轮同一阻塞将写 BLOCKED.md）。

## 附录：基线 git status --short（2026-08-12T14:19:00Z，首次快照后）

共 184 行：103 个 tracked 变化、49 个删除、81 个 untracked（含本里程碑新增
scripts/ops/alert_backup.sh 与 PROGRESS.md）。

```
 M .env
 M .env.example
 M .gitignore
 M README.md
 M backend/Dockerfile
 M backend/app/api/alerts_api.py
 M backend/app/api/backtest_api.py
 D backend/app/api/edge_api.py
 D backend/app/api/fundamentals_api.py
 M backend/app/api/market.py
 D backend/app/api/opportunities_api.py
 D backend/app/api/recommender_api.py
 M backend/app/api/router.py
 D backend/app/api/strategy_library_api.py
 M backend/app/api/watchlist_api.py
 M backend/app/core/alert_engine.py
 M backend/app/core/backtest_engine.py
 M backend/app/core/candle_store.py
 M backend/app/core/config.py
 D backend/app/core/confluence.py
 M backend/app/core/discord_webhook.py
 D backend/app/core/edge_service.py
 D backend/app/core/event_bus.py
 D backend/app/core/fundamentals_service.py
 M backend/app/core/indicators.py
 D backend/app/core/opportunity_backtester.py
 D backend/app/core/profile_loader.py
 D backend/app/core/recommender.py
 M backend/app/core/signal_registry.py
 M backend/app/core/signals/__init__.py
 M backend/app/core/signals/backtest_indicators.py
 D backend/app/core/signals/indicator_signals.py
 D backend/app/core/signals/pattern_signals.py
 M backend/app/core/signals/primitives.py
 D backend/app/core/signals/structure_signals.py
 D backend/app/core/trade_plan.py
 M backend/app/main.py
 M backend/app/models/__init__.py
 M backend/app/models/models.py
 M backend/app/providers/binance.py
 M backend/app/providers/local_crypto.py
 M backend/app/providers/yahoo_finance.py
 M backend/app/schemas/alerts.py
 D backend/app/schemas/edge.py
 D backend/app/schemas/fundamentals.py
 M backend/app/schemas/market.py
 M backend/app/schemas/notes.py
 D backend/app/schemas/opportunities.py
 D backend/app/schemas/strategies.py
 M backend/app/tasks/alert_scheduler.py
 D backend/app/tasks/candle_worker.py
 D backend/app/tasks/edge_discovery.py
 D backend/app/tasks/opportunity_consumer.py
 D backend/app/tasks/outcome_tracking.py
 D backend/data/fundamentals/company_profiles.xlsx
 M backend/requirements.txt
 M backend/tests/test_backtest_golden.py
 M backend/tests/test_signal_detectors.py
 D docs/architecture/ARCHITECTURE.md
 D docs/notes/stock.md
 D docs/strategies/OPPORTUNITY_WORKFLOW.md
 D docs/strategies/STRATEGY_LIBRARY.md
 M frontend/app/(main)/alerts/page.tsx
 M frontend/app/(main)/chart/[symbol]/page.tsx
 D frontend/app/(main)/fundamentals/[symbol]/page.tsx
 D frontend/app/(main)/fundamentals/page.tsx
 D frontend/app/(main)/ma-strategies-c/page.tsx
 D frontend/app/(main)/ma-strategies-pro/page.tsx
 M frontend/app/(main)/ma-strategies/page.tsx
 D frontend/app/(main)/opportunities/page.tsx
 M frontend/app/(main)/page.tsx
 M frontend/app/(main)/replay/[symbol]/page.tsx
 D frontend/app/(main)/strategies/[strategyId]/page.tsx
 D frontend/app/(main)/strategies/page.tsx
 M frontend/app/(main)/watchlist/page.tsx
 M frontend/components/SymbolCombobox.tsx
 M frontend/components/SymbolSearch.tsx
 M frontend/components/chart/CandlestickChart.tsx
 D frontend/components/chart/TradePlanOverlay.tsx
 M frontend/components/chart/TradeReplayPlayer.tsx
 D frontend/components/fundamentals/BusinessOverviewCard.tsx
 D frontend/components/fundamentals/EarningsCalendarCard.tsx
 D frontend/components/fundamentals/FinancialSnapshotCard.tsx
 D frontend/components/fundamentals/RevenueModelCard.tsx
 M frontend/components/layout/Rail.tsx
 M frontend/components/layout/Statusbar.tsx
 M frontend/components/layout/Topbar.tsx
 M frontend/components/layout/WatchlistSide.tsx
 D frontend/components/opportunities/EdgeBadge.tsx
 D frontend/components/opportunities/OpportunityCard.tsx
 D frontend/components/strategies/ConfidenceBadge.tsx
 D frontend/components/strategies/StrategyCard.tsx
 D frontend/components/strategies/StrategyPerfTable.tsx
 M frontend/lib/api.ts
 D frontend/lib/api/fundamentals.ts
 D frontend/lib/api/opportunities.ts
 D frontend/lib/api/strategies.ts
 M frontend/next.config.mjs
 M frontend/types/index.ts
 M scripts/backtests/backtest_json_export.py
 M scripts/backtests/backtest_ma_strategies.py
 M scripts/backtests/backtest_ma_strategies_3x.py
 M scripts/backtests/backtest_ma_strategies_c.py
?? .codex/
?? backend/app/api/ma_strategies_unified_api.py
?? backend/app/api/monitoring_api.py
?? backend/app/core/candle_utils.py
?? backend/app/core/chanlun_chart.py
?? backend/app/core/chanlun_service.py
?? backend/app/core/daily_report_analysis.py
?? backend/app/core/daily_report_delivery.py
?? backend/app/core/daily_report_service.py
?? backend/app/core/daily_report_sources.py
?? backend/app/core/divergence_detectors.py
?? backend/app/core/feishu_webhook.py
?? backend/app/core/hyperliquid_stream.py
?? backend/app/core/market_recommendations.py
?? backend/app/core/matrix_backtest.py
?? backend/app/core/monitor_engine.py
?? backend/app/core/notification_channels.py
?? backend/app/core/position_sizing.py
?? backend/app/core/regime_classifier.py
?? backend/app/core/signal_detectors.py
?? backend/app/core/signal_strength.py
?? backend/app/core/signals/evidence.py
?? backend/app/core/signals/monitor_signals.py
?? backend/app/core/signals/strategy_detectors.py
?? backend/app/core/strategy_backtest_engine.py
?? backend/app/core/symbol_branding.py
?? backend/app/core/trend_classifier.py
?? backend/app/core/triangle.py
?? backend/app/providers/dukascopy.py
?? backend/app/providers/hyperliquid.py
?? backend/app/schemas/daily_report.py
?? backend/app/schemas/monitoring.py
?? backend/app/tasks/chanlun_cli.py
?? backend/app/tasks/daily_report_cli.py
?? backend/data/ma_strategies_2x_4h/
?? backend/data/ma_strategies_3x_4h/
?? backend/data/ma_strategies_4h/
?? backend/data/ma_strategies_c_2x/
?? backend/data/ma_strategies_c_3x/
?? backend/tests/test_binance_provider.py
?? backend/tests/test_chanlun_scan.py
?? backend/tests/test_chanlun_service.py
?? backend/tests/test_daily_report_analysis.py
?? backend/tests/test_discord_webhook.py
?? backend/tests/test_divergence_alerts.py
?? backend/tests/test_dukascopy_provider.py
?? backend/tests/test_hyperliquid_provider.py
?? backend/tests/test_ma_strategy_scope.py
?? backend/tests/test_market_recommendations.py
?? backend/tests/test_market_source_routing.py
?? backend/tests/test_monitor_signals.py
?? backend/tests/test_monitoring_contract.py
?? backend/tests/test_new_architecture_golden.py
?? backend/tests/test_signal_evidence.py
?? backend/tests/test_signal_strength.py
?? backend/tests/test_strategy_a_alert.py
?? backend/tests/test_triangle.py
?? backend/tests/test_yahoo_finance_provider.py
?? data/market/
?? email_alter.md
?? experiments/ma_a_atr_entry/
?? fenxing/
?? frontend/app/(main)/strategy-overview/
?? frontend/components/DailyRecommendations.tsx
?? frontend/components/alerts/
?? frontend/components/strategies/MaControlBar.tsx
?? frontend/components/strategies/MaModeBanner.tsx
?? frontend/components/strategies/MaStatsBar.tsx
?? frontend/components/strategies/MaTradeTable.tsx
?? frontend/hooks/useChartMeasure.tsx
?? frontend/hooks/useHyperliquidStream.ts
?? frontend/lib/chartMeasurement.ts
?? frontend/tests/
?? scripts/backtests/backtest_new_architecture.py
?? scripts/backtests/build_chart_viewer.py
?? scripts/backtests/build_summary.py
?? scripts/backtests/generate_tradingview.py
?? scripts/backtests/run_analysis.py
?? scripts/backtests/visualize_per_trade.py
?? scripts/backtests/visualize_trades.py
?? scripts/ops/
```

---

# M1 — 单周期 MarketState（2026-08-13 启动）

## M1 任务 0：基线取证与旧入口冻结

- 分支/HEAD: `refactor/strategy-spine` @ `965d1501db98e351956326d5eb066afb9ff539b1`
- git status: 189 行（与 M0 结束时一致，无新增变化）
- M1 前快照: `reports/alert-backups/m1-pre-20260813T062054Z`
  - create/verify/drill/test_restore_schema 全部通过（六表行数 964 为活库最新值）
- 基线输出: 后端全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` = 209 passed
  （另有既存中文字体 warning）；M0 定向集 101 passed；前端 `npx tsc --noEmit` 通过。
- 目标（≤10 行）: 统一 MarketState（趋势/波动/K线质量/审计）；三个旧入口
  regime_classifier / trend_classifier / daily_report_analysis 经适配器收口；
  回测与日报为正式迁移消费者；monitor 保持 ma/macd/structure 原路径；M1 不碰
  多周期、结果统计、Pinbar、API/DB/告警 payload。
- 顺序: ① 冻结旧入口 golden → ② market_state 核心引擎 → ③ 适配器 + 旧入口迁移 →
  ④ 全量验证 + 文档 + 红→绿证据。
- 最大风险: ① 日报 stage/tactical_phase/conclusion 判定逐字复现；② trend 五态
  稳定过滤（过去5根≥4根同一状态）在适配器复现；③ 回测经 regime 入口迁移后
  fast/slow 仍等价且 by_regime 不变；④ K线质量阈值边界与零振幅/缺ATR 降级。

### 旧入口 golden 冻结（修改前代码输出，预期常量）

四组确定性序列（n=500，生成器定义见 `backend/tests/test_market_state_legacy_parity.py`
与 `/tmp/gen_m1_golden.py` 逐字一致）：
- up: drift=1+0.002i，双正弦波；down: drift=1-0.0015i 镜像；sideways: drift=1 横盘；
- vol_cycle: 波动率包络 amp=0.02+0.06(0.5+0.5sin(i/70)) 收缩→扩张。

被冻结文件 SHA-256（golden 出自该版本旧代码，后续不得改预期迁就新实现）：
- trend_classifier.py: `0866de2c…aeb7`
- regime_classifier.py: `9aebef5a…08ea`
- daily_report_analysis.py: `2a800da6…ccf`
- strategy_backtest_engine.py: `1d40d09d…cb15`
- position_sizing.py: `014aed41…a850`
- indicators.py: `6f27f9bc…a31a`
- 生成器 /tmp/gen_m1_golden.py: `e0c4bf1c…acfa8`

| 序列 | trend_array_digest | regime_ids_digest | regime_alignments_digest | daily_model_dump_digest | daily_stage / phase | bt_summary_digest | bt_trades |
|---|---|---|---|---|---|---|---|
| up | b4f71f69c2d7c6ea | a8127c377a3d1935 | 5f7a2f8afbb7a5c1 | 5e5308c50bd1aded | 上涨趋势 / continuation | 4add623ba2770b8c | 62 |
| down | b620dcb064905292 | 1a7121a8e206b744 | d7b66634a7df3b4b | d3ff32e27fc5ed38 | 下跌趋势 / continuation | 37c16af308e17fdf | 50 |
| sideways | 6d40c3bcd9a95f02 | b5ae27bbc45c83c5 | 1ac0a1f7b62052a7 | d263c3c7bdc476af | 趋势不明确 / unclear | 5a5c9bb987a9bd90 | 113 |
| vol_cycle | 93a37090d636a580 | b4938fa503518d70 | fbdb37aae24fd58b | b8e32336c0fcffc5 | 趋势不明确 / unclear | 4585b19dc8e94aea | 40 |

## M1 任务 1：统一状态模型 ✅

- 新增 `backend/app/core/market_state.py`：
  - `MarketStateConfig`（frozen，全阈值可配置 + config_hash）与 `MarketState`（frozen）。
  - `precompute_market_states` / `market_state_at`（== 相同前缀批量预计算最后一项）。
  - `to_dict()` 稳定 JSON 可序列化，NaN/Infinity 一律降级 None。
  - 趋势（方向=摆动+MA 投票，强度=ADX 独立）、波动（ATR/price、百分位、5 根变化、
    level、phase contraction/transition/expansion + previous_phase/phase_age）、
    K 线质量（四项阈值 ≥3 strong / ≤1 weak / 其余 normal；十字/零振幅/缺 ATR 安全降级）、
    volume（复用 evidence.py 口径，缺失/为零/不适用→unknown）。
  - 摆动点因果确认（idx+pivot<=t）与 M0 契约一致；纯计算，不读库/不取网络。
- 测试 `tests/test_market_state.py`（21 个）：三组 500 根序列逐前缀每个历史位置
  不一致数=0、market_state_at 等价、expansion→contraction→transition→expansion
  波动路径、强阳/强阴/普通/十字零振幅/缺ATR/缺volume、阈值边界（>= 语义）、
  配置变更/config hash/重复回放一致、append future bar 不改变既有状态。全部通过。

## M1 任务 2：兼容适配与消费者迁移 ✅

- 新增 `backend/app/core/market_state_adapters.py`：
  - Regime 六态坐标（ADX 档 20/35 × 波动档 0.5 切分，仅适配层）、alignment（20/60/120）。
  - trend 五态（三维打分 score_to_regime + 稳定过滤，逐位复现旧实现）。
  - 日报 stage/tactical_phase/conclusion（旧 _market_regime 逻辑逐字复现，由
    MarketState.features 驱动，bar_index+1>=122 才判定）。
- `regime_classifier.py`：precompute_regime_arrays / classify_regime_at /
  alignment_direction 内部改用统一预计算；公开函数/类型/ID/标签不变。
- `trend_classifier.py`：classify() 改走 MarketState + 适配器，classify_single 保留。
- `daily_report_analysis.py`：analyze_instrument 的 stage/phase/conclusion 改从
  适配器获得，其余字段与文字不变。
- 回测经 regime 入口自动成为消费者（strategy_backtest_engine 未改动）。
- 验证：`/tmp/gen_m1_golden.py`（修改前旧代码生成）四组序列 digest 与迁移后
  输出**逐字一致**（见任务 0 冻结表）；monitor 未触碰。

## M1 任务 3：验证与文档 ✅

- 新增 `tests/test_market_state_adapters.py`（13 个）：六态坐标边界（adx 20/35、
  pct 0.5）、alignment 五档、五态分数域、日报四类 stage。全部通过。
- 新增 `tests/test_market_state_legacy_parity.py`（6 个）：四组冻结序列的 trend
  全数组 / regime ID+alignment / 日报 model_dump+stage / 回测摘要 digest 与
  冻结常量逐字段一致；四组序列回测 fast/slow 等价。全部通过。
- **红→绿证据**：临时把适配器 `_ADX_STRONG` 35→36 →
  `pytest -q tests/test_market_state_legacy_parity.py tests/test_market_state_adapters.py`
  输出 5 failed（parity 四组 + adapters 边界），保存于 /tmp/red_evidence.txt；
  还原后 19 passed。贴证据见下。
- 文档：`docs/architecture/MARKET_STATE_V1.md`（字段/公式/阈值/warmup/降级/适配/
  as-of/未证明交易优势说明）；`ALERT_V2_ROADMAP.md` M1 标记已落地。
- 全量回归：`pytest -q --ignore=tests/test_ma_strategy_scope.py` = **249 passed**
  （209 基线 + 40 新增，总数 >209，无 skip/xfail）；M0 定向集 101 passed；
  `cd frontend && npx tsc --noEmit` 通过；生产 monitor/API/schema/DB/前端改动为 0。

### 红→绿证据（节选 /tmp/red_evidence.txt）

```
FAILED tests/test_market_state_legacy_parity.py::test_legacy_entries_match_frozen_golden[up]
FAILED tests/test_market_state_legacy_parity.py::test_legacy_entries_match_frozen_golden[down]
FAILED tests/test_market_state_legacy_parity.py::test_legacy_entries_match_frozen_golden[sideways]
FAILED tests/test_market_state_legacy_parity.py::test_legacy_entries_match_frozen_golden[vol_cycle]
FAILED tests/test_market_state_adapters.py::test_regime_components_adx_tiers
5 failed, 14 passed   ← 临时改错（ADX_STRONG=36）
```
还原（ADX_STRONG=35）后：19 passed ✅

## M1 复验整改（2026-08-13，验收方退回项修复）

复验退回两项：① `classify_regime_at` 保留但忽略自定义 adx_period/atr_period/
lookback（实测 (5,5,20) 末态旧版 5→新版 4）；② `market_state.py` 消费
is_closed=False 的 K 线并输出 ready=True。已修复：

1. **参数透传**：`classify_regime_at` 与 `precompute_regime_arrays` 把
   adx_period/atr_period/lookback 构造为 `MarketStateConfig(adx_period=…,
   atr_period=…, vol_percentile_lookback=…)` 传给统一引擎；旧「数据不足→-1」
   分支保留在 regime_classifier。实测 down 序列 (5,5,20) 末态 5→4（与验收
   反例一致），透传后与旧公式逐位一致（三组周期 × 四组序列全部断言通过）。
2. **未收盘保护**：`precompute_market_states` 从第一个 `is_closed=False` 的
   bar 起不再消费价格数据，该 bar 及其后输出 `ready=False`（缺失原因含
   「未收盘」），此前已收盘 bar 逐位不变；`market_state_at` 对未收盘 index
   同样返回 not-ready。
3. **新增回归测试 5 个**：
   - `test_classify_regime_at_custom_periods_match_legacy_formula`（含
     「自定义必须改变结果」防空转）
   - `test_precompute_regime_arrays_custom_periods_match_legacy_formula`
   - `test_last_unclosed_bar_not_consumed` / `test_unclosed_middle_bar_marks_tail_not_ready`
   - `test_unclosed_bar_in_batch_prefix_consistency`
4. **回归**：M1 三文件 45 passed（40+5）、全量 254 passed（>209）、M0 101
   passed、tsc 通过；monitor/API/schema/DB/前端改动 0；git 仍 196 行
   （白名单 7 个新增文件，无越界）。

## M1 二次复验整改（2026-08-13）

复验退回两项：① `market_state.py` 仍读取未收盘 bar 的 OHLC（缺少 OHLC 时
KeyError）；② 适配器忽略 ready=False——末根未收盘时 regime 输出有效 0、trend
输出有效 1。已修复：

1. **只消费 candles[:cutoff]**：`precompute_market_states` 从第一个
   `is_closed=False` 的 bar（cutoff）起，仅读取 `candles[:cutoff]` 的 OHLC；
   cutoff 之后的 bar 可缺失 OHLC（只有 time/is_closed），无 KeyError，输出占位
   状态（`ready=False`、`is_closed=False`、缺失原因含「未收盘」）。
2. **未收盘占位在旧入口保持不可用**（适配器按 `ms.is_closed` 区分未收盘与
   warmup，不误伤 warmup 前旧行为）：
   - `regime_components_from_state` 对未收盘返回 None → `classify_regime_at`
     返回 Regime(-1)；`precompute_regime_arrays` 对应位置 -1；
   - `trend_regime_from_state` 对未收盘返回 None → `market_state_to_trend_array`
     保持 -99（含稳定过滤跳过未收盘位置，不被窗口主导值覆盖）；
   - `analyze_instrument` 对未收盘末根抛 ValueError（与 `len<2` 一致；调用方
     daily_report_service 在 try/except 内捕获并跳过该品种）。
3. **新增回归测试 3 个**：缺 OHLC 未收盘 bar 不消费（无 KeyError）、末根未收盘
   三消费者不可用（regime -1 / trend -99 / 日报 ValueError）、cutoff 起消费者
   全部不可用而此前正常。
4. **回归**：M1 三文件 48 passed、全量 257 passed（>209）、M0 101 passed、
   tsc 通过；monitor/API/schema/DB/前端改动 0；git 白名单无越界。

## M1 三次复验整改（2026-08-13，未收盘旁路封死）

- 目标: ① regime_is_stable 遇序列中首个显式 is_closed=False 必返回 False；
  ② analyze_instrument 在读取任何 OHLC 前预检首个未收盘 bar，发现即抛含
  symbol 的 ValueError；全收盘与 warmup 旧行为逐位不变。
- 顺序: ① 复跑 48 项基线并复现缺陷 A/B（regime_is_stable=True、KeyError close）
  → ② 追加 4 个反例测试取红灯 → ③ 修复两处实现取绿灯 → ④ 定向/回归/全量/
  tsc 验证并记录红→绿证据。
- 最大风险: ① analyze_instrument 预检若晚于 closes 列表读取仍漏 KeyError；
  ② regime_is_stable 只新增未收盘分支，不得影响全收盘路径（含窗口内全 -1 的
  既有判定）。

### 执行结果（本轮）

1. **缺陷复现（修复前）**：末根仅 time/is_closed=False 时
   `regime_is_stable(window=5, required_ratio=0.8)` = True（4 有效态压过 1 个 -1）；
   中间缺 OHLC 未收盘 + 后面完整 K 线时 `analyze_instrument` 抛 `KeyError: 'close'`。
2. **修复**：
   - `regime_classifier.regime_is_stable`：统计 dominant regime 前预检全序列，
     出现首个显式 `is_closed=False` 即返回 False（字段缺省仍视为已收盘；
     len<window 与全收盘路径不变）。
   - `daily_report_analysis.analyze_instrument`：末根未收盘原 ValueError 语义
     保留；新增 cutoff 预检——在读取任何 OHLC 前遍历全序列，首个显式未收盘
     bar 即抛含 symbol/未收盘/cutoff 含义的 ValueError（后续完整 K 线不可绕过）。
3. **新增测试 4 个**（test_market_state.py 仅追加，现有断言未删改）：
   - `test_regime_is_stable_false_on_unclosed_last_bar`（末根无 OHLC → False，无 KeyError）
   - `test_regime_is_stable_false_on_unclosed_middle_bar`（中间未收盘后完整 → False）
   - `test_analyze_instrument_valueerror_on_unclosed_middle_missing_ohlc`（ValueError 非 KeyError，含 symbol）
   - `test_analyze_instrument_unclosed_control_full_closed`（全收盘对照正常）
4. **红→绿证据**：
   - 新增用例未修复时：`3 failed, 8 passed`（2 个 regime_is_stable + 1 个 KeyError）
   - 修复后定向：`pytest -q tests/test_market_state.py -k "stable or unclosed"` → **11 passed, 0 failed, 0 skipped**
   - 反向验证：临时撤掉 regime_is_stable 预检 → `2 failed, 9 passed`；临时撤掉
     analyze_instrument 预检 → KeyError 复现 `1 failed, 10 passed`；均已恢复并
     diff 一致。
5. **回归**：M1 三文件 52 passed（48+4）；M0 六文件 101 passed；全量
   `pytest -q --ignore=tests/test_ma_strategy_scope.py` = **261 passed**（257+4，
   0 skipped）；前端 `npx tsc --noEmit` 通过。git 状态 196 行（与开工一致，
   白名单 5 文件均为既有 untracked，无越界）。

# M2 — 周/日/4H 多周期融合（2026-08-13）

## M2 任务 0：基线取证与演练 ✅

- 快照: `reports/alert-backups/m2-pre-20260813T091909Z/`
  - `alert_backup.sh create` ✅（source.tgz 451385 B / alerts.pgdump 231045 B）
  - `alert_backup.sh verify` ✅（SHA-256 / 权限 / pg_restore 清单 / 行数一致）
  - `alert_restore.sh drill` ✅（六表行数一致，临时库已删，现用库 alert_events=968 前后不变）
  - `scripts/ops/test_restore_schema.sh` ✅（同构恢复 + 失败回滚 + 源码同步全 PASS）
- HEAD: `965d150`（与 M1 一致）；`git status --short` = 196 行（103 tracked 变化 + 93 untracked，与 M1 收尾一致，无新增越界）。
- 基线测试（本次实测）:
  - M1 三文件 `test_market_state.py test_market_state_adapters.py test_market_state_legacy_parity.py` = **52 passed**
  - M0 六文件 = **101 passed**
  - 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` = **261 passed, 0 skipped**
  - 前端 `npx tsc --noEmit` = exit 0
- 目标/顺序/最大风险（≤10 行）:
  1. 时间对齐：纯函数截取每周期截至 as_of 的最后已收盘 bar（close_time<=as_of，is_closed=False 即 cutoff），重排不变。
  2. 角色融合：周 Context 定方向 → 日 Setup 同向 → 4H Trigger 定候选，逆势/未就绪/周期错/陈旧/方向非法均拒绝并留审计。
  3. 证明与文档：≥12 测试（含逐前缀/不同 as_of 历史不可变）+ 红→绿破坏实验 + 架构文档；只建 3 新文件 + 改 2 文档。
  4. 最大风险：cutoff 语义（未来 1 秒/未收盘 bar 泄漏）与到达顺序重排的确定性；任何偏离立即写 BLOCKED。

## M2 任务 1：时间对齐 ✅

- 新增 `backend/app/core/multi_timeframe.py`（纯计算，无网络/库/时钟读取）:
  - `TIMEFRAME_DURATIONS` = {1week:604800, 1day:86400, 4h:14400}（close=time+duration）；
  - `align_period`：按 time 稳定排序 → 扫描 cutoff（首个显式 is_closed=False 或
    close_time>as_of）→ 只消费 bars[:cutoff] 交给 M1 precompute（过滤先于 OHLC 读取）；
  - 边界精确：close_time==as_of 可用、早 1 秒不可用、晚 1 秒不影响当前对齐；
  - 受控不可用：缺周期/历史不足/重复时间（不抛异常）；非法 timeframe/as_of → ValueError；
  - 重放一致性：空输入与「全部被 cutoff 过滤」输出逐字段一致；审计 reasons 只含
    消费摘要（cutoff 触发条件是输入形态差异，不写入，保证前缀/全量输入结果一致）；
  - `AlignedPeriod`（frozen）：role/timeframe/available/unavailable_reason/bar_time/
    bar_close_time/config_hash/market_state/reasons；to_dict 稳定 JSON 无 NaN。
- 关键决策：重复 time 只在已收盘可见区域内判定冲突（未来不可见区域重复不影响结果，
  与「未来追加不改结果」一致）。

## M2 任务 2：角色融合与事件 schema ✅

- `evaluate(symbol, candles_by_timeframe, as_of, trigger_event, market_state_config, fusion_config)`
  → `FusionDecision`（frozen）：schema_version=m2-v1、symbol/as_of/direction/eligible、
  三角色快照、原 Trigger 摘要（含 normalized_direction）、稳定 reason_codes + 中文
  explanations、M1/M2 config hash、vol/candle/volume 证据（只写证据，不做硬门槛）。
- 门控（少假设，任务书拍板）：周线 up/down→long/short、sideways/unknown 拒绝；
  日线方向必须与周线及 Trigger 同向；4H 只要求 ready；Trigger 方向
  long/buy→long、short/sell→short；必须来自对齐后最新已收盘 4H bar。
- 拒绝路径（均 eligible=False 且返回完整审计）：ctx_not_ready/ctx_sideways/
  ctx_reject_direction（逆势）、setup_not_ready/setup_inconsistent、
  trigger_not_ready/trigger_wrong_period/trigger_stale/trigger_bad_direction。
- 新增 `backend/app/schemas/market_fusion.py`：`MarketFusionOut` 对端到端 dict 严格
  校验（extra="forbid"），`validate_decision()` 入口；纯数据契约，非 DB/API schema。

## M2 任务 3：证明与文档 ✅

- 新增 `backend/tests/test_multi_timeframe_fusion.py` **24 项全绿**（覆盖任务书全部
  场景：到达顺序不变、close 边界、未来/未收盘缺 OHLC 不消费、追加未来不改结果、
  三类状态不足、逆势拒绝、Setup 拒绝、buy/sell 归一、陈旧 Trigger、周期错误、
  方向非法、成功路径、证据不参与硬门控、schema/JSON/config 回放、
  逐前缀/不同 as_of 历史不可变（≥70 个 as_of，含成功路径）、非法 timeframe→ValueError、
  缺周期/重复时间受控不可用、周线 sideways 拒绝）。
- 红→绿实验（临时破坏实现，新测试变红，还原后全绿，输出已保存）:
  1. 破坏 close 边界（close_time >= as_of）→ **9 failed, 15 passed**（边界/未来/
     未收盘/追加未来/逐前缀等 9 项红）；
  2. 破坏方向门控（删除 ctx_reject_direction 逆势分支）→ **2 failed, 22 passed**
     （test_context_rejects_opposite_trigger / test_buy_sell_normalization 红）；
  还原后 diff 一致，**24 passed**。
- 新增 `docs/architecture/MULTI_TIMEFRAME_V1.md`：公式/cutoff/门控矩阵/reason codes/
  降级/示例/延后项/未证明交易优势（诚实声明）；路线图
  `ALERT_V2_ROADMAP.md` 在全部验收后标 M2 已落地。

## M2 最终验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| 新 M2 测试 `test_multi_timeframe_fusion.py` | **24 passed** |
| M1 三文件 | **52 passed** |
| M0 六文件 | **101 passed** |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **285 passed**（261+24，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m2-pre 快照 verify / drill / test_restore_schema | 通过（开工时已做，收尾 verify 再确认） |
| 范围 diff（m2-pre source.tgz） | 白名单外零源码差异（backend/app 仅 +multi_timeframe.py、schemas +market_fusion.py、tests +test_multi_timeframe_fusion.py；frontend 差异均为快照白名单外既有 untracked） |
| git status | 200 行（196 既有 + 4 个白名单新文件） |
| BLOCKED.md | 无阻塞 |

- 完成条件核对：同一 as_of 无论到达顺序/未来追加，融合结果完全一致
  （test_reorder_same_result + test_append_future_bars_no_change +
  test_prefix_history_immutable_across_asofs）；逆势和无 Setup 绝不 eligible
  （test_context_rejects_opposite_trigger + test_setup_rejects_inconsistent_direction）；
  成功路径可产生带完整审计证据的影子事件（test_success_path_full_audit）。

## M2 复验整改（2026-08-13，管理者独立验收退回 → 已修复）

### 退回 4 个反例（先复现写测试取红灯 → 修复取绿灯）

1. **Trigger 周期缺失仍通过**：删除顶层与 payload 的 timeframe 后 evaluate 仍
   eligible=True。修复：`validate_trigger_event` 周期校验——顶层与 payload 的
   timeframe 必须一致且等于 Trigger 周期（默认 4h）；缺失/冲突/非 4H 一律
   `trigger_wrong_period` 拒绝。
2. **不可信事件字段崩溃**：`signal_time='bad'` 在 `int(sig)` 抛 ValueError、
   `reference_price='bad'` 在 `float(ref)` 抛 ValueError。修复：`_parse_int_seconds`
   / `_parse_reference` 受控解析（缺失/非数值/NaN/Inf/负数 → None），
   `signal_time` 非法 → 新 code `trigger_bad_time`（拒绝）；`reference_price`
   缺失/非法 → 新 code `trigger_bad_reference`（降级 None + 拒绝）；
   payload 冲突（direction/occurred_at/confirmed_at/bar_closed）→ 新 code
   `trigger_payload_conflict`（拒绝）；to_dict 永不因事件字段崩溃、无 NaN。
3. **MultiTimeframeConfig 空转**：durations/roles/buy_sell_map 只进 hash 不驱动
   行为。修复（选方案 A）：全部字段真正驱动——durations 驱动 close_time 对齐、
   roles 驱动 align_all_periods 与 Trigger 周期、buy_sell_map 驱动方向归一、
   direction_map 驱动 Context/Setup 方向映射；`__post_init__` 完整验证（角色集合
   恰为 {context,setup,trigger}、duration 正整、映射值 long/short）；自定义配置
   有行为差异测试（test_custom_config_drives_behavior）防空转。
4. **Pydantic 语义约束缺失**：只做 extra=forbid，接受 eligible=True+direction=
   'banana'+role='wrong'+reason_codes=[]。修复：role/timeframe/direction/reason
   codes 全部 Literal 枚举；model_validator 交叉校验——三角色 role/timeframe 固定、
   eligible=True ⇔ direction∈{long,short} 且 reason_codes 恰为三个 ok codes 且
   三周期 available、eligible=False ⇔ direction=None、codes/explanations 等长；
   非法 dict 一律 ValidationError。

### 额外审查修复

- **bar time 缺失/非法**：`_bar_time` 明确 ValueError（缺 time/非数值/NaN/Inf/
  负数/非整数），不再以 KeyError/未说明异常穿透；cutoff bar 可缺 OHLC 但必须有
  合法 time（测试覆盖）。
- **顶层 vs payload timeframe 冲突**：拒绝（trigger_wrong_period，说明冲突双方）。
- **payload 校验**：payload.direction / occurred_at / confirmed_at / bar_closed
  与顶层或对齐契约冲突即 `trigger_payload_conflict` 拒绝（防只校验外壳）；
  payload 保留在审计摘要中（被校验，非只读外壳）。
- 原 24 项测试未删改；反例测试仅追加（现共 35 项）。

### 红→绿（本轮）

1. 破坏配置驱动（normalize_direction 改硬编码，配置空转）→
   `test_custom_config_drives_behavior` 红（1 failed, 34 passed）→ 还原全绿；
2. 破坏 schema 语义（跳过 eligible 校验）→ `test_schema_semantic_validation`
   红（1 failed, 34 passed）→ 还原全绿。

### 反例复现（修复前实测）

```
1) 无 timeframe → eligible=True（BUG）
2a) signal_time='bad' → ValueError: invalid literal for int()（崩溃）
2b) reference_price='bad' → ValueError: could not convert string to float（崩溃）
3) buy_sell_map 加 go_long → normalized=None（空转）
4) schema 接受 banana/空 codes/wrong role（BUG）
```

### 复验验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M2 定向 `test_multi_timeframe_fusion.py` | **35 passed**（24 原 + 11 反例，未删改原 24 项） |
| M1 三文件 | **52 passed** |
| M0 六文件 | **101 passed** |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **296 passed**（285+11，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m2-pre 快照 verify | 通过 |
| 范围 diff | 白名单外零源码差异；git status 200 行（196 既有 + 4 白名单新文件） |
| BLOCKED.md | 无阻塞 |

## M2 第二轮复验整改（2026-08-13，管理者退回 A–E → 已修复）

### 退回 5 个反例（先复现写测试取红灯 → 修复取绿灯）

1. **反例 A（roles 契约矛盾）**：`MultiTimeframeConfig(roles={'context':'1day',...})`
   能构造，但 schema 硬编码角色拒绝 → 运行契约与 schema 矛盾。
   修复（采纳缩小配置方案）：roles 是 M2 首版**固定业务法则**，从配置删除
   （传入即 ValueError「roles 是固定业务法则」）；运行统一使用 ROLE_TIMEFRAMES
   固定映射；schema FIXED_ROLE_TIMEFRAMES 与运行时同源一致。
2. **反例 B（frozen 内 dict 可变）**：`cfg.durations['4h']=1` 成功，hash/行为
   构造后漂移。修复：**深冻结**——构造时复制 dict 并转 `MappingProxyType` 只读
   映射；调用者原 dict 后改、cfg 字段赋值均不影响 hash/行为；config_hash 显式
   序列化真实字段；测试「构造前原 dict 后改」「cfg 字段后改失败」。
3. **反例 C（payload 污染 JSON）**：payload.evidence 含 NaN/set 时 to_dict
   无法 `json.dumps(allow_nan=False)`。修复：**M2 输出不携带原始 payload**，
   Trigger 摘要只含经校验的 event_type/direction/time/ref/timeframe/summary；
   to_dict 永远可 `json.dumps(out, allow_nan=False)`。
4. **反例 D（payload 弱校验）**：payload.direction='banana'、bar_closed=0 被忽略。
   修复：payload.direction 存在就必须可归一且等于顶层（否则
   trigger_payload_conflict）；bar_closed 存在必须严格为 bool True
   （0/'true'/None/False/1 均 conflict）；新增 payload.signal_type 必须等于
   event_type、payload.reference_price 必须有限正数且等于顶层。
5. **反例 E（schema 伪接受）**：伪造 eligible=True + 三周期 state 全 None +
   config_hash={'anything':'goes'} 仍通过。修复：schema 全模型
   `ConfigDict(extra='forbid', strict=True)`；ConfigHash 对象恰有
   market_state/fusion 两键且为 16 位小写 hex；available=True ⇒
   unavailable_reason=None、bar_time/close_time/state 非空、state.ready 且
   is_closed、state.bar_time==period.bar_time、config_hash 链一致
   （period==state==顶层 market_state）、close_time>bar_time；eligible=True 再
   要求 trigger_event.timeframe=='4h'、normalized_direction==direction、
   signal_time==trigger.bar_time、reference_price 非空；available=False 不强求
   state 为空（warmup 可携 state）；JSON roundtrip 后仍通过。

### 整改 6/7 及其他

- **整改 6**：evaluate/align_all_periods 在任何周期处理前先验证 as_of
  （bool/负数/非整数 → ValueError），缺全部周期也不能绕过；trigger_event 非
  dict → 受控 ValueError（写明契约）。
- **整改 7**：reference_price 必须为有限正数且与对齐 4H MarketState.close
  一致（相对容差 1e-6，极小防浮点噪声）；测试 helper 从对应 4H bar 读取真实
  close（强化契约，未弱化断言）；错价 → trigger_bad_reference。
- **配置进一步收紧**：schema_version 固定 m2-v1；durations 恰为三键（值可配）；
  direction_map 恰有 up/down；buy_sell_map 键规范化为非空小写（重复归一键拒绝）。
- 新增 reason code：`trigger_bad_event_type`（event_type 非空）。
- 原 35 项未删改；反例测试仅追加（现共 50 项；test_evidence_not_hard_gate 与
  test_reference_must_match_4h_close 的 reference 改为取对应序列真实 close，
  属整改 7 强化契约的必要适配，断言未放松）。

### 红→绿（本轮两次）

1. **JSON 污染**：临时把 payload 加回 Trigger 摘要 → `test_payload_not_in_output_and_no_nan`
   红（1 failed）→ 还原全绿；
2. **schema 缺 state**：临时移除 available=True ⇒ market_state 非空校验 →
   `test_schema_strict_forgery_rejected` 红（1 failed）→ 还原全绿。

### 反例复现（修复前实测）

```
A) custom roles construct -> accepted (schema 会拒绝, 矛盾)
B) mutate durations after construct -> hash changed: True
C) payload NaN/set -> to_dict allow_nan=False FAIL: Out of range float values
D) banana/bar_closed=0 -> eligible: True ('ctx_ok','setup_ok','trigger_ok')
E) forged eligible -> schema ACCEPTED (BUG)
```

### 第二轮复验验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M2 定向 `test_multi_timeframe_fusion.py` | **50 passed**（35 原 + 15 反例/整改，未删改原 35 项） |
| M1 三文件 | **52 passed** |
| M0 六文件 | **101 passed** |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **311 passed**（296+15，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m2-pre 快照 verify | 通过 |
| 范围 diff | 白名单外零源码差异；git status 200 行（196 既有 + 4 白名单新文件） |
| BLOCKED.md | 无阻塞 |

## M2 第三轮复验整改（2026-08-13，管理者退回：决策快照深不可变 + schema 语义等价）

### 退回 5 个 FAIL（先复现写测试取红灯 → 修复取绿灯）

1. **FAIL1（决策随调用者 ev 漂移）**：evaluate 返回后修改调用者 ev，d.to_dict()
   的 event_type/direction/summary/timeframe 随之变化。根因：FusionDecision
   保存原始 trigger_event 可变引用。修复：新增 **frozen TriggerSnapshot**——
   evaluate 时一次性复制规范化审计字段（event_type/raw_direction/
   normalized_direction/signal_time/reference_price/summary/timeframe），
   to_dict 只读快照；summary 受控字符串（None→None、str→原样、其他→str()，
   契约写入文档）。
2. **FAIL2/3（decision 内部可变）**：d._direction_map['up']='short'、
   d.aligned['trigger']=... 成功。修复：FusionDecision.__post_init__ 把
   aligned 与 _direction_map 复制并深冻结为 MappingProxyType（值本身为
   frozen dataclass）；调用者修改原事件/原 candles/原 config 或修改 decision
   内部均不得改变 to_dict/hash/direction。
3. **FAIL4（eligible 与 OK codes 不等价）**：schema 接受 eligible=False/
   direction=None 但 reason_codes 仍三个 OK。修复：**eligible iff 恰为三个
   OK codes**；eligible=False 必须至少一个 reject code（不能全 OK/空）；
   direction 等价不变。
4. **FAIL5a（evidence 嵌套 NaN）**：schema 接受 evidence 嵌套 NaN。修复：
   evidence 改用**严格 typed 角色模型**（context/setup/trigger 三键固定、
   extra forbid、数值字段 finite），不再用 dict[str, Any] 承载；
   全模型 allow_inf_nan=False；MarketState 状态字符串 Literal 化。
5. **FAIL5b（reference 可篡改）**：eligible=True 事件 reference_price 篡改为
   1.1 倍仍通过。修复：eligible=True ⇒ reference_price 必须与
   trigger.market_state.features.close 在 1e-6 相对容差内（二者有限正数）。

### 红→绿（本轮两次）

1. **深不可变**：临时还原「保存原始 trigger_event 引用 + aligned/_direction_map
   不冻结」→ `test_decision_snapshot_immune_to_caller_edits`、
   `test_decision_internal_fields_frozen` 红（2 failed）→ 还原全绿；
2. **nested NaN**：临时把 evidence 改回 dict[str, Any]（无 allow_inf_nan/
   typed 角色）→ `test_schema_nested_nan_inf_rejected` 红（1 failed）→ 还原全绿。

### 反例复现（修复前实测）

```
1) caller edits ev -> to_dict changed: True | event_type: hacked
2) _direction_map mutate -> allowed
3) aligned mutate -> allowed
4) eligible=False + all OK codes -> ACCEPTED (BUG)
5a) evidence nested NaN -> ACCEPTED (BUG)  5b) reference x1.1 -> ACCEPTED (BUG)
```

### 第三轮复验验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M2 定向 `test_multi_timeframe_fusion.py` | **57 passed**（50 原 + 7 第三轮反例，未删改原 50 项） |
| M1 三文件 | **52 passed** |
| M0 六文件 | **101 passed** |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **318 passed**（311+7，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m2-pre 快照 verify | 通过 |
| 范围 diff | 白名单外零源码差异；git status 200 行（196 既有 + 4 白名单新文件） |
| BLOCKED.md | 无阻塞 |

## M3A 开工回执（2026-08-13）

- 任务0：快照 m3-pre-20260813T121023Z 已建，verify / drill / test_restore_schema（生产同构恢复）全部通过；现用库六表行数 users=1 watchlists=7 alerts=0 notification_channels=2 monitor_plans=1 alert_events=974，演练前后不变（活库 974 vs M0 基线 943 属正常漂移）。
- 基线核对：M2 57 / M1 52 / M0 101 定向 **210 passed**；全量 **318 passed**（0 skipped）；tsc exit 0；git 约 200 行脏状态保留未动。
- 边界：本轮只做 M3A 离线结果标注与汇总（不接生产影子流）；只新增 4 个白名单文件 + 修改 3 文档。

## M3A 任务 1–3 执行记录（2026-08-13）

### 任务 1：纯计算标签引擎（backend/app/core/outcome_labeling.py）

- 公开入口：`label_decision`（单事件）/ `label_batch`（批量，重复 decision_digest
  → ValueError）/ `summarize`（确定性汇总，只聚合成熟样本）/ `decision_digest`。
- 只接受经 `validate_decision` 通过且 eligible 的 m2-v1 dict / FusionDecision；
  伪造/拒绝态/NaN → 受控 ValueError。
- 序列校验：time 唯一、消费前缀内 4H 步长连续、OHLC 有限且
  high>=max(open,close)>=min(open,close)>=low；cutoff 后 OHLC 可缺且不读取。
- 1R = 决策 trigger.market_state.features.atr（Wilder ATR(14)），有限正数；
  传入 MarketStateConfig hash 必须等于决策 config_hash.market_state；
  同时输出 atr_pct/mfe_pct/mae_pct（相对参考价百分比）。
- 窗口 5/10/20/40 不含触发 bar；缺根/未收盘/label_as_of 未到 → censored
  （原因区分，不拿短窗口冒充）。同 bar 双触 → ambiguous_same_bar（fake=null，
  不假设 OHLC 内路径）；fake_breakout 仅 negative_first=true。
- MFE/MAE 从 0 起非负；long/short 镜像；事件 bar 极值不计（事件 bar 极值被
  替换为 ±2R 仍不影响）。
- 输出保留 decision digest、三层条件快照、config hash、事件态→终点态与逐
  bar 波动 phase/趋势 strength 路径；结果深不可变（递归冻结 + to_dict 脱钩），
  json.dumps(allow_nan=False) 恒可序列化。

### 任务 2：严格 schema、汇总与文档

- `schemas/outcome_labeling.py`（m3a-v1）：全模型 extra forbid/strict/
  allow_inf_nan=False；交叉约束 matured⇔完整字段、censored⇔censor_reason+空
  字段、touch/fake 一一对应（ambiguous→null、negative_first→true）、
  窗口集合/顺序恰好 [5,10,20,40]、digest 16hex、config_hash 链一致；
  `validate_outcome_label` / `validate_outcome_summary` 公开。
- 汇总：每窗口样本/删失、MFE/MAE 均值（R 与 pct）、四类首触及计数、已知样本
  fake_rate；按 (window, 三层条件) 与 (window, 状态迁移) 稳定排序分组；
  空样本受控、混合 symbol → ValueError、批量重复 decision → ValueError。
- 文档 `docs/architecture/OUTCOME_LABELING_V1.md`（公式/边界/删失/同 bar 歧义/
  状态路径/as-of/示例/「历史分布不等于预测」）；路线图只标 M3A，M3B/C 未完成。

### 任务 3：测试与反向验证（backend/tests/test_outcome_labeling.py，54 项）

- 覆盖：long/short 数值手算（R 与 pct）、事件 bar 极值不计、0 下限、5/10/20/40、
  未成熟/三原因删失、缺口/重复/乱序、cutoff 缺 OHLC、同 bar 双触、gap 跨阈值、
  ATR 非法/hash 错、伪造与拒绝态 decision、事件 bar 缺失/reference 不匹配、
  未来追加不改成熟窗口与 digest、终点后数据不改早期窗口、状态路径、深不可变、
  严格 schema/NaN、批量重复、golden 摘要、混合 symbol、空样本、fake_rate。

### 反向红→绿证据（两次，均已完整还原）

1. 临时破坏「窗口包含事件 bar」（win_states 起点 event_idx+1 → event_idx）：
   `test_event_bar_extremes_excluded`、`test_long_manual_mfe_mae_window5`
   **2 failed** → 还原 → 全绿。
2. 临时破坏「双触强判先后」（ambiguous 分支强判 positive_first）：
   `test_same_bar_both_touch_ambiguous_fake_null`、
   `test_summarize_fake_rate_unknown_none` **2 failed** → 还原 → 全绿。
   破坏后 diff 与备份一致（`diff /tmp/outcome_labeling.py.bak ...` 无差异）。

### M3A 复验验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M3A 定向 `test_outcome_labeling.py` | **54 passed**（≥35 要求，0 skipped） |
| M2 57 / M1 52 / M0 101 定向 | 210 passed（无回归） |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **372 passed**（318+54，0 skipped，≥353） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m3-pre 快照 verify / drill / 同构恢复 | 全部通过（任务 0 与复验各一次） |
| 范围 diff | 白名单外零新增差异；git status 204 行（200 既有 + 4 白名单新文件） |
| 成熟标签逐字段不可变 | 追加任意窗口后未来数据（test_append_future_no_change 等全绿） |
| BLOCKED.md | 无阻塞（另写「无」） |

## M3A 第一轮整改（暗卷未通过 → 修复，2026-08-13）

### 暗卷实测（复现）与整改

独立暗卷发现问题（首轮不得交付）：duplicate_summary_counted=2；forged
risk.atr_pct / path 时序与 end / snapshot role-timeframe / 顶层 reference /
summary 计数与 rate 全被 schema ACCEPTED；事件前一根 high+1000/low-1000 后
label 仍 ACCEPTED；测试 QUICK.atr_period=5 与冻结的 ATR(14) 冲突。

先追加 **24 项暗卷回归测试**（tests/test_outcome_labeling.py，只追加不删改），
取红灯 **19 failed / 59 passed**（复现）→ 修复 → 78 全绿。

### 修复清单（均在白名单内，未动 M0/M1/M2 实现与既有测试）

1. **风险口径硬化**（core + schema）：M3A 只接受 config.atr_period==14；
   risk = {method:'wilder_atr', period:14, atr_abs, atr_pct}；atr_abs 来自决策
   trigger features.atr，atr_pct=atr_abs/reference；测试 QUICK 改 atr_period=14
   （事件 bar 600/n4=700，周线可见 14 根才 ready；不以测试便利改业务口径）。
2. **同源重放**：用决策 trigger.market_state.bar_index 定位事件时点历史起点
   （time=signal_time-bar_index*step），只在该 slice 重算事件时点 MarketState，
   与决策 trigger market_state 逐字段一致才标注；事件前 bar 篡改 → 受控拒绝
   （「同源重放不一致」）；窗口路径也从同一 replay slice 计算；事件 bar 极值
   改为 **evaluate 之前**构造（决策即反映，绝不事后篡改 decision 对应 bar）。
3. **可验证审计**：OutcomeLabel 保存深冻结完整 normalized m2 decision_snapshot；
   decision_digest 用严格 canonical JSON（无 default=str）；schema 用
   MarketFusionOut 重验证 decision_snapshot + canonical 重算 digest，顶层
   symbol/as_of/signal_time/direction/reference/config_hash、三层 snapshots、
   event_state、risk 全与 snapshot 一致；snapshot 三层 role/timeframe 固定
   （context/1week、setup/1day、trigger/4h）且 available/ready/is_closed。
4. **标签 schema 语义**：reference==trigger features.close；risk 等式；
   mfe_pct≈mfe_r*atr_pct、mae 同理；path 从 signal_time+step 严格连续、
   长度==window、末步与 end_time/end_close/end 状态一致；end_time==
   signal_time+window*step；引擎 OHLC 四价必须有限正数。
5. **汇总可信**：summarize 拒绝重复 decision_digest 与混合 config_hash；
   touch 四类之和==samples；fake_rate==negative_first/(samples-ambiguous)
   （分母 0 → null）；group/transition positive_first_rate==positive_first/
   samples；每窗口 group/transition samples 总和==窗口 samples；行唯一且
   稳定排序；schema 全部交叉校验（不只类型）。
6. **条件分组补齐维度**：group 至少含 direction、event_type、Context
   direction+strength、Setup direction+strength+vol_phase、Trigger
   vol_phase+candle_strength+volume_state；状态迁移保留。
7. **label_batch**：同一 candles 序列 → 拒绝混合 symbol 与重复 digest。

### 红→绿证据（第一轮整改）

- 暗卷 24 项先红灯：**19 failed**（duplicate_summary_counted、forged
  risk.atr_pct/path/end/snapshot role-timeframe/reference/summary 计数与
  rate、事件前 bar 篡改、atr_period=5 冲突等）→ 修复 → **78 passed**。
- 手动复跑全部暗卷场景（risk.atr_pct 伪造、path 时序断、end 与末步不一致、
  snapshot role/timeframe 错、顶层 reference 伪造、snapshot 篡改 digest 不
  匹配、mfe_pct 伪造、事件前 bar 篡改、duplicate summary、summary touch
  和/fake_rate 公式/group 样本和/rate/行重复）**全部 REJECTED** ✓
- 测试 QUICK.atr_period 5→14；事件 bar 500→600、n4 600→700（ATR(14) 周线
  ready 需 14 根可见）；事件 bar 极值构造移至 evaluate 前。

### 第一轮整改后验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M3A 定向 `test_outcome_labeling.py` | **78 passed**（54 原 + 24 暗卷，0 skipped） |
| M2 57 / M1 52 / M0 101 定向 | 210 passed（无回归） |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **396 passed**（318+78，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m3-pre 快照 verify | 通过 |
| 范围 diff | 白名单外零新增差异（git 204 行 = 200 既有 + 4 白名单新文件，未新增文件） |
| BLOCKED.md | 无阻塞 |

## M3A 第二轮整改（第二层暗卷未通过 → 修复，2026-08-13）

### 第二层暗卷复现（先追加 11 项回归测试取红灯）

1) summarize([合法全删失label]) 能生成 samples=0,censored=1，但
   validate_outcome_summary 自拒（SummaryWindowOut 错把 samples=0 推导成
   censored=0）；
2) dataclasses.replace 合法 OutcomeLabel 把 w5 mfe_r/mfe_pct 改 -99 后
   summarize ACCEPTED（对象分支未走 validate）；
3) 两个不同 label_as_of 被 summarize ACCEPTED（顶层悄悄取 max）；
4) 同一 decision/label_as_of，输入只到未来 4 根时 w5 censor=缺根；追加不可见
   未来 bar 后变 label_as_of 未到（同 as-of 输入形态改变输出）；
5) 成套伪造成熟窗口 mfe_r/mfe_pct 同 9 倍、或 first_touch/fake 同伪，
   validate_outcome_label ACCEPTED（path 不含 OHLC 无法复算）；
6) groups/transitions 倒序 summary schema ACCEPTED（未守稳定排序契约）。

红灯：**10 failed / 79 passed**（复现）→ 修复 → **89 passed**。

### 修复清单（均在白名单内，未动 M0/M1/M2 实现与既有测试）

- **A. PathStep 完整 OHLC**：core PathStep 增加 open/high/low/close（有限
  正数 + OHLC 关系合法）；schema 基于 direction/reference/risk 与 path 逐
  bar high/low **独立复算**每个成熟窗口的 MFE/MAE（R 与 pct）、首触及分类与
  fake_breakout，逐项必须匹配（成套伪造也拒绝）；文档记录体积取舍
  （path 3→7 数值字段，以体积换审计）。
- **B. summarize 不信任内存对象**：OutcomeLabel 也先 to_dict →
  validate_outcome_label（dataclasses.replace 篡改 → 拒绝）。
- **C. 全删失 schema**：SummaryWindowOut samples=0 允许 censored=n_decisions
  （均值 None、touch 全 0、fake None）；空报告（n=0）与全删失（n>0）靠
  n_decisions/symbol 区分清楚。
- **D. 混合 label_as_of 拒绝**：summarize 拒绝混合 label_as_of（报告只能同一
  观察截止，不取 max 冒充）；输出统一 label_as_of。
- **E. 删失结构化稳定码**：WindowOutcome 增加 censor_code
  （label_as_of_not_reached/unclosed/missing_bars，成熟 None）+ 人话
  censor_reason；判定**先看** expected end close_time（signal_time+w*step）
  是否超过 label_as_of——同一 as_of 下追加任何不可见未来 bar，完整 label
  逐字段不变；截止已到但 bar 不存在才 missing_bars；显式未收盘且窗口所需
  bar 落在 cutoff 则 unclosed；schema 校验 code/reason/matured 关系。
- **F. summary 可审计元信息**：输出统一 config_hash（空报告 None）、唯一
  decision_digests（稳定排序、数量 == n_decisions）、统一 label_as_of；
  schema 校验。
- **G. 严格排序**：schema 校验 groups/transitions 按公开 key 严格排序
  （倒序拒绝，不只唯一）。
- **H. 清理**：AST 检查 core/schema 无重复字典键。

### 红→绿证据（第二轮整改）

- 暗卷 11 项先红灯：**10 failed**（全删失自拒、replace 篡改、混合
  label_as_of、censor 形态漂移、成套伪造、倒序）→ 修复 → **89 passed**。
- 手动复跑 6 类暗卷场景：全删失 schema PASS、replace 篡改 REJECTED、混合
  label_as_of REJECTED、追加不可见未来 bar 逐字段一致（censor_code=
  label_as_of_not_reached）、mfe 9 倍伪造 REJECTED、first_touch/fake 同伪
  REJECTED、groups/transitions 倒序 REJECTED ✓

### 第二轮整改后验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M3A 定向 `test_outcome_labeling.py` | **89 passed**（78 原 + 11 第二轮暗卷，0 skipped） |
| M2 57 / M1 52 / M0 101 定向 | 210 passed（无回归） |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **407 passed**（318+89，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m3-pre 快照 verify | 通过 |
| 范围 diff | 白名单外零新增差异（git 204 行 = 200 既有 + 4 白名单新文件，未新增文件） |
| BLOCKED.md | 无阻塞 |

## M3A 第三轮收口（最终候选审查未通过 → 修复，2026-08-13）

### 最终候选审查反例（先追加 8 项回归测试取红灯）

1) 闭合公式错：expected_end_close 应为 signal_time+(w+1)*step（第 w 根未来
   bar 开盘=t+w*step、收盘=t+(w+1)*step）。旧实现 signal_time+w*step 早一根
   判成熟——w5 在 label_as_of=t+5*step 给 missing_bars，正确应
   label_as_of_not_reached；t+6*step 才可成熟。
2) 把另一合法标签的整个 w5 替换进当前标签 schema ACCEPTED（无跨窗口约束）；
   短窗改删失、长窗仍成熟也可 ACCEPTED（无连续前缀约束）。
3) 把 path 所有 vol_phase/trend_strength 与 end 一起伪造 schema ACCEPTED
   （只验证自洽，未验证 M1 计算）。
4) summary 的 group mean_mfe_r+100、各组 positive_first/rate 成套改写、
   decision_digests 同长度替换 schema 全 ACCEPTED（仅凭 summary 自身无法
   证明源数据）。

红灯：**7 failed / 90 passed**（复现）→ 修复 → **97 passed**。

### 修复清单（均在白名单内，未动 M0/M1/M2 实现与既有测试）

- **A. 闭合公式**：core `_window_outcome` expected_end_close =
  signal_time+(w+1)*step；新增边界反例（t+(w+1)*step-1 不成熟 /
  精确边界成熟）。
- **B. schema 跨窗口约束**：成熟窗口必须是 [5,10,20,40] 的连续前缀；较短
  成熟 path 逐字段等于较长 path 前缀；MFE/MAE 随窗口不减；若短窗已触及
  （非 neither），长窗 first_touch/fake 不得改变。交换合法 w5、成熟倒挂、
  路径不一致、MFE 倒挂均拒绝。
- **C. 标签自包含可重放**：新增 `market_state_config`（完整 MarketStateConfig
  字段快照，atr_period=14，schema 重算 hash == config_hash.market_state）与
  `replay_context`（原始起点到事件 bar 的 time/OHLC/volume；有限、OHLC 正数、
  volume 有限非负、严格连续、末根==signal_time/reference）；PathStep 带
  volume；schema 用 config 快照构造 MarketStateConfig，对 replay_context +
  每个成熟窗口 path 重建重跑 M1 precompute——事件末状态/完整 trigger
  MarketState 语义一致，逐 bar 验证 path 的 vol_phase/trend_strength
  （路径成套伪造拒绝）。不修改 M1，只消费其公开纯函数。
- **D. summary 诚实区分**：`validate_outcome_summary` 只承诺严格结构与内部
  算术；新增公开 `validate_outcome_summary_against_labels(summary, labels)`
  （schema 文件，局部导入避免循环）：对源 labels 全部先严格校验，再用
  summarize 重算并逐字段比较，不匹配抛 ValueError。文档明确 source-linked
  validator 是审计入口，不再暗示 summary schema 单独能证明来源。
- **E. 体积取舍**：文档记录 replay_context（约 bar_index+1 根 × 6 数值/根）
  与 path（8 数值/根）的离线存储代价；输出仍深不可变/JSON allow_nan=False。
- **F. 20 组固定 seed 随机 OHLC 属性测试**：每个成熟窗口 schema 通过；
  MFE/MAE 非负且随窗口不减；短/长 path 前缀一致；同 label_as_of 追加不可见
  bar 逐字段不变；long 公式由 schema 独立复算（validate 即证）。不依赖 mock。
- **G. AST 重复键扫描**：core/schema 均无重复字典键。

### 红→绿证据（第三轮收口）

- 暗卷 8 项先红灯：**7 failed**（闭合公式边界、跨窗口一致、M1 重放、source-
  linked）→ 修复 → **97 passed**。
- 手动复跑 4 类暗卷场景：t+5*step → label_as_of_not_reached（t+6*step 成熟）、
  交换 w5/成熟倒挂/路径不一致 REJECTED、伪造 path M1 状态 REJECTED、
  group mean+100/成套改写 rate/digests 同长度替换 REJECTED（source-linked）✓

### 第三轮收口后验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M3A 定向 `test_outcome_labeling.py` | **97 passed**（89 原 + 8 第三轮暗卷，0 skipped） |
| M2 57 / M1 52 / M0 101 定向 | 210 passed（无回归） |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **415 passed**（318+97，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m3-pre 快照 verify | 通过 |
| AST 重复键扫描 | core/schema 均无重复键 |
| 范围 diff | 白名单外零新增差异（git 204 行 = 200 既有 + 4 白名单新文件，未新增文件） |
| BLOCKED.md | 无阻塞 |

## M3A 最终审查整改（2026-08-13）

最终审查发现三项，均已修复（白名单内，未动 M0/M1/M2）：
1. **时序语义（CRITICAL）**：schema 新增按 step/signal_time 校验每窗口与
   label_as_of/censor_code 的时序——成熟窗口要求 label_as_of >=
   signal_time+(window+1)*step；未成熟窗口 label_as_of_not_reached ⟺
   label_as_of < 边界（边界已到只能 missing_bars/unclosed；schema 单凭标签
   无法区分后二者，文档说明边界）。追加回归反例（1 项先红灯 → 绿灯）：
   成熟标签 label_as_of 改到边界前拒绝（不动窗口字段）；未成熟窗口边界前
   missing_bars/unclosed 拒绝；边界已到 not_reached 拒绝；精确边界成熟合法。
2. **or True 假断言**：test_random_seed_property_matrix 的
   `assert w["mae_r"] >= prev_mfe - 1e-9 or True` 改为独立 prev_mae 正确检查
   MAE 随窗口不减；红→绿证据：故意把断言改为要求每窗 MAE 增长 1R →
   **1 failed** → 还原 → **1 passed**。
3. **core docstring 闭合公式**：_window_outcome docstring 的
   expected=signal_time+w*step 改为 +(w+1)*step，与实现和正式文档一致。
另：rg 确认测试源无 or True/xfail/skip/pass 绕过；AST 重复键扫描通过。

最终审查后验收：M3A **98 passed**（97 原 + 1 时序反例）；M2/M1/M0 210 无回归；
全量 **416 passed**（318+98，0 skipped）；tsc 0；m3-pre verify 通过；
白名单外零新增差异。

## M3A 主审独立反例整改（2026-08-13）

### 反例（backend cwd 复现）
build_label(n_window=0)，label_as_of=as_of_4h_close(640)：原标签四窗均
missing_bars；只追加 {time:T0+700*STEP4, is_closed:false} 后同 label_as_of
四窗变 unclosed，完整标签不等。另追加截止后 closed bar 会因
total_after=len(bars)-1-event_idx 改写 missing_bars reason。

### 根因两处（已修）
1. `_prepare_4h` 在 close_time>label_as_of 判定前先看 is_closed：截止之外的
   未收盘 bar 错误触发 cutoff_reason="unclosed"。
   → 修复：**as_of cutoff 判定优先于读取 is_closed**（先 t+step>label_as_of，
   再 is_closed=False）。
2. `_window_outcome` 的 missing 根数读取全 bars（含截止外）→ 追加远期 bar
   改写 reason。
   → 修复：**missing 数量只基于可见已收盘前缀**（visible_after =
   len(closed)-1-event_idx）；移除不再使用的 bars 参数。

原则落地：截止之外 bar 除 time 用于 cutoff 外，其 is_closed/OHLC/volume/
数量不得影响任何 label 字段。

### 新增回归测试（先红后绿，2 项）
- test_cutoff_outside_bars_do_not_affect_label（先红灯）：
  截止已到且缺根时，追加 1) OHLC 缺失远期 is_closed=False、2) 完整远期
  is_closed=True、3) 多根远期混合 → 输出逐字段完全相等且仍 missing_bars；
  输入列表未被修改。
- test_cutoff_inside_unclosed_still_unclosed（保留语义）：
  截止以内显式未收盘 → unclosed；窗口 20 仍成熟。

红→绿：test_cutoff_outside_bars_do_not_affect_label **1 failed**（复现）→
修复 → **100 passed**（98 原 + 2 新增）。手动复现主审场景：追加远期
is_closed=False/True/多根后逐字段相等、censor 全 missing_bars ✓。

### 主审整改后验收（2026-08-13）

| 验收项 | 结果 |
|---|---|
| M3A 定向 `test_outcome_labeling.py` | **100 passed**（98 原 + 2 主审反例，0 skipped） |
| M0+M1+M2 组合（10 文件） | 210 passed（无回归） |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **418 passed**（318+100，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m3-pre snapshot verify | 通过 |
| AST 重复键 | core/schema/tests 均无 |
| rg or True/xfail/skip | 0 匹配 |
| git 白名单/越界 | 204 行 = 200 既有 + 4 白名单新文件；未提交、零越界 |

---

# M3B — 安全影子运行时（M3B-A，2026-08-14 开工）

## 0. 任务 0 开工回执（2026-08-14T06:23Z）

快照 `reports/alert-backups/m3b-pre-20260814T062356Z`（alert_events=991，4H=94）：
create/verify/drill/test_restore_schema 全绿，六表演练前后不变（users1/watchlists7/
alerts0/channels2/plans1/events991）；基线复跑：全后端 418 passed（排除既有
test_ma_strategy_scope.py 收集错误）、M3A 100、M0+M1+M2 210、tsc exit 0、
HEAD 965d150、git status 204 行；活库数字与任务书一致，无异常停工。

## 1. 任务 1 — 持久化与纯运行时（M3B-A）

- 白名单新增：`app/core/m3_shadow_runtime.py`（核心运行时）、
  `app/schemas/m3_shadow_runtime.py`（严格校验/脱敏/status 契约）；
  修改 `models.py`（+M3ShadowRuntime/M3ShadowEvent）、`models/__init__.py`、
  `config.py`（+M3_SHADOW_* 6 项，默认 disabled）。
- 表结构：`m3_shadow_runtime` 单例心跳（id=1 CHECK，started_at 首次 enabled
  cycle 原子创建，INSERT ON CONFLICT DO NOTHING）；`m3_shadow_events` 事件表
  （source_dedupe_key 唯一、只读 source 快照、canonical M2 JSON+16 位 digest、
  eligible/reasons/config_hash、状态/attempt/backoff/错误、label_json/
  label_as_of/maturity_window/next_label_at）；**无任何 V1 外键**。
- 运行时：独立 worker 读 4H AlertEvent（不改 monitor_engine）；摄取条件
  created_at>=started_at + 4h + allowlist + 未处理；M2 as_of=signal_time+14400
  只用该时点可见周/日/4H；拒绝决策也持久化（rejected），eligible 才排 M3A；
  标签仅在 5/10/20/40 下一闭合边界（=signal_time+(w+1)*14400）到期时更新，
  40 成熟后 completed 停止；重复/崩溃重跑不重复计数、不改写已完成结果；
  同 symbol+signal_time 复用同周期取数（缓存 + load_calls 统计）。
- 错误脱敏截断（webhook/token/secret 打码 + 300 字符）；JSON allow_nan=False，
  读写均过既有严格 validator（validate_decision / validate_outcome_label）。
- ⚠️ 事实记录：backend 容器 uvicorn --reload 在编辑 models.py 后热重载，
  main.py 启动时 Base.metadata.create_all 在**活库自动建了两张空表**（SQLAlchemy
  风格索引 + ck 约束，与 schema 脚本 DDL 同构、幂等兼容）；未做正式 apply，
  保留待 canary 阶段核对；不违反「删表仅由新脚本确认词执行」。

## 2. 任务 2 — 调度隔离与运维（M3B-A）

- `alert_scheduler.py` 新增独立 `_shadow_job`（id=m3_shadow，interval 1min）：
  默认关闭时直接 return（零 DB/零网络）；异常只写 shadow heartbeat/log，
  不冒泡影响 _check_alerts_job / V1 提交 / 发送；fallback loop 同样只在不
  关闭时调用。shadow 模块无任何 sender 导入（AST 测试守护）。
- `tasks/m3_shadow_cli.py`：status（汇总/心跳/30 天与 100 唯一 eligible 进度，
  不吐 payload/秘密）/ run-once / dry-run-event <既有4h id>（不写库）。
- `scripts/ops/m3_shadow_schema.sh`：status/apply（幂等）/rollback（精确确认词
  `ROLLBACK M3 SHADOW`，只删两 M3 表）/test（临时库证明建表幂等、唯一键、
  无 V1 FK、回滚只删 M3 表、心跳单例 CHECK）——全绿。
- 修复两个脚本缺陷：--db 参数解析顺序（曾误操作活库，已改函数调用 +
  main 重写）；macOS bash 3.2 把全角括号当变量名（`$db（` → unbound）已改
  `${db}` 花括号；cleanup 局部变量陷阱改全局。

## 3. 任务 3 — 证明与文档（M3B-A）

- `tests/test_m3_shadow_runtime.py` **49 项全绿**（要求 ≥35）：flag/allowlist、
  heartbeat 原子单例、start 边界、4H 过滤、as_of 口径、决策持久化+digest、
  M2 拒绝持久化、标签 5/10/20/40 分期、40 完成停止、同 bar 歧义、幂等/重试/
  崩溃恢复、attempts 上限、同周期取数复用、未来追加不变、错误脱敏、CLI 汇总
  诚实进度、无发送、V1 失败隔离；不 mock M1/M2/M3A（evaluate/label_decision/
  validate_* 全真实调用），只注入确定性行情 loader/时钟/内存 repo。
- 反向红→绿三组（临时改坏实现 → 恢复，实测输出）：
  a) 影子异常若冒泡：run_cycle_safe 去掉隔离 → `test_cycle_safe_swallows_
     exception`/`test_cycle_safe_writes_heartbeat_error` **2 failed**（红）；
     恢复 → **2 passed**（绿）。
  b) 同源重复若产生两行：去掉 existing_keys+唯一键 → `test_rerun_no_duplicate_
     counting`/`test_existing_source_skipped_on_rerun` **2 failed**（红）；
     恢复 → **2 passed**（绿）。
  c) 未来 bar 若改写决策：as_of 改用 now → `test_decision_digest_immutable_
     append_future`/`test_as_of_is_signal_time_plus_14400` **2 failed**（红）；
     恢复 → **2 passed**（绿）。
- 文档：`docs/architecture/M3_SHADOW_RUNTIME_V1.md`（表结构/状态机/计数口径/
  开关/恢复/30 天门槛未完成）；`ALERT_V2_ROADMAP.md` M3A 97→100、3.2 M3B
  状态更新；`.env.example` 增加 M3_SHADOW_* 6 项（默认 false）。
- 实测结论（任务书猜测验证）：BTC 4H 700 根 ≈ 117 天，事件 as_of 处日线可见
  ~116 根 > M1 warmup（vol_percentile_lookback=100+1）；1week 700 根（13 年）
  充足——`M3_SHADOW_MAX_BARS=700` 对首批 BTC 事件足够，记录在案；若未来事件
  过老需调大该常量（不改 M1/M2/M3A 口径）。

## 4. 任务 4a — disabled 全验收（2026-08-14T06:5xZ）

| 验收项 | 结果 |
|---|---|
| 新测试 `test_m3_shadow_runtime.py` | **49 passed**（≥35 要求；0 skip/xfail） |
| M3A `test_outcome_labeling.py` | 100 passed |
| M0+M1+M2（10 文件） | 210 passed |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **467 passed**（418+49，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| m3b-pre 快照 verify / drill | 通过；六表行数不变 |
| `m3_shadow_schema.sh test` | 通过（exit 0） |
| AST 重复字典键 | CLEAN |
| grep skip/xfail/||true | 0 匹配 |
| 源码 vs m3b-pre 快照 | 差异仅白名单（config/models/__init__/alert_scheduler/.env.example/ALERT_V2_ROADMAP + 6 个新文件）；白名单外零差异 |
| 活库 m3 两表 | 已存在（backend --reload create_all 自动建，0 行，结构与脚本 DDL 同构） |

## 5. 任务 4b — 生产 canary（2026-08-14T07:12Z 完成）

启用前现场（07:07:43Z）：users1/watchlists7/alerts0/channels2/plans1/
alert_events991；m3 两表 0 行。流程与证据：

1. `m3_shadow_schema.sh apply`：幂等，两表已存在（backend --reload create_all
   自动建，同构），无 V1 外键（0）。
2. .env 仅追加 `M3_SHADOW_ENABLED=true`、`M3_SHADOW_SYMBOLS=BINANCE:BTCUSDT`
   （无其他 M3 键）；`docker compose up -d --force-recreate backend`。
3. 修复 canary 发现缺陷：`run_production_cycle` 缺 commit（CLI/scheduler 的
   async session 关闭时回滚）→ 心跳写不进。修复后 heartbeat 成功创建。
4. `status → run-once → status`：
   - heartbeat started_at=1786691348（2026-08-14T07:09:08Z，首次成功 cycle），
     force-recreate 后仍不变（单例不可变），started_at 不早于启用；
   - run-once：候选=0 摄取=0（历史 94 条 4H 未回填，created_at 早于 started_at）；
   - scheduler shadow job 每 1 分钟自动运行（last_run_at 07:10:11→07:12:49 自动更新）。
5. `dry-run-event 54516`（最新 BTC 4H ma_breakout short，signal_time=
   2026-08-12T12:00Z）：as_of=signal_time+14400 ✓；真实 M2 决策 eligible=False
   （setup_inconsistent，真实行情下日线方向与周线/Trigger 不同向）、
   digest=6bf7d82afeaaee7c、config_hash 输出正常；不写任何表。
6. 证明：V1 六表与启用前逐表一致（991 未增未改）；无任何发送日志/
   sender 导入；m3_shadow_events=0。
7. 30 天/100 唯一 eligible 进度如实报告（0.0 天 / 0 / milestone=False），
   不伪造门槛。

## 6. 最终状态

- 本机影子状态 enabled=true 且 heartbeat 成功开始；历史不回填；可对既有
  BTC 事件 dry-run。V1 行为零变化；m3b-pre 可恢复（verify/drill 通过）；
  两表可精确回滚（schema rollback 确认词 + 临时库测试）。
- 全量 467 passed（0 skipped）、M3A 100、M0/M1/M2 210、tsc 0、
  49 项新测试、三组红→绿实测、AST CLEAN、白名单外零源码差异。
- 未提交（禁止提交）；BLOCKED：无。

## 7. 最终回归与收尾（2026-08-14T07:2xZ）

- 中途发现磁盘满（OSError Errno 28）：`docker builder prune -f`（回收 2.954GB）
  + 清理 ~/.npm（1.5GB）→ 空间 510MiB→5.5GiB；非代码问题，已记录。
- canary 后最终回归：新测试 49 passed（0 skip/xfail）、全量 **467 passed**
  （0 skipped）、快照 verify 通过、schema test PASS、tsc exit 0。
- live 状态（最终）：enabled=true、heartbeat started_at=1786691348 不变、
  scheduler 每分钟自动更新 last_run、m3_shadow_events=0（历史未回填）、
  V1 六表行数不变（users1/watchlists7/alerts0/channels2/plans1/events991）、
  无任何发送、30 天/100 门槛进度如实为 0。
- 未提交（禁止提交）；白名单外零源码差异；BLOCKED 无。

## 8. 主审整改（2026-08-14T07:5x–08:0xZ）

主审反例 19 项先红后绿（保存红输出：16 failed + 3 碰巧通过 → 改实现后 68 全绿）：

| 整改项 | 反例（红） | 修复（绿） |
|---|---|---|
| 1 分页饥饿 | test_pagination_no_starvation_second_page / test_fetch_candidates_sql_anti_join_structure（旧实现先 order/limit 再 Python 跳过 → 第二轮吃不到下一页 / SQL 无 NOT EXISTS） | fetch_candidates 用 NOT EXISTS 在 LIMIT 前排除已处理（fetch_candidates_stmt 可测 SQL 结构）；Memory repo 同口径 |
| 2 decision 失败隔离+持久化重试 | 6 项（决策失败无行、坏源阻塞整轮、无 backoff、上限不停、label 失败丢 decision） | failure_stage∈{decision,label}；insert_failed 唯一行落库（source 快照+脱敏错误+attempts+backoff，decision 字段可空）；list_decision_retry_due 到期重试；promote_decision 原行升级无重复；max_attempts 停止；label 失败不丢 decision；坏源不阻塞好源 |
| 3 loader before 透传 | test_loader_passes_before_to_provider_and_filters_future（旧实现拉最新再后过滤） | production_market_loader 传 before=end_ts+duration、outputsize=max_bars+16；保留二次过滤+截断 |
| 4 scheduler 兜底 commit | test_shadow_job_outer_heartbeat_commit_is_isolated（兜底写 heartbeat 后无 commit） | _shadow_job 兜底补 session.commit()；commit 失败也不冒泡 |
| 5 标签成熟真实性 | test_label_missing_w5_no_fake_maturity（matured=[] 伪设 5 跳 10） | compute_label_record：maturity_window=实际成熟最大值（无则 None 且保持 pending）；只有 40 真实成熟才 completed；缺根/未收盘保持重试该窗口 |
| 6 core 语义校验 + 不硬编码 max_attempts | test_core_rejects_bad_timeframe / nonpositive_params / test_sql_repo_uses_core_max_attempts | ShadowCore.__post_init__ 强校验（timeframe=4h、正参数）；SqlShadowRepo(session, core) 用 core.max_attempts |
| 7 DB CHECK + 读回验证 | test_readback_validates_decision_digest_consistency / label_content（脏行不拒） | 8 个 CHECK（status/timeframe/attempts/digest/maturity/failure_stage/decision_complete/label_bound，COALESCE 防 NULL 放行）；validate_row_content 读回重验 decision/label+digest；ORM/DDL/活库一致 |
| 8 既有隔离/禁 sender/disabled 零 DB | test_disabled_production_cycle_zero_db（空 allowlist 零 DB） | 保持成立 |

- 修复 PostgreSQL CHECK 对 NULL 放行的坑：`NULL OR ...` 通过 → 改用
  COALESCE 显式（非法行必拒绝测试在临时库 + 活库实测通过）。
- 全量：M3B 定向 **68 passed**（0 skip/xfail）；全后端 **486 passed**
  （467+19，0 skipped）；M3A 100；M0+M1+M2 210；tsc exit 0；
  schema 临时库测试全绿（含非法行必拒绝、迁移幂等、无 V1 FK、rollback 范围）。
- 活库迁移（事务化，保留 heartbeat.started_at=1786691348）：m3_shadow_events
  加 failure_stage 列、decision 列改可空、重建 8 个 CHECK（单事务 COMMIT）；
  迁移前后 events=0、V1 六表计数不变；非法行活库实测拒绝、合法 decision
  失败态可插（已删除）；重启 backend 后 scheduler 心跳持续（last_run_ok=t）、
  历史 0 回填、无发送副作用；dry-run-event 54516 正常（before 透传后 1week
  可见 362 根 > warmup 101 根，决策 eligible=False 与旧 loader 语义一致；
  digest 随取数窗口 bar_index 变化属可预期快照语义，生产 loader 固定故稳定）。

## 9. 第二轮主审整改（2026-08-14T11:1xZ，管理者 8 项主审 → 已修复）

### 反例先行（红结果已保存）

- 修复前（A–H 反例已在测试文件中，共 10 项红灯）：`/tmp/m3b_red_before.txt`
  = **10 failed / 72 passed**（label_bound maturity 无 label、failed/label
  next_label_at NULL 不重试、loader before 传错、from_settings 吞 0/空、
  缺数据无 backoff、后排饿死、cross-validate 缺失、reasons/状态一致性不验）。
- 另补反例（第 16 节：ShadowEventRow/validate 层 label_bound、Sql
  list_label_due SQL 结构、scheduler 完全隔离、loader 未来追加、状态一致性
  关键非法行、decision canonical 契约、failed/decision 全空与 label 空）。
- 临时整体还原修复后复跑：`/tmp/m3b_red_after.txt` = **18 failed / 75 passed**
  （新补反例全部红灯，证明测试真实覆盖缺陷）；还原修复后 **93 passed**。

### 8 项修复清单（白名单内，未动 M0/M1/M2/M3A 实现与既有测试语义）

1. **label_bound（DB CHECK / ShadowEventRow / validate_row_content 三层）**：
   允许 pending censored（label_json+label_as_of 非空、maturity NULL）；
   仍拒绝 maturity 非空却无 label_json/label_as_of、label 非空却无 as_of；
   真实 SQL（schema 临时库 + 活库实测）与 SqlRepo/Schema/validate 层测试覆盖。
2. **首次 W5 标签异常行重试**：failed/label + next_label_at NULL 不再永不选中；
   `list_label_due_stmt` 对 failed/label 在 backoff 到期后按 W5 边界（或既有
   next_label_at）选中；SQL 结构断言 + Memory 行为测试双覆盖。
3. **scheduler 兜底 commit 完全隔离测试重写**：patch runtime.run_production_cycle
   强制抛 + patch `sched.AsyncSessionLocal` 与 `app.db.session.AsyncSessionLocal`
   （兜底 import 的同一模块）为可观测 fake——主路径与兜底均不产生真实
   session/连接；断言兜底 touch 后 commit 恰好 1 次；commit 失败不冒泡；
   settings 用 try/finally 自动还原。
4. **loader before 精确传 end_ts**（不得 end_ts+duration）：before 是开盘时点
   上界；保留 outputsize=max_bars+16 padding 与 close_time<=end_ts 二次过滤与
   截断；spy 断言相等 + provider 返回未来追加 bar 被过滤（结果不变）。
5. **schema apply 显式单事务**：建表+迁移包在 `BEGIN; ... COMMIT;` +
   ON_ERROR_STOP；临时库加故意失败反例证明原子回滚（无半成品表）；活库修正
   同样事务化并保留 heartbeat.started_at=1786691348；另补
   `ALTER ... SET DEFAULT` 使活库 attempts/status/created_at/updated_at 与
   ORM/DDL 对齐（后端 create_all 建的旧表原本只有 Python 侧默认）。
6. **状态一致性三层统一**（DB CHECK / ShadowEventRow / validate_row_content）：
   failed/decision 决策五字段全 NULL 且 label 空；rejected eligible=false 且
   无 label；pending/labeled/completed/failed-label eligible=true；labeled
   真实 maturity<40、completed maturity=40；非 failed attempts=0、error 空
   （rejected/completed 不得有 next_retry_at）；新增
   `ck_m3_shadow_events_state_consistency` / `ck_m3_shadow_events_retry_state`
   两个 CHECK；validate_row_content 交叉验证 label.decision_digest==行 digest、
   label_as_of 一致、实际 matured 最大值==行 maturity_window、reasons 与
   decision codes/explanations 一致、decision/label JSON 必须 canonical 字符串
   （交换他行 label / 伪造字符串 → 拒绝）；关键非法行逐一测试（validate +
   ShadowEventRow 同层 + schema 临时库 + 活库实测）。
7. **from_settings 不吞 0/空**：`M3_SHADOW_MAX_BARS=0`、`M3_SHADOW_TIMEFRAME=""`
   原样进入 __post_init__ 抛 ValueError（不用 `value or default` 静默替换）；
   新增 M3_SHADOW_RETRY_DELAY_SECONDS / MAX_ATTEMPTS / INGEST_LIMIT /
   LABEL_LIMIT / LABEL_RETRY_DELAY_SECONDS 配置项（config.py + .env.example）。
8. **缺数据 backoff + 不饿死后排**：正常缺根/部分成熟不算 failed；update_label
   新增 next_retry_at；0/部分成熟未到目标窗时设 now+label_retry_delay_seconds
   （默认 900s），list_label_due 让 pending/labeled 尊重 backoff（不热循环）；
   成功推进清空；排序改「新鲜优先 → backoff 最早到期优先 → signal_time」——
   同批缺数据重试不会永远压住新鲜后排（Memory 与 Sql 同口径）。

### 红→绿证据

```
修复前: 10 failed, 72 passed  （/tmp/m3b_red_before.txt）
还原修复: 18 failed, 75 passed（/tmp/m3b_red_after.txt，含第 16 节新补反例）
修复后: 93 passed（M3B 定向）
```

### 第二轮整改后验收（2026-08-14）

| 验收项 | 结果 |
|---|---|
| M3B 定向 `test_m3_shadow_runtime.py` | **93 passed**（68 原 + 25 反例/补充，0 skip/xfail） |
| M3A `test_outcome_labeling.py` | 100 passed（无回归） |
| M0+M1+M2（10 文件） | 210 passed（无回归） |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **511 passed**（0 skipped，>467） |
| 前端 `npx tsc --noEmit` | exit 0 |
| schema 临时库测试 | 全绿（含原子回滚、label_bound/状态一致性 CHECK 非法行必拒绝、合法 pending censored/decision 失败态可插） |
| ORM/DDL CHECK 一致性 | AST 比对全匹配（10 个 ck_* 约束） |
| 活库迁移（事务化） | 单事务完成；events=0 保留；heartbeat.started_at=1786691348 不变；V1 六表逐表计数不变（users1/watchlists7/alerts0/channels2/plans1/events991）；非法行活库实测拒绝（6/6），合法 pending censored/decision 失败态可插（2/2，已清理）；新增 state_consistency/retry_state CHECK、label_bound/decision_complete 已更新、DB 级默认值已补 |
| 重启 backend 后 | scheduler 心跳持续自动更新（last_run_ok=true、last_run_at 每 60s 前进）；events=0（历史 0 回填）；无任何发送副作用；dry-run-event 54516 正常（before=end_ts 下 eligible=False 与 canary 语义一致）；CLI status 正常 |
| 30 天/100 门槛 | 未完成（如实报告：0.2 天 / 0 唯一 eligible / milestone=False） |
| git | 未提交（禁止提交）；白名单外零越界修改 |

### 已知调整（既有测试随新行为语义更新，断言未放松）

- `test_loader_passes_before_to_provider_and_filters_future`：before 断言从
  `end_ts+duration`（旧缺陷行为）改为 `end_ts`（整改 4 要求）。
- `test_label_data_arrives_later_progresses`：缺根后数据到齐的推进需在
  backoff 到期后进行（整改 8 语义），并断言成功清空 backoff。
- `test_label_starvation_does_not_happen_over_limit`：原用例 30 个事件含
  15 个 short（M2 融合下 rejected，只有 15 eligible，断言基数错误）→ 改为
  30 个全 eligible long（strategy/event_type 区分 digest），并断言公平轮转
  （稀疏轮次下后排不被前排重试永久压住）。

## 10. M3B-A 最后四项严格性整改（2026-08-14T19:2xZ）

管理者验收 M3B-A 最后四项，全部完成（测试先行、红→绿语义锁死；无越界）。

### 整改 1：分页公平性重写（test_label_starvation_does_not_happen_over_limit）

- 旧用例直接 W5+60 一次性摄入并断言 backoff 计数（摄入轮即完成标签，
  不能证明「>limit 多轮分页」）；重写为严格分页序列：
  1. W5-60 摄入 30 条（全部 pending、label_json/label_as_of 均空）——
     断言初次摄入不得直接完成标签（ingested=30、labeled=0）；
  2. W5+60 起用 thin loader（5 窗口缺根）+ label_limit=10 连跑三轮
     （backoff 900s 未到期，每轮只可能处理 10 个新 key）；
  3. 断言三轮**实际处理**的 event key 各 10 条、三轮互不相交、并集覆盖
     全部 30 条；已处理者均为 pending censored（未虚报成熟）且进入 backoff。
- 语义：已处理者下一轮被 backoff 排除 → 后排逐轮被处理，无人饿死、无人重复。

### 整改 2：SQL due 分支精确锁定（强化后部 test_failed_label_null_next_label_sql_condition）

- 旧版（两处同名，后定义覆盖前定义）只断言 `repo._max_attempts()==6`；
  前部同名已改名 `test_failed_label_null_next_label_sql_max_attempts_from_core`
  （命名唯一），真正的 due 分支断言收敛到第 16 节后部同名测试。
- 强化版 `_assert_due_sql_branch(sql)` 精确锁定 6 个关键条件：
  status=failed（列表含 pending/labeled/failed）、failure_stage='label'、
  next_label_at IS NULL（与 IS NOT NULL 并存）、W5 已成熟
  （now >= signal_time + 86400）、next_retry_at 已到期（IS NULL OR <= now）、
  attempts < max（来自 core，不硬编码）+ ORDER BY 公平轮转。
- 自证：对完整 SQL 逐个移除关键条件（手术式 replace/re.sub 构造 6 个残缺
  变体），`_assert_due_sql_branch` 必须全部失败——证明测试能抓缺陷，
  不再只测 max_attempts。

### 整改 3：failed 三层契约收紧（DB CHECK / ShadowEventRow / validate_row_content）

- 新契约：任何 status=failed 必须 attempts>=1、last_error 非空白
  （btrim 语义）、next_retry_at 非空；数据库约束
  `(status = 'failed' AND attempts >= 1 AND next_retry_at IS NOT NULL
    AND last_error IS NOT NULL AND btrim(last_error) <> '')`（防 NULL 放行）。
- 同步四处：ORM `ck_m3_shadow_events_retry_state`（models.py）、Pydantic
  `ShadowEventRow._state_consistency`、`validate_row_content._validate_state_machine`、
  schema DDL/迁移（scripts/ops/m3_shadow_schema.sh _ddl + _migrate_ddl）——
  ORM/DDL AST+平衡括号比对 10 个 ck_* 全匹配。
- mark_failed 错误文本：run_cycle 4 处调用改为
  `sanitize_error(exc) or "label failed"/"decision failed"`（空/全脱敏异常
  → 安全兜底，不违反契约）；Memory/Sql 两个 repo 的 mark_failed 再加
  空白防御兜底。
- 新测试：validate 层 + ShadowEventRow 层各 1 个契约测试；空异常文本
  （RuntimeError("   ") → sanitize_error=None）下 mark_failed 落兜底文本且
  读回三层契约通过的测试。
- schema 临时库测试：b21-b24 非法 failed（attempts=0 / next_retry_at 空 /
  last_error 空 / last_error 全空白）必拒绝，ok5 合法 failed（attempts=2 +
  '  boom  ' + next_retry_at）可插——全绿。

### 整改 4：reasons canonical 契约

- validate_row_content 的 reasons 校验由「把输入重新 canonicalize 后比较」
  改为**直接字符串比较** `rec.reasons == expected_reasons`（逐字节一致；
  同内容但 key 顺序/缩进/分隔符不同的非规范序列化不再被放行）。
- 新测试 `test_readback_reasons_canonical_contract`：同内容但 key 顺序颠倒 /
  pretty 缩进 / 紧凑分隔符三种非规范序列化均必须拒绝（语义等价但字符串
  不同）。

### 验收结果（全部真实执行）

| 验收项 | 结果 |
|---|---|
| M3B 定向 `test_m3_shadow_runtime.py` | **100 passed**（93→100，+7：3 个新测试 + 2 个改名后不再被同名覆盖 + 重写 1 + reasons canonical 1；0 skip/xfail） |
| `bash scripts/ops/m3_shadow_schema.sh test` | 全绿（含新增 b21-b24/ok5 failed 契约用例、原子回滚、幂等、无 V1 FK） |
| 全量 `pytest -q --ignore=tests/test_ma_strategy_scope.py` | **518 passed**（511→518，0 skipped） |
| 前端 `npx tsc --noEmit` | exit 0 |
| ORM/DDL CHECK 一致性 | 10 个 ck_* 全匹配（含新 retry_state） |
| 活库（事务化 apply，仅 M3 表 CHECK） | retry_state CHECK 已更新；heartbeat started_at=1786691348 保留、last_run_ok=t；events=0；V1 六表未写（users1/watchlists7/alerts0/channels2/plans1；alert_events 1002 为 V1 系统自身 4 天内自然增长，非本整改写入）；非法 failed 行活库实测 3/3 拒绝、合法 failed 可插（已清理） |
| 既有脏改动 | 全部保留（含 4 个未跟踪 M3B 新文件）；无 git commit/reset/checkout、无 rm/prune/重装/回填/发送 |

### 已知调整（既有测试随新行为语义更新，断言未放松）

- `test_shadow_event_row_state_consistency_layers`：failed 合法构造补上
  attempts/next_retry_at/last_error（新三层契约要求）。
- `test_readback_reasons_canonical_contract`：分隔符反例用紧凑 separators
  (",", ":")（默认 separators 与 canonical 相同，不会真正改变字符串）。

---

# 里程碑 MTF-BTC-V1（2026-08-16 开工）—— 4H 告警入场位置 vs 周线门槛放宽（A/B 对照事件研究）

## 目标 / 顺序 / 最大风险（开工记录，≤10 行）

1. 目标：用 BINANCE:BTCUSDT 历史真实评估「当前 4H 告警入场位置」以及候选规则
   「周线只作背景、日线必须与 4H 告警同向」；对照 A=current_strict vs B=daily_trigger。
2. 顺序：任务0 取证/备份/基线 → 任务1 冻结历史数据 + 失败测试 + 纯计算引擎/CLI
   → 任务2 全样本事件研究报告（raw/A/B/B_minus_A + 分组 + 反向验证）→ 验收复跑。
3. 主口径：M3A（事件收盘参考价、5/10/20/40 完整 4H、ATR14=1R、MFE/MAE、
   先到 ±1R、同根歧义、fake）；另报「下一根4H开盘入场、窗口末根收盘退出」方向收益。
4. 最大风险：①未来追加改动历史事件（须逐前缀因果 + 冻结数据只读）；
   ②重复 time/非连续 4H/未收盘越界未显式失败 → 结果污染；③误改只读核心
   （multi_timeframe/market_state/outcome_labeling/schemas/monitor 等）→ 基线退步。

## MTF-BTC-V1 开工回执（2026-08-16，执行者 Pi-2）

- 六表实测: users=1 watchlists=7 alerts=0 notification_channels=2 monitor_plans=1
  alert_events=1005（与快照 mtf-btc-pre-20260815T235411Z 一致）。
- M3: m3_shadow_runtime started_at=1786691348（固定）、last_run_ok=t、
  m3_shadow_events=0。
- 快照 verify ✅ + drill ✅（六表一致、临时库已清理、现用库未触碰）。
- 白名单外指纹基线 436 文件 → reports/mtf-btc-backtest-v1/fingerprint_baseline.sha256
  （排除 __pycache__/.pytest_cache/node_modules/.next/.DS_Store/reports/；运行期
  缓存 data/market/daily_report_cache 由后台调度自然刷新，另记不比对）。
- HEAD 965d1501（既有 209 行脏改全保留）；pytest 9.0.3 环境 OK（M1/M2/M3A 188 绿）。

## MTF-BTC-V1 完成与验收（2026-08-16）

- 备份：`mtf-btc-pre-20260815T235411Z` 的 create / verify / drill 均已执行，
  最终 verify 再次通过；未恢复、未触碰现用库。
- 数据：Binance USD-M BTCUSDT，79 月 + 14 日 = 93/93 齐全；
  2020-01-01T00:00Z 至 2026-08-14T20:00Z 开盘；14508 根 4H / 2418 日 /
  344 完整周；SHA 见 REPORT。
- 策略：ma / structure / macd；2094 events / 2082 唯一 (time, direction)；
  A/B 定义见 REPORT。
- 结论：日线同向是硬门；周线不硬拒（agree / sideways / disagree 标签）；
  B 5 根 53.62% 但 CI 含 50，20/40 约 50.6/50.5；sideways 事后分组；
  disagree 中长弱；不切生产。
- 产物：REPORT.md 与架构文档（路径见 reports/ 与 docs/architecture/）。
- 验收：定向 127 passed；全量 645 passed（命令明确
  `--ignore=tests/test_ma_strategy_scope.py`；该文件既有 collection
  ModuleNotFoundError: scripts，不归本任务）；tsc 0；真实 run 两遍三核心
  产物 sha 相同，最终 manifest 更新后核心 sha 仍相同；source verify 通过。
- DB 只读核查：users1 / watchlists7 / alerts0 / notification_channels2 /
  monitor_plans1 / alert_events1013；开工 1005 → 1013 是后台自然增长，本任务
  无 DB 写；M3 events0 / runtime started_at=1786691348 / last_run_ok=t。
- 白名单：仅三个代码测试新文件、两个文档、reports、PROGRESS/BLOCKED；
  monitor / API / schema / DB / 前端改动 0。
- M3B 仍未完成（30 天且 ≥100 eligible 未达），不冒充完成。
