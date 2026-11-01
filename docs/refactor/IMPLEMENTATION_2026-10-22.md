# 实施记录 · 2026-10-22（第二轮）

> 对应 [架构设计](ARCHITECTURE_DESIGN_2026-10-22.md) 的 P1–P6。原则不变：
> **一处定义（`indicator_core`），五处出口（看盘/练习/统计/告警/实验）**；
> 指标计算永远不落库，库里只存「事实」（事件、结果、运行、版本）。

## 1. 现在能做什么

| 能力 | 入口 | 说明 |
|---|---|---|
| 盯盘：K 线 + MA/MACD + 指标买卖点 | `/watch` | 一个 `POST /api/series` 同时拿 K 线、序列、事件；点事件跳到那根 K 线 |
| 练习：步进/播放/揭晓 | `/practice` | 不依赖「复盘会话」，随机起点、方向键步进、指标事件逐个揭晓结果；手机端单列 |
| 统计：历史触发频率/胜率/期望 | `/stats` | 按指标聚合 + 按年份分解 + Wilson 区间；每行可「跳到当时」 |
| 实验：spec → run → 买卖点 | `/lab` | 创建实验、运行、看净值/交易明细/图上 B/S 点，归档或彻底删除 |
| 指标库：配置/版本/晋升 | `/library` | primitive 不可删；indicator 可归档；version 用实验运行作证据晋升 |
| 指标告警 | `POST /api/alerts/indicator-scan`、`scripts/indicator_alert_cli.py` | 飞书卡片带「历史 n/胜率/期望」+ 回到当时的深链 |

## 2. 数据模型（Alembic `0002_indicator_layer`）

```
indicator_events      uid 唯一；indicator_id + symbol + timeframe + bar_time + params_hash
indicator_outcomes    event_id + horizon/target_r/stop_lookback 唯一（多口径并存）
experiments           spec_json + params_hash + status(draft/tested/...)
experiment_runs       metrics_json + artifact_dir + git_sha（一次运行 = 一份可复现快照）
experiment_trades     入场/出场时间、价格、R、原因、事件 uid
indicators            用户命名的配置（name 唯一，指向 primitive）
indicator_versions    不可变快照：spec_json / params_hash / 证据 / 指标快照
promotion_audit       create/version/promote/demote/archive/restore/purge/import
```

新增 ORM 在 `apps/platform-api/app/models/models.py` 末尾；SQLite 测试用 `PK = BigInteger().with_variant(Integer,"sqlite")`。

## 3. API 一览（新增）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/series/catalog` | 序列目录（ma/macd/rsi/atr） |
| POST | `/api/series` | K 线 + 序列 + 可选事件（`as_of` 严格前缀） |
| GET | `/api/events` | 库内事件；`with_outcomes=true` 附带结果 |
| POST | `/api/events/backfill` | 回填某品种/周期的事件与结果（幂等） |
| GET | `/api/stats/indicators` | 指标总览（n/胜率/期望/Wilson） |
| GET | `/api/stats/indicators/{id}` | 单指标：按年、按方向、净值、逐次结果 |
| GET/POST | `/api/experiments` | 列表 / 创建（spec 校验 + primitive 白名单） |
| POST | `/api/experiments/{id}/runs` | 执行并落库、写产物 |
| GET | `/api/experiments/{id}/runs/{run_id}/chart` | K 线 + 买卖点 markers |
| GET | `/api/experiments/{id}/runs/{run_id}/trades` | 交易明细 |
| DELETE | `/api/experiments/{id}` | 默认归档；`?purge=true` 物理删除（DB + 目录） |
| POST | `/api/experiments/cleanup?scope=failed\|stale\|orphan` | 批量清理 |
| GET | `/api/library/primitives` | 代码内 primitive（不可删） |
| GET/POST | `/api/library/indicators` | 指标列表 / 创建（自动 v1） |
| POST | `/api/library/indicators/{id}/versions` | 新版本 |
| POST | `.../versions/{vid}/promote` | 晋升：必须带成功实验运行、spec 参数一致、n≥30 |
| POST | `.../versions/{vid}/demote` | 降级 |
| POST | `/api/library/indicators/{id}/archive\|restore` | 归档 / 恢复 |
| DELETE | `/api/library/indicators/{id}` | 默认归档；`?purge=true` 物理删除 |
| GET/POST | `/api/library/export\|import` | bundle 导出/导入（跨环境，为双部署留门） |
| POST | `/api/alerts/indicator-scan` | 扫描最近事件并推送到渠道 |

## 4. 关键语义（不要改错）

1. **因果**：事件 `confirmed_at` 之前不可见；`/api/series` 的 `as_of` 与 `confirmed_before` 是练习模式的地基。
2. **删除**：一律「先归档、后物理删除」。
   - primitive 永远不可删（409）；
   - indicator 归档可恢复；purge 只删库内定义与版本，历史事件保留（事实不随配置消失）；
   - 实验 purge 才删 `data/experiments/<id>/` 目录；`cleanup?scope=orphan` 清理无 DB 记录的目录。
3. **晋升**：evidence = 成功 run id；run 的 spec 必须包含同一 primitive + 同一 params；n < 30 直接 400。
4. **结果口径**：入场 = 信号确认后下一根开盘；止损/目标用入场时 ATR；同根双触按止损；指标离场按收盘；
   `r_multiple` 与 `pnl` 都扣双边成本（fee + slippage）。
5. **幂等**：事件按 uid、结果按 (event_id, horizon, target_r, stop_lookback) 去重，回填可重复跑。

## 5. 验证

```bash
cd apps/platform-api && python3 -m pytest -q        # 707 passed（含本轮新增）
cd apps/platform-web && npx tsc --noEmit           # 通过
```

`next build` 在本机因 `@next/swc-darwin-x64` 二进制加载失败而中断（环境问题，与本次代码无关；
`tsc` 已覆盖类型与语法）。

新增测试：

- `tests/test_event_store.py`：upsert 幂等、过滤、回填真实 BTC 数据、结果写入；
- `tests/test_stats_db.py`：回填 → 总览/明细/事件列表（含 `confirmed_before`）；
- `tests/test_experiment_runner.py`：spec 校验、下一根开盘入场、止损/目标/时间/指标离场、成本；
- `tests/test_experiments_api.py`：创建→运行→产物→图表→归档→恢复→删运行→purge；
- `tests/test_library_api.py`：primitive 不可删、晋升门禁（无证据/样本不足）、发版、归档/恢复、bundle 导入导出；
- `tests/test_indicator_alert.py`：深链格式、统计摘要、空事件不发送。

## 6. 使用顺序（第一次跑）

```bash
# 1) 建表（默认 DB_AUTO_DDL=True 会自动建；显式迁移时）
cd apps/platform-api && alembic upgrade head

# 2) 回填事件与结果（统计/练习的数据源）
python ../scripts/backfill_indicator_events.py --symbol BTC-USD --timeframe 1day
python ../scripts/backfill_indicator_events.py --symbol BTC-USD --timeframe 4h \
    --indicators chanlun.buy3 ma.breakout

# 3) 前端
cd apps/platform-web && npm run dev     # /watch /practice /stats /lab /library

# 4) 告警（cron）
python scripts/indicator_alert_cli.py --symbol BTC-USD --timeframe 4h \
    --channel 1 --since-hours 8 --base-url http://localhost:3000
```

## 7. 还没做的（按优先级）

1. ~~练习内核 TS 化~~ ✅ 完成（`lib/practiceCore.ts`）；离线缓存 ✅（`lib/practiceCache.ts`）；错题本/随机起点可继续加。
2. **告警按计划维度配置**：调度器已按全局配置扫描；下一步把 `MonitorPlan` 指向
   `indicator_version_id`，让每个计划只订阅自己晋升过的版本。
3. ~~旧接口标 deprecated~~ ✅ 本轮完成（响应头 + OpenAPI）；下一步把看盘页从 `/api/chanlun/analyze` 切干净。
4. **仓库分区**：`apps/web`、`apps/api`、`packages/indicator-core`（纯移动，无逻辑改动）——**尚未开始**，
   需要同时改 Docker 构建上下文与 compose 挂载，放到单独一轮做。
5. **双部署**：bundle 已可导出/导入；下一步给 `IndicatorVersion` 加 `environment` 或独立 promotion 表，
   支持「实验环境 → 正式环境」的拉取/推送。

---

## 8. 第二轮增补（practice-core / 告警调度 / 旧接口 / 出库）

### 8.1 练习内核真正解耦

- 新增 `apps/platform-web/lib/practiceCore.ts`：纯函数 `buildView`（只看光标前的 K 线与已确认事件）、
  `revealedStats`（只统计已揭晓）、`PracticeSession`（步进/跳转/重置/揭晓，可被移动壳或 CLI 直接 new）。
- `/practice` 已改为调用它；桌面与移动共用同一份逻辑，步进不再依赖后端会话。
- 新增 `apps/platform-web/tests/practiceCore.test.ts`（5 项）：warmup 边界、无未来函数、揭晓计分、会话步进/重置。

### 8.2 告警：调度器接入指标维度

- 新增 `apps/platform-api/app/core/indicator_alert_service.py`：`scan_and_notify` 按品种/周期扫描 Registry 事件，
  **先把事件 upsert 进 `indicator_events` 去重**，只推「库里原本没有」的事件（重启不重复轰炸）。
- `alert_scheduler` 新增 `_indicator_scan_job`（APScheduler + 内置回退循环 + 启动补扫）。
- 配置（默认关闭，避免升级即刷屏）：
  `INDICATOR_ALERTS_ENABLED` / `INDICATOR_ALERT_TIMEFRAMES` / `INDICATOR_ALERT_LOOKBACK_HOURS` /
  `INDICATOR_ALERT_BASE_URL` / `INDICATOR_SCAN_INTERVAL_MIN`（已写入 `.env.example`）。
- 卡片内容与 `scripts/indicator_alert_cli.py`、`POST /api/alerts/indicator-scan` 共用 `build_digest`，
  必带「历史 n/胜率/期望/Wilson」与 `/watch?...&t=` 深链。

### 8.3 旧接口 deprecated 转发

- 新增 `apps/platform-api/app/api/deprecation.py` 中间件：`/api/chanlun`、`/api/ma-strategies`、`/api/analysis`
  继续可用，但响应带 `Deprecation: true`、`Link: </api/series>; rel="successor-version"`、
  `Warning: 299`。`/api/chanlun/analyze` 同时在 OpenAPI 标记 `deprecated=True`。
- 新增 `tests/test_deprecation.py` 锁定「旧路由有头、新路由没有」。

### 8.4 数据出库

- 新增 `apps/platform-api/app/core/web_data.py` + `scripts/build_web_data.py`：
  把 K 线 + MA/MACD 序列 + 指标事件写成 `apps/platform-web/public/data/<SYMBOL>/<TF>.json` + `manifest.json`。
- 前端新增 `apps/platform-web/lib/staticData.ts`；`/watch` 优先吃静态出库，缺失时自动回退实时 API，
  侧栏显示「静态出库」标记。
- 新增 `tests/test_web_data.py`：bundle 结构、manifest 计数、缺失品种跳过。

### 8.5 本轮验证

```bash
cd apps/platform-api && python3 -m pytest -q      # 707 passed（网络用例偶发失败，与本轮无关）
python3 -m ruff check backend/app apps/platform-api/tests scripts
cd apps/platform-web && npm test                 # 28 passed（含 practiceCore 5 项）
npx tsc --noEmit
npm run build                           # ✅ 18/18，修复了 Topbar useSearchParams 缺 Suspense
```

> `npm run build` 之前失败有两个原因：本机 `@next/swc-darwin-x64` 二进制缺失（已从 npm 包恢复），
> 以及共享布局里的 `Topbar` 用了 `useSearchParams` 但无 Suspense 边界（已修，影响所有 (main) 页面）。

---

## 9. 第三轮增补（仓库分区第一步）

### 9.1 指标层出 backend

`apps/platform-api/app/indicator_core/` 的实现整体平移到 **`packages/indicator-core/indicator_core/`**，
成为可独立安装的包（含 `pyproject.toml`）；`apps/platform-api/app/indicator_core/__init__.py` 只保留兼容 shim：

- 自动发现包目录（`INDICATOR_CORE_PATH` → `REPO_ROOT/packages/indicator-core` → 向上逐级查找）；
- 把 `indicator_core.*` 子模块注册成 `app.indicator_core.*` 的别名，旧 import 全部继续可用；
- 迁移期两端拿到的是**同一批对象**（`app.indicator_core.Event is indicator_core.Event`）。

新增 `tests/test_package_layout.py` 锁定「实现必须在 packages、shim 必须指向同一对象」。

### 9.2 Docker / Compose 跟着改

- `docker-compose.yml` 的 backend 构建上下文从 `./backend` 提到 `.`，这样镜像能带上 `packages/`；
- `apps/platform-api/Dockerfile` 改为 `COPY backend/...` + `COPY packages/ ./packages/`；
- compose 的 `REPO_ROOT=/repo` 与 `./:/repo` 挂载让 shim 在开发模式也能找到包（不依赖镜像内路径）。

### 9.3 出库命令入口

`apps/platform-web/package.json` 增加 `npm run data` → `python3 ../scripts/build_web_data.py`，
产物默认写 `apps/platform-web/public/data/`（已 gitignore）。

### 9.4 本轮验证

```bash
python3 -m pytest -q                 # 全绿（见本轮结果）
python3 -m ruff check packages/indicator-core backend/app apps/platform-api/tests   # 通过
python3 scripts/audit_causality.py --symbol BTC-USD --tf 1day --samples 3 # 0 泄漏 / 0 漏报
cd apps/platform-web && npm test && npx tsc --noEmit && npm run build              # 通过
```

