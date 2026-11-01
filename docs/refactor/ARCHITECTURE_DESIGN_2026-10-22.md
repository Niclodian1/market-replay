# 目标架构与实施设计：练习 / 看盘 / 指标统计 / 实验与指标库 / 飞书告警

> 日期：2026-10-22（R5 实验环境 11:21 补充；指标库与发布 15:38 补充） · 状态：设计稿（未改代码）
> 关联：[仓库梳理](REPO_AUDIT_2026-10-22.md) · 前置结论：先抽「指标层」，再谈拆产品

---

## 0. 一句话方案

**把「技术指标」变成系统里唯一的一等公民：指标只定义一次（Python），产出统一的「事件」；
看盘、练习、统计、告警、实验只是同一份事件的五个出口。** 前端不再计算指标语义，只做渲染与状态管理。

```
                       ┌──────────────────────────────┐
   行情数据 ──────────▶ │  indicator-core (Python)     │
   （parquet/DB/API）   │  registry + detectors         │
                       │  chanlun / ma / macd / ...    │
                       └──────────────┬───────────────┘
                                      │  统一产物
                    ┌─────────────────┼──────────────────┐
                    ▼                 ▼                  ▼
             Series（数值）      Event（事件）       Outcome（结果）
             MA/MACD/结构线      买点/背离/突破      +2R/-1R/首触
                    │                 │                  │
        ┌───────────┴───────┐  ┌──────┴───────┐   ┌──────┴────────┐
        ▼                   ▼  ▼              ▼   ▼               ▼
     R2 看盘            R1 练习（双端）   R4 告警（飞书）  R3 统计 + 跳回复盘
                                      ▲
                                      │ 复用同一份 Event 做组合与撮合
                              ┌───────┴────────┐
                              │ R5 实验环境     │  买卖点标记 / 结果页 / 一键删除
                              └────────────────┘
```

**四条需求对应关系**

| 需求 | 主要复用 | 主要新建 |
|------|----------|----------|
| R1 练习解耦 + 指标展示 | `confirmedT` 过滤法、视口锁、练习记账 | `practice-core`（TS 状态机）、`chart-kit`、预计算注释流 |
| R2 看盘 + 同指标 | `/api/chanlun/analyze`、`CandlestickChart`、MA/MACD | 统一 `/api/series`、事件标记图层、收盘增量刷新 |
| R3 历史统计 + 跳转 | `outcome_labeling`（MFE/MAE/首触/窗口）、`mtf_historical_backtest` | `indicator_events` 表、统计 API、`/stats` 页、深链 |
| R4 飞书告警 | `MonitorPlan`、`AlertEvent` 去重/重试、`FeishuSender`（**已实现**） | 指标维度规则、卡片内嵌统计摘要 + 深链、chanlun 入 plan |
| R5 实验环境 + 买卖点 + 易删除 | `strategy_lab_api` 统一信封、`matrix_backtest` 的 Trade/Stats、`MaStrategyChart`/`TradeReplayPlayer`、`experiments/ma_a_atr_entry` 的实验纪律 | `experiment-core`（spec→run→artifacts）、`/lab` 页、删除/清理机制 |
| R6 指标库 + 发布到正式环境 | 现有 registry 的 `params_schema`、统计与告警版本字段 | `indicators` / `indicator_versions` 生命周期、发布门槛、`/library` 页、指标包导入导出 |

**R5 的一句话**：实验是「**配置 + 运行记录 + 产物**」，不是「又一个 700 行脚本」。
这一步同时解决你最初抱怨的「越堆越乱」——因为**可删除的前提是它不是代码**。

**R6 的一句话**：**发布的是「版本快照 + 证据链」，不是一串数据库行**。
你在实验室里试指标，不满意就归档/删除；满意就把某个版本「晋升」到正式环境，
看盘/告警/练习立刻用它，且随时可以下线回滚。

---

## 1. 现状盘点：哪些能直接用，哪些必须新建

### 1.1 可直接复用（不要重写）

| 资产 | 位置 | 为什么重要 |
|------|------|------------|
| 缠论引擎 | `apps/chanlun-web/chanlun.py` + `chanlun_service.py` | 已输出 `confirmedT / why / sub / upper / ratio / tier`，因果信息完整 |
| 检测器协议 | `apps/platform-api/app/core/signal_registry.py` | 已有 `detect()` + `signal_array()` + `params_schema`，正是需要的抽象 |
| 告警状态机 | `monitor_engine.py` + `AlertEvent` | `dedupe_key` 唯一约束、`pending/sent/failed`、重试与退避 |
| 飞书投递 | `notification_channels.py` + `feishu_webhook.py` | 卡片、颜色、图片上传、`/channels/{id}/test`、前端配置 UI **全都有** |
| 结果标注 | `outcome_labeling.py` | 5/10/20/40 窗口、MFE/MAE（R）、首触、censored 语义，可直接用于「胜率」 |
| 因果过滤 | `apps/chanlun-web/js/02-data.js`（`before()`） | 练习中按 `confirmedT` 过滤，且视口硬锁，方案已验证 |
| 数据分片 | `scripts/ops/build_intraday_chunks.py` | 15m 月度分片按需加载，日线/4H 可照抄 |
| 实验信封 | `apps/platform-api/app/api/strategy_lab_api.py` | 已定义「catalog → run → variants(stats+trades) + candles + overlays」的统一返回，前端已有消费方 |
| 交易/统计结构 | `matrix_backtest.py` 的 `Trade` / `CellStats` | 字段设计（入场/出场/原因/MFE/连亏）可直接作为实验结果契约 |
| 实验纪律 | `experiments/ma_a_atr_entry/` | dev/holdout 切分、成本口径、三种执行口径、防过拟合排名——应固化成 spec 字段而非每个脚本重写 |
| 结果渲染 | `MaStrategyChart` / `MaTradeTable` / `TradeReplayPlayer` | 买卖点标记、交易表、逐笔复盘的现成实现，改造为通用组件即可 |

### 1.2 必须解决的问题（现状证据）

1. **两套检测器抽象**：`signal_registry.SignalDetector`（trend/momentum/reversal，仅回测用）与 `monitor_signals.DETECTORS = {ma,macd,structure}`（生产告警用）互不相通；缠论又是第三条独立链路（`_chanlun_scan_job`）。
2. **指标语义三份实现**：Python 引擎、后端适配、前端 TS（`lib/chanlunStats.ts` / `chanlunStrategy.ts`），只靠测试守口径。
3. **练习端没有指标**：`build_mobile_data.py` 明确只保留 OHLCV（`KEEP_TFS`，注释「no chanlun, no minutes」）；桌面练习虽有指标，但只能靠 `data-view/*.js`（单文件 33MB）。
4. **统计能力是「决策级」而非「指标级」**：`outcome_labeling` 以 `FusionDecision` 为锚，无法回答「二买这个指标历史出现几次、胜率多少」。
5. **告警粒度是「计划级」**：`MonitorPlan.strategies` 只有 ma/macd/structure；缠论告警来自环境变量 `CHANLUN_SYMBOLS` 的全局扫描，不能按品种/级别/买卖点类型订阅。
6. **实验是「脚本」而不是「数据」**：`experiments/` 3 个目录 + `scripts/backtests/` 15 个脚本 + `apps/chanlun-web/` 3 个研究脚本，每个都自带 `main()`、`argparse`、`matplotlib`、输出路径。
   结果是：**加一个实验 = 加一个文件，删一个实验 = 删不干净**（产物在 `results/`、`reports/`、`data/*.xlsx` 各一份，路径写死在脚本里）。
   `strategy_lab_api` 虽然已有统一的运行信封，但**运行是瞬时的**：没有 `experiment_id`，关掉页面结果就没了，也没法删除或对比。

---

## 2. 核心契约（先定契约，再写代码）

| 契约 | 作用 | 定义位置 |
|------|------|----------|
| IndicatorSpec | 指标是什么、参数、依赖多少根 K 线 | §2.1 |
| Event | 系统里唯一的事件格式（因果锚点） | §2.2 |
| Series | 数值序列（前端只画不算） | §2.3 |
| Outcome | 一次事件的结果（胜率最小单元） | §2.4 |
| ExperimentSpec / Run / Artifact | 一次实验的完整描述与产物 | §3.6.1 |

### 2.1 IndicatorSpec —— 指标注册项

扩展现有 `SignalDetector`（不新造概念），补三个方法：

