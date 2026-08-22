# OUTCOME_LABELING_V1 — M3A 历史结果标注（离线、纯计算、可回放）

> 状态：**M3A 已落地（2026-08-13），第一轮/第二轮整改完成，第三轮收口
> （最终候选审查）完成（2026-08-13）**。核心引擎
> `backend/app/core/outcome_labeling.py` + 数据契约
> `backend/app/schemas/outcome_labeling.py` + 100 项测试已交付；
> 纯计算离线标注，不接生产影子流（M3B 需独立评审 DB/运行风险后另行实施）。
> 关联文档：`MULTI_TIMEFRAME_V1.md`（M2 决策）、`MARKET_STATE_V1.md`（M1 状态）、
> `ALERT_V2_ROADMAP.md`（路线图）。

## 1. 目的与定位

M2 产生 eligible 的 V2 影子决策（事件时点快照）。M3A 把这些决策与**之后真实
发生的历史 4H K 线**对齐，产出**因果、诚实、可重放**的结果标签：每窗口
（5/10/20/40 根）的 MFE/MAE、首触及分类、假突破标记、状态迁移路径，
并做确定性汇总。这些标签是后续影子校准的证据；**标签是历史事实，不是预测**。

红线（继承路线图）：
- 只表达历史条件分布与时间窗口，不输出确定性预测；文档与代码都不得宣称
  「历史分布等于未来预测」。
- 未来 K 线只可生成标签，**不得改变事件时点 decision/risk/condition 快照**
  （决策 digest、三层条件快照、风险 ATR 全部来自 M2 决策，不重算）。
- 不接生产 monitor、不持久化、不发送；不做任何自动切换。

## 2. 输入契约

公开入口（`app/core/outcome_labeling.py`）：

| 入口 | 签名 | 说明 |
|---|---|---|
| `label_decision` | `(decision, candles_4h, label_as_of, config)` | 单事件标签 |
| `label_batch` | `(decisions, candles_4h, label_as_of, config)` | 批量标签（同一序列/时刻） |
| `summarize` | `(labels)` | 确定性汇总（只聚合成熟样本） |
| `decision_digest` | `(decision_dict)` | 决策快照严格 canonical 摘要（sort_keys + allow_nan=False，无 default=str） |

- `decision`：M2 `FusionDecision` 对象或 m2-v1 dict。dict 必须通过
  `validate_decision`（严格 schema：伪造 / NaN / 语义矛盾一律拒绝）且
  `eligible=True`（拒绝态不产生标签）。
- `candles_4h`：4H K 线列表（可含未来 bar；cutoff 后 OHLC 可缺且不读取）。
- `label_as_of`：标注时刻（非负整数秒）；只消费 `close_time <= label_as_of`
  且未显式 `is_closed=False` 的连续前缀。
- `config`：M1 `MarketStateConfig`；其 `config_hash()` **必须等于**
  决策 `config_hash.market_state`（同一配置重放，否则拒绝）。

输入校验（任一失败 → `ValueError`，受控错误）：
1. 按 `time` 稳定排序；time 必须唯一（重复 → 「4H bar time 重复」）。
2. 消费前缀内相邻 bar 的 time 差必须恒等于 4H 步长
   （步长 = 决策 `trigger.bar_close_time - trigger.bar_time`）。
3. 消费前缀内 OHLC 必须有限**正数**且
   `high >= max(open, close) >= min(open, close) >= low`。
4. 事件 bar（`signal_time`）必须存在于消费前缀；其 `close` 必须与决策
   `reference_price` 在 1e-6 相对容差内一致。
5. 风险口径硬化：`config.atr_period` 必须 == 14（Wilder ATR(14) 冻结，
   不以测试便利改变业务口径）。
6. 风险 ATR = 决策 `trigger.market_state.features.atr`，必须有限正数。
7. **同源重放**（第一轮整改）：用决策 `trigger.market_state.bar_index` 定位
   事件时点历史起点（time = `signal_time - bar_index * step`），只在该 slice
   上重算事件时点 MarketState；其**完整语义快照必须与决策 trigger
   market_state 逐字段一致**后才标注——事件前任意 bar 被篡改 → 受控拒绝。
   窗口路径也从同一 replay slice 逐窗口计算。

## 3. 结果定义

### 3.1 风险（R）

```
risk = { method: "wilder_atr", period: 14,
         atr_abs: 事件 4H MarketState.features.atr,   # 来自 M2 决策快照
         atr_pct: atr_abs / reference_price }          # 相对参考价的百分比（小数比例）
```