---

## 10. 第四轮增补（计划订阅 + 双环境推送/拉取）

### 10.1 每个监控计划订阅自己的指标版本

- `MonitorPlan` 新增 `indicator_version_ids`（JSON 数组）与 `indicator_cursor`（上次扫描到的
  `confirmed_at`，重启不丢）；Alembic `0003_plan_indicator_subs` + 启动自动补列同步。
- API：创建/更新计划接受 `indicator_version_ids`，只允许 **已晋升且未下线、指标未删除** 的版本
  （`_validate_indicator_versions` 返回 400）；计划输出带订阅列表与游标。
- 调度：`_indicator_scan_job` 先跑 `scan_plan_subscriptions`（按计划的自选列表 + 周期 + 渠道 +
  版本参数），再跑全局兜底扫描。游标按计划持久化，同一事件不会重复推送。
- 前端：`/alerts` 创建计划时可勾选「指标订阅」（只列 `GET /api/library/promoted-versions`），
  计划卡片显示「指标订阅 ×N」。

### 10.2 实验环境 ↔ 正式环境的主动推送/拉取

- 新增 `POST /api/library/push`：导出本地 bundle → 调远端 `/api/library/import`；可只推选中指标；
  成功后在 `promotion_audit` 记 `push`（actor + 目标地址）。
- 新增 `POST /api/library/pull`：从远端 `/api/library/export` 拉 bundle 再导入；同名指标跳过，
  不覆盖已有版本（拉取结果是 draft，需要时再晋升）。
- 远端地址只接受 `http(s)://`；HTTP 错误统一转 502。
- 前端 `/library` 增加远端地址输入 + 「推送到正式环境 / 从正式环境拉取」按钮。
- 顺带修正：`pull` 使用远端 `/api/library/export?ids=` 的真实参数名。

### 10.3 新增测试

- `tests/test_plan_indicator_subscription.py`：参数透传、计划游标去重、未晋升版本 400、
  `promoted-versions` 只列 live 版本、`_plan_out` 带订阅列表。
- `tests/test_library_sync.py`：push 的 URL/鉴权头/bundle schema/审计、pull 的导入与同名跳过、
  非 http(s) 地址 400。

### 10.4 工程入口与 CI

- 新增根 `Makefile`：`make test / lint / data / audit / build / up / down`，全部用 `python3`。
- `.github/workflows/ci.yml`：
  - Ruff 覆盖 `packages/`，并新增「因果审计（BTC 1D 采样 3 点）」步骤；
  - 新增 `repo-hygiene` job：`apps/platform-web/public/data` 若被 Git 跟踪直接失败；
  - 前端 job 增加 `npm run build`，与本地验收口径一致。
- 顺手修复两个让 CI 一直是红的历史问题：
  `scripts/ops/build_intraday_chunks.py` 未使用的 `import sys`；
  `experiments/ma_a_atr_entry/run_experiment.py` 的 f-string 里字面量 `{stocks,gold,...}` 未转义。

---

## 11. 第五轮增补（仓库分区第二步：apps/）

### 11.1 目录

```
apps/platform-api/     ← 原 backend/
apps/platform-web/     ← 原 apps/platform-web/
packages/indicator-core/
```

`apps/chanlun-web/`（缠论产品 B）暂不移动：它有自己的静态页与 fenxing 数据链，等 `apps/chanlun-web` 单独一轮再做，
避免把「目录移动」和「产品拆分」混在一起。

### 11.2 代码/配置同步

- 所有 `app/**` 的仓库根从 `parents[3]` 改为 `parents[4]`；tests 从 `parents[2]` 改为 `parents[3]`；
  `tests/conftest.py` 的 `REPO_ROOT` 改为 `BACKEND_DIR.parents[1]`（现在多一层 `apps/`）。
- 脚本：`scripts/*.py` 与 `scripts/backtests/*.py` 的 `sys.path`/数据目录改为 `apps/platform-api`；
  出库默认目录改为 `apps/platform-web/public/data`。
- 基础设施：`docker-compose.yml`（backend 上下文仍是仓库根，Dockerfile 指向 `apps/platform-api/Dockerfile`）、
  `apps/platform-api/Dockerfile`、`.github/workflows/ci.yml`、`Makefile`、`.gitignore`、`pyproject.toml`
  的 `testpaths`、`apps/platform-web/package.json` 的 `data` 脚本。
- 运维脚本 `scripts/ops/alert_{backup,restore}.sh`、`test_restore_schema.sh` 的备份白名单同步到新路径。

### 11.3 验收

```bash
cd apps/platform-api && python3 -m pytest -q    # 714 passed
python3 -m ruff check apps packages scripts experiments   # 通过
python3 scripts/audit_causality.py --symbol BTC-USD --tf 1day --samples 3  # 0 泄漏 / 0 漏报
cd apps/platform-web && npm test                  # 28 passed
npx tsc --noEmit && npm run build                 # ✅ 18/18
```

补充：构建上下文变成仓库根后新增 `.dockerignore`（排除 node_modules/.next/data/reports 等），
避免把行情数据与前端依赖送进 Docker daemon。

README 的目录树与数据源路径已同步。

---

## 12. 第六轮增补（chanlun-web 归位）

### 12.1 目录

`fenxing/` 整体迁到 **`apps/chanlun-web/`**（含 `index.html`、`js/`、`css/`、`mobile/`、`tools/`、
`chanlun.py`、研究目录）。用户未提交的 `js/*`、`tools/verify_*.mjs`、`research/btc_ma60_bias/`
随目录一起移动，未丢失。

> 尚未做「引擎抽包」：`chanlun.py` / `fractals.py` / `backtest.py` 仍在 `apps/chanlun-web/` 根，
> 后端通过 `sys.path` 引用。下一步若要彻底解耦，再抽成 `packages/chanlun-core` 并让两端都 `import chanlun_core`。

### 12.2 引用同步

- 后端 `chanlun_service._FENXING_DIR` → `_REPO_ROOT / "apps" / "chanlun-web"`；
- `scripts/ops/build_intraday_chunks.py` 的 `OUT_ROOT` → `apps/chanlun-web/intraday`；
- `.gitignore` / `.dockerignore` / `pyproject.toml`（ruff exclude）同步；
- `alert_backup.sh` 源码白名单加入 `apps/chanlun-web`；
- README、docs 与前端注释里的 `fenxing/` 路径统一改为 `apps/chanlun-web/`。

### 12.3 验收

```bash
cd apps/platform-api && python3 -m pytest -q          # 714 passed
python3 -m ruff check apps packages scripts experiments  # 通过（apps/chanlun-web 按历史代码排除）
python3 -c "from app.core.chanlun_service import chanlun_available; print(chanlun_available())"  # True
```

---

## 13. 第七轮增补（缠论引擎抽包）

### 13.1 目录

`chanlun.py`、`fractals.py` 从 `apps/chanlun-web/` 抽到 **`packages/chanlun-core/chanlun_core/`**：

- `chanlun_core/__init__.py` 统一出口（`analyze`、`link_levels`、买卖点/中枢/背驰等）；
- 引擎内部 `from fractals import ...` 改为相对导入 `from .fractals import ...`；
- 行情数据目录改为 `REPO_ROOT/data/crypto`（原来用 `parents[1]`，迁移后会指错）。

### 13.2 兼容层

- `apps/chanlun-web/chanlun.py`、`apps/chanlun-web/fractals.py` 保留为 **shim**：
  自动定位 `packages/chanlun-core` 并把实现模块的公共名字重新导出，
  旧的 `import chanlun` / `from fractals import ...`（`backtest.py`、`multilevel_backtest.py`、
  `tools/verify_*.py`、`build_all_symbols.py`）一行不改继续可用。
- 后端 `chanlun_service` 优先 `import chanlun_core`，失败才回退 shim；
  路径发现改为「向上找 packages/chanlun-core」，镜像/本地/挂载三种情况都能定位。
- 测试里的可用性开关 `_FENXING_AVAILABLE` 更名为 `_CHANLUN_AVAILABLE`。

### 13.3 验收

```bash
cd apps/platform-api && python3 -m pytest -q         # 714 passed
python3 -m ruff check apps packages scripts experiments  # 通过（chanlun-core 引擎按历史代码排除）
python3 scripts/audit_causality.py --symbol BTC-USD --tf 1day --samples 3  # 0 泄漏 / 0 漏报
python3 -c "import sys; sys.path.insert(0,'apps/chanlun-web'); import chanlun; print(hasattr(chanlun,'analyze'))"
```

下一步（未做）：给 `chanlun_core` 补 `tests/` 与「Python 生成 → TS 比对」的 golden JSON 契约，
让前端 `chanlunOverlay.ts` 与 `chanlunStats.ts` 不再各写一遍口径。

### 13.4 练习离线缓存（顺带）

- 新增 `apps/platform-web/lib/practiceCache.ts`：把一次拉到的 `{candles, series, events, outcomeMap}`
  写入 `localStorage`（最多 4 个 `symbol:timeframe`，超限裁最旧），请求失败自动回退缓存。
- `/practice` 命中缓存时显示「离线缓存」chip；断网/地铁里仍可步进、揭晓、计分。
- 新增 `tests/practiceCache.test.ts` 3 项（读写、LRU 裁剪、无 storage 降级），前端测试 28 → 31。

---

## 14. 第八轮增补（缠论 golden 契约 + CI chanlun-web）

### 14.1 Python → TS golden 契约

- 新增 `scripts/export_chanlun_golden.py`：用真实 BTC 1D 数据（默认 400 根）跑
  `chanlun_service.analyze_symbol`，把端点同款 payload + `closes` + `macd_hist` 写成
  `apps/platform-web/tests/fixtures/chanlun_golden.json`（约 62 KB，入库）。
- 新增 `apps/platform-web/tests/chanlunGolden.test.ts` 5 项：
  - `chanlunMacdHist` 与 Python `macdHist` 逐根一致（Python 侧 `%.6g`，允许相对误差 1e-5）；
  - `buildChanlunLayerData` 的笔/线段/中枢数量与 Python 一致；
  - `buildBuySellMarkers` / `buildChanlunMarkers` 数量正确且按时间升序；
  - 所有买卖点/背驰点满足 `confirmedT >= t`（因果约束）；
  - `computeChanlunStats` 在同一输入上确定性输出。
- CI backend job 增加「golden 是否最新」检查：重新生成后 `git diff --exit-code`，
  引擎或序列化口径一变就红。
- `npm run golden:chanlun` 可本地重生成。

### 14.2 CI 增加 chanlun-web job

- `apps/chanlun-web/js/**/*.js` 逐个 `node --check`（语法门禁）；
- `import chanlun, fractals`（走 shim）冒烟，确认引擎抽包后 shim 仍可用。

### 14.3 验收

```bash
python3 scripts/export_chanlun_golden.py --bars 400
cd apps/platform-web && npm test        # 36 passed（原 31 + golden 5）
npx tsc --noEmit && npm run build       # 通过
cd ../platform-api && python3 -m pytest -q   # 714 passed
```

---

## 15. 第九轮增补（看盘图切统一出口）

### 15.1 后端：payload 构造下沉 + `/api/series` 支持结构

- 前端 overlay payload 的构造从 `api/chanlun_api.py` 下沉到
  `core/chanlun_service.py`：`chart_payload()`（raw → payload）与
  `analyze_chart()`（跑链路 + 出 payload），`_chart_merge_points` / `_chart_beichi_points` 同处。
- `POST /api/series` 新增 `structure: bool = false`：为 true 时在同一次请求里返回
  `structure`（strokes / segments / pivots_stroke / pivots_segment / buy_sell_points /
  beichi_points / top_fractals / bot_fractals / meta），且严格遵守 `as_of` 前缀过滤。
  非 1day/4h/1h 返回 400。
- 旧 `POST /api/chanlun/analyze` 改为**纯转发**：直接返回 `analyze_chart()` 结果，
  保留 `deprecated=True` 与 Deprecation/Link/Warning 响应头。

### 15.2 前端：`/chart/[symbol]` 走统一出口

- 图表页的缠论叠加数据改为 `computeSeries({..., structure: true})`，
  失败时回退旧 `analyzeChanlun()`（回滚兜底）；仅对 1day/4h/1h 发起请求。

### 15.3 验收

```bash
cd apps/platform-api && python3 -m pytest tests/test_series_structure.py -q   # 4 passed
python3 -m pytest -q                # 714 + 4 = 718 passed
python3 -m ruff check apps packages scripts experiments
cd apps/platform-web && npm test    # 36 passed；npx tsc --noEmit；npm run build 18/18
```

---

## 16. 第十轮增补（引擎自有测试 + Makefile/CI 收口）

### 16.1 `packages/chanlun-core/tests/`

- 新增 `tests/test_engine.py` 6 项，用固定种子的**合成随机游走**数据（300 根，不依赖仓库行情文件）：
  包含处理与分型/交替、`analyze` 键与 meta 计数自洽、MACD 递推与 `%.6g` 序列一致、
  买卖点 `confirmedT >= t` 因果约束、两次分析结果逐字节一致（确定性）、
  `apps/chanlun-web/chanlun.py` shim 与 `chanlun_core.chanlun` 同名函数同一对象。
- 该目录独立于后端 pytest，可单跑：`make test-engine` / `python3 -m pytest packages/chanlun-core/tests -q`。

### 16.2 Makefile 修正与 CI

- `make lint` 里残留的 `ruff check backend`（重命名后已失效）改为 `ruff check apps packages scripts experiments`；
  `make test` 现在串起「引擎 → 后端 → 前端」三步。