```python
@dataclass(frozen=True)
class IndicatorSpec:
    id: str                      # 'chanlun.buy2' / 'ma.breakout' / 'macd.divergence'
    label: str                   # '二买'
    group: str                   # 'chanlun' | 'ma' | 'macd' | 'structure'
    timeframes: tuple[str, ...]  # ('1day','4h','1h')
    params_schema: dict          # JSON Schema，前端自动生成表单
    warmup_bars: int             # 至少多少根才可能出信号（用于增量窗口）
    direction_support: tuple[str, ...]   # ('long',) / ('long','short')
    description: str
    logic: str                   # 判定要点，给 UI 的「为什么」
    schema_version: str

class Detector(Protocol):
    spec: IndicatorSpec
    def detect(self, bars: list[Bar], params: dict) -> list[Event]: ...
    def series(self, bars: list[Bar], params: dict) -> dict[str, list[float | None]]: ...
    def signal_array(self, bars: list[Bar], params: dict) -> list[int]: ...   # 已有，回测用
```

> 关键点：**`series()` 与 `detect()` 必须来自同一份底层数学**。MA 线画在图上、MA 突破事件报警，两者不能各算一遍。

### 2.2 Event —— 系统里唯一的事件格式

```json
{
  "uid": "chanlun.buy2|BTC-USD|4h|1710000000|a1b2c3d4",
  "indicator_id": "chanlun.buy2",
  "symbol": "BTC-USD",
  "timeframe": "4h",
  "bar_time": 1710000000,
  "confirmed_at": 1710432000,
  "first_seen_at": 1710432000,
  "direction": "long",
  "price": 64880.5,
  "strength": 0.72,
  "state": "confirmed",
  "title": "二买 · 4H",
  "evidence": {"why": "一买(…) 后回调不创新低", "sub": "次级别无同向背驰", "upper": "—", "ratio": 0.31},
  "params_hash": "a1b2c3d4",
  "schema_version": "ev1"
}
```

- **`confirmed_at` 是因果锚点**：任何界面（图、练习、告警）只允许在 `cursor >= confirmed_at` 后展示该事件。
- **`first_seen_at` 预留给「当时才知道」的严格语义**：`confirmed_at` 是引擎的说法，`first_seen_at` 是系统第一次观测到它的时间（见 §2.5 审计）。
- **去重键**：`uid = indicator_id|symbol|timeframe|bar_time|params_hash`。

### 2.3 Series —— 数值序列（前端只画不算）

```json
{ "ma20": [null, null, 64200.1, ...], "ma60": [...], "macd": {"diff": [...], "dea": [...], "hist": [...]} }
```

与 bars 等长、`null` 表示暖机期。前端拿到的永远是「已对齐 K 线」的数组。

### 2.4 Outcome —— 一次事件的结果（胜率的最小单元）

```python
@dataclass(frozen=True)
class Outcome:
    event_uid: str
    horizon: int          # 5/10/20/40 根（沿用 outcome_labeling.WINDOWS）
    entry: float          # confirmed_at 那根收盘
    stop: float           # 结构止损：买点最低价；无结构则 entry - 1×ATR(14)
    target: float         # entry + 2R（R = |entry-stop|）
    mfe_r: float          # 窗口内最大有利偏移（R）
    mae_r: float          # 窗口内最大不利偏移（R）
    first_touch: str      # positive_first / negative_first / neither / ambiguous_same_bar
    end_r: float | None   # 到期按收盘计的 R
    matured: bool
    censor_code: str | None
```

> 直接复用 `outcome_labeling._window_outcome` 的判定逻辑（首触及、R 口径、censored），只是把锚点从 `FusionDecision` 换成 `Event`。**不要**另写一套胜率算法。

### 2.5 因果契约：预计算 + `confirmed_at` 过滤，实测等价

练习端要离线可用，就不可能每次都把 Python 引擎搬过去。方案是**构建期预计算全部注释，运行期按 `confirmed_at <= cursor` 过滤**。这个做法是否安全？我在 BTC 真实数据上做了审计：

| 级别 | K 线数 | 采样点 | 前缀重算可见事件 | 全量过滤多出（未来泄漏） | 全量过滤缺失（漏报） |
|------|--------|--------|------------------|--------------------------|----------------------|
| 1D | 3,297 | 8 | 648 | **0** | **0** |
| 4H | 19,754 | 8 | 4,428 | **0** | **0** |
| 1D（12 指标复跑） | 3,297 | 12 | 2,738 | **0** | **0** |

> P0 已把审计固化为 `scripts/audit_causality.py`（可进 CI），并用测试
> `test_full_run_can_be_filtered_by_confirmed_at_equals_prefix_run` 在 1D 上常驻断言。
> 新增的 `ma.breakout` / `macd.divergence` 也纳入同一审计：均线只依赖过去；
> MACD 背离虽「极值 +3 根确认」，但极值本身不依赖未来，因此同样 0 泄漏。

结论：**在缠论买卖点/背驰点上，「全量结果 + confirmedT 过滤」与「前缀重算」逐事件一致**（本次采样范围内）。因此：

- ✅ 练习端可以只带预计算注释，手机离线也能展示同款指标；
- ✅ 无需把 1,969 行引擎移植到 JS；
- ⚠️ 该审计要变成**常驻测试**（每次改引擎跑 20 个采样点，CI 或 nightly），否则这条假设会悄悄失效；
- ⚠️ `first_seen_at` 仍要落库：将来若某类指标（如自适应参数）出现「事后改写」，用它兜底。

**性能实测**（同一台机器）：

| 场景 | 耗时 | 含义 |
|------|------|------|
| 1D 全史 3,297 根 | 0.85 s | 构建/回填可接受 |
| 4H 全史 19,754 根 | 18.9 s | 每品种每级别约 19 s，20 品种≈6 min，可并行 |
| 400 根前缀 | 0.043 s | 告警只需最近 600 根；盘中增量完全够用 |

---

## 3. 模块设计

### 3.1 `packages/indicator-core`（Python，唯一指标事实源）

```
indicator_core/
├── spec.py            # IndicatorSpec / Event / Outcome 数据类
├── registry.py        # register / get / list / run
├── adapters/
│   ├── chanlun.py     # 包 chanlun_service，拆成一/二/三买、背驰、盘整背驰
│   ├── ma.py          # 均线突破/回踩/多头排列
│   ├── macd.py        # 背离（复用 divergence_detectors）
│   └── structure.py   # 三角收敛、SR 突破（现有 detectors 平移）
├── series.py          # MA/MACD/ATR 序列（唯一公式实现）
└── golden/            # 冻结样本 + 期望事件（JSON）
```

**改造动作**
1. 把 `signal_registry.SignalDetector` 升级为本包接口，`monitor_signals.DETECTORS` 与 `strategy_detectors` 作为 adapter 迁入。
2. 缠论 adapter 把 `buyPointsStrokeRaw` 等拆成 `chanlun.buy1/2/3`、`chanlun.sell1/2/3`、`chanlun.beichi.buy/sell`、`chanlun.panbei.buy/sell`，每条事件带 `evidence`。
3. `series()` 统一 MA/MACD/ATR，前端 `lib/indicators.ts` 最终删除（先保留做对比）。
4. 用现有 625 个测试 + 新增 golden（引擎输出摘要哈希）保证不漂移。

### 3.2 `packages/practice-core`（TS，无 DOM）

练习的本质是**带状态的回放**，与渲染无关，所以可以做成纯逻辑包，桌面/手机共用：

```ts
interface PracticeSpec {
  symbol: string; timeframe: TF;
  startTime: number; lookback: number; maxSteps: number;
  capital0: number; seed: number;
  indicators: string[];        // 要显示的指标 id
  drill?: '突破失败' | '震荡止损' | '趋势持有' | '加仓后回撤';
}

class PracticeSession {
  constructor(spec: PracticeSpec, data: CausalDataset, store?: PracticeStore)
  step(n?: number): void
  open(order: Order): void; close(part?: number): void; add(order: Order): void
  mark(kind: 'signal' | 'watch'): void
  reveal(reason: 'manual' | 'max_steps' | 'blowup'): void
  view(): { bars: Bar[]; series: Series; annotations: Annotation[] }  // 已按游标裁剪
  snapshot(): PracticeRecord                                        // 可持久化
}
```