`reference_price` = 决策 `trigger_event.reference_price`（= 事件 4H bar close
= 决策 trigger market_state features.close）。

### 3.2 窗口与成熟

窗口 = 触发 bar **之后**的 5/10/20/40 根完整连续已收盘 4H bar，**不含触发
bar**。每窗口独立判定：

- **成熟（matured）**：事件 bar 后连续 N 根全部在消费前缀内且已收盘。
- **删失（censored）**：结构化稳定码 `censor_code` + 人话 `censor_reason`
  （第二轮整改，判定**先看时间**；第三轮收口修正闭合公式）：
  - `label_as_of_not_reached`：窗口第 N 根 expected close_time
    （`signal_time + (N+1)*step`，第 N 根未来 bar 开盘 = `signal_time+N*step`、
    收盘 = `+(N+1)*step`）`> label_as_of`（观察截止未到；同一 as_of 下
    追加任何不可见未来 bar 不改变完整 label——输入形态不改变输出；
    `t+(N+1)*step-1` 不成熟、精确边界 `t+(N+1)*step` 成熟）；
  - `unclosed`：截止已到但窗口内存在显式 `is_closed=False` 的 bar；
  - `missing_bars`：截止已到但事件 bar 后已见 bar 总数不足 N。
  - 成熟窗口 `censor_code=None`。

删失窗口不携带任何结果字段（mfe/mae/first_touch/fake/end/path 全空）。

### 3.3 MFE / MAE

```
对 long：  fav_i = high_i - entry；  unfav_i = entry - low_i
对 short： fav_i = entry - low_i；   unfav_i = high_i - entry
MFE_abs = max(0, max_i fav_i)；  MAE_abs = max(0, max_i unfav_i)   # 从 0 起取非负极值
MFE_r = MFE_abs / R_abs； MFE_pct = MFE_abs / entry
MAE_r = MAE_abs / R_abs； MAE_pct = MAE_abs / entry
```

- **事件 bar 极值不计**：只统计窗口内 bar。
- long/short 完全镜像：同一 OHLC 下 short 的 MFE == long 的 MAE（逐字段）。
- `entry` = `reference_price`。

### 3.4 首触及与假突破

逐 bar 扫描窗口（取**最早**触及 bar）：

| 情形 | first_touch | fake_breakout |
|---|---|---|
| 同 bar 首次同时触及 ±1R | `ambiguous_same_bar` | `null` |
| 先触及 +1R（有利方向） | `positive_first` | `false` |
| 先触及 -1R（不利方向） | `negative_first` | `true` |
| 窗口内都未触及 | `neither` | `false` |

- **不得假设 OHLC 内路径**：同 bar 内 high/low 的先后不可知，双触一律
  `ambiguous_same_bar`，不做强判。
- 对 long：+1R = `high >= entry + R`（上涨，有利）；-1R = `low <= entry - R`。
  对 short 镜像：+1R = `low <= entry - R`（下跌，有利）；-1R = `high >= entry + R`。
- gap 跨阈值仍算触及（bar 开盘已越过 ±1R 亦然）。

### 3.5 状态路径

- **事件态** = 决策快照 `trigger.market_state` 的
  `volatility.phase`（contraction/transition/expansion）与
  `trend.strength`（ranging/weak/strong）——**不重算**，保持与决策一致。
- **逐 bar 路径** = 窗口内每根 bar 的 `(bar_time, vol_phase, trend_strength,
  open, high, low, close, volume)`（第二轮整改：**path 含完整 OHLC**；第三轮
  收口：**path 再带 volume**），使 schema 能基于逐 bar OHLC 独立复算
  MFE/MAE 与首触及分类，并能重跑 M1 precompute 验证状态路径。用与决策相同
  `config_hash` 的 M1 `precompute_market_states` 因果计算。
  **体积取舍**：path 从 3 数值扩展到 8 字段（40 根窗口 ≈ 320 数值/窗口），
  换取「标签任何字段不可独立于 path 伪造」的可验证性——以体积换审计。
- **终点态** = 窗口最后一根 bar 的状态（= 路径末步）。

### 3.6 自包含可重放（第三轮收口）

OutcomeLabel 额外携带两份**自包含重放证据**（离线存储体积取舍，见下）：
- `market_state_config`：完整 M1 `MarketStateConfig` 字段快照（与
  MarketStateConfig 一一对应、`atr_period=14`）；schema 用它构造
  `MarketStateConfig` 并重算 `config_hash()`，必须等于
  `config_hash.market_state`。