- CI `chanlun-web` job 增加 `pytest packages/chanlun-core/tests -q`，
  与 JS 语法、shim 冒烟一起构成引擎门禁。

### 16.3 验收

```bash
make test-engine                                  # 6 passed
python3 -m ruff check apps packages scripts experiments   # All checks passed
cd apps/platform-api && python3 -m pytest -q      # 718 passed
cd apps/platform-web && npm test && npx tsc --noEmit && npm run build
```

---

## 17. 第十一轮增补（指标层 golden + 设计文档对齐）

### 17.1 指标层 golden 快照（P0 最后一项）

- 新增 `scripts/export_indicator_golden.py`：12 个指标 × BTC 1D 尾段 600 根，
  导出 `apps/platform-api/tests/fixtures/indicator_events_golden.json`（112 个事件，67 KB）。
- 新增 `tests/test_indicator_golden.py` 3 项：
  - 重算事件列表与快照**逐字段相同**（uid / confirmed_at / direction / params_hash 全锁死）；
  - 快照覆盖全部 12 个已注册指标且事件数稳定；
  - uid 唯一 + `confirmed_at >= bar_time` 因果约束。
- CI 的 golden 校验步骤同时覆盖缠论与指标层两份 fixture；
  本地 `make golden` 一键重生成。

### 17.2 设计文档与真实进度对齐

`ARCHITECTURE_DESIGN_2026-10-22.md` §7 的 P0–P7 勾选项长期停留在计划态，
本轮逐条按代码事实重写：已完成项标 `[x]` 并注明轮次，未完成项写明缺口，例如

- P1：`/api/chanlun/analyze` 已转发 ✅；`chart-kit` 抽出、收盘增量刷新 ❌
- P2：practice-core + 离线存档 ✅；随机起点/加仓/错题重练、逐指标图层开关 ❌
- P4：实验台主体 ✅；后台队列 + 取消接口、旧实验迁移样例 ❌
- P5：生命周期与门槛 ✅；引用链影响面 API ❌
- P6：版本订阅 + 飞书卡片 ✅；收盘对齐调度、429/超时压测 ❌

### 17.3 验收

```bash
make golden                                       # 两份快照可重现
python3 -m ruff check apps packages scripts experiments
cd apps/platform-api && python3 -m pytest -q      # 721 passed（718 + 3）
cd apps/platform-web && npm test && npx tsc --noEmit && npm run build
```

---

## 18. 第十二轮增补（练习端：随机起点 / 错题重练 / 指标开关）

### 18.1 practice-core 纯函数

- `randomStart(length, seed?)`：mulberry32 确定性随机，同一 seed 在桌面/移动/测试给出同一起点；
- `nextReviewTime(events, outcomes, revealed, afterTime)`：返回当前时点之后**最早**一条已揭晓亏损的确认时间；
- `filterEvents(events, indicatorIds)`：按指标过滤练习事件（空选择 = 不显示）；
- `PracticeSession.randomReset(seed)` / `PracticeSession.nextReview()`：可变外壳同步支持。

### 18.2 练习页

- 顶部新增「随机起点」「错题重练」按钮（无错题时禁用）；
- 新增「指标图层」chips：可逐个开关 `chanlun.buy3` / `chanlun.sell3` / `ma.breakout` /
  `macd.divergence`，只影响练习视图、标记与计分，不改动底层数据；
- 指标过滤、错题定位都走 practice-core，桌面/移动共用同一逻辑。

### 18.3 验收

```bash
cd apps/platform-web && npm test        # 40 passed（原 36 + practice-core 4）
npx tsc --noEmit && npm run build       # 通过
cd ../platform-api && python3 -m pytest -q   # 721 passed
```

---

## 19. 第十三轮增补（指标库影响面）

### 19.1 后端

- `library/service.py` 新增 `indicator_impact(db, indicator)`：
  - `events.by_version` / `events.by_primitive` / `events.total`（事件是历史事实，purge 也保留）；
  - `plans`：解析 `MonitorPlan.indicator_version_ids` JSON，统计订阅了该指标任一版本的计划；
  - `experiments`：解析 `Experiment.spec_json` 的 `entry.all[].indicator` / `exit.any[].indicator`，
    命中 primitive 或版本号即计入；
  - `has_references`：是否被计划/实验引用（供 UI 提示）。
- 新端点 `GET /api/library/indicators/{indicator_id}/impact`；
  `POST .../demote` 与 `DELETE ...?purge=true` 的响应内联 `impact`，删除/下线前一眼看到影响面。

### 19.2 前端

- `getLibraryImpact()` + `/library` 详情面板新增「影响面」卡片：
  事件总数（版本/原始）、订阅计划数、实验数；有引用时红字提示先处理。

### 19.3 验收

```bash
cd apps/platform-api && python3 -m pytest tests/test_library_impact.py -q   # 2 passed
python3 -m pytest -q          # 723 passed（721 + 2）
python3 -m ruff check apps packages scripts experiments
cd apps/platform-web && npm test && npx tsc --noEmit && npm run build
```

---

## 20. 第十四轮增补（告警收盘对齐调度）

### 20.1 扫描窗口

- 新增 `app/core/scan_window.py`（纯函数）：
  - `timeframe_seconds(tf)`：15min/30min/1h/2h/4h/6h/12h/1day 的秒数；
  - `closed_bar_time(now, tf)`：最近一根**已收盘** K 线的收盘时间戳（UTC 对齐）；
  - `due_timeframes(now, tfs, scanned_close, last_fallback, grace, fallback)`：
    返回 `(周期列表, 原因)`，原因 `aligned` / `fallback` / `idle`。
- `INDICATOR_ALERT_GRACE_MIN=5`（`.env.example` 已加），`INDICATOR_SCAN_INTERVAL_MIN` 同时作为兜底周期。

### 20.2 调度器

- 指标 job 从「每 30 分钟固定 interval」改为「每 1 分钟 tick + 内部节流」：
  - 收盘后 5 分钟内、且该收盘 bar 未扫过 → 扫（`aligned`）；
  - 距上次兜底扫描超过 `INDICATOR_SCAN_INTERVAL_MIN` → 强制补扫（`fallback`）；
  - 只有扫描成功才记账，失败下一分钟自动重试；同一根 bar 不会重复扫描。
- APScheduler 与内置兜底循环两条路径共用同一套判断。

### 20.3 验收

```bash
cd apps/platform-api && python3 -m pytest tests/test_scan_window.py -q   # 6 passed
python3 -m pytest -q          # 729 passed（723 + 6）
python3 -m ruff check apps packages scripts experiments
```

---

## 21. 第十五轮增补（告警投递幂等与重试）

### 21.1 问题

原实现用「事件是否已入库」判断是否推送：入库在发送之前，因此**发送失败的事件永远不会重试**
（下一轮扫描认为它已存在），飞书 429/超时时会静默丢消息。

### 21.2 改动

- `IndicatorEvent` 新增 `notified_at BIGINT`（Alembic `0004_event_notified` + `DB_AUTO_DDL` 补列）；
- `event_store.load_pending_notifications()`：窗口内 `notified_at IS NULL` 的事件（新插入 + 历史失败）；
- `event_store.mark_notified()`：只有投递成功才记账；
- `scan_and_notify`：待推送改为查 DB 待推送集合；发送异常（429/超时）记为失败；
  成功才 `mark_notified`；返回体增加 `notified`；
- `scan_plan_subscriptions`：发送异常不推进 `indicator_cursor`（下一轮重试），
  成功的事件同样标记 `notified_at`，避免被全局兜底扫描重复推送。

### 21.3 测试

`tests/test_indicator_alert_retry.py` 3 项：
- 首次返回 False（429）→ 事件留待重试，二次扫描补发并记账，三次扫描不再发送；
- 首次抛 `TimeoutError` → 同样按失败处理并重试成功；
- 计划订阅投递失败不推进游标，成功后全局兜底扫描不再重复推送。

### 21.4 验收

```bash
cd apps/platform-api && python3 -m pytest tests/test_indicator_alert_retry.py -q   # 3 passed
python3 -m pytest -q          # 732 passed（729 + 3）
python3 -m ruff check apps packages scripts experiments
```

---

## 22. 第十六轮增补（实验运行控制 + spec 迁移样例）

### 22.1 超时与后台执行

- 新增 `EXPERIMENT_RUN_TIMEOUT_SEC`（默认 180s，`.env.example` 已同步）。
- `experiment_core/service.execute()` 把同步引擎丢进 `asyncio.to_thread` + `asyncio.wait_for`：
  超时 → run 落库 `failed` + `运行超时(>Ns)`，主事件循环不再被长实验阻塞。
- `create_run()` / `spawn_background()`：`POST /api/experiments/{id}/runs?background=true`
  立即返回 `queued` run，后台 asyncio 任务执行（`_RUN_TASKS: run_id -> task`）。
- `cancel()` + `POST /api/experiments/runs/{run_id}/cancel`：取消 queued/running，
  写 `cancelled` + `finished_at`；已结束返回 409，不存在返回 404。

### 22.2 旧实验 → spec 迁移样例

- `experiments/specs/ma_breakout_2r.spec.json`：把 `experiments/strategy_c_ma_pool_compare.py`
  的「均线突破 + ATR 止损 + 2R 止盈 + 成本 + holdout」声明化。
- `scripts/run_experiment_spec.py <spec> [--json]`：不依赖 DB 的快速复算。
- `tests/test_spec_migration.py`：文件合法、`spec_hash` 稳定、非法字段被拒、
  本地数据上 `n=75` 且交易都带 `entry_event_uid`。
- 前端：`/lab` 运行历史中 queued/running 行出现「取消」按钮（`cancelExperimentRun`）。

### 22.3 验收

```bash
cd apps/platform-api && python3 -m pytest tests/test_experiment_run_control.py tests/test_spec_migration.py -q  # 8 passed
python3 scripts/run_experiment_spec.py experiments/specs/ma_breakout_2r.spec.json --json | head
python3 -m pytest -q && python3 -m ruff check apps packages scripts experiments
```

---

## 23. 第十七轮增补（chart-kit 抽出 + 收盘增量刷新）

### 23.1 新模块 `apps/platform-web/lib/chart-kit/`

| 文件 | 职责 |
| --- | --- |
| `series.ts` | 统一序列 → 折线 / 直方图 / 买卖标记 / 图表页 `Indicators`；`alignValues` 按索引对齐 K 线并丢空值 |
| `structure.ts` | 缠论 overlay 增量合并：逐列表按时间键去重、新结果覆盖同键、按时间排序、上限 2000 条 |
| `barClose.ts` | 周期毫秒表、`lastClosedBarMs` / `msUntilBarClose` / `nextRefreshDelayMs`（与后端 `scan_window.py` 同口径） |
| `useBarCloseRefresh.ts` | 收盘对齐刷新 hook：跨过一根已收盘 K 线（+5s grace）才回调；长周期按分钟轮询，`visibilitychange` 重新校准 |

### 23.2 页面接入

- `/watch`：`seriesToLines` / `seriesToHistogram` / `eventsToMarkers` 取代页内手写映射；
  新增 `useBarCloseRefresh(timeframe, () => query.refetch())`。
- `/chart/[symbol]`：MA/MACD/RSI 改为 `POST /api/series`（`seriesToIndicators`），
  前端 `calculateIndicators` 仅作统一出口不可用时的降级；收盘后 `setRefreshTick` 触发
  「最近 600 根」结构重算 + `mergeStructure` 合并；同时 invalidate candles 查询。

### 23.3 测试与验收

```bash
cd apps/platform-web && node --test tests/chartKit.test.ts   # 8 passed
npm test && npx tsc --noEmit && npm run build
```

新增 `tests/chartKit.test.ts` 8 项：序列对齐/取 panel/颜色稳定/直方图丢零值/标记合并与排序、
收盘对齐（4h 与 1day 边界、未知周期返回 null）、`tailCandles`、`mergeStructure` 覆盖与保留、
`seriesToIndicators` 映射（含缺序列降级）。

---

## 24. 第十八轮增补（练习端加仓 / 数值序列开关 / 盲测锁视口）

### 24.1 practice-core 加仓

`apps/platform-web/lib/practiceCore.ts` 新增纯函数（无 React、无网络，双端复用）：

| 函数 | 语义 |
| --- | --- |
| `createPosition(event, weight=1)` | 以事件开仓，首仓一条腿 |
| `addLeg(position, event, {weight, maxLegs=3, minStepPct})` | 同向、未超腿数、价格更有利才加仓，否则 `null` |
| `positionR(position, outcomes)` | 按腿权重加权平均 R；无结果返回 `null`，同时给出 `covered/total` |
| `closePosition(position, outcomes, closedAt)` | 平仓结算，`won = rMultiple > 0` |
| `positionStats(closed[])` | 已平仓笔数/胜率/加权均 R |

`PracticeSession` 增加 `openPosition` / `addToPosition` / `closePositionNow` /
`currentPosition` / `closedPositions` / `positionStats`，移动壳与 CLI 可直接用。

### 24.2 页面

- `/practice` 事件卡片：`进场` / `加仓`（同向且 <3 腿）/ `平仓`（反向）；侧栏新增持仓卡
  （方向、腿数、均价、权重、每条腿明细、加权 R、已平仓统计）。
- MA / MACD 数值序列开关：序列来自 `POST /api/series`（`ma` + `macd`），
  经 `chart-kit.seriesToLines` 映射；`SimpleChart` 新增 `lockRightEdge`
  （`fixRightEdge` + `lockVisibleTimeRangeOnResize`）实现盲测锁视口。

### 24.3 测试与验收

```bash
cd apps/platform-web && node --test tests/practiceCore.test.ts   # 12 passed（新增 3 项）
npm test && npx tsc --noEmit && npm run build
```