- **`view()` 是唯一出口**：它同时完成「截断 K 线」「裁剪 series」「按 `confirmed_at` 过滤事件」三件事，宿主拿不到未来数据（比现在散落在各访问器里更安全）。
- **盲测**：`view()` 支持 `mask: {symbol:true, time:true}`，时间轴用 `T+n` 相对序号（沿用现有 `trLabel` 思路）。
- **计分**：本金/净值曲线/最大回撤/加仓贡献/R 倍数，语义对齐现有 `06-trainer.js` 的 `capital0 / endCapital / ddActual / ddCf / addPnlRealized`，迁移时可逐字段核对。
- **持久化**：`PracticeStore` 接口（`load/save/list/export`），移动端 IndexedDB、桌面端 localStorage 或服务端同步二选一；数据带 `schema_version` 做迁移。
- **随机性**：所有随机（起点、品种）由 `seed` 决定，便于「错题重练」与回归测试。

### 3.3 `packages/chart-kit`（TS，渲染层）

从现有实现中提炼，避免两端各写一遍：

- 图表工厂：4 面板（K 线+MA / 量 / RSI / MACD），响应式尺寸，触屏手势；
- 图层：MA 线、MACD 柱、缠论笔/段/中枢、买卖点标记、背驰标记、**事件标记（新）**；
- 练习锁：`lockViewport(left, right)` + 硬边界回弹（现有 `enforcePracticeBounds` 的等价物）；
- 持仓层：成本线、止损线、浮盈标签；
- 主题：现有 `THEMES` 双色板。

> 搬运优先级：`apps/platform-web/components/chart/chanlunOverlay.ts`（已验证）→ `apps/chanlun-web/js/03-charts.js` 的图层逻辑 → 删掉重复实现。

### 3.4 `stats-engine`（Python，指标级统计）

流程：`Event → Outcome（多窗口）→ 汇总`，两条数据来源：

1. **回填**：构建期对全史跑检测器，事件入库，标注任务算 Outcome；
2. **增量**：每日收盘后把新增事件追加，标注未成熟样本（`matured=false` 时后续补齐）。

**汇总口径（必须写死并展示）**

| 指标 | 定义 |
|------|------|
| 出现次数 `n` | 去重后的 confirmed 事件数（同 bar 同类型只算一次） |
| 胜率 `win_rate` | `first_touch == positive_first` 占比（目标 2R，止损 1R） |
| 期望 `expectancy_r` | `mean(end_r)`，到期未触及按收盘计 |
| 平均 MFE/MAE | 窗口内最大有利/不利（R） |
| 稳定性 | 按年/按品种分组的胜率极差 + 样本量 |
| 置信区间 | Wilson 95% CI；`n < 30` 时 UI 必须显示「样本不足」 |

> **诚实性约束**：参数一旦用于统计就冻结 `params_hash`；改参数 = 新指标版本，不许混算。这能防住「反复调参直到胜率好看」。

### 3.5 `alert-engine`（在现有 monitor 上扩展）

```
规则(Plan) ──▶ 调度(每 5 min / 收盘触发) ──▶ 取最近 600 根已收盘 K 线
      ──▶ registry.detect(增量窗口) ──▶ 新事件? ──▶ 去重(uid)
      ──▶ 附统计摘要(该指标历史 n/胜率/期望) ──▶ 飞书卡片 ──▶ 记录投递状态
```

**改动清单**
1. `MonitorPlan.strategies` 接受指标 id（`["chanlun.buy2","ma.breakout"]`），`parameters` 传各指标参数；`chanlun` 作为 adapter 接入，废弃 `CHANLUN_SYMBOLS` 全局扫描。
2. `AlertEvent` 增列 `indicator_id`、`event_uid`（或复用 `payload`，推荐加列 + 索引，便于统计与跳转）。
3. 飞书卡片增加两行：
   - `历史：该信号近3年 42 次 · 胜率 61% · 期望 +0.8R`（样本不足则显示「样本不足」）
   - `查看：[图表深链] [练习该次]`
4. 频率控制：同 `uid` 永久去重；同指标同品种加 `cooldown_minutes`（默认 60）。

### 3.6 `experiment-core`（R5，实验环境）

**定位**：R3 是「单指标 + 固定出场」的退化实验，R5 是「多条件组合 + 自定义出场」的完整实验。
两者共用同一套 Event、Outcome、统计口径——**不要在统计页和实验台各写一套胜率**。

#### 3.6.1 三层模型：Spec（不可变）/ Run（一次执行）/ Artifact（产物）

```json
// ExperimentSpec —— 可读、可 diff、可 fork、可删除
{
  "schema_version": "exp1",
  "name": "二买 + ATR 过滤（4H）",
  "universe": { "symbols": ["BTC-USD", "ETH-USD"], "timeframes": ["4h"] },
  "period": { "from": "2020-01-01", "to": "2026-10-01" },
  "entry": {
    "all": [
      { "indicator": "chanlun.buy2", "params": { "mode": "stroke" } },
      { "indicator": "atr.filter", "params": { "k": 0.8 } }
    ]
  },
  "exit": {
    "any": [
      { "indicator": "chanlun.sell1" },
      { "type": "stop",  "atr": 1.5 },
      { "type": "target", "r": 2 },
      { "type": "time",  "bars": 20 }
    ]
  },
  "sizing": { "mode": "fixed_risk", "risk_pct": 1.0, "leverage": 1 },
  "costs":  { "fee_bps": 10, "slippage_bps": 5 },
  "validation": { "split": "holdout", "dev_share": 0.7, "min_trades": 30 }
}
```

- **Spec 里没有代码**：所有能力来自 `indicator-core` 的指标 + 少量通用出场原语（stop/target/time/trailing）。
- 需要新逻辑时，加的是 **adapter**（进 `indicator-core`），不是新脚本——这就是「越堆越乱」的根治点。
- `validation` 把 `experiments/ma_a_atr_entry` 里那套「开发集选参 / 留出集验证」固化成标准字段，避免每个实验各写一遍防过拟合逻辑。

#### 3.6.2 执行模型

```
POST /experiments/{id}/runs
   → 入队(experiment_runs.status=queued)
   → 后台任务：加载 spec → 取数(candle_store / data/derived)
       → indicator-core 出信号 → 撮合器(仓位/成本/止损/目标/时间)
       → trades + equity + metrics → 写产物目录 + 回填 DB
   → status=succeeded | failed(带 error) | cancelled
```

- **确定性**：同一 `spec + data_version + git_sha` 必得同一结果，写入 run 记录便于复现与审计。
- **隔离**：实验只读行情，绝不写 `alert_events` / `chanlun_signals`；「固化为告警」是显式 API（`/promote`）。
- **限流**：同时最多 1–2 个 run；单 run 超时（默认 10 min）；支持 cancel。

#### 3.6.3 数据复用（你说的「已有数据底座」）

| 数据 | 来源 | 说明 |
|------|------|------|
| K 线 | `candle_store.load_candles` / `data/{crypto,stocks,indices,...}` parquet | 与看盘/回测同源，不另建一份 |
| 指标序列 | `data/derived/<symbol>/<tf>/series.bin` | 构建期已算好，实验直接复用 |
| 事件 | `indicator_events`（R3 建的表） | 实验入场直接用已入库事件，省去重算 |
| 结果标注 | `outcome_labeling` 的窗口/首触逻辑 | 单笔 R 与统计口径一致 |

> 关键收益：`data/derived` + `indicator_events` 一旦建好，一个实验的运行成本从「跑 700 行脚本 + 联网抓数」降到**读本地产物 + 撮合**，秒级到分钟级。

#### 3.6.4 存储：一张主表 + 两个子表 + 一个目录

```sql
experiments(
  id, name, spec_json, params_hash, status,       -- draft | active | archived
  created_at, updated_at, archived_at,            -- 软删标记
  last_run_id, note
)
experiment_runs(
  id, experiment_id FK ON DELETE CASCADE,
  status, started_at, finished_at, error,
  git_sha, data_version, metrics_json, artifact_dir
)
experiment_trades(
  id, run_id FK ON DELETE CASCADE,
  symbol, timeframe, direction,
  entry_time, entry_price, exit_time, exit_price,
  r_multiple, pnl_pct, exit_reason,
  entry_event_uid, exit_event_uid               -- 指回 Event，图上可高亮
)
-- 索引: (experiment_id), (run_id, entry_time), (symbol, timeframe)
```

产物目录（**全部 gitignore，且唯一根目录**）：
```
data/experiments/<experiment_id>/<run_id>/
├── spec.json        # 本次运行的 spec 快照（不可变）
├── trades.parquet   # 全量交易（DB 存索引列，parquet 存全字段）
├── equity.json      # 净值/回撤曲线
├── markers.json     # 图上的买卖点（含 evidence 摘要）
└── log.txt          # 运行日志（失败时排查）
```

