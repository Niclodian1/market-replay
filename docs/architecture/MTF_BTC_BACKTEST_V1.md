# MTF_BTC_BACKTEST_V1 — BTC USD-M 4H 多周期历史事件研究（离线回测）

> 状态：**V1 已落地（2026-08-16）**。纯只读研究管线（fetch 联网只读，run/verify 离线）：只新增核心纯函数与 CLI，
> **不改 monitor / API / schema / DB / 发送**，不联网写入任何生产路径，**不自动切生产**。
> 唯一产品：Binance USD-M 永续 **BTCUSDT**，**4H 单源**
> （data.binance.vision futures/um，无现货、无其它合约混入）。
> 关联：`../../reports/mtf-btc-backtest-v1/REPORT.md`（结果与解读唯一入口，
> 本文不重复大结果表）、`MULTI_TIMEFRAME_V1.md`（A/B 门控语义）、
> `MARKET_STATE_V1.md`（M1 状态）。

## 1. 范围与隔离（红线）

- 纯只读研究管线：fetch 联网只读官方 zip（不写任何生产路径）、run/verify 离线只读本地 gzip，**不写 DB、
  不发消息、不调生产 monitor 常驻流**；不改现有告警文案 / dedupe / payload / 渠道。
- 只新增两个代码文件 + 一个测试文件（见 §2）；未提交 commit；未动
  monitor / API / models / migration / providers / 现有测试 / golden / 前端 / 依赖。
- 结果为离线事件研究，**不自动切生产**；任何实盘引用须先影子验证。
- 本文只约束 V1 回测链路本身：不承诺信号有效性、不寻优、不做组合 PnL。

## 2. 文件职责

| 文件 | 职责 |
|---|---|
| `backend/app/core/mtf_historical_backtest.py` | 计算主体纯函数：CSV 解析、4H 校验、UTC 聚合、A/B 门控、事件回放、结果计算、Wilson、清单校验、merge/确定性 gzip/closed_state helper；附只读 HTTP helper（CLI 复用、仅 GET 官方 zip）；无 DB / 时钟写 / 发送 |
| `backend/app/tasks/mtf_backtest_cli.py` | CLI：`fetch`（URL 生成→下载→校验→写 gzip+清单）、`verify`（重算哈希/聚合）、`run`（verify 通过后只读 raw_4h 跑固定回测，写五份产物） |
| `backend/tests/test_mtf_historical_backtest.py` | 127 项测试（2083 行）守护全部契约 |
| `reports/mtf-btc-backtest-v1/{data,results}` | 产物：data 下三份 gzip + source_manifest.json + MANIFEST.sha256（+fetch_metadata.json）；results 下 events/decisions/summary + run_manifest.json + RUN_MANIFEST.sha256；`REPORT.md` 为结果唯一入口 |

## 3. fetch 契约

- URL：**79 个月度 zip**（2020-01..2026-07）+ **14 个每日 zip**（2026-08-01..14）= 93 份，
  路径模板 `futures/um/{monthly|daily}/klines/BTCUSDT/4h/...zip`；
  正式边界 `EXPECTED_FIRST_TIME=2020-01-01T00:00:00Z`（首根 4H 开盘）、
  `EXPECTED_LAST_TIME=2026-08-14T20:00:00Z`（末根 4H 开盘）；
  requested_cutoff=2026-08-16T00:00:00Z。
- **HTTP 非 200 只记录（`error="HTTP xxx"`），绝不把响应体交 zip 解析**；
  200 时才解压解析并记 `content_sha256`（原始 zip 字节摘要）；
  User-Agent 自标识研究用途、超时 60s、HTTPS 用 certifi CA 校验；
  网络级错误（连接/超时/证书）→ FetchError 上抛；每 URL 一条确定性记录进清单。
- 合并：按 time 去重（**同 time 同内容幂等；同 time 内容冲突 → ValueError**）、
  排序、`validate_closed_4h`：time 唯一、相邻差恒 **14400**、OHLC 有限正数且
  `high >= max(open,close) >= min(open,close) >= low`、volume 有限且 >= 0、
  全部已收盘；任何违反 → ValueError，不产出部分结果。
- **首末必须精确匹配正式边界**：缺头/缺尾即使内部严格连续也拒绝；
  测试小样本必须显式传其边界（expected_first_time / expected_last_time）。