新增 3 项：加仓方向/腿数上限/最小间距、加权 R 与平仓统计、`PracticeSession` 进场→加仓→平仓→重置。

---

## 25. 第十九轮增补（独立 PWA 壳）

### 25.1 文件

| 文件 | 说明 |
| --- | --- |
| `public/manifest.webmanifest` | `display: standalone`、`start_url: /practice`、`scope: /`、192/512 PNG + maskable + SVG 图标、练习/盯盘/指标库 shortcuts |
| `public/icon.svg`、`public/icon-192.png`、`public/icon-512.png` | 图标（PNG 由脚本内联生成，无外部依赖） |
| `public/sw.js` | 预缓存练习页与图标；**只缓存 GET**（`POST /api/series` 直连，避免指标口径漂移）；导航 network-first，断网回退 `/practice`；`_next/static` stale-while-revalidate |
| `lib/pwa.ts` | `PWA_MANIFEST_PATH` / `PWA_SERVICE_WORKER_PATH` / `PWA_OFFLINE_URL` / `PWA_CACHE_NAME` / `PWA_PRECACHE`、`isStandalone`、`shouldCacheRequest`、`offlineFallbackUrl` |
| `components/PwaRegister.tsx` | 注册 service worker；显示「独立窗口」「离线」角标（`online`/`offline` 事件） |
| `app/layout.tsx` | `metadata.manifest`、`appleWebApp`、`icons`、`viewport.themeColor`，挂载 `PwaRegister` |

### 25.2 离线练习链路

`/practice` → 首次访问 `sw.js` 预缓存页面与图标 → 之后断网时导航回退到缓存的 `/practice`；
练习数据本身走 `lib/practiceCache.ts`（localStorage LRU 4 份），因此离线也能继续步进/揭晓/加仓。

### 25.3 测试与验收

```bash
cd apps/platform-web && node --test tests/pwa.test.ts   # 4 passed
npm test && npx tsc --noEmit && npm run build
```

`tests/pwa.test.ts`：manifest 可安装字段与图标文件存在性、`sw.js` 预缓存与 `lib/pwa.ts` 常量一致、
只缓存 GET、导航回退、`isStandalone`/`shouldCacheRequest` 判定、根布局引用 manifest 与 `PwaRegister`。

---

## 26. 第十九轮增补（生成数据出库 + 卫生守卫）

### 26.1 出库

- `apps/chanlun-web/data-view/*.js`（42 个，192 MB）与 `apps/chanlun-web/intraday/**/*.bin`
  （109 个，32 MB）执行 `git rm --cached`：**磁盘文件与 git 历史都保留**，
  随时可 `git checkout <commit> -- <path>` 取回。
- `.gitignore` 增加 `apps/chanlun-web/data-view/*.js`、`*.json`、`apps/chanlun-web/intraday/`。
- 新增 `apps/chanlun-web/README.md`：运行方式 + 四类产物的重建命令
  （`update_data.py` / `chanlun.py BTC` / `build_intraday_chunks.py` / `tools/build_*.py`）。

### 26.2 守卫与陈旧路径

- `.github/workflows/ci.yml` 的 repo-hygiene 扩展：`data-view/*.js`、`intraday/*.bin`、
  `.pytest_cache`/`.ruff_cache`/`.DS_Store`/`tmp_parquet` 入库即失败（本地实测 0 命中）。
- `scripts/ops/update_all.sh`、`scripts/ops/build_intraday_chunks.py` 中残留的 `fenxing/` 路径
  改为 `apps/chanlun-web/`；`chanlun_service.py` 等 4 处「fenxing 引擎」注释/报错文案
  统一改为 `chanlun-core`（旧 shim 仍可用）。

### 26.3 验收

```bash
git ls-files | grep -cE 'data-view/.*\.js$|intraday/.*\.bin$'   # 0
python3 scripts/audit_causality.py                              # 0 泄漏 / 0 漏报
python3 -m pytest packages/chanlun-core/tests -q                # 6 passed
for f in apps/chanlun-web/js/*.js; do node --check "$f"; done   # 全部通过
python3 -m pytest -q && python3 -m ruff check apps packages scripts experiments
cd apps/platform-web && npm test && npx tsc --noEmit && npm run build
```

---

## 27. 第二十轮增补（labs 收口 + 文档单一事实源）

### 27.1 `labs/` 实验收口

- `labs/README.md`：三态原则（`labs/ideas` → `labs/experiments` → `apps/platform-*`）与新增实验流程。
- `labs/experiments/registry.yaml`：6 条登记（4 个 active 实验、1 个 promoted 的 spec 样例、
  1 个 archived 的 MA 同族回测），字段含 `id/title/hypothesis/status/data/command/artifacts/conclusion`。
- `labs/backtests/ma.py`：MA 同族统一入口（`--variant base|3x|c` 委托旧脚本，`--list` 查看映射）。
- `labs/ideas/chanlun-p1-p5.md`：由 `apps/chanlun-web/docs/TODO.md` 迁入（一组待验证假设）。
- 契约测试 `apps/platform-api/tests/test_labs_registry.py`（5 项）：字段齐全、id 唯一、
  `experiments/` 下每个目录/脚本都有登记、命令指向的文件存在、定稿项必须有结论与日期。

### 27.2 文档单一事实源

- `docs/README.md`：索引（状态/runbook/架构/策略实验/归档），每条标「最后校对」日期。
- `STATUS.md`（根）：当前里程碑表（P0–P7 + 审计收尾）、阻塞（无）、下一步、最近验收命令与结果、
  环境开关、历史阻塞（原 `BLOCKED.md` 内容并入，文件删除但历史保留）。
- `docs/PROGRESS.md` → `docs/archive/PROGRESS_2026H1.md`；4 处架构文档引用同步更新。
- `README.md` 顶部加「两个产品 + 两个共享包」说明，并修掉失效链接（`email_alter.md` →
  `docs/runbooks/DAILY_EMAIL_REPORT.md`）。
- `REPO_AUDIT` §4 加执行状态表（P0–P5 ✅，§5.3 `core/` 分域标 ⏳ 低优先）。

### 27.3 顺手修掉的 CI 隐患

`.github/workflows/ci.yml` 的 lint 仍是 `ruff check backend ...`（分区后 `backend/` 已不存在，
CI 必失败）→ 改为 `ruff check apps packages scripts experiments labs`；Makefile 同步。

### 27.4 验收

```bash
cd apps/platform-api && python3 -m pytest -q                     # 745 passed
python3 -m ruff check apps packages scripts experiments labs     # All checks passed
cd apps/platform-web && npm test && npx tsc --noEmit && npm run build
python3 labs/backtests/ma.py --list
```

---

## 28. 第二十一轮：真机浏览器验证（发现并修掉两个真 bug）

> 起因：此前各轮只做到「pytest / tsc / build 通过」就宣称完成。构建通过 ≠ 页面能用。
> 本轮用 headless Chrome + CDP 真跑页面，抓到两个构建期看不出来的 bug。

### 28.1 验证环境

- 后端：本地无 Postgres、Docker 守护进程未启动，故用 SQLite 冒烟后端
  （`/tmp/smoke_server.py`，`DB_AUTO_DDL=false` + 跳过 Postgres 专属的 `m3_shadow_*` 两张表，
  `PYTHONPATH=apps/platform-api/.testdeps` 提供 aiosqlite）。**仅用于验证，不代表生产配置。**
- 前端：`npm run build` + `next start -p 3100`（验证的是生产产物）。
- 浏览器：`Google Chrome 155 --headless=new --remote-debugging-port=9222`。
- 数据：`scripts/backfill_indicator_events.py` 灌入 BTC-USD 1day（390 事件/390 结果）
  与 4h（2524/2524），用于验证统计页与练习揭晓。

### 28.2 修掉的 bug

1. **前端调用不存在的接口（每页 404）**
   - 现象：每个页面控制台都有 `GET /api/opportunity-notifications → 404`（右栏机会通知轮询）。
   - 根因：`apps/platform-web/lib/api.ts` 定义并使用了 6 个接口
     （list / summary / scan-now / simulate / {id}/read / {id}/retry），后端从未实现，
     `alert_events` 表虽已具备全部投递字段（status/attempts/next_retry_at/sent_at/last_error/is_read）。
   - 修复：新增 `apps/platform-api/app/api/opportunity_notifications_api.py`（`alert_events` 的投递视图，
     不新增表，投递/重试复用 `monitor_engine`）+ 注册路由 + `tests/test_opportunity_notifications_api.py`（7 项，
     含「前端 6 条路径必须存在」的回归断言）。

2. **练习页「揭晓结果」永远 0/N**
   - 现象：`已揭晓 0/57`，事件卡片始终显示「（该事件还没有结果标注）」。
   - 根因：练习页 `getEvents({ symbol, timeframe, with_outcomes: true, limit: 2000 })` 不带时间窗，
     而 `/api/events` 按 `confirmed_at` **升序**再截断 limit；库里 4h 有 2914 条事件，
     返回的是最早 2000 条，而练习窗口是最近 800 根 K 线 → uid 一条都对不上。
   - 修复：`app/(main)/practice/page.tsx` 传 `start/end`（按 `bar_time` 过滤）+ limit 5000，
     只取当前窗口内事件。真机复验：点击揭晓 → `已揭晓 1/57`。

3. **胜负口径前后端不一致（顺带修）**
   - 后端 `summarize(exclude_censored=True)`：`wins = r_multiple > 0`，截尾样本不计入。
   - 前端 `revealedStats` 却用 `outcome.won`（= 命中 2R 目标）判胜负，还把 censored 算成负：
     真机实测「结果：censor / +0.42R」被记成「负 1」。
   - 修复：`lib/practiceCore.ts` 新增 `isWinningOutcome`（`!censored && r_multiple > 0`）、
     `PracticeStats.censored` 单列截尾、`nextReviewTime` 只挑真实亏损（排除截尾与盈利）；
     练习页结果文案显示「截尾(未走完)」并按 R 正负着色。测试补 2 项（共 14 项）。
   - 真机复验：连点 8 次揭晓 → `已揭晓 11/57 · 胜 7 负 4 · 截尾 1 · 命中率 63.6% · 平均 0.31R`，
     分指标 `ma.breakout n=8 87.5% +0.64R` / `macd.divergence n=3 0.0% −0.57R`。

### 28.3 固化为可重跑验证

- `scripts/verify_browser.mjs`（Node 内置 WebSocket 直连 CDP，无第三方依赖）：30 项断言 ——
  5 个页面 DOM 渲染 + 每页「JS 异常 0 / API 报错 0」+ 练习页进场→加仓→平仓→揭晓交互 +
  PWA（`Page.getAppManifest`、`Page.getInstallabilityErrors`、SW 注册/激活/接管）+
  断网后 `/practice` 仍渲染且由 SW 接管。失败退出码非 0。
- `docs/runbooks/BROWSER_VERIFICATION.md` + `make verify-browser`；判定口径与已知限制（不覆盖真机 iOS/Android、
  Docker/Postgres 路径需另行冒烟）写在其中。
- 截图存 `reports/browser-verify-2026-10-22/`（watch / stats / library / practice-revealed / chart / offline）。

### 28.4 本轮验收

```bash
cd apps/platform-api && python3 -m pytest -q                     # 752 passed
python3 -m ruff check apps packages scripts experiments labs     # All checks passed
python3 -m pytest packages/chanlun-core/tests -q                 # 6 passed
cd apps/platform-web && npm test                                 # 57 passed
npx tsc --noEmit && npm run build                                # TSC_OK / BUILD_OK
node scripts/verify_browser.mjs --base http://127.0.0.1:3100     # 30/30 通过
```

---

## §29 缠论引擎因果性审计与缓存修复（2026-10-23）

### 29.1 结论：两条链，一条不看未来、一条必须看

| 链 | 产物 | 是否读端点之后的 K 线 | 时间戳 |
|---|---|---|---|
| **信号链** `_collect_causal_points` | `buyPoints*Raw` / `sellPoints*Raw` / `beichiPoints*Raw` / `panbeiPoints*Raw` | **否**：逐根只追加 `bars[t]`，`close_confirm_index(..., limit=t+1)` 限定前缀；分型只在右邻冻结（`n_m-3`）后确认 | 每点 `confirmedT` = 发出该信号的 bar |
| **结构链** `process_inclusions → find_fractals → build_strokes → build_segments → build_pivots` | `strokesRaw` / `segmentsRaw` / `pivots*Raw` / `topMarkers` | **是**：分型要右邻、笔要下一个反向分型、线段/中枢要后续笔 —— 这是缠论定义本身 | 只有端点时间 `t`，**不带 `confirmedT`** |

因此：结构数组**不能**按端点时间当信号用（图上最后一笔/中枢是「截至最后一根的重绘结果」，
`meta.edge` 会标注在途/待确认）；信号一律走信号链，消费端按 `confirmedT` 过滤。

### 29.2 实测（`scripts/audit_chanlun_causality.py`，BTC-USD）

前缀重算 vs 全量按 `confirmedT` 过滤，逐点比对：

| 周期 | 切点 | 因果点 | 泄漏 | 漏报 |
|---|---|---|---|---|
| 1day | 8 | 209 | 0 | 0 |
| 4h | 8 | 1388 | 0 | 0 |
| 1h | 2 | 6088 | 0 | 0 |

确认延迟（`confirmedT - t`，单位 bar）：

- 笔级一/二/三买卖点：2–11 根（**无 0 延迟**）
- 线段级买卖点：20–729 根
- 盘整背驰（笔级）：2–8 根
- `beichiPoints*`（弱速率抢先背驰）：**0 根** —— 定义即「当根收盘入场」
  （`alertT == confirmedT == t`），是口径选择而非未来函数；消费端若把它当
  「收盘前可知」使用，会多算 1 根优势。