#### 3.6.5 「方便删除」：这是需求，不是附带功能

| 层级 | 行为 | 实现 |
|------|------|------|
| 删一次运行 | 列表行内删除，立即从 UI 消失 | `DELETE /runs/{rid}`：删 DB 行 + `rmtree(artifact_dir)` |
| 删一个实验 | 默认**软删**（`archived_at`），7 天内可恢复 | `DELETE /experiments/{id}`；`POST /restore` |
| 彻底清理 | 显式二次确认 | `DELETE /experiments/{id}?purge=true`：DB cascade + 删目录 |
| 批量清理 | 「删除所有失败运行」「删除 30 天前草稿」 | `POST /experiments/cleanup?scope=failed\|stale` |
| 运行中删除 | 先 cancel 任务，再删记录 | 任务取消 + 目录清理（幂等） |
| 孤儿目录 | 每日巡检：无 DB 记录的目录自动清 | `cleanup` job |

**安全约束**
- 路径只由 `experiment_id/run_id` 拼接（整数），**不接受用户输入路径**；删除前 `resolve()` 校验必须位于 `data/experiments/` 下。
- 删除只影响实验域，不动行情、事件、统计、告警数据。
- 软删期间列表默认隐藏，但深链仍可打开并提示「已归档」。

### 3.7 `indicator-library`（R6，指标库与发布）

> 你补充的场景：**「实验跑完觉得不行，就把这个技术指标从指标库里删掉；觉得行，就把指标推到正式环境，实验过程留在实验室。」**
> 这句话要求系统区分「引擎能力」与「你的指标配置」，并且发布必须以版本为单位。

#### 3.7.1 三个概念，先分清再谈删除

| 概念 | 例子 | 谁能改 | 能删吗 |
|------|------|--------|--------|
| **Primitive**（引擎能力） | `chanlun.buy2`、`ma.breakout`、`macd.divergence` | 只能改代码 | ❌ 不可删，只能**停用** |
| **Indicator**（指标实例） | 「二买 + ATR≥0.8 + 4H + BTC/ETH」 | 你在 UI/实验里配置 | ✅ 归档或彻底清除 |
| **Version**（版本快照） | v3：参数/过滤条件改了一次 | 不可变 | ✅ 未发布可删；已发布需先下线 |

所以「把这个技术指标删掉」在系统里是两件事：
- 想停用缠论二买本身 → **停用 primitive**（代码还在，选择器里不再出现）；
- 想删掉你调的那个二买变体 → **归档/删除 indicator**（连带它的版本与统计，可恢复）。

#### 3.7.2 生命周期

```
draft ──做出实验──▶ testing ──发布──▶ promoted ──下线──▶ deprecated ──归档──▶ archived
  │                   │                  │
  └──── 删除 ─────────┘                  └── 回滚：重新 promote 上一个版本
```

- `draft`：刚建，随便改、随便删，不产生任何生产影响；
- `testing`：已有实验证据，能跑统计，但仍只出现在实验室；
- `promoted`：正式环境可见（看盘/练习/统计/告警），**不可直接删除**；
- `deprecated`：已下线，历史与统计保留，不再触发告警；
- `archived`：从指标库隐藏，7 天内可恢复。

#### 3.7.3 发布 = 版本快照 + 证据链（不是复制几行数据）

一次 `promote` 冻结并记录：

| 字段 | 内容 |
|------|------|
| `spec_json` | 指标定义（primitive + 参数 + 过滤 + 品种/周期） |
| `params_hash` | 参数指纹，保证统计可复算 |
| `engine_version` | 引擎版本（引擎一变，旧版本统计不再可比） |
| `evidence_run_ids` | 支撑发布的实验运行（可点回 `/lab` 看全过程） |
| `metrics_snapshot` | 发布时的 n / 胜率 / 期望 R / 最大回撤 |
| `promoted_at / promoted_by / note` | 审计信息 |

生产面（看盘、练习、告警）只读**已发布版本**；`indicator_events` 按
`indicator_version_id` 落库，**跨版本永远不混算**。

#### 3.7.4 发布门槛（默认值，可配置）

| 条件 | 默认 | 说明 |
|------|------|------|
| 至少 1 次成功 run | 必须 | 没有回测证据不能上线 |
| 样本 `n >= 30` | 必须 | 低于 30 笔的胜率没有意义 |
| 留出期期望 ≥ 0 | 建议 | `validation.split=holdout` 的结果 |
| `engine_version` 一致 | 必须 | 引擎升级后需重新验证 |
| `schema_version` 一致 | 必须 | 数据结构变更需迁移 |

未达标时允许「强制发布（需填备注）」，但指标卡与告警卡片会显示 **未达标** 标记——
不阻止你，但让你每次看到它时都记得这件事。

> 这条门槛会带来一个「正确但可能不舒服」的结果：**二买在 BTC 日线只有 1 次、4H 只有 5 次（实测），
> 它永远达不到 30 笔，因此不应该被发布成告警**。真正该先上的是三买（1D 25 次 / 4H 147 次）
> 与背驰（1D 50 次 / 4H 451 次）。

#### 3.7.5 删除语义（你关心的「方便删除」）

| 对象 | 默认动作 | 彻底删除 | 前置检查 |
|------|----------|----------|----------|
| primitive | 停用（不影响历史事件） | ❌ 不允许 | — |
| indicator（draft/testing） | 归档（可恢复） | ✅ 允许 | 无进行中 run |
| indicator（promoted） | **禁止删除**，提示先下线 | ✅ 下线并归档后 | 引用它的告警规则需先停用 |
| version（未发布） | 删除 | ✅ | 无实验引用 |
| version（已发布） | 禁止删除，先 demote | ✅ demote 后 | 同上 |
| experiment run（被发布引用） | **禁止删除** | ❌ | 先解除引用 |
| indicator_events | 随指标归档保留 | ✅ 可选（可重建） | — |

**要点**
- **归档 ≠ 删除**：归档只是从选择器里消失，历史事件与统计还在，随时可恢复；
- **彻底删除**是单独动作，UI 必须列出影响面（N 条事件、M 个告警规则、K 个实验引用）；
- 删除指标不会删掉行情、实验产物或别人的数据；
- 所有删除写 `deleted_at / deleted_by / reason` 审计。

#### 3.7.6 实验室 → 正式环境：两种隔离方式

| 方式 | 做法 | 优点 | 代价 |
|------|------|------|------|
| **A. 单部署 + stage（推荐）** | 一个库，`stage` 区分 lab/prod；发布 = 状态变更 | 原子、可回滚、无需同步数据 | 「实验室」不是物理隔离 |
| B. 双部署 | lab 与 prod 两套容器 + 两个库；发布 = 导出指标包 → 导入 | 真隔离，可给不同人用 | 行情/实验数据要同步，运维翻倍 |

**建议先 A**，同时把「指标包」格式定下来（B 的前置）：

```json
{
  "schema_version": "bundle1",
  "exported_at": "2026-10-22T08:00:00Z",
  "indicator": { "name": "...", "primitive_id": "chanlun.buy3" },
  "versions": [ { "version_no": 3, "spec_json": {}, "params_hash": "a1b2c3d4",
                  "engine_version": "chanlun@1.3.0", "metrics": {}, "evidence": [] } ],
  "requirements": { "engine_version": "chanlun@1.3.0", "schema_version": "ev1" },
  "checksum": "sha256:..."
}
```

A 模式下 `promote` 落库；B 模式下 `promote` = 导出 bundle + 在 prod 导入。**同一套语义，两种实现**。

#### 3.7.7 表结构

```sql
-- primitive 可代码注册，这里落表只为在 UI 展示与「停用」
primitives(id TEXT PK, label, group, engine_version, enabled BOOL, params_schema_json)

indicators(
  id, name, primitive_id, status,          -- draft|testing|promoted|deprecated|archived
  current_version_id, created_at, updated_at,
  archived_at, deleted_at, deleted_reason
)

indicator_versions(
  id, indicator_id FK, version_no, spec_json,
  params_hash, engine_version, schedule_json,
  evidence_run_ids, metrics_snapshot_json,
  promoted_at, promoted_by, demoted_at, note
)

promotion_audit(id, indicator_id, version_id, action, actor, note, created_at)

-- 已有事件表加一列（统计按版本聚合）
ALTER TABLE indicator_events ADD COLUMN indicator_version_id BIGINT REFERENCES indicator_versions(id);
```

#### 3.7.8 前端 `/library`