- `replay_context`：事件时点 replay slice 全部 bar
  `(time, open, high, low, close, volume)`（原始起点到事件 bar，含）；
  schema 校验严格连续（4H 步长）、末根 `bar_time == signal_time`、
  末根 `close ≈ reference_price`、OHLC 有限正数、volume 有限非负，并对
  `replay_context` 与 `replay_context + 每个成熟窗口 path` 重跑 M1
  `precompute_market_states`：事件末状态必须与决策 trigger market_state
  逐字段一致；逐 bar 验证 path 的 `vol_phase/trend_strength`（路径成套
  伪造 → 拒绝）。**不修改 M1**，只消费其公开纯函数。

**体积取舍（离线存储）**：`replay_context` 与事件时点历史等长（约
bar_index+1 根 × 6 数值/根，如 601 根 ≈ 3606 数值/标签）；path 8 数值/根。
换取「标签可脱离原始输入独立重放验证」。输出仍深不可变（递归冻结 +
to_dict 脱钩）且 JSON `allow_nan=False` 恒可序列化。

### 3.7 输出结构（`to_dict()`，JSON `allow_nan=False` 恒可序列化）

```
schema_version: "m3a-v1"
decision_digest: 决策快照严格 canonical JSON 的 sha256 前 16 位（16 位小写 hex）
symbol / as_of / label_as_of / signal_time / direction / reference_price
risk:        { method: "wilder_atr", period: 14, atr_abs, atr_pct }
config_hash: { market_state, fusion }          # 与决策一致
event_state: { vol_phase, trend_strength }
snapshots:   { context, setup, trigger }       # 三层条件快照（role/timeframe 固定）
decision_snapshot: 完整 normalized m2 decision dict（深冻结，可验证审计）
market_state_config: 完整 M1 MarketStateConfig 字段快照（重算 hash 匹配）
replay_context: [ {time, open, high, low, close, volume}, ... ]  # 原始起点→事件 bar
windows:     [ 5, 10, 20, 40 ] 按序
  每窗口: window / matured / censor_code / censor_reason /
          mfe_r / mfe_pct / mae_r / mae_pct /
          first_touch / fake_breakout /
          end_vol_phase / end_trend_strength / end_close / end_time /
          path: [{bar_time, vol_phase, trend_strength,
                  open, high, low, close, volume}, ...]
```

可验证审计（schema 层，第一轮 + 第二轮整改）：
- `decision_snapshot` 用 M2 `MarketFusionOut` 重验证（伪造/拒绝态/NaN → 拒绝）；
- `decision_digest` 用严格 canonical JSON 重算，必须与顶层一致；
- 顶层 symbol/as_of/signal_time/direction/reference/config_hash、三层
  snapshots、event_state、risk 全部与 decision_snapshot 逐字段一致；
- snapshots 三层 role/timeframe 固定（context/1week、setup/1day、
  trigger/4h）且 available/ready/is_closed；
- 每窗口：reference==trigger features.close；risk 等式（atr_pct==atr_abs/
  reference）；mfe_pct≈mfe_r*atr_pct、mae 同理；path bar_time 从
  signal_time+step 严格连续、长度==window、末步与 end_time/end_close/
  end 状态一致；end_time==signal_time+window*step；
- **schema 独立复算（第二轮整改）**：基于 direction/reference/risk 与
  path 逐 bar OHLC 重算每个成熟窗口的 MFE/MAE（R 与 pct）、首触及分类与
  fake_breakout，逐项必须匹配——成套伪造（如 mfe 同倍数、first_touch+fake
  同伪）也拒绝。
- **跨窗口约束（第三轮收口）**：成熟窗口必须是 [5,10,20,40] 的连续前缀；
  较短成熟 path 逐字段等于较长 path 前缀；MFE/MAE 随窗口不减；若短窗已触及
  （非 neither），长窗 first_touch/fake 不得改变——交换合法 w5、成熟倒挂、
  路径不一致均拒绝。
- **时序语义（最终审查）**：按 step/signal_time 校验每窗口与
  label_as_of/censor_code 的时序：成熟窗口要求 `label_as_of >=
  signal_time+(window+1)*step`；未成熟窗口 `label_as_of_not_reached ⟺
  label_as_of < 边界`（边界已到只能 missing_bars/unclosed；schema 单凭标签
  无法区分后二者——missing=截止已到但 bar 不存在、unclosed=显式未收盘，二者
  由引擎在原始输入上判定，文档说明此边界）。精确边界 `label_as_of ==
  signal_time+(window+1)*step` 成熟合法；边界前 `-1` 秒不成熟。