### 29.3 修掉的 bug：因果链模块级缓存跨调用不清理

`_loc_val_cache` 以 `id(bars)` 为键且从不清理，`_enforce_cached` 的 `_state` 用默认参数
跨调用持有上一轮的交替分型列表。后果：

1. 长驻进程内存无界增长（每次 `analyze()` 留一份极值表；实测 12 次调用 12 份、1392 条）；
2. `id(bars)` 被回收后的新列表复用时，可能读到**别的序列**的极值价格 → 分型交替判错。

修复：`_ensure_causal_caches(bars)` 在 `(bars 对象, 长度)` 变化时清空两处缓存
（单次 `analyze()` 内新笔/老笔两条链仍共享，跨调用不复用；持有强引用避免 `id` 复用）。
实测缓存从「每次调用 +1 份」变为「恒定一份」，`analyze(4h)` 耗时 33→28 s（无回退）。

### 29.4 回归测试

- `packages/chanlun-core/tests/test_engine.py::test_prefix_recompute_equals_confirmed_slice`
  （600 根合成数据，4 个切点，断言 0 泄漏 / 0 漏报）
- `...::test_caches_do_not_accumulate_across_calls`（缓存随调用次数不累积 + 同输入结果稳定）

### 29.5 本轮验收

```bash
cd apps/platform-api && python3 -m pytest -q                     # 752 passed
python3 -m ruff check apps packages scripts experiments labs     # All checks passed
python3 -m pytest packages/chanlun-core/tests -q                 # 8 passed
python3 scripts/audit_chanlun_causality.py                       # 0 泄漏 / 0 漏报
```

## §30 缠论参数实验室（chanlun-web 前端，2026-10-23）

目标：把「买点是一个点位」落到可调参的界面上——不改 `data-view/*.js` 预计算结果，
在浏览器里按同一套口径**重算** 包含→分型→笔→(线段)→中枢→背驰→一/二/三买，
用滑动条实时观察参数对中枢与买卖点的影响。

### 30.1 一买定义（用户定稿）

> 一买 = 一个**点位**：底背驰成立后，**绿柱（MACD 柱 < 0）第一次开始缩短**那根 K 线的
> **收盘价**就是买点。

实现要点（`js/09-lab-engine.js::trigger`）：

| 口径 | 判定 | 说明 |
| --- | --- | --- |
| `shrink`（默认） | `hist[i] < 0 && hist[i-1] < 0 && hist[i] > hist[i-1]` | 绿柱首次比前一根短（向零轴收敛） |
| `stop` | 缩短一段后 `\|hist[i]\| >= \|hist[i-1]\|` | 「不再缩短」，字面口径 |
| `flip` | `hist[i] > 0 && hist[i-1] <= 0` | 柱体首次翻红 |

- 触发窗口 `trigWindow`（默认 20 根）：背离低点之后多久内找确认柱，超时视为失效；
- 点位取价 `priceMode`：默认 `close`（收盘价，可执行），可切 `low`；
- 背驰判定：同向两段（隔一段反向）比较 MACD 柱**面积**（或峰值），后段/前段 < `divRatio`
  且价格创新低 → 底背驰；一买只取底背驰；
- 三买固定用**笔中枢**判突破（线段级中枢只影响显示），并要求回抽低点距 ZG ≤ `thirdMaxDist`
  （默认 12%），否则视为「远离中枢」不作买点。

### 30.2 可调参数（面板滑动条）

| 分组 | 参数 | 默认 | 范围 |
| --- | --- | --- | --- |
| MACD | 快线 / 慢线 / 信号 | 12 / 26 / 9 | 3–30 / 10–60 / 3–20 |
| 背驰 | 面积比阈值 / 比较方式 | 0.90 / 柱面积 | 0.30–1.50 / 面积·峰值 |
| 一买触发 | 柱体口径 / 取价 / 窗口 | shrink / 收盘 / 20 | 3 口径 / 收盘·最低 / 3–60 |
| 结构 | 成笔间隔 / 分型确认 / 中枢重叠 / 中枢级别 / 三买距 ZG | 4 / 3 / 0 / 笔中枢 / 12% | 2–10 / 0–5 / 0–0.80 / 笔·线段 / 2–30% |
| 显示 | 中枢 / 笔 / 分型 / 买卖点 / 区间 / 背驰 | 全开（分型关） | — |

### 30.3 渲染

- 中枢：半透明矩形 + ZG/ZD 虚线（可切线段级中枢）；
- 笔：细虚线；分型：顶/底小圆点；
- 背驰：两段低点（高点）之间黄色虚线 + 面积比读数；
- 一买：虚线横贯 + 三角 + `一买 <price> · <date>`（**点位**，不是区间）；
- 二买/三买：圆点 + 半透明区间带（二买 `[前低, 回抽低点]`，三买 `[ZG, 回抽低点]`）。

### 30.4 因果性

实验室只吃 `candlesFor('raw')`——复盘态该数组已按 `replayT` 截断，因此实验室在复盘模式下
同样是「无未来信息」的；一买的时间 = 触发柱时间，恒 `> 背驰低点时间`（单测断言）。

### 30.5 文件与验收

```
apps/chanlun-web/js/09-lab-engine.js     # 纯计算(可在 Node 直接 require)
apps/chanlun-web/js/09-params-lab.js     # 面板/图层/事件
apps/chanlun-web/tests/lab-engine.test.mjs
```

```bash
node --test apps/chanlun-web/tests/*.test.mjs     # 19 passed
node --check apps/chanlun-web/js/09-*.js          # ok
```

实测（BTC，默认参数 = 引擎口径，headless Chrome 真机）：
1D 笔 195 / 中枢 32 / 一买 13 / 二买 5 / 三买 5 / 背驰 37；
4H 笔 1271 / 中枢 163 / 一买 112 / 二买 65 / 三买 66 / 背驰 244；
重算耗时 1D 7 ms、4H 17 ms（滑动条实时无压力）。

### 30.6 交互与一致性

- **信号清单**：面板底部列出最近 8 个买点（类型/日期/点位/区间），点击把视口移到该 K 线附近；
- **参数持久化**：参数写 `localStorage['chanlun.lab.params.v1']`，刷新后保留，「重置」恢复默认；
- **MACD 面板同步**（默认开）：实验室的 MACD 参数接管副图（`macdFor` 包装，关闭即回原逻辑），
  否则「绿柱缩短」看到的柱子和买点对不上。实测默认 12/26/9 时实验室 MACD 与预计算值
  最大差 5e-5（仅存储精度），fast=6 时副图与实验室完全一致；
- **重算节流**：输入签名（K 线根数 + 末根时间 + 参数 JSON）不变则只重绘不重算，
  平移/缩放拖动不触发计算；
- **复盘态**：`candlesFor('raw')` 已按 `replayT` 截断 → 实验室在复盘里同样看不到未来。

## §31 复盘交易模块可隐藏（chanlun-web，2026-10-23）

需求：桌面右侧「复盘交易」给一个可隐藏按钮，点完只剩主图 + 副图（量 / MACD），图表占满全宽。

实现（三个入口共用一份状态）：

| 入口 | 位置 | 行为 |
| --- | --- | --- |
| `#sideHideBtn`「⇥」 | 复盘交易卡标题右上 | 收起整列右栏 |
| `#sideToggleBtn`「⇱/⇲ 侧栏」 | 顶部工具栏 | 与上面等价，文本随状态翻转 |
| `#sideShowBtn`「« 复盘交易」 | 收起后浮在图表右上 | 一键展开 |

细节：

- `setSideCollapsed(v)` 统一改 `body.side-collapsed`、同步工具栏文案、清掉 `#volume/#macd` 的
  行内 `width` 后重跑 `alignPanes()`（否则副图还按旧宽度写死 px）；
- 状态写 `localStorage['chanlun.side.collapsed']`，刷新后保持；
- 折叠后主图 1440px、量/MACD 各 1418px（差额为价格轴宽度），副图时间轴仍与主图对齐；
- 移动端（≤760px）同样可用，浮动按钮仍在视口内。

验证（headless Chrome，1440×900 + 390×844）：收起/展开/工具栏协同/刷新保持 4 条路径全过，
按钮命中测试 `elementFromPoint` 返回自身，控制台 0 报错。

## §32 实验室的笔 vs 系统原生笔：口径对照与对齐（2026-10-23）

结论先说：**两者的骨架本来是同一条规则**（顶底交替 + 合并K间隔 ≥4 + 端点随更极端分型延伸），
但实验室多了/少了两处细节，导致笔数不同；把这两处改齐后，**逐笔完全一致**（1D/4H 都比对过）。

### 32.1 逐条对照

| 环节 | 系统引擎（`packages/chanlun-core`） | 实验室（`apps/chanlun-web/js/09-lab-engine.js`） | 是否影响笔数 |
| --- | --- | --- | --- |
| 包含处理 | `fractals.append_merged_bar`：并入后 `while` 回带级联合并，方向每步按 `merged[-3]/[-2]` 重定 | `merge`：只与最后一根合并一次，方向用持久变量 | 本数据上合并K数相同（2307/13615/56269），理论上有边界差异 |
| 分型几何 | 三根合并K双向极值（严格 `>`） | 同 | 否 |
| **收盘确认** | **强制**：`CONFIRM_WINDOW=3`，中K结束后 3 根原始K线内任一根收盘反向突破 | 滑条「分型确认」，**现在默认 3**（0=原文纯几何） | **是，主因** |
| 交替 | `enforce_alternation`：连续同类保留更极端（本地化极值价） | `alternate`：同 | 否 |
| 成笔间隔 | `MIN_STROKE_SPAN=5` → `i-li >= 4`（合并K下标差） | `minGap=4`（同义） | 否 |
| **成笔价格条件** | 向上笔：`顶.high > 底分型所在合并K.high`；向下笔：`底.low < 顶分型所在合并K.low` | 原先：`顶.high > 底的low`（更宽松）；**已改为引擎同式** | **是，次因** |
| 端点延伸 | 同类更极端 → 替换开放端点并延伸上一笔 | 同 | 否 |
| 老笔（old） | `old=True`：间隔 ≥1、无价格条件（仅对照，不入信号） | 无老笔链，只能靠 `minGap=2` 近似 | 仅在老笔口径 |

### 32.2 实测（BTC 1D，3297 根 → 合并 2307 根）

| 配置 | 分型 | 笔 |
| --- | --- | --- |
| 引擎：收盘确认3 + 引擎价格条件 | 808 | **195** |
| 实验室默认（改前：纯几何 + 宽松价格条件） | 1071 | 245 |
| 实验室：分型确认=3，价格条件仍旧 | 808 | 197 |
| 实验室：分型确认=3 + 引擎价格条件（改后默认） | 808 | **195**（逐笔一致） |

4H 同样：1271 笔，端点序列与引擎**完全相同**；1H 5409 笔（数量一致）。
1D 中枢也从 27 回到 32（见下），与原生 `pivotsStrokeRaw` 一致。

### 32.3 本轮同步修复

1. `strokes()` 的成笔价格条件改成引擎同式（参照合并K的 high/low，而不是分型极值本身）——
   否则宽幅合并K会被过度切笔（实测多出 2 笔：2024-11 那段 100372→123218 被拆成 3 笔）；
2. 「分型确认」默认 0 → **3**，与引擎 `CONFIRM_WINDOW` 对齐；滑到 0 即原文纯几何口径（笔 235）；
3. 「中枢重叠」默认 0.30 → **0**，与引擎 `build_pivots` 一致（引擎只要求 `ZD<ZG`，不做重叠比过滤）；
   调大可只看宽中枢（0.30 时 1D 中枢 32 → 27）。

> 注：线段中枢仍是近似（3笔成段），引擎是特征序列法——线段级只影响显示，不影响笔。

## §33 「笔的区别」图解页（chanlun-web，2026-10-23）

背景：§32 用文字解释「原文 / 引擎」两套笔口径，用户反馈看不懂，要可视化。

### 33.1 交付物

| 文件 | 作用 |
| --- | --- |
| `apps/chanlun-web/bi-compare.html` | 对照页（左解说 + 右图表，暗色） |
| `apps/chanlun-web/js/bi-compare.js` | 4 个 tab、示意图、双口径叠加、差异标注 |
| `apps/chanlun-web/bi-compare-data.js` | BTC 1D 全量 3297 根（142 KB，只读） |
| 入口 | 参数实验室面板标题栏「笔的区别?」→ 新标签页打开 |

### 33.2 四个 tab

| tab | 对比 | 窗口 | 说明 |
| --- | --- | --- | --- |
| ① 间隔 | 原文(gap4) vs 图4(gap3) | 2022-05-10 ~ 06-30 | 图3/图4/图5 三张示意图 + 05-26 底→05-31 顶只隔 3 根 |
| ② 收盘确认 | 原文 vs +确认(3根) | 2025-01-28 ~ 03-20 | 3 根收盘没跌破 → 分型被否，窗口内 4 笔 → 2 笔 |
| ③ 价格条件 | 原文 vs +价格条件 | 2023-09-25 ~ 12-20 | 10-31 底 34025 没跌回顶K区间(33751) → 向下笔取消 |
| ④ 全叠加 | 原文 vs 引擎 | 2025-06-01 ~ 07-20 | 3 笔压成 1 笔：100372→108952→105100→123218 ⇒ 100372→123218 |

每页：蓝色=原文、橙色=对比口径、红色粗线 + `≠` 标出差异笔、被「确认」否掉的分型画红圈 ✕；
底部给全历史笔数（原文 245 / 图4 329 / 引擎 195），窗口内给「完整落在窗口内的笔数」。

