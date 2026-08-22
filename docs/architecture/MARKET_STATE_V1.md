# MARKET_STATE_V1 — 单周期 MarketState 模型（M1）

> 状态：M1 已落地（核心引擎 + 三个旧入口适配 + golden 零差异）。本文档描述
> 字段、公式、默认阈值、warmup、降级、适配关系与 as-of 契约。
> 关联：`backend/app/core/market_state.py`（引擎）、
> `backend/app/core/market_state_adapters.py`（兼容适配）、
> `docs/architecture/ALERT_V2_ROADMAP.md`（M1—M3 路线）。

## 1. 定位

MarketState 是回测、日报与后续多周期融合的唯一市场状态来源。M1 只做**单周期**：
- 趋势方向/强度；
- 波动率 level 与 phase（contraction/transition/expansion）；
- K 线质量（body/ATR、range/ATR、body/range、close-location、可选 volume）。

三个旧入口（regime_classifier 六态、trend_classifier 五态、日报 stage）通过
`market_state_adapters.py` 兼容收口，保留旧 ID、标签与文字；回测与日报为两个
正式迁移消费者；monitor 保持 ma/macd/structure 原路径（M1 不触碰）。

## 2. 公开接口

- `precompute_market_states(candles, config=..., volume_applicable=True) -> list[MarketState]`
  一次性预计算全部 bars，返回等长数组。
- `market_state_at(candles, index, config=..., volume_applicable=True) -> MarketState`
  恒等于 `precompute_market_states(candles[:index+1])[-1]`。
- `MarketState.to_dict()`：稳定、JSON 可序列化、无 NaN/Infinity。
- `MarketStateConfig.config_hash()`：全字段稳定序列化 sha256[:16]。

引擎是**纯计算**：不读数据库、不取网络、不看未收盘或未来 K 线。

## 3. as-of 契约（继承 M0）

1. 时刻 t 的状态只依赖 `candles[:t+1]`；
2. 中心摆动点只有经过右侧 pivot 根（`idx + pivot <= t`）后才确认可用；
3. 追加任何未来 K 线不得改变已产生结果（`test_market_state.py` 逐前缀断言
   每个历史位置不一致数 = 0）。

## 4. 字段与公式

### 4.1 trend

| 字段 | 公式/取值 |
|---|---|
| direction | up/down/sideways/unknown：`vote = swing_score/2 + ma_direction_score`；>0→up，<0→down，=0→sideways；ma60 未就绪→unknown |
| strength | ranging/weak/strong/unknown：ADX < 20→ranging，< 35→weak，≥35→strong（独立于方向） |
| swing_score | -2/0/2：已确认摆动点（idx+pivot≤t）最后两个高/低点，HH+HL→2，LH+LL→-2，其余 0 |
| ma_direction_score | +1/-1/0：MA20 vs MA60 |
| price_position_score | +1/-1/0：近 5 根收盘全部 >/< MA60 |
| adx | ADX(14) |

### 4.2 volatility

| 字段 | 公式/取值 |
|---|---|
| atr_price | ATR(14)/close |
| vol_percentile | ATR/price 在最近 lookback=100 根窗口内的百分位（rank/(n)） |
| vol_change_5 | (atr_price[t] - atr_price[t-5]) / atr_price[t-5] |
| level | percentile < 0.30→low，> 0.70→high，其余 normal；无值→unknown |
| phase | 低位且变化不升（change≤0 或缺失）→contraction；高位且变化不降（change≥0 或缺失）→expansion；其余→transition |
| previous_phase / phase_age | 上一根 phase；当前 phase 连续持续的根数 |

### 4.3 candle

| 字段 | 公式/取值 |
|---|---|
| direction | close>open→bullish，<→bearish，=→neutral |
| body_atr / range_atr | \|close-open\|/ATR、(high-low)/ATR |
| body_range | \|close-open\|/(high-low)，零振幅→None |
| close_location_long/short | (close-low)/range、(high-close)/range |
| strength | 四项阈值计数：body_atr≥0.8、range_atr≥1.0、body_range≥0.6、方向性 close_location≥0.75（多头用 long、空头用 short、十字不计）；≥3→strong，≤1→weak，=2→normal；ATR 缺失→unknown |
| volume_state | 复用 `signals/evidence.py` volume_evidence 口径（unknown/low/normal/elevated/high）；缺失、为零或不适用（volume_applicable=False）→unknown，不影响价格状态 |

### 4.4 features / audit

- features：close、ma20/60/120、atr、ma*_prev2（日报 slope 判定用 ma[t]-ma[t-2]）。
- reasons：每根 bar 的实际特征值与阈值判定原因（trend/volatility/candle/volume）。