- 列表：名称 / primitive / 状态徽章 / 当前版本 / 样本数 / 胜率 / **引用数**（告警 × N、实验 × M）
- 操作：发布、下线、归档、恢复、删除、导出指标包
- 详情：版本历史 + 每个版本的证据 run（可跳 `/lab`）+ 统计趋势（版本间不可混算）
- 实验运行页加一个按钮：**「发布为指标」**——从一次 run 直接生成 `testing` 指标与 v1 版本
- 删除确认弹窗必须列出影响面，并提供「改为归档」的默认选项

---

## 4. API 设计

| 方法 | 路径 | 用途 | 备注 |
|------|------|------|------|
| GET | `/api/indicators` | 指标目录：primitive 能力 + 已建 indicator（可按 `stage` 过滤） | 驱动四处下拉框与参数表单 |
| POST | `/api/indicators` | 新建指标实例（`draft`） | 参数来自 primitive 的 schema |
| PATCH | `/api/indicators/{id}` | 改名 / 描述 / 启停 | 不产生新版本 |
| POST | `/api/indicators/{id}/versions` | 改参数 = 新版本（`draft`） | 版本不可变 |
| POST | `/api/indicators/{id}/versions/{vid}/promote` | 发布到正式环境（带证据与门槛检查） | 核心动作 |
| POST | `/api/indicators/{id}/versions/{vid}/demote` | 下线（可回滚到上一版本） | 影响告警，需确认 |
| POST | `/api/indicators/{id}/archive` \| `/restore` | 归档 / 恢复 | 默认删除动作 |
| DELETE | `/api/indicators/{id}?purge=true` | 彻底删除（校验引用） | 返回影响面后才执行 |
| GET | `/api/indicators/{id}/references` | 引用链（告警 × N、实验 × M、事件数） | 删除/下线前的确认弹窗 |
| GET | `/api/indicators/{id}/bundle` | 导出指标包（双部署迁移用） | B 方案的前置 |
| POST | `/api/indicators/import` | 导入指标包 | 导入后为 `testing` |
| GET | `/api/series?symbol&tf&outputsize&indicators=&as_of=` | 读库版：K 线 + 数值序列 + 事件注释 | P1 补（当前用 POST 版） |
| POST | `/api/series` | 传 K 线，返回对齐的 `series[]` +（可选）`events[]` | **已实现**；`as_of` 严格前缀，练习/看盘共用 |
| GET | `/api/series/catalog` | 序列目录（ma / macd / rsi / atr 及参数 schema） | **已实现** |
| GET | `/api/events?symbol&tf&indicator&from&to` | 事件列表（图标记/列表） | 支持 `direction`、`state` |
| GET | `/api/stats/indicators` | 指标总览（各指标 n/胜率/期望） | 首页卡片 |
| GET | `/api/stats/indicators/{id}?symbol&tf&horizon&from&to` | 分组统计 + 逐次发生列表（分页） | R3 主接口 |
| GET | `/api/stats/events/{uid}` | 单事件详情 + 多窗口结果 | 供「跳回复盘」浮层 |
| POST | `/api/monitoring/plans` | 规则增删改（扩展指标维度） | 复用现有 |
| POST | `/api/monitoring/channels/{id}/test` | 飞书连通性测试 | **已存在** |
| GET | `/api/practice/datasets?symbol&tf` | 练习数据清单（可选） | 移动端离线包 |
| POST/GET | `/api/practice/sessions` | 跨端同步（可选，后置） | 本地优先 |
| GET | `/api/experiments?status=&q=` | 实验列表（默认排除已归档） | R5 主界面数据 |
| POST | `/api/experiments` | 创建/保存 spec（`draft`，不运行） | 参数表单由指标 schema 生成 |
| POST | `/api/experiments/{id}/fork` | 复制为新实验（改参数不污染历史） | 迭代的主要方式 |
| POST | `/api/experiments/{id}/runs` | 触发运行（异步，立即返回 run_id） | 入队后轮询/SSE |
| GET | `/api/experiments/{id}/runs/{rid}` | 状态 + metrics + 摘要 | 前端轮询 |
| GET | `/api/experiments/{id}/runs/{rid}/chart?from&to` | 窗口内 K 线 + 买卖点标记 + overlays | 复用 `chart-kit` |
| GET | `/api/experiments/{id}/runs/{rid}/trades?page=` | 交易明细分页 | 表格 + 图上定位 |
| DELETE | `/api/experiments/{id}` | 软删（`archived_at`，7 天可恢复） | 默认行为 |
| DELETE | `/api/experiments/{id}?purge=true` | 物理删除（DB cascade + 产物目录） | 二次确认 |
| DELETE | `/api/experiments/runs/{rid}` | 删除单次运行 | 行内操作 |
| POST | `/api/experiments/{id}/restore` | 恢复软删 | 误删兜底 |
| POST | `/api/experiments/cleanup?scope=failed\|stale\|orphan` | 批量清理 | 定时任务也调用 |
| POST | `/api/experiments/{id}/promote` | 固化为告警规则 | 可选，实验→生产的唯一通道 |

> **已实现（P0）**：`GET /api/indicator-catalog`（10 个缠论指标目录）、`GET /api/indicator-catalog/{id}`、
> `POST /api/indicator-events`（传 K 线，返回统一事件；支持 `as_of` 严格前缀与 `confirmed_before` 过滤）。
> **已实现（P1 第一步）**：`GET /api/series/catalog`、`POST /api/series`（K 线 + MA/MACD/RSI/ATR 序列 +
> 可选事件，一次请求喂饱图表）。

**深链约定**（R3「回到对应时间」+ R5「从结果跳图/练习」）
```
/watch?symbol=BTC-USD&tf=4h&t=1710432000&event=<uid>
/practice?symbol=BTC-USD&tf=4h&event=<uid>&start=<confirmed_at>&lookback=200
/lab/<experiment_id>/runs/<run_id>?trade=<trade_id>        # 结果页定位到某笔
/lab/<experiment_id>/runs/<run_id>?t=<ts>&trade=<trade_id> # 图上定位到买卖点
```
练习从事件的 `confirmed_at` 前 200 根开始，事件本身在第 200 步出现——这既能让用户「看到信号如何形成」，又不会提前泄露。

---

## 5. 数据与构建

### 5.1 产物布局（替代 197MB 的 `data-view/*.js`）

```
data/derived/BTC-USD/4h/
├── manifest.json     {schema_version, bars, first, last, indicators:[...], files:{...}}
├── bars.bin          6×Float64 小端（沿用现有分片格式）
├── series.bin        指标序列（header JSON + Float32）
└── annotations.json  事件注释（含 confirmed_at / evidence）
```

体积估算（BTC 4H，19,754 根）：bars ≈ 0.95 MB、series ≈ 0.5 MB、annotations ≈ 0.3 MB，**合计 < 2 MB**；对比现在单文件 33 MB。15m 沿用月度分片 + 月度注释分片。

### 5.2 一条构建命令

```bash
python -m market_data.build --symbols all --tfs 1D,4H,1H --indicators chanlun,ma,macd --out data/derived
```

约束：
- `schema_version` 不匹配时前端**报错而非静默降级**；
- 构建产物不入库（`data/derived/` gitignore），CI 只校验「能否重建」；
- 引擎变更必须同时更新 golden 与注释，否则提示重新构建。

---

## 6. 前端信息架构

| 路由 | 端 | 职责 | 复用 |
|------|----|------|------|
| `/watch/[symbol]` | 桌面+移动 | 看盘：K线 + 指标 + 最近事件 + 事件浮层（含历史统计） | chart-kit |
| `/practice` | 桌面+移动 | 练习：随机/错题/事件定向，盲测、记账、揭示 | practice-core + chart-kit |
| `/stats` | 桌面优先 | 指标统计：筛选、汇总卡、分布图、逐次表格 | 新页面，纯数据 |
| `/library` | 桌面优先 | 指标库：状态徽章、版本历史、引用链、发布/下线/归档/删除、导出指标包 | 新页面 |
| `/library/[id]` | 桌面 | 指标详情：版本 + 发布证据 + 统计趋势（跨版本不混算） | 新页面 |
| `/lab` | 桌面优先 | 实验列表：状态、样本、胜率、数据版本、删除/清理 | `strategy_lab_api` 演进 |
| `/lab/[id]` | 桌面 | 实验详情：spec 只读、运行历史、参数表单、fork | 新页面 |
| `/lab/[id]/runs/[rid]` | 桌面 | 结果页：K线+买卖点标记、指标卡、净值/回撤、交易表、图上定位 | `MaStrategyChart`/`TradeReplayPlayer` 改造 |
| `/alerts` | 桌面 | 规则与渠道（飞书）、事件流、投递状态 | 现有 `MonitorPlans` 扩展 |
| `/replay` | 桌面 | 保留为「无记账的纯回放/笔记」 | 逐步被 `/watch` 覆盖，不急着删 |