### 33.3 实现要点

- 只用 `09-lab-engine.js` 的纯函数（`merge/fractals/alternate/strokes`），口径通过参数组合：
  `{gap:4,confirm:0,price:false}` 原文 / `{gap:3}` 图4 / `{confirm:3}` 确认 / `{price:true}` 价格条件；
- 图表用 lightweight-charts 蜡烛图 + 自定义 primitive（`paneViews` + `useMediaCoordinateSpace`）叠画折线，
  不用 line series（笔的端点会重复，line series 不允许重复时间）；
- `bi-compare-data.js` 由 parquet 一次性生成（脚本见 §33.4），页面不读写 `data-view/data.js`。

### 33.4 重新生成数据

```bash
python3 - <<'PY'
import json, pandas as pd
df = pd.read_parquet('data/crypto/BTC_USD_1day.parquet').sort_values('time')
rows=[[int(t),round(float(o),1),round(float(h),1),round(float(l),1),round(float(c),1)]
      for t,o,h,l,c in zip(df.time,df.open,df.high,df.low,df.close)]
open('apps/chanlun-web/bi-compare-data.js','w').write('window.BI_DATA='+json.dumps(rows,separators=(',',':'))+';\\n')
PY
```

### 33.5 验证

headless Chrome 1440×900 / 1280×800 / 390×844：tab 切换正常、示意图 SVG 均渲染（16–21 个子元素）、
全历史计数 245/329/195 与 Node 直算一致、无横向溢出、0 控制台错误。
截图：`reports/lab-2026-10-23/8-bi-{1440,1280,390}.png`、`bi-tab0..3.png`。

## §34 买点对比（bi-compare 第 5 个 tab，2026-10-26）

需求：把一买/二买/三买全部画到前端，直观看到「笔口径不同 → 买点不同」。

### 34.1 页面

`bi-compare.html` 第 5 个 tab「⑤ 买点」，全历史 1D，双层叠加：

| 元素 | 含义 |
| --- | --- |
| ▲ / ◆ / ■ | 一买 / 二买 / 三买 |
| 蓝 | 只在原文口径出现（15 个） |
| 橙 | 只在引擎口径出现（7 个） |
| 白 | 两套都有（16 个，同 bar 只画一个标记） |
| 左表 | 分类型计数：原文 15/8/8=31，引擎 13/5/5=23 |
| 右侧列表 | 22 条差异买点，点击跳到该位置（±55 根） |
| 过滤器 | 一买/二买/三买 勾选 + 只看差异；标记总数 38 → 29 → 22 |
| 范围 | 全部 / 近 3 年 / 近 1 年 |

### 34.2 实现要点

- 买点参数固定（MACD 12/26/9、area 背驰、shrink 触发、close 价、中枢=笔），只让 `minGap/confirmWin/priceCheck` 随口径变：
  `E.compute(CS, {...MACD, minGap, confirmWin, priceCheck})`，结果按口径缓存；
- 标记画在同一个 primitive 里（`state.buys` 非空时只画蜡烛 + 标记，不画笔）；
- 差异判定：`kind + barIdx` 作为键，同键=共有；
- 二买/三买在两套口径里若都存在，bar 完全一致（前面 §33 已验证），所以差异只体现为“有/无”。

### 34.3 近一年实例（2025-08-25 ~ 2026-08-25）

| 日期 | 类型 | 价格 | 归属 |
| --- | --- | --- | --- |
| 2025-08-30 | 三买 | 107,350 | 只在原文 |
| 2025-09-01 | 一买 | 109,237 | 共有 |
| 2025-09-26 | 二买 | 108,620 | 共有 |
| 2026-03-30 | 一买 | 66,797 | 只在引擎 |

### 34.4 验证

headless Chrome 1440×900：5 个 tab 正常、买点 38 个标记、过滤器 38→29→22、切回笔 tab 恢复折线且
`state.buys` 清空、0 控制台错误。截图：`reports/lab-2026-10-23/9-buys-all.png`、`9-buys-1y.png`。

## §35 bi-compare 副图 MACD（2026-10-26）

需求：主图下方补 MACD 副图，右侧每个 tab 都能看到（一买的「背驰 + 绿柱首次缩短」就在这副图里）。

### 35.1 实现

lightweight-charts v4 没有多 pane，用「独立 priceScale + scaleMargins」在同一张图里切出下带：

| 系列 | 参数 |
| --- | --- |
| 蜡烛 | `scaleMargins: {top: 0.05, bottom: 0.32}` → 占上方 5%~68% |
| 柱 | `priceScaleId:'macd'`，涨 `rgba(38,166,154,.62)` / 跌 `rgba(239,83,80,.62)`，`base:0` |
| DIF | 同 scale，`#cbd5e1` 1px |
| DEA | 同 scale，`#f59e0b` 1px |
| 零轴 | 同 scale，虚线 `rgba(110,123,139,.45)` |
| macd scale | `scaleMargins:{top:0.72,bottom:0.02}`、`visible:false`（不占右侧刻度） |

数据来自 `LabEngine.macd(closes,12,26,9)`，与参数实验室同一份实现，不额外引依赖。
左上角 `.macd-tag`（68.5% 处）标注 `MACD(12,26,9) DIF DEA 柱`。

### 35.2 验证

1440×900 / 1280×800 / 390×844：副图都在下带（柱状像素 teal/red 均 >1900），蜡烛区仍在 68% 以上，
无横向溢出、0 控制台错误；5 个 tab 切换正常。截图 `reports/lab-2026-10-23/10-macd-*.png`。

## §36 切品种后参数实验室串品种（chanlun-web，2026-10-26）

**现象**：ETH 页面上，参数实验室面板仍是 BTC 的笔/中枢/买卖点（badge `笔195 中枢32 1买13/2买5/3买5`，
最近信号价 66797/108620 这种 BTC 价位），叠图也按 BTC 价位画。

**两个原因叠加**：

1. `switchSymbol()`（js/00-app-core.js）重载 `data_<sym>.js` 后只走 `refreshAll()`，
   **从不重建实验室结果** —— `LAB.result` 还是上一个品种。
2. 就算重建也会被缓存挡住：`labRecompute()` 的签名只有
   `cs.length + 末根时间 + 参数`，**不含品种/周期**。BTC 与 ETH 同为 3297 根、
   末根都是 2026-08-25，签名完全相同 → 直接 `labRepaint(); return;`。
   这也是"切品种后连可见范围变化触发的自动重算都救不回来"的原因。

**修复**：

- 签名改为 `currentSym + ':' + activeTF + ':' + cs.length + ':' + 末根时间 + ':' + 参数`。
- `switchSymbol()` 在换完 `DATA` 后：`LAB.result = null; LAB._sig = null;`，
  若 `LAB.on` 则立即 `labRecompute()`。
- 实验室面板标题带品种（`参数实验室 · ETH`），切品种同步更新，避免误读。

**验证**（headless Chrome，1440×900）：

| 动作 | 实验室 badge | 末根收盘 |
|---|---|---|
| BTC 开实验室 | 笔195 中枢32 1买13/2买5/3买5 背驰37 | 80549.3 |
| 切 ETH | 笔222 中枢33 1买20/2买8/3买6 背驰42 | 2500.06 |
| 切回 BTC | 笔195 中枢32 1买13/2买5/3买5 背驰37 | 80549.3 |

1D→4H 切换本来就没问题（根数不同，签名天然不同）：4H 下 1271 笔 / 163 中枢。

**边界**：生产层（烘焙的 `data-view/data.js` / `data_eth.js`）一直是对的——
ETH 1D 自有 222 笔 / 33 中枢 / 27 买点，`buysFor('raw','stroke').length === 27`，
切品种后 `DATA.meta.symbol` 与 `统计` 卡（收盘 2,500.1）都正确。
串品种的只有实验室这套前端重算结果。

**仍未解决**：实验室的买点口径与生产不同（ETH：生产 27 个；实验室 20/8/6=34），
见 §34 与 2026-10-26 讨论，待"生产口径开关"或重写 JS 买点逻辑。

## §37 副图关掉后再打开不显示 / 页面卡死（chanlun-web，2026-10-26）

**现象**：点 MACD / 量 的按钮把副图关掉，再点开时副图**空白**；有时整个页面直接卡死，
按钮点了没反应。

**根因（两层）**：

1. **隐藏方式把容器压成 0×0**。原来用 `el.style.display = 'none'`。lightweight-charts 的
   时间轴按容器宽度算 `barSpacing`，宽度为 0 时退化成 NaN/Infinity，渲染线程空转
   （主线程被占满 → CDP 的 evaluate 也进不去，表现就是"点了没反应"）。
   即使没卡死，图表的时间轴/可见范围已经被写坏，重新显示时就是一片空白。
2. **三面板时间轴同步没有重入锁**。`03-charts.js` 的 `syncAll` 订阅在三条图上，
   彼此 `setVisibleLogicalRange` 互写；被隐藏的面板宽度为 0，回写的范围会被钳成
   退化值再传播给主图 —— 手机端 `charts.js` 有 `syncing` 锁，桌面端漏了。

**修复**：

| 改动 | 位置 |
|---|---|
| 副图隐藏改"移出视口"：`#volume.pane-off / #macd.pane-off` = `position:absolute; left:-20000px; visibility:hidden`，容器尺寸不再归零，主图吃掉腾出的空间（608→798px） | `css/main.css`、`mobile/css/app.css` |
| 开关不再写 `display`，改切 `pane-off` 类；重新显示时强制重排 + 按主图范围对齐一次 | `js/07-main.js`、`mobile/js/experience.js` |
| 副图图表关掉 `autoSize`，由 `alignPanes()` 显式给宽高（隐藏中跳过，不再对 0×0 做 resize） | `js/03-charts.js`、`js/07-main.js`、`js/00-app-core.js` |
| `syncAll` 加重入锁 + 过滤非有限/退化范围 + 跳过离屏面板 | `js/03-charts.js`、`mobile/js/charts.js` |

**验证**（headless Chrome 1440×900，新建标签页先开 `Network.setCacheDisabled` 再导航）：

- 双副图连开连关 4 轮：不卡死；三图可见范围恒为 `[1218, 3301]`；
  副图尺寸恢复为 volume 89×88 / macd 99×98，与初始一致。
- MACD 副图关开前后**像素级 0 差异**；整个 `.chart-box` 也是 0 差异（含时间轴对齐）。
- 场景：单开单关、关着副图切 4H、关着副图进复盘、切品种、折叠侧栏、缠论总开关 —— 均正常。

**踩坑记录（重要）**：验证脚本一开始用 `/json/new?<url>` 直接开页面，此时页面已开始加载，
`Network.setCacheDisabled` 到达太晚 → 读到的是浏览器缓存里的旧 CSS/JS，
所以同一操作"时好时坏"。以后所有前端验证都改成
`/json/new?about:blank` → 开 Network/禁用缓存 → `Page.navigate`。
**用户端更新后需要强制刷新一次（⌘⇧R）**，否则仍会跑旧脚本。

**未验证**：移动端只做了逻辑对齐与语法检查，本机模拟视口下移动端图表高度恒为 0，
未能做渲染验证，待真机确认。

## §38 均线斜率高亮（chanlun-web，2026-10-26）

**需求**：给每条均线一个斜率滑块，斜率落在设定区间内的均线段在图上加粗；拖滑块实时变化。

**斜率口径**：`slope = (MA[i] − MA[i−w]) / w / MA[i−w] × 100`，单位 **%/根**（每根 K 线均线变动百分比）。
用百分比而不是价格差，换品种、换周期量级可比。`w` = 计算窗口，默认 3 根，可调 1~10。

**判定模式**（下拉）：

| 模式 | 含义 | 用途 |
|---|---|---|
| 斜率 < X | 均线向下走弱 | 找下跌段 |
| 斜率 > X | 均线向上走强 | 找上涨/进攻段 |
| \|斜率\| < X | 均线走平 | 找横盘/蓄势 |
| \|斜率\| > X | 均线陡峭 | 找加速段 |

**交互**：

- 工具栏 `📐 斜率` 按钮 = 高亮总开关，**默认关闭**（不改变原有观感）；点开时面板贴着按钮弹出，再点关闭。
- 面板每根均线一行：勾选框 + `MA20/60/120`（用该线颜色）+ 滑块（−2.00 ~ +2.00，步长 0.02）+ 当前阈值 + 命中根数。
- 顶部还有：条件下拉、窗口滑块、`归零`（阈值全 0）、`×`（只收面板，高亮保留）。
- 面板标题栏实时显示 `斜率 < X · 高亮 4558 根`。

**实现**：

- 新文件 `js/10-ma-slope.js`；用 `candleSeries.attachPrimitive` 挂一个 pane view，
  只对命中的连续段描边：基础线 1.4px，高亮段 3.0px + `shadowBlur 4`，颜色沿用该 MA 的颜色。
- **只画可见逻辑区间（±2 根）**：4H 有 19754 根，全量逐点做 `timeToCoordinate`/`priceToCoordinate`
  会让拖动掉到 ~10fps；限定窗口后单次拖动（输入 + 两帧）从 103ms 降到 55ms。
- `rebuildMA()` 末尾调用 `maSlopeRefresh()`：切品种/周期/原始-合并视图/复盘步进/改 MA 周期后
  斜率数组自动重算，各 MA 阈值保留。
- MA 总开关（`#overlayToggle` 的 MA）关掉时不画。

**验证**（headless Chrome 1440×900，先禁缓存再导航）：