## 5. 默认阈值（M1 初始工程值，未经参数寻优）

| 参数 | 值 |
|---|---|
| ADX ranging / strong | 20 / 35 |
| 波动百分位 low / high | 0.30 / 0.70 |
| body/ATR | 0.8 |
| range/ATR | 1.0 |
| body/range | 0.6 |
| directional close-location | 0.75 |
| pivot（摆动窗口） | 10 |
| 波动百分位窗口 | 100 |
| ATR / ADX 周期 | 14 |
| MA | 20 / 60 / 120 |

> ⚠ 这些阈值是**猜测的初始工程值**，尚无证据表明具有交易优势；M1 不做参数寻优
> （M3 用历史条件分布评估后由人决策是否调整）。

## 6. warmup 与降级

- 单字段自然降级：ma60 未就绪→trend unknown；ATR 未就绪→candle strength unknown；
  波动百分位窗口不足→vol unknown。
- `ready` = direction/strength/level/phase/candle_strength 全部非 unknown（约需
  ≥100 根）；未 ready 时 `missing_reason` 非空。
- 零振幅：body_range/close_location 为 None，质量计数降级为 weak（安全，不报错）。
- 缺 volume / volume=0 / volume_applicable=False：volume_state=unknown。
- **未收盘保护**：从第一个 `is_closed=False` 的 bar（cutoff）起不再消费价格数据——
  只读取 `candles[:cutoff]` 的 OHLC（cutoff 之后的 bar 可缺失 OHLC，仅有 time/is_closed），
  该 bar 及其后全部输出 `ready=False`、`is_closed=False` 的占位状态（缺失原因含
  「未收盘」）；此前已收盘 bar 的状态不受影响（已收盘前缀逐位一致）。
  三个正式消费者对未收盘占位保持不可用：regime -1、trend -99、日报抛 ValueError
  （调用方捕获后跳过该品种）。

## 7. 适配关系（market_state_adapters.py）

| 旧入口 | 映射 | 备注 |
|---|---|---|
| regime_classifier 六态 | `regime_components_from_state`：t=ADX 档（<20→0，<35→1，≥35→2），v=percentile 档（<0.5→0，≥0.5→1）→ `_REGIME_MATRIX[(t,v)]` | 旧六态用 0.5 切分波动，与核心 vol_level（0.30/0.70）无关，仅适配层；`classify_regime_at` / `precompute_regime_arrays` 的自定义 adx_period/atr_period/lookback **透传**为 MarketStateConfig（默认 14/14/100） |
| regime alignment | `alignment_from_state`：MA20/60/120 排列 +2/+1/0/-1/-2 | 旧 _ma_alignment 口径 |
| trend_classifier 五态 | `trend_regime_from_state`：score=swing+ma_dir+price_pos → score_to_regime（≥4→2，≥2→1，≥-1→0，≥-3→-1，否则-2）；`market_state_to_trend_array` 复现旧稳定过滤（[60,n) 打分、64 起过去 5 根≥4 同值） | 逐位一致（golden 锁定） |
| 日报 stage/phase/conclusion | `daily_stage_from_state`：bar_index+1≥122 时用旧 `_market_regime` 判定（positions/slopes 由 features 重算），否则数据不足 | 逐字一致（golden 锁定） |

若旧系统之间互相矛盾，canonical MarketState 给出自己的唯一结论（direction/
strength/vol/candle），适配器只做兼容映射，不把冲突藏进核心模型。

## 8. 迁移消费者

- 回测：经 `regime_classifier.classify_regime_at` / `precompute_regime_arrays` /
  `alignment_direction` 自动使用 MarketState（strategy_backtest_engine 未改动）。
- 日报：`analyze_instrument` 的 stage/tactical_phase/conclusion 改从
  `daily_stage_from_state` 获得，其余字段与文字不变。
- monitor：仍走 ma/macd/structure 原路径，未改动。

## 9. 验证

- `test_market_state.py`（21）：逐前缀一致性、market_state_at 等价、波动路径、
  K 线质量定向、阈值/配置/回放、append future bar、to_dict 无 NaN。
- `test_market_state_adapters.py`（13）：六态坐标/alignment/五态/日报 stage 边界。
- `test_market_state_legacy_parity.py`（6）：四组冻结序列，旧入口与回测 golden
  逐字段一致；fast/slow 等价。红→绿证据：临时把 ADX_STRONG 35→36 使 parity
  4 组 + adapters 1 个全红，还原后全绿（输出见 PROGRESS.md）。