**实验台交互要点（对应你说的「展示买卖点 + 方便删除」）**

1. **买卖点必须能落到图上**：结果页的 marker 来自 `experiment_trades.entry/exit_event_uid`，hover 显示触发指标、`why/evidence`、该笔 R；点表格行 → 图上定位并高亮；点图 → 表格滚动到该笔。
2. **结果必须可比较**：同一实验的多次 run 可勾选叠加净值曲线（不同颜色），避免「跑完就忘」。
3. **删除必须顺手**：列表支持多选 + 批量删除；已归档的显示在「回收站」页签；删除后 toast + 撤销（软删期内）。
4. **不做「自动保存所有运行」**：默认保留最近 20 次/实验，超出由清理任务回收——否则实验库本身会变成新的垃圾场。这一点是刻意的。

**指标库交互要点（对应「实验不行就删、行了就发布」）**

1. **一个指标一行，状态一眼可见**：`草稿 / 实验中 / 已上线 / 已下线 / 已归档`，颜色区分；已上线的行不显示「删除」。
2. **删除默认是「归档」**：弹窗第一行写清「将从指标库隐藏，历史统计保留 7 天，可恢复」；「彻底删除」折叠在二级动作里。
3. **发布是一步动作，但必须看到证据**：弹窗里直接列出「基于 run #12：n=46、胜率 58%、期望 +0.7R」，以及门槛是否达标。
4. **下线比发布更常见**：已上线指标的主按钮是「下线」，不是「删除」。

**双端策略（建议）**

- **桌面**：直接进 `platform-web`（Next.js），`/practice` 用同一套 React 组件，在线可用全部数据；
- **移动**：保留独立静态壳（Vite + PWA，部署 GitHub Pages），离线包由 `market_data.build` 产出；
- 两端共享 `practice-core` + `chart-kit`，**UI 布局各自实现**（移动是单手操作为主，硬套桌面布局会更差）。

> 若你更想「只维护一个应用」，也可把移动做成 Next.js 的 PWA。代价是离线能力与首屏体积变差，且 kpractice 的独立发布链路要废弃。**建议先按双壳做，`practice-core` 是纯逻辑，合并成本始终存在但很低。**

---

## 7. 实施路线（垂直切片优先）

> 核心原则：**先把「一个指标」在五个出口全打通，再横向铺指标**。

**切片选择依据（实测，BTC 全史）**

| 指标 | 1D（3,297 根） | 4H（19,754 根） | 结论 |
|------|---------------|----------------|------|
| `chanlun.buy1` | 5 | 20 | 太少，只能观察 |
| `chanlun.buy2` | **1** | **5** | ❌ 无法支撑统计与告警 |
| `chanlun.buy3` | 25 | **147** | ✅ **首选切片** |
| `chanlun.sell3` | 31 | 134 | ✅ |
| `chanlun.beichi.buy` | 50 | 451 | ✅ 样本充足，适合验证告警频率 |
| `chanlun.beichi.sell` | 70 | 445 | ✅ |
| `ma.breakout`（20/60） | 336 | 1,967 | ✅ 样本充足 |
| `macd.divergence` | 91 | 605 | ✅ 样本充足 |
| 缠论 10 个指标合计 | 203 | 1,300 | — |

> 原计划选 `chanlun.buy2`，实测后必须改：二买在 BTC 日线 9 年只出现 1 次、4H 只有 5 次。
> **最终切片：`chanlun.buy3` × `BTC-USD` × `4H`**（147 次），`chanlun.beichi.buy` 作为「样本充足」的对照。
> 这也提前暴露了 R6 发布门槛的价值：样本不足的指标会被自动挡在正式环境之外。

### P0 · 契约与护栏（2 天）—— ✅ 已完成（2026-10-22）

- [x] 契约落地 `apps/platform-api/app/indicator_core/`（`spec.py` / `registry.py` / `adapters/chanlun.py`）
- [x] 缠论 10 个指标接入（一/二/三买卖、背驰、盘整背驰），统一输出 `Event`
- [x] 因果约束硬校验：构造时 `confirmed_at >= bar_time`，`uid` / `params_hash` 稳定
- [x] 因果审计脚本 `scripts/audit_causality.py`（BTC 1D × 12 采样点：992 个可见事件、**0 泄漏 / 0 漏报**、5.5s）
- [x] 回归测试 `apps/platform-api/tests/test_indicator_core.py`（14 项，含「全量过滤 == 前缀重算」与 API 断言）
- [x] 只读 API：`GET /api/indicator-catalog`、`POST /api/indicator-events`
- [x] golden JSON 快照（2026-10-22 第十一轮：`apps/platform-api/tests/fixtures/indicator_events_golden.json`，
      12 指标 × BTC 1D 尾段 600 根 = 112 事件；`tests/test_indicator_golden.py` + CI 校验最新）

**验收**：`pytest` 全绿；审计 0 泄漏；Event 字段与 `chanlun_api` 输出一致。

> 落地位置说明：P0 先放在 `apps/platform-api/app/indicator_core/`，因为 Docker 构建上下文是 `backend/`；
> P7 仓库分区时整体迁到 `packages/indicator-core`（纯移动，无逻辑改动）。

### P1 · 指标层 + 看盘（4 天）—— ✅ 已完成（2026-10-22 第十七/十九轮）

- [x] `Series` 契约 + SMA/EMA/MACD/RSI/ATR 纯函数（`apps/platform-api/app/indicator_core/series.py`）
- [x] `GET /api/series/catalog`、`POST /api/series`（K 线 + 序列 + 可选事件，`as_of` 前缀）
- [x] MA/MACD **事件** adapter（`ma.breakout` / `macd.divergence`），复用现有 detector 逻辑
      —— 指标总数 12（缠论 10 + 均线突破 + MACD 背离）
- [x] 旧 `/api/chanlun/analyze` 标 deprecated 并转发到新出口（2026-10-22 第九轮：
      payload 构造下沉到 `chanlun_service.analyze_chart`，`/api/series?structure=true` 是唯一实现）
- [x] `chart-kit` 抽出（2026-10-22 第十七轮）：`apps/platform-web/lib/chart-kit/`
      （`series.ts` 序列→线/柱/标记/`Indicators`、`structure.ts` 增量合并、`barClose.ts` 收盘对齐、
      `useBarCloseRefresh.ts`）；看盘页与图表页统一使用，图表页 MA/MACD/RSI 改为 `/api/series`
      （前端 `calculateIndicators` 仅作降级兜底）
- [x] 收盘后增量刷新（2026-10-22 第十七轮）：`useBarCloseRefresh` 对齐 K 线收盘（+5s grace），
      图表页只把最近 600 根丢给 `/api/series?structure=true` 重算，再用 `mergeStructure` 合并旧图层

**验收**：同一 symbol/tf，看盘图与 `chanlun_api` 的买卖点完全一致；页面无「自己算 MA」的代码路径。

### P2 · 练习内核 + 双端（4 天）—— ✅ 已完成（2026-10-22 第十八/十九轮）

- [x] `practice-core`（2026-10-22 第六轮）：`buildView` / `clampCursor` / `defaultStart` / `revealedStats`
      + 离线存档 `lib/practiceCache.ts`（LRU 4 份，断网回退）
- [x] 桌面 `/practice`：步进/播放/揭晓/计分、随机起点（`randomStart`）、
      错题重练（`nextReviewTime`）、**加仓（2026-10-22 第十八轮）**：
      `createPosition` / `addLeg`（同向 + 最多 3 腿 + 可选最小间距）/ `closePosition`
      按腿权重加权 R 结算，`positionStats` 汇总已平仓；页面「进场/加仓/平仓」按钮 + 持仓卡
      （完整错题本仍未做：目前只按事件跳转，无独立错题集页面）
- [x] 指标图层接入：事件级逐指标开关（练习页 chips）、**MA/MACD 数值序列开关（2026-10-22 第十八轮，
      走 `/api/series` + `chart-kit`）**、**盲测锁视口（`SimpleChart lockRightEdge`：
      `fixRightEdge` + `lockVisibleTimeRangeOnResize`，拖不到最后一根 K 线之后）**