| 场景 | 结果 |
|---|---|
| 默认（未点按钮） | 无高亮；三色像素数 = 基线 1448 / 1927 / 1803 |
| 点开（阈值全 0） | 高亮 4558 根；像素 2722 / 6058 / 3200 |
| 再点关闭 | 像素精确回到基线 1448 / 1927 / 1803（可逆） |
| MA60 阈值 −2 / 0 / +2 | 命中 0 / 1511 / 3221 根（全量 3238） |
| 模式 `|斜率|<0.1` | MA60 580 根，MA20/120 为 0（其阈值为 0） |
| 窗口 3 → 10 | 计数 580 → 614，重算生效 |
| 切 ETH | 394 根，跟随品种重算 |
| 复盘 2025-06 | 数据截断到 2846 根，命中 495 |
| 合并K线 | 2307 根，高亮正常（1661 像素） |
| MA 周期改 10,30 / 清空 / 恢复 | 行跟着变 / 显示提示不报错 / 恢复 3 行 |

**已知边界**：移动端 `mobile/` 未接（那边有独立图表与 MA，需要另写一份或抽公共模块）；
当前每根均线只有一个阈值 + 模式，没有「区间上下限」双滑块。

## §39 日线 MA 突破 × 斜率：图表标记 + 牛市覆盖（2026-10-26）

**研究结论**（BTC 1D，3297 根，2017-08-17→2026-08-25，详见 `reports/ma-slope/研究-日线MA突破与斜率.md`）：

1. **突破本身没有方向性**：同一根均线按斜率分桶后，突破后 60 日均值从 −15% 到 +32%。
2. **斜率越陡的突破越差**：MA20 斜率 ≥0.3 的 24 次均值 −1.2%/胜率 33%；MA120 ≥0.3 的 5 次全亏。
3. **年线走平/微跌时被收复最好**：MA200 斜率 −0.15~0 的 10 次，60 日均值 +31.6%，胜率 90%。
4. **MA60 穿越噪音最大**：各桶均值为负或接近 0 —— 适合当持仓过滤，不适合当入场信号。

**覆盖牛市的口径**：持仓 = 收盘 > MA50 且 MA50 的 5 根斜率 > −0.15 %/根，否则空仓。
95.8x / 最大回撤 −55% / 持仓 47% / 70 次入场；19 段 ≥30% 牛市段里 **15 段捕获 ≥50%，这 15 段占全部牛市涨幅的 92%**。

**图表实现** `js/11-ma-trend.js`：

- 工具栏 `📈 趋势` 按钮 = 开关（默认关）；面板滑块：均线 10~200、斜率窗口 1~20、斜率阈值 −0.60~+0.40、持仓底色开关。
- 两个 pane view：底色 `zOrder:'bottom'`（画在 K 线下面，不遮挡蜡烛），买卖点 `zOrder:'top'`。
- ▲ 绿色 = 入场（`pos[i]` 由 0→1），▼ 红色 = 出场；只画可见逻辑区间 ±2 根。
- 面板实时统计：入场次数、持仓占比、覆盖牛市段（捕获 ≥50% 的段数 / 总段数）、≥30% 捕获、分段捕获中位数、当前是否持仓。
- `rebuildMA()` 钩子 `maTrendRefresh()`：切品种/周期/视图/复盘步进后重算（牛市段用同一批收盘价重跑 zigzag）。

**验证**（headless Chrome，先禁缓存再导航）：

| 项 | 结果 |
|---|---|
| JS 与 Python 口径一致性 | 入场 70 次、持仓 47%、15/19 段 ≥50%、中位捕获 63% —— 与研究报告完全一致 |
| 底色像素 | 开启 +287k 像素、关闭回到基线；K 线颜色计数不变（7722 / 6467）→ 底色确实在 K 线之下 |
| 买卖点 | 绿 ▲ 1155px、红 ▼ 1014px（1280×898 图表区） |
| 滑块 | 均线 20 / 窗口 10 / 阈值 0.10 / −0.30 都实时改统计与标记 |
| 切 ETH | 133 次入场、16/19 段，跟随品种重算 |
| 复盘 2024-01 | 数据截断到 2329 根，统计重算 |
| 开关可逆 | 底色开→关→开两次截图逐像素一致 |

**局限**：单标的、样本内、无手续费滑点；MA50 是全网格峰值（MA40 81x / MA60 47x），别当最优参数；
最大回撤 −55% 真实存在（2021 顶部与 2025-10→2026-06）；不做空，53% 时间空仓无收益。

## §40 MA20/60 双均线：金叉死叉统计 + 信号 K 线闪烁（2026-10-26）

**研究结论**（BTC 1D 3297 根，详见 `reports/ma-slope/研究-双均线MA20-60.md`）：

- 金叉买 / 死叉卖：**32 笔、胜率 47%、中位 −1.4%、盈亏比 4.47、均值 +19.7%**、持仓中位 32 天、复利 15.5x。
- 典型趋势跟随画像：**5 笔大赢占全部正收益 86%**，去掉这 5 笔剩下 27 笔均值 −2.4%。
- 金叉事件本身只有短线效应：金叉后 5 日 +3.15%/上涨 59%，60 日 **−1.16%/上涨 38%**。
- **K 线关系**：金叉当根实体 >4% 或振幅 >8% → 胜率 0~25%、均值 −6.5%~−11.9%（追高）；
  实体 1~2% 胜率 58%；收盘在区间 0.5~0.8 胜率 62%/中位 +4.3%；
  缩量金叉（量比<0.8）胜率 62%，放量（>2 倍）2 笔全亏。
- 加"MA60 斜率>0"过滤反而变差（11 笔，盈亏比 0.85）。

**图表实现** `js/12-ma-dual.js`：

- 工具栏 `📊 双均线`（默认关）；滑块：快线 5~60、慢线 20~200；离场：死叉 / 跌破快线 / 跌破慢线。
- 信号 K 线画圆角框（买绿/卖红）+ 三角；**闪烁 6 秒**：`setInterval(110ms)` → `requestUpdate()`（~9fps，
  不用 60fps rAF，避免全图重画吃满 CPU）；「重播」再闪一次，勾选可常闪。
- 框宽随 `timeScale().options().barSpacing` 自适应（5~26px），只画可见区间 ±2 根。
- 面板统计：笔数/胜率/盈亏比/平均/中位/平均盈亏/平均持仓/复利。

**验证**（headless Chrome，先禁缓存再导航）：

| 项 | 结果 |
|---|---|
| JS 与 Python 一致性 | 32 笔、胜率 47%、盈亏比 4.47、均值 +19.7%、中位 −1.4%、平均盈 +54%/亏 −10.7%、复利 15.5x —— 完全一致 |
| 闪烁 | 闪烁中两帧差 2292 / 1615 px；6 秒后定时器自动停、连续两帧 0 差异；勾持续闪烁后 1748 px |
| 标记可见性 | 150 根放大视图下开/关标记差 46.5k px（4.05%） |
| 参数联动 | 改快慢线/离场规则实时重算统计与框位置；切品种/周期/复盘后由 `rebuildMA` 钩子重算 |

## §41 标注模式：人工复判实验信号（2026-10-26）

目标：**规则先出点，人来裁决**。把已经做过的实验信号逐个过一遍，回答"这单我会不会做"，
用人工判断反推哪些条件真有 edge，最终收敛成个人交易体系（而不是继续堆策略）。

**实现** `js/13-annotate.js` + 工具栏 `🏷 标注`（默认关）：

| 能力 | 说明 |
|---|---|
| 实验源 | 6 个：双均线金叉 MA20/60(32) / MA50 斜率趋势(70) / 突破 MA20 且斜率>0(65) / 缠论买点·笔中枢(30) / 缠论买点·线段中枢(1) / 背驰买点·笔中枢(45) |
| 逐个过 | ◀ ▶ 或 ←/→；"只看未标注"自动跳过已判过的；面板显示 第 k/N 与日期 |
| 盲判 | 默认"遮住信号之后"：用背景色 98.5% 覆盖信号右边（主图实测彩色像素 0.79% → 0.02%）；未来 5/20/60 日收益折叠在 `<details>` 里，先判断再点开 |
| 指标快照 | 当日涨跌/实体/振幅/收盘位置/量比；MA20、MA60 值·距离·斜率、双线差；DIF/DEA/柱；最近笔方向；所在中枢；±3 日内的缠论买点 |
| 裁决 | 会做(1) / 不做(2) / 跳过(3)，键盘直选；判定后**不自动跳转**（避免理由记到下一笔） |
| 理由标签 | 12 个 chip 可多选：趋势向上/向下、突破有效、假突破、缩量、放量、大阳追高、均线压制、中枢上沿、背驰、位置太高、等回踩 |
| 备注 | 自由文本 |
| 存储 | localStorage `chanlun.ann.v1`，key = `品种\|周期\|实验\|信号时间`；切品种/周期互不干扰；导出 JSON（`annotations-<sym>-<tf>-<src>.json`） |

**验证**（headless Chrome）：6 个源计数正确（32/70/65/30/1/45）；标注后位置不变、理由挂当前信号；
键盘 1/2/3 与 ←/→ 生效；刷新后判定仍在；ETH 侧 0/26 不串 BTC；4H 源重算 192；
复盘状态下按截断数据重算（2021-01-01 时 11 个信号）。
**已知不足**：遮罩只覆盖主图，副图（量/MACD）右侧仍可见；`缠论买点·线段中枢` 在 1D 上只有 1 个信号（数据本身稀疏）。

## §42 标注模式补 6 个均线实验：单均线突破 / 双均线金叉（MA20·60·120，2026-10-26）

`js/13-annotate.js` 的实验源改成工厂生成，新增两组（下拉用 `<optgroup>` 分组）：

| 分组 | 实验 id | 名称 | BTC 1D 信号数 |
|---|---|---|---:|
| 单均线突破 | ma20 / ma60 / ma120 | 收盘上穿 MA20/60/120 | 185 / 75 / 40 |
| 双均线突破 | x2060 / x60120 / x20120 | 金叉 MA20×60 / 60×120 / 20×120 | 32 / 12 / 15 |
| 其它实验 | trend / slope | MA50 斜率趋势 / 突破 MA20 且斜率>0 | 70 / 65 |
| 缠论 | chan / chan_seg / fast | 买点·笔 / 买点·线段 / 背驰·笔 | 30 / 1 / 45 |

- 旧键 `dual` 自动迁移到 `x2060`（localStorage 里按 `品种|周期|实验|时间` 存的判定不丢）
- 首屏默认实验改为 `x2060`；图表默认均线就是 20/60/120，复判时三条线都在

**研究口径**（收盘确认、次日执行、无手续费；单均线用"跌破本均线"离场，双均线用"死叉"离场）：

| 规则 | 笔数 | 胜率 | 均值 | 中位 | 盈亏比 | 持仓中位 | 持仓均值 | 复利 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 突破 MA20 | 185 | 28% | +2.9% | −2.1% | 2.14 | 4 天 | 9 天 | 27.2x |
| 突破 MA60 | 75 | 24% | +9.0% | −1.7% | 4.28 | 4 天 | 21 天 | 28.6x |
| 突破 MA120 | 40 | 30% | +17.6% | −1.8% | **9.77** | 6 天 | 40 天 | **42.4x** |
| 金叉 20×60 | 32 | 47% | +19.7% | −1.4% | 4.47 | 32 天 | 50 天 | 15.5x |
| 金叉 60×120 | 12 | 50% | **+34.7%** | +2.0% | 5.00 | 135 天 | 129 天 | 6.9x |
| 金叉 20×120 | 15 | 47% | +28.9% | 0.0% | 4.67 | 60 天 | 106 天 | 7.6x |
| 买入持有 | 1 | — | — | — | — | — | — | 18.8x |

要点：单均线突破笔数多、胜率 24~30%、靠盈亏比 2.1~9.8 赚；双均线金叉笔数少、胜率 47~50%、持仓 32~135 天。
`突破 MA120` 复利最高（42.4x）但持仓均值 40 天、最长一笔 246 天；`金叉 60×120` 均值最高（+34.7%）只因 12 笔、进场太晚，复利只有 6.9x。

## §43 标注数据升级为「特征快照」：价格 / 均线 / 关系三类全量落库（2026-10-26）

目的：复核完就能直接做判别分析，不用再事后反推当时看到的指标。

**`annFeatures(i)`**（`js/13-annotate.js`）在**点击判定那一刻**冻结整条特征，随判定一起存进 localStorage：

| 类别 | 字段 |
|---|---|
| 价格 / K 线 | `open high low close chg body range closePos upWick dnWick vol volRatio20 volRatio5` |
| 均线及斜率 | `ma[20/60/120] = {v, dist, slope5, slope10, slope20}`（dist = 收盘/MA−1，slope 单位 %/根） |
| K 线与均线 | `arrange(bull/bear/mixed) above(0~3) streak20 streak60 gap2060 gap60120 priceVsGap2060` |
| 波动 / 位置 | `atr14 atrPct distHigh60 distLow60` |
| 指标 / 缠论 | `dif dea hist strokeDir pivotIn pivotZd pivotZg chanBuys[]` |
| 结果（只用于评估，不作特征） | `fwd5 fwd20 fwd60 mfe20 mae20` |

- 中枢只认**包含当根**的笔中枢，避免把很远的历史中枢当成当前位置
- 导出（`导出` 按钮）→ `annotations-<品种>-<周期>.json`，一次导出**当前品种+周期下所有实验**的判定，`schema: chanlun.ann/2`
- 旧数据没有 `features` 时，导出会按当前数据补算
- 验证：3 笔跨实验标注 → 导出 3 条，字段齐全（样例 `reports/ma-slope/标注导出样例.json`）

## §44 标注遮罩补全副图 + 隐藏右侧价格轴（2026-10-26）