- 确定性：gzip JSON `sort_keys` + 紧凑分隔符 + **gzip 头 mtime=0**；
  每文件 sha256 进 `source_manifest.json`，`MANIFEST.sha256` 锁清单本身；
  `fetch_metadata.json` 的 fetched_at 每次不同、**不进清单**；可重复执行字节级一致。

## 4. 聚合（1D/1W 派生）

- 只从同一份 raw_4h 聚合：1day 每桶恰 **6 根**（UTC 00 边界）、1week 每桶恰 **42 根**
  （周一 00:00 边界，7×6=42）；头/尾不完整桶丢弃、桶内任何 14400 缺口整桶丢弃
  （不跨缺口聚合）。
- OHLC = 首 open / 最高 high / 最低 low / 末 close，volume 求和；
  同一产品不混源（杜绝"日线现货、4H 合约"口径错配），日/周与 4H 同一条底层数据链。

## 5. as-of 与因果

- 事件 as_of = `signal_time + 14400`（**事件 bar 收盘后**）；higher TF 只取
  `close_time <= as_of` 的最新已收盘 bar（bisect）：`close_time == as_of` 可用、
  `as_of - 1` 秒不可用；无已收盘 bar / **未 ready / 未 closed → 受控拒绝**
  （A/B=false 并明示 reason），不抛未说明异常。
- 4H 序列从第一个 `is_closed=False` 起立即 **cutoff**：不读该 bar OHLC、
  不调 detector、后续即使标 closed 也不可见；已收盘前缀上的事件逐字不变
  （追加尾巴不改变旧结果；传全量或恰截至前缀结果逐字段一致）。
- 状态只预计算一次：4H/1D/1W 各一次 `precompute_market_states`，逐 as_of 只读查询；
  结果不随 K 线到达顺序变化，追加任何未来 bar 不改变已产生的事件与结果。

## 6. A/B 定义（用户决策：日线必须同向）

- 共同前提：week/day/trigger 三者均 ready 且 closed；`day.trend` 明确 up/down 且
  **严格等于事件方向**（日线 sideways/unknown 或反向 → A/B 均 false）。
- **A（周日同向）**：在 B 基础上另要求 `week.trend == 事件方向`。
- **B（日线同向，周线仅 ready+closed，方向不限）**：基线组。
- week_relation：agree / disagree / sideways（周线方向映射，无法映射 → sideways）；
  事件方向支持 long/short/buy/sell，统一归一 long/short。

## 7. MarketState（M1 只读复用）

- 波动公式：**ATR14/close 的 100 根历史百分位**（high=0.7 / low=0.3）与 5 根变化合成
  → contraction / transition / expansion（只读已收盘 bar，无 look-ahead）。
- K 线强度：**body_atr / range_atr / body_range / close_location 四项中 >= 3 项 strong
  → strong**；**volume 独立证据，不计入四项**、仅作旁证（volume_state 单独输出）。
- 趋势：因果 pivot + MA(20/60/120) + ADX(14)（adx_ranging=20 / adx_strong=35）
  → up/down/sideways + ranging/weak/strong。

## 8. 事件回放（replay 现有探测器）

- 复用 `monitor_signals.run_monitor_detectors`，策略固定 **ma / structure / macd**；
  逐根把「最近最多 **360 根**」前缀交给探测器（窗口外历史不参与计算）；
  按**生产 dedupe 键子集** `(strategy, event_type, direction, signal_time)` 只留首个
  （生产键 = plan:symbol:timeframe:strategy:event_type:direction:signal_time，
  本项目 plan/symbol/timeframe 固定故取其子集）。
- 事件字典与生产 `_event` 同构（strategy / event_type / direction / signal_time /
  reference_price 等），event_key = `strategy|event_type|direction|signal_time`，
  同时刻同策略重复触发只算一个事件。

## 9. 结果（outcome）契约

- reference = 事件 bar close（须有限正数且与 close 1e-6 相对容差内，否则拒绝；
  缺失才用 close）；**入场价 = 下一根 4H open**（entry_basis=next_4h_open_price，实际收益基准）。
- 窗口 = 事件 bar 后 **5/10/20/40 根完整连续已收盘 4H**（不含事件 bar；
  = 20/40/80/160 小时）；不足 → 未成熟（matured=false，结果字段全空）。