- [x] 移动壳（2026-10-22 第十九轮）：同一 TS core + 本地存档在 Web 移动端可用；
      独立 PWA 壳已接：`public/manifest.webmanifest`（standalone / 192+512 / maskable / shortcuts）、
      `public/sw.js`（只缓存 GET，导航 network-first → 断网回退 `/practice`）、
      `components/PwaRegister.tsx`（注册 + 独立窗口/离线角标）、`lib/pwa.ts`（纯判定函数）

**验收**：同一 `seed + spec` 在两端产生**逐字段相同**的会话记录（这是双端一致性的硬指标）；练习中无法通过缩放/滚动看到未来 K 线（自动化测试）。

### P3 · 事件库 + 统计 + 跳转（4 天）—— ✅ 已完成（2026-10-22）

- [x] Alembic 迁移：`indicator_events`、`indicator_outcomes`（2026-10-22 第二轮，`0002_indicator_layer.py`）
- [x] 回填脚本 `scripts/backfill_indicator_events.py`（含结果标注）
- [x] `/api/stats/*` + `/stats` 页（总览 + 分组 + 逐次列表 + 深链）
- [x] 深链打开练习：`start = confirmed_at - 200 bars`（`/practice?event=…`）

**验收**：任选一个指标，页面上「出现次数/胜率/期望」与线下脚本复算一致（同一 `params_hash`）；点击任意一行能在练习中定位到该事件。

### P4 · 实验环境（5 天）—— ✅ 已完成（2026-10-22 第十六轮）

- [x] `experiment-core`：`spec.py`（校验 + `spec_hash`）、`runner.py`、`service.py`（2026-10-22 第四轮）
- [x] Alembic 迁移：`experiments` / `experiment_runs` / `experiment_trades`（cascade）
- [x] 后台执行 + 取消（2026-10-22 第十六轮）：`POST /{id}/runs?background=true` 立即返回
      `queued` run 并由 asyncio 任务执行；`POST /api/experiments/runs/{run_id}/cancel` 取消
      queued/running（已结束返回 409）；`EXPERIMENT_RUN_TIMEOUT_SEC`（默认 180s）超时落库失败。
      进程内队列，非持久化消息队列（单实例部署够用）
- [x] `/api/experiments/*`（创建/运行/查询/删除/清理/fork/restore/trades/chart）
- [x] `/lab` 列表 + `/lab/[id]/runs/[rid]` 结果页：买卖点标记、指标卡、净值/回撤、交易表、图上定位
- [x] 删除链路：软删 → 恢复 → 彻底清理 → 孤儿目录巡检
- [x] 迁移 1 个现有实验为 spec（2026-10-22 第十六轮）：
      `experiments/specs/ma_breakout_2r.spec.json`（由 `strategy_c_ma_pool_compare.py` 迁移）
      + `scripts/run_experiment_spec.py` + `tests/test_spec_migration.py`；
      在本地 BTC-USD 1day 上跑出 75 笔交易（stop 54 / target 15 / time 6）

**验收**：新建一个「三买 + 2R 止盈」实验，10 秒内出结果；图上买卖点与交易表一致；
删除后 DB 与磁盘均无残留（用 `find data/experiments` 验证）；重启后列表一致。

### P5 · 指标库与发布（3 天）—— ✅ 已完成（2026-10-22）

- [x] Alembic 迁移：`primitives` / `indicators` / `indicator_versions` / `promotion_audit`，
      `indicator_events` 加 `indicator_version_id`
- [x] 生命周期 API：创建 / 改参 = 新版本 / promote / demote / archive / restore / purge
- [x] 发布门槛校验（n≥30、参数与 spec 一致、有成功 run 证据）+ 强制发布标记
- [x] 引用链影响面 API（2026-10-22 第十三轮）：`GET /api/library/indicators/{id}/impact`
      返回事件数（按版本/原始）、订阅计划、实验引用；`demote` 与 `purge` 响应内联 impact
- [x] `/library` 列表 + 详情：状态徽章、版本历史、发布证据、统计趋势
- [x] 实验运行页「发布为指标」；指标包导出/导入 + `POST /push`、`POST /pull`（双部署）
- [x] 「停用 primitive」与「归档 indicator」分开（`GET /api/library/primitives` vs `/indicators`）

**验收**：从一次实验 run 发布指标 → 看盘/告警立即可选；下线后立即从选择器消失但历史统计仍在；
删除一个 `promoted` 指标被拒绝并提示先下线；归档后 7 天内可恢复。

### P6 · 告警 + 飞书（2 天）—— ✅ 已完成（2026-10-22 第十四/十五轮）

- [x] `MonitorPlan` 指向 `indicator_version_id`（`indicator_version_ids` 列 + 参数随版本冻结）
- [x] 调度收盘对齐（2026-10-22 第十四轮）：`app/core/scan_window.py` 计算最近收盘 bar，
      指标 job 改为每分钟 tick + 「收盘后 5 分钟窗口（`INDICATOR_ALERT_GRACE_MIN`）」内扫一次，
      同一根 bar 不重复；超过 `INDICATOR_SCAN_INTERVAL_MIN` 未扫则兜底补扫。
      缠论 job 仍为固定 interval
- [x] 飞书卡片：指标、版本、品种、级别、价格、触发时间、**历史统计摘要**、深链
- [x] 投递幂等与重试（2026-10-22 第十五轮）：`IndicatorEvent.notified_at` 记账，
      429/超时/异常视为失败留待下一轮重试；计划订阅失败不推进游标；
      已送达事件不会被全局兜底扫描重复推送（`tests/test_indicator_alert_retry.py` 模拟 429/超时）

**验收**：构造一条 4H 三买，5 分钟内飞书收到卡片；重启服务不重复发送；卡片链接能打开对应图表并高亮事件。

### P7 · 数据链与出库（3 天）—— ✅ 已完成（2026-10-22）

- [x] 数据构建：`scripts/build_web_data.py` 产出 `apps/platform-web/public/data/*.json` + manifest（2026-10-22）
- [x] 前端 manifest 加载：`apps/platform-web/lib/staticData.ts`，`/watch` 静态优先、API 回退
- [x] 仓库分区第一步：`packages/indicator-core` + `apps/platform-api/app/indicator_core` 兼容 shim（2026-10-22）
- [x] 仓库分区第二步（2026-10-22 第五/六/七轮）：`apps/platform-api`、`apps/platform-web`、
      `apps/chanlun-web`、`packages/indicator-core`、`packages/chanlun-core`；
      Docker/compose/CI/Makefile/`.gitignore`/`.dockerignore` 同步
- [x] CI 增加：`public/data` 产物不得入库（repo-hygiene job）、因果审计（backend job）、
      两份 golden fixture 最新性校验、引擎测试 job
- [x] 生成数据出库（2026-10-22 第十九轮）：`apps/chanlun-web/data-view/*.js`（192 MB）
      与 `intraday/**/*.bin`（32 MB）`git rm --cached` 并写入 `.gitignore`（磁盘与历史保留，
      可 `git checkout <commit> -- <path>` 取回）；重建方式见 `apps/chanlun-web/README.md`
      与 `scripts/ops/update_all.sh`；CI hygiene 守卫扩展为
      `data-view/*.js` / `intraday/*.bin` / 缓存目录一律不得入库

**验收**：`git clone` + `make data` 后两端可用；仓库体积 < 100 MB；CI 跑通因果审计。

**总工期**：单人约 **27 人日（5–6 周）**；若只做 R1+R2 的桌面部分，1.5 周可上线；
若先做 R5（实验台）单独上线，约 1 周（它不依赖练习内核）；
P5（指标库）依赖 P3 的版本化事件，但不依赖练习，可与 P2 并行。

> 排期建议：**P4（实验台）可以提前到 P1 之后做**——它依赖「指标层 + 图 + 事件」，不依赖练习内核，
> 且能立刻替代现在 15 个回测脚本，收益最直观。真正的顺序约束：
> `P0 → P1 → {P2 练习, P3 统计+版本, P4 实验} 并行 → P5 指标库 → P6 告警 → P7 出库`。

---

## 8. 关键取舍（需要你拍板的六件事）

### 决策 1：练习端形态

| 方案 | 优点 | 代价 | 建议 |
|------|------|------|------|
| A. 并入平台 `/practice` | 一个应用、一套登录/数据 | 离线难；移动体验受 Next 约束 | 桌面用 A |
| B. 双壳（Next + Vite PWA）共享内核 | 离线、发布独立、移动体验好 | 两套构建 | **推荐** |
| C. 维持现状两套代码 | 无迁移成本 | 永久双写、指标永远不同步 | 不建议 |