- **M1 重放验证（第三轮收口）**：用 market_state_config 构造
  MarketStateConfig，对 replay_context（+ 每个成熟窗口 path 重建）重跑 M1
  precompute——事件末状态/完整 trigger MarketState 语义一致，逐 bar 验证
  path 的 vol_phase/trend_strength（路径成套伪造拒绝）。

## 4. 汇总（`summarize`）

只聚合**成熟**样本；空报告（n=0）与全删失（n>0，samples=0/censored=n）
区别清楚；混合 symbol → `ValueError`；**重复 decision_digest → `ValueError`**；
**混合 config_hash → `ValueError`**（必须同一 M1/M2 配置重放）；**混合
label_as_of → `ValueError`**（第二轮整改：报告只能同一观察截止，不得取 max
冒充统一口径）。`summarize` 对 OutcomeLabel 对象也先 `to_dict → 
validate_outcome_label`（绝不信任 dataclass 内存对象）。

- 每窗口：`samples`（成熟数）/ `censored`（删失数）、
  `mean_mfe_r` / `mean_mae_r` / `mean_mfe_pct` / `mean_mae_pct`、
  四类首触及计数、`fake_rate`（公式 = `negative_first /
  (samples - ambiguous_same_bar)`，分母 0 → `null`）。
- `groups`（第一轮整改补齐维度）：按 `(window, direction, event_type,
  Context direction+strength, Setup direction+strength+vol_phase,
  Trigger vol_phase+candle_strength+volume_state)` 稳定排序分组，每组样本数 /
  positive_first 数及率 / MFE/MAE 均值。
- `transitions`：按 `(window, 事件态 phase → 终点态 phase, 事件态 strength →
  终点态 strength)` 稳定排序分组，每组样本数 / positive_first 数及率。
- 每窗口 group/transition samples 总和 == 窗口 samples；行唯一且**严格排序**
  （第二轮整改：倒序拒绝）。
- **可审计元信息（第二轮整改）**：输出统一 `label_as_of`、统一 `config_hash`
  （空报告为 None）、唯一 `decision_digests`（稳定排序、数量 == n_decisions）。

批量受控规则：`label_batch` 同一 candles 序列 → **拒绝混合 symbol**；重复
decision（相同 `decision_digest`）→ `ValueError`；标签按输入顺序返回，
结果确定性（重复调用逐字段一致）。

schema 汇总交叉校验（第一轮 + 第二轮整改）：samples==0 时均值 None / touch
全 0 / fake None（全删失合法，不再误判 censored）；touch 四类之和 == samples；
fake_rate == negative_first/(samples-ambiguous)（分母 0 → null）；
group/transition positive_first_rate == positive_first/samples；
每窗口 group/transition samples 总和 == 窗口 samples；行唯一且严格排序；
samples+censored == n_decisions；元信息（config_hash/decision_digests/
label_as_of）交叉校验；均值非负。

**重要边界（第三轮收口）**：`validate_outcome_summary` 只承诺「严格结构与
内部算术」——**仅凭 summary 自身无法证明源数据**（group 均值被 +100、
各组 positive_first/rate 成套改写、decision_digests 同长度替换在结构上
都合法）。审计入口是 **`validate_outcome_summary_against_labels(summary,
labels)`**（source-linked validator）：对源 labels 全部先严格校验，再用
`summarize` 重算并逐字段比较，不匹配抛 `ValueError`。

## 5. as-of 与因果保证

- 只消费 `close_time <= label_as_of` 且未显式未收盘的连续前缀；cutoff 之后
  OHLC 可缺且**不得读取**（与 M2 同契约）。
- **追加任意未来 K 线不改已成熟窗口**：窗口 bar 的 MarketState 只依赖截至
  窗口末 bar 的前缀。
- **终点后数据不改早期窗口**：label_as_of 从窗口 5 移到窗口 40，5/10 窗口
  逐字段不变。
- **decision digest 与事件时点快照不变**：未来数据只生成标签，不改决策。
- **截止之外 bar 除 time 用于 cutoff 外完全不可见（主审反例整改）**：追加
  截止之外的 bar（无论 is_closed/OHLC/volume/数量如何）不改变任何 label
  字段——完整标签逐字段不变。
- **as_of cutoff 优先于 is_closed（主审反例整改）**：cutoff 判定先按
  `close_time > label_as_of`（as_of），再按显式 `is_closed=False`；截止之外
  的未收盘 bar 不得把删失从 missing_bars 改写为 unclosed。
- **missing 数量只基于可见已收盘前缀（主审反例整改）**：missing_bars 的
  reason 根数 = 事件 bar 后在可见已收盘前缀（closed）内的 bar 数，不读取
  截止之外的 bar。