**为什么之前遮不住副图**：遮罩是挂在序列上的绘制层（lightweight-charts `ISeriesPrimitive`），
我只把它挂在主图的 `candleSeries` 上；量能（`volumeChart`）和 MACD（`macdChart`）是**三个独立的图表实例**，
各有自己的画布、时间轴与坐标系，主图的绘制层画不到它们上面 —— 不是遮不住，是没挂。

**修复**：

- `AnnPrimitive(series, chart, mode)` 增加 chart/mode；用各自 `chart.timeScale().timeToCoordinate()` 换算 x
- `annInit()` 同时挂三个：`candleSeries`(main，画遮罩+黄框+标签)、`volumeSeries`(shade)、`macdHistSeries`(shade)
- `annDraw()` 遍历 `ANN._prims` 全部 `requestUpdate()`
- 顺带修掉一个更隐蔽的泄露：**右侧价格轴上的"最新价"就是未来价**。现在 `annApplyBlind()` 在遮罩开启时
  把三个图的 `priceScale('right').visible` 设 false，关闭遮罩/收起面板时恢复

**验证**（1600×1000，信号 x=964）：

| 区域 | 遮罩关 | 遮罩开 |
|---|---:|---:|
| 量能副图右侧彩色像素 | 23.68% | **0.00%** |
| MACD 副图右侧彩色像素 | 3.83% | **0.00%** |
| 主图右侧彩色像素 | 1.26% | 0.39%（只剩黄框和标签） |
| 价格轴可见性（三图） | true/true/true | false/false/false |

步进/切实验/切品种后遮罩跟随新信号；收起面板后副图与价格轴全部恢复。

## §45 修复：点「标注」后整张图变黑（2026-10-26）

**现象**：点 `🏷 标注` 后主图/副图全被盖住，像图挂了。

**根因链**：

1. 程序启动时视图停在最近约 1200 根（`[1218,3301]`），而第一个信号在 2018 年（index 256）。
2. `annGo()` 会 `setVisibleLogicalRange` 跳到信号，但紧接着 `annApplyBlind()` 隐藏右侧价格轴触发一次重排，
   把刚设的范围又还原回 `[1218,3301]` —— 信号仍在视野外。
3. 遮罩按"信号右边全盖"计算：`x = timeToCoordinate(信号)` 得到 **−16808**，
   `fillRect(x+2, 0, W−x, H)` 的宽度变成 `W + 16808` → **整屏涂满**。

**修复**（`js/13-annotate.js`）：

- 遮罩只在 `0 ≤ x ≤ 画布宽` 时绘制，并且 `fillRect` 的起点/宽度都夹在画布内（永远不可能铺满整屏）
- `annGo()` 设置范围后，在 `requestAnimationFrame` 和 160ms 各补设一次，抵消隐藏价格轴引起的重排
- 打开面板时先隐藏价格轴，下一帧再 `annGo`，避免互相覆盖
- 信号不在这一屏时**不遮罩**，只画一行提示："← 信号 K 线在左侧视野外，按 ←/→ 回到信号"
- 四个新脚本加 `?v=20261026b` 缓存版本号，普通刷新就能拿到新代码（不必非按 ⌘⇧R）

**验证**（1440×900，默认视图 `[1218,3301]`）：

| 场景 | 结果 |
|---|---|
| 点标注 | 视图跳到 `[76,301]`，信号 x=894 在屏内，左半区彩色 3.55%（K 线可见）/ 右半 0.82%（已遮） |
| 手动滚到最右（信号在屏外） | 左半 4.21% / 右半 1.23%，**全图可见** + 顶部提示（修复前是 0.02%/0.07% 全黑） |
| 步进到第 4 笔 | 视图跟随，信号 x=894 |

## §46 标注模式改版：右侧抽屉 / 分色下拉 / 「不做」必填理由 / ATR%(14)（2026-10-26）

1. **右侧抽屉**：`#annDrawer` 从浮动面板改成 `.layout` 里的右侧栏（`aside`），收起时 34px 竖排「🏷 标注」，
   展开 380px 经 `flex` 挤压主图（主图 1246 → 900，不遮挡）。`Esc` 或 `»` 收回；工具栏按钮同步高亮。
2. **分色下拉**：原生 `<select>` 在 macOS 里无法给 option 上色，改成自定义菜单：
   单均线突破·蓝 / 双均线突破·橙 / 其它实验·绿 / 缠论·紫，每项右侧带该实验当前信号数。
3. **「不做」必须留理由**：判定为 `no` 且既没选标签、也没写备注时，红框闪烁 + 提示，不写入；
   「会做/跳过」不受限。
4. **ATR% → ATR%(14)**：即"过去 14 根 K 线的平均真实波幅 ÷ 收盘价"，带 tooltip；
   导出字段 `ctx.atr14`（绝对值）与 `ctx.atrPct`（占比）。

**顺带修掉一个 alinePanes 冲突**：盲判原先把三张图的 `priceScale('right').visible` 设 false，
隐藏后 `priceScale('right').width()` 返回 **0**，`alignPanes()` 的 `target = Wm + ps` 算不出来 → 直接跳过，
副图卡在旧宽度（主图 900px 时副图仍 1258px，右侧被裁）。
改为**用 DOM 覆盖层** `#annBlindCover` 盖住右侧价格轴（`bottom:30px` 避开时间轴），不动图表布局：
价格轴宽度保持 56px，副图宽度 884 = 828+56 与主图一致，轴上文字被盖住（轴区非背景像素 3973 → 120）。

**验证**（1600×1000）：抽屉 34 ↔ 380px、主图宽度随之 1246 ↔ 900；下拉 11 项分 4 组带计数；
不做无理由被拦下并提示、补理由后写入；切 ma120 后信号数 40；`ATR%(14)` 显示 6.00%。

## §47 标注抽屉独立 + 视觉改版（2026-10-26）

**问题**：抽屉挂在 `.layout` 里，会被 `body.side-collapsed .layout { flex-direction: column }` 影响，
位置跟着「复盘交易」侧栏走，看起来像它的子面板。

**改法**：`#annDrawer` 挂到 `body`，`position: fixed; right: 0; top: <header 高度>; bottom: 0; z-index: 80`，
收起用 `transform: translateX(100%)` 滑出；打开时 `body.ann-open { }` 让 `.layout` 让出 `396px`
（`margin-right`）。于是它与复盘交易侧栏完全解耦：侧栏收起后图表变成 1204px 宽，抽屉仍在 1204~1600 不动。

**视觉改版**：

- 分四张卡片：K 线 / 均线·斜率 / 位置·MACD / 未来收益，卡片有底色、圆角、左侧色条小标题
- 底部「判断区」常驻不滚动：理由 chips（12 个，最多 84px 高，可滚）、备注、会做/不做/跳过三个大按钮、统计行
- 涨跌统一配色：**涨 = `--up` 绿、跌 = `--down` 红、平 = 灰**（与图表 K 线一致）
  - 当日涨跌、实体（按阴阳）、均线距离、均线斜率、MACD 柱、最近笔方向、未来 5/20/60 日、20 日 MFE/MAE
  - 中性值不着色：振幅、收盘位置、量比、ATR%、距 60 日高/低
- 表格头、胶囊标签（实验名）、按钮 hover/选中态都做了区分

**验证**（1600×1000）：抽屉 x=1204/宽 396，主图 884（侧栏开）或 1204（侧栏收）都不重叠；
面板 4 张卡片、12 个理由 chips、foot 214px 固定在底部、正文区 643px 可滚；绿色 12 处、红色 3 处。

## §48 标注面板里的均线配色与图表一致（2026-10-26）

`annMaColor(p)` 读 `MA_COLORS` + `#maPeriods` 的当前顺序，按"周期在第几位"取色，
所以面板里的 MA20/MA60/MA120 与图上 K 线叠加的均线颜色永远同色；
改均线周期顺序（如 `60,120,20`）后，面板色点跟着换位。

- 色点 + 数值同时上色：MA20 `#2962ff` 蓝 / MA60 `#ff9800` 橙 / MA120 `#9c27b0` 紫
- 「距离」「斜率」仍按涨跌红绿，不与均线身份色混淆

验证：面板 `.ma-dot` 与图例 `#legendMa i` 的 computed color 完全一致
（rgb(41,98,255) / rgb(255,152,0) / rgb(156,39,176)）；把周期改成 `60,120,20` 后色点同步换位。

## §49 标注：按钮常亮 + 自动保存可感知（2026-10-26）

- **按钮常亮**：`annRender()` 末尾按当前信号已有判定给 `.ann-btn` 加 `.on`，
  会做=绿实心、不做=红实心、跳过=灰实心；翻到别的信号自动换/灭。
- **没有保存按钮，因为每次都存**：`annMark()` → `annSave()` → `localStorage['chanlun.ann.v1']`，
  同时底部闪一行 `✓ 已保存 20:54:45`；写失败（隐私模式/配额满）显示 `⚠ 保存失败`。
- **备注边打边存**：原来只在 `change`（失焦）时写，输入后直接刷新会丢字；现在 `input` 0.35s 防抖即写。
- **存储键含品种|周期|实验**：`${symbol}|${tf}|${source}|${time}`，切品种/周期/实验互不覆盖，切回来仍在。

实测：标记「会做」→ 按钮 `on=true`；输入备注不 blur，700ms 后 localStorage 已含该备注；
整页刷新后 `已标注 1/32`、备注、按钮高亮全部恢复；切 ETH 显示 `0/26`，切回 BTC 仍 `1/32`。

## §50 标注标签重做 + 新特征 + MA20 标注分析（2026-10-27）

- `ANN_REASON_GROUPS` 取代旧的 `ANN_REASONS`：19 个标签分「不做 / 会做」两组，
  全部来自 `annotations-BTC-1D.json` 里 127 条备注的真实说法，旧通用标签（趋势向上/假突破/缩量…）全删。
  chips 渲染带分组小标题 `.ann-chip-sep`。
- `annFeatures()` 新增：`price.volShrink`（前5日均量÷前20日均量，<1 缩量）、`ctx.pullback20`（距20根最高点回撤）；
  面板新增「缩量比」「距20日高」「距上一个/下一个信号 N 根」。
- 分析报告：`reports/ma-slope/分析-标注MA20-2026-10-27.md`
  （184 笔标注 + 真实 MA20 出场回测：会做 51.99x/PF5.37 vs 不做 0.54x/PF1.11；
   发现"上笔向上仍会做"的 14 笔全亏、"力度不够"37 笔其实是正期望 1.48x）。

## §51 ZZY-MA突破 信号源（2026-10-27）

- `ANN_ZZY` 参数对象 + `annZzyBuild()`：首次站上 MA20 + 阳线 + 距MA20≤5% + 量比≥0.9 +
  上笔向下 + 距MA120<15% + 排除(距MA60>6% 且 MA120斜率>0.4) + 间隔≥5 根；A/B 分档。
- 注册为标注源 `zzy`（group「我的规则」，粉色 `g-zzy`），BTC 1D 47 个信号（A 25 / B 22）。
- 跨品种验证：BTC 1D PF 5.45 / ETH 1D PF 5.49 / BTC 4H PF 2.53（362 笔，未计手续费）。
- 设计文档：`docs/design/ZZY-MA突破-v1.md`（含边际贡献、敏感性、待确认项）。

## §52 标注操作区: 上/下一个待标注 + 会做/不做/跳过 同一行（2026-10-27）

- 底部操作区改成 5 列 `42px 1fr 1fr 1fr 42px`：`◀ 上一笔` ｜ 会做 ｜ 不做 ｜ 跳过 ｜ `▶ 下一个待标注`
- 三个判定按钮放大：高度 47px（原 ~30px）、字号 14px、圆角 8px；`▶` 用蓝色强调
- `▶` / `→` 走 `annGotoNext()`：**自动跳过已判定的信号**，到末尾回绕；全部标完提示"可以导出了"
- 位置指示 `12/47` 移到顶部开关行右侧；`←` 仍是上一笔（逐笔回退，便于回看已标注的）
- 验证：已标 1/2/3 后从第 1 笔点 `▶` 直接到第 4 笔；3 个信号全部标完时提示正确

## §53 ZZY v2: 实体/间距下限 + 突破低点止损（2026-10-27）

- 47 笔复核（会做 27 / 不做 20，精确率 57.4%）后定位假阳性模式：**贴着 MA20 的小阳线**
  （不做组 距MA20 中位 0.74%、实体 1.92%；会做组 2.20%、4.00%）。
- `ANN_ZZY` 增 `minBody=0.02`、`minDist=0.015`、可选 `maxUpWick`（默认关，实测无区分度）；
  A 档改为 量比≥1 & 收盘位置≥0.7 & 实体≥3%。信号 47 → 21（A 17 / B 4）。
- 止损口径改为「突破 K 线最低点」：47 笔 PF 5.45→6.07、复利 16.83x→18.40x。
- 报告：`reports/ma-slope/分析-ZZY复核与收敛.md`（含 67→47 归因、收敛曲线、
  "被过滤掉的 28 笔仍 PF 6.48"的警告）。

## §54 分歧复核源 + 标注导入（2026-10-27）

- 新源 `dif`（我的规则 / 分歧复核 我 vs 规则）：对比 `ma20` 人工判定与 `zzy` v2 输出，
  输出「漏·你会做」(50) /「错·规则出」(4) /「新·未复核」三类，共 54 个。判定写入独立键，不覆盖旧记录。
- 新增 `annDecisionOf(time, src)` 支持跨源查判定；`annImportData()` + 底部「导入」按钮，
  支持从导出的 JSON 恢复判定（按 `features.time` 对齐 K 线，失败按格式化日期兜底）。
- 导出补 `tag` / `kind` 字段，便于下一轮按分歧类型统计。