**推荐组合**：桌面 A + 移动 B，共享 `practice-core`/`chart-kit`。

### 决策 2：胜率口径

- 默认：**确认 K 收盘入场，止损=结构低点（无结构用 1×ATR14），目标 2R，窗口 20 根，首触判定**；
- 备选：固定持有 N 根（更简单，但对「止损被打」不敏感）；
- 建议同时展示「首触胜率」与「到期期望 R」，避免单一数字误导。

### 决策 3：先做哪些指标

原计划 `chanlun.buy2`，**实测后必须改**：二买 BTC 1D 仅 1 次、4H 仅 5 次，无法统计也无法告警。

| 顺序 | 指标 | 实测样本（1D / 4H） | 理由 |
|------|------|--------------------|------|
| 1 | `chanlun.buy3` | 25 / 147 | 样本够、语义清晰，作为首个垂直切片 |
| 2 | `chanlun.beichi.buy` | 50 / 451 | 样本最充足，用来验证告警频率与卡片 |
| 3 | `ma.breakout` | 336 / 1,967 | 样本充足，与现有 MA 代码贴合，收敛 4 套 API |
| 4 | `macd.divergence` | 91 / 605 | 样本充足；天然滞后 3 根确认，正好验证因果契约 |
| 5 | 一买/二买/盘整背驰/结构 | 5 / 1 … | 样本不足，只做观察，不上告警 |

### 决策 4：实验用「声明式 Spec」还是「允许写代码」

| 方案 | 优点 | 代价 | 建议 |
|------|------|------|------|
| A. 纯声明式（本文设计） | 可删除、可对比、可固化告警、安全 | 表达力有上限 | **默认 A** |
| B. 声明式 + 逃生舱（注册自定义 adapter 进 `indicator-core`） | 复杂逻辑也能做 | 需 code review，不能从 UI 删 | A 不够用时再开 |
| C. 每个实验一个脚本（现状） | 自由 | 越堆越乱、不可删、不可比 | 淘汰 |

**推荐**：A 为主；确实需要新逻辑时，写的是 **adapter**（进指标库、有测试、有 schema），
而不是在 `experiments/` 下再建一个目录。这样「删除实验」永远只是删数据，不会删掉别人依赖的代码。

另一个小决策：**实验运行保留策略**——建议每个实验只保留最近 20 次运行，更早的自动归档清理；
`promote` 过的运行永久保留（它是生产规则的证据）。

### 决策 5：实验室与正式环境怎么隔离

| 方案 | 优点 | 代价 | 建议 |
|------|------|------|------|
| A. 单部署 + `stage` 字段 | 原子、可回滚、不用同步数据 | 不是物理隔离 | **先做 A** |
| B. 两套部署（lab / prod）+ 指标包导入导出 | 真隔离、可多人协作 | 运维与数据同步成本翻倍 | 需要时再上 |

**推荐**：先 A；同时把 `bundle` 格式定下来，B 只是「导出 + 导入」两个接口的差别。

### 决策 6：发布门槛要不要强制

- 建议**软门槛**：n≥30、holdout 期望≥0、引擎/ schema 版本一致；不达标可强制发布，但永久标记「未达标」；
- 不建议硬阻断：你可能有自己的判断（例如新指标先小仓位观察），系统只负责把风险摆出来；
- 但**已发布版本必须可一键下线**，且下线不影响历史统计。

---

## 9. 风险与对策

| 风险 | 表现 | 对策 |
|------|------|------|
| 因果泄露 | 练习里「提前」看到后来才确认的买卖点 | §2.5 审计常驻；`view()` 统一裁剪；练习自动化测试「视口外不可见」 |
| 样本太小 | 某指标历史只有 5 次，胜率 80% 毫无意义 | `n<30` 强制标注；Wilson CI；按年分组展示稳定性 |
| 引擎慢 | 4H 全史 19s，1H 更久 | 增量窗口（600 根）+ 构建期并行；1H 只保留近 3 年 |
| 口径漂移 | 改引擎后历史统计与新信号不可比 | `params_hash` + `schema_version` + golden；旧统计冻结不重算 |
| 飞书限流/失败 | 卡片丢失、重复 | 现有重试状态机 + `uid` 永久去重 + 429 退避 |
| 双端不一致 | 桌面/手机同一局结果不同 | `seed` 决定一切；同一 core；跨端快照对比测试 |
| 实验跑飞 | 参数组合爆炸、单 run 跑几小时 | 组合数上限 + 超时（10 min）+ 并发 1–2 + 取消按钮 |
| 实验产物成新垃圾 | 目录/DB 越积越多 | 单一产物根 + 保留 20 次 + 定时清理 + 孤儿巡检 |
| 误删实验 | 删掉还在用的结论 | 默认软删 7 天 + 回收站 + 撤销 toast + `promote` 过的不可删 |
| 过拟合 | 反复调参直到胜率好看 | spec 里强制 `validation`（dev/holdout）+ 参数冻结 `params_hash` + 样本量提示 |
| 误发布 | 把样本不足/过拟合的指标推上线 | 发布门槛 + 证据链 + 「未达标」永久标记 + 一键下线 |
| 版本混算 | 新旧参数的事件混在一起统计 | 事件按 `indicator_version_id` 落库；跨版本查询需显式 `compare` |
| 删除已上线指标 | 告警突然失效、历史断档 | 已发布禁止直接删，先 demote；删除前列引用面；归档可恢复 |
| 迁移中断 | 重构做到一半，旧系统不可用 | 垂直切片；旧接口保留 deprecated 转发；每阶段可回滚 |

---

## 10. 与仓库整理的关系

本设计是 [仓库梳理](REPO_AUDIT_2026-10-22.md) 中 P2「抽 `chanlun-core`」的**具体化**，并给出优先级调整：

1. **先做指标层（本文 P0–P1）**，因为它是 R1–R6 的共同前置；仓库分区（`apps/`）可以稍后做，两者不冲突。
2. 数据出库（原 P1）建议**提前到本文 P0 之后**：注释流是新架构的产物，先止血再铺量。
3. 原计划「MA 4 套 API 收敛」可以借本文 P1 的 `/api/series` 顺带完成——看盘只走新接口，旧接口自然退役。
4. **R5 实验台是原计划「15 个回测脚本合并」的正确形态**：不是把脚本合并成一个巨型 CLI，
   而是把「实验」从代码里搬出来变成 spec + run。做完 R5，`scripts/backtests/` 与 `experiments/` 才有资格整体归档删除。
5. **R6 指标库是原计划「4 套 MA API / 6 个脚本 / 4 个页面」的正确收敛点**：不是把它们合并成一个页面，
   而是把「指标」变成有版本、有状态、可发布可下线的对象。收敛发生在数据模型层，页面自然只剩一个入口。

---

## 11. 建议的下一步

1. **确认 §8 的六个决策**（练习形态 / 胜率口径 / 首批指标 / 实验声明式 / 环境隔离 / 发布门槛）；
2. **P0 已完成**：契约、10 个缠论指标、因果审计脚本、10 项回归测试全部落地并通过；
3. 下一步是 **P1 指标层 + 看盘**：`/api/series` 统一出口 + `chart-kit` 抽出 + `/watch` 切换数据源；
   同时补 MA/MACD adapter（平移现有 detector，不重写）。

> 如果你希望更快看到 R5 的成果，可以走「P0 → P1 → P4」的顺序：先有指标层与统一图，再接实验台，
> 练习与统计随后并行。这条路径对现有系统扰动最小，且 1 周左右就能用实验台替代手工脚本。

---

## 12. 第二轮实施记录（2026-10-22）

按「直接全项目改造」的指令，P1–P6 的后端主体 + 前端四个入口已落地，详见
[实施记录](IMPLEMENTATION_2026-10-22.md)。一句话总结：

- **数据层**：`indicator_events` / `indicator_outcomes` / 实验三表 / 指标库三表，Alembic `0002`；
- **出口**：`/api/series`、`/api/events`（可带结果）、`/api/stats/indicators`、`/api/experiments`、`/api/library`、`/api/alerts/indicator-scan`；
- **前端**：`/watch`、`/practice`、`/stats`、`/lab`、`/library` 五个入口，桌面/移动共用同一套数据契约；
- **验证**：`pytest` 698 passed；`tsc --noEmit` 通过；`next build` 受本机 SWC 二进制缺失阻塞（与代码无关）。

下一轮优先级：练习内核 TS 化（`practice-core`）→ 调度器接入指标告警 → 仓库分区（`packages/indicator-core`）。