## 6. 示例

构造：事件 bar index=500（long），`R_abs` 来自决策快照；窗口 bar 相对 entry
按 R 倍数设计：`[(0.1, 0.5, -0.3, 0.4), (0.3, 1.2, -0.1, 1.1), ...]`（前 5 根）。

窗口 5 输出（手算）：
```
mfe_r = 1.2      # bar2 high = entry + 1.2R
mae_r = 0.5      # bar5 low  = entry - 0.5R
first_touch = "positive_first"   # bar2 先触及 +1R
fake_breakout = false
path = [ {bar_time=…501, …}, … ]  # 5 步，bar_time 每步 +14400
end_vol_phase = …  # 路径末步 phase
```

若事件 bar 自身 high=+2R / low=-2R（极值），**不计入** MFE/MAE（仍为
1.2 / 0.5）；若实现错误把事件 bar 计入则测试 `test_event_bar_extremes_excluded`
立即变红（反向验证证据，见 PROGRESS）。

## 7. 边界与已知限制

- 1R 只取事件时点 ATR(14)，窗口内不重估风险；这是「初始风险」口径。
- 同 bar 双触不区分先后（诚实缺失，不编造路径）。
- 汇总 `fake_rate` 的已知样本不含 `ambiguous_same_bar`。
- 股票时区、1H 周期、生产影子流（M3B）与校准（M3C）不在本里程碑。

## 8. 验证（任务 3）

- `backend/tests/test_outcome_labeling.py`：**100 项**，覆盖数值手算、镜像、
  事件 bar 极值不计（evaluate 前构造）、0 下限、四窗口、删失三原因、缺口/
  重复/乱序、cutoff 缺 OHLC、同 bar 双触、gap 跨阈值、ATR/hash/伪造/拒绝态、
  未来追加、终点后数据、状态路径、深不可变、严格 schema、批量重复、golden
  摘要，第一轮整改全部暗卷回归（风险口径、同源重放、可验证审计、标签/汇总
  schema 语义、分组维度、批量混合 symbol），第二轮整改暗卷回归（全删失
  schema、不信任 dataclass 对象、混合 label_as_of、同 as-of 输入形态不变、
  path OHLC 复算、censor_code 稳定码、meta 元信息、严格排序），以及第三轮
  收口回归（闭合公式边界 t+(w+1)*step-1 不成熟/精确边界成熟、跨窗口连续
  前缀与路径一致、M1 重放验证路径成套伪造拒绝、market_state_config 快照与
  hash 重算、replay_context 校验、path volume、source-linked validator、
  20 组固定 seed 随机 OHLC 属性矩阵），以及主审反例回归（截止外 bar
  is_closed=False/完整 is_closed=True/多根远期 → 输出逐字段不变且仍
  missing_bars；截止以内显式未收盘 → unclosed 保留）。
- 反向红→绿（M3A 首轮）：临时破坏「窗口包含事件 bar」→ 2 failed；临时破坏
  「双触强判先后」→ 2 failed；还原后 54 全绿（证据存 PROGRESS）。
- 第一轮整改红→绿：24 项暗卷回归测试先红灯（19 failed 复现）→ 修复 →
  78 全绿；手动复跑全部暗卷场景均 REJECTED（证据存 PROGRESS）。
- 第二轮整改红→绿：11 项暗卷回归测试先红灯（10 failed 复现）→ 修复 →
  89 全绿；手动复跑 6 类暗卷场景全部 REJECTED/一致（证据存 PROGRESS）。
- 第三轮收口红→绿：8 项暗卷回归测试先红灯（7 failed 复现）→ 修复 →
  97 全绿；手动复跑 4 类暗卷场景全部 REJECTED（证据存 PROGRESS）。
- 最终审查整改：时序语义 schema 校验（1 项新反例先红灯 → 绿灯）、
  修掉随机属性测试中的 or True 假断言（独立 prev_mae，故意倒挂 1 failed →
  还原 1 passed）、修正 core docstring 闭合公式。全量 100 项全绿。
- 主审独立反例整改红→绿：2 项回归测试先红灯（追加远期 is_closed=False 改
  写 censor_code/reason，1 failed 复现）→ 修复（as_of cutoff 优先、
  missing 数量只基于可见 closed 前缀）→ 100 全绿；手动复现主审场景
  追加远期 bar 后逐字段相等 ✓（证据存 PROGRESS）。
- 全量后端 418 passed（318 + 100，0 skipped）；M2/M1/M0 210 无回归；tsc
  通过；m3-pre 快照 verify/drill/同构恢复通过。