- 1R = 事件 bar 处 **Wilder ATR(14)**；MFE/MAE 从 0 起取非负极值（事件 bar 极值不计），
  long/short 完全镜像（同一 OHLC 下 short MFE == long MAE）；first = 窗口内**最早**
  先触及 +1R（positive）/ 先触及 -1R（negative，**fake=true**）/ 同 bar 双触
  （ambiguous，fake=null，不假设 bar 内路径）/ 均未触及（neither，fake=false），
  gap 跨阈值仍算触及；return = next_open → 窗口末 close 的方向收益
  （long: end_close−next_open；short 取反），return_r 除事件 ATR14、
  return_pct 分母为 next_open。
- **0 手续费 0 滑点** event study（costs.fees=0、slippage=0；pnl_note：逐事件
  窗口收益，**非组合 PnL**，无仓位重叠/资金占用假设）。

## 10. summary 契约（只从 events.csv 复算）

- `summarize_events_csv` 只读 events.csv 重新聚合，不信任内存对象；cohorts =
  raw / A / B / B_minus_A / B_week_agree / B_week_disagree / B_week_sideways，
  **成员关系非互斥**（恒等式：B = A ∪ B_minus_A；B = agree ∪ disagree ∪ sideways，
  两两不交；raw 含全部行；行唯一且严格排序）。
- decision 层按 **(time, direction, window) 去重**（多策略同根同窗口只计首信号）；
  `n_signals` = 同 (time, direction) 的**唯一 event_key 数**（4 窗口不重复计）。
- **n < 30 → exploratory=True**；窗口统计：n、unique_entry_points（去重
  (time, direction) 数）、win%+Wilson95（Wilson score interval，默认 z=1.96）、
  median return%、mean MFE/MAE_r、first 四类计数、fake 率
  （= negative_first / non-ambiguous known，known 排除 ambiguous；分母 0 → null）。
- 分组维度：direction / event_type / year / setup_vol_phase / trigger_vol_phase /
  trigger_candle_strength；events_rows=8376 / decision_rows=8328 为 V1 实际计数
  （详见 REPORT，不在此重复结果表）。

## 11. CLI 与产物

```bash
cd backend
python3 -m app.tasks.mtf_backtest_cli fetch  --output-dir ../reports/mtf-btc-backtest-v1/data
python3 -m app.tasks.mtf_backtest_cli verify --output-dir ../reports/mtf-btc-backtest-v1/data
python3 -m app.tasks.mtf_backtest_cli run --data-dir ../reports/mtf-btc-backtest-v1/data \
    --output-dir ../reports/mtf-btc-backtest-v1/results
```

- **失败处理**：fetch 任一校验失败 → stderr + exit 1，且不生成新的有效清单；
  已有目录可能保留旧产物，操作者必须以本次 exit code 并再次 verify 为准；
  run 在写结果前先 verify/契约校验，失败即拒绝执行；全部异常 → stderr + exit 1。
- run 在 verify 之后**再做产品/首尾再校验**（verify 只验哈希/连续性/聚合）：
  product==BTCUSDT、interval==4h、raw 非空、**首末精确匹配正式边界**、
  清单 raw_4h count/first/last 与 raw 一致；任一不符 ValueError 拒绝。
- verify 覆盖四层：① MANIFEST.sha256 与 source_manifest 一致；② 各 gzip 重算
  sha256 一致；③ raw_4h 解压后连续性/闭合通过；④ 日/周派生与独立重算一致。
- 确定性：同一输入 fetch/run 产物字节级一致；manifest 无时钟/绝对路径；
  summary 只从 events.csv 复算；run_manifest 记 market_state_config_hash、
  策略参数、A/B 原文规则、costs、entry_basis、各产物 sha256。

## 12. 结果与交付

- 结果一律链接 `../../reports/mtf-btc-backtest-v1/REPORT.md`（结论、解读、
  复现命令与各产物 sha256），本文不重复大结果表。
- 结论要点（详见 REPORT）：日线必须同向放行；周线不作硬否决、只作风险分层
  （agree 正常持有 / sideways 压缩预期 / disagree 只做 5 根短窗）；
  **未宣称任何优势**（多数 Wilson 区间含 50%，事后分组 + 多重比较）。
- **不自动切生产**：任何实盘引用须先通过 walk-forward / 独立留出集 / 影子验证。
- 延后事项：其它币种/周期、手续费与滑点、组合 PnL、参数寻优、任何自动切换。
