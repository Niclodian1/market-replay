# MULTI_TIMEFRAME_V1 — M2 周/日/4H 多周期角色融合

> 状态：**M2 已交付（2026-08-13，三轮复验整改完成）**。纯计算影子决策，不持久化、不发送、
> 不接生产 monitor，不改变 V1 任何行为。本文为 M2 架构与可回放契约。
> 关联：`docs/architecture/ALERT_V2_ROADMAP.md`（M2 路线定义）、
> `docs/architecture/MARKET_STATE_V1.md`（M1 单周期 MarketState）、
> `PROGRESS.md`（M2 执行记录，含复验整改）。

## 1. 目标与边界

从「各周期独立判断后简单 OR」改为**角色化融合**：

```
周线定 Context（大环境方向）→ 日线定 Setup（候选结构）→ 4H 给 Trigger（入场时机）
```

- 同一 `as_of` 下，任何 K 线到达顺序 / 未来追加都产生**完全一致**的融合结果；
- 只用已收盘、已确认 K 线；输出可解释、可回放的影子决策；
- 首版只服务 crypto UTC：`bar.time` 是开盘秒；
  `close_time = time + duration`（1week=604800，1day=86400，4h=14400）；
- 股票时区、1H 周期、Pinbar 不进入首版（见 §8 延后项）。

代码边界（白名单，其余只读）：
- 新增 `backend/app/core/multi_timeframe.py`（纯计算，无网络/库/时钟读取）；
- 新增 `backend/app/schemas/market_fusion.py`（Pydantic 数据契约，非 DB/API schema）；
- 新增 `backend/tests/test_multi_timeframe_fusion.py`（57 项：24 原项 + 11 首轮反例 + 15 第二轮反例/整改 + 7 第三轮反例）；
- 未改 V1 文案 / dedupe / payload / 渠道；未接 Pinbar / 1H / 股票；
  未提交 commit；未动 monitor / API / models / DB migration / providers /
  现有测试 / golden / 前端 / 依赖。

## 2. 时间对齐（任务 1）

### 2.1 cutoff 语义

对每个周期：

1. **按 `time` 稳定排序**（唯一 time 前提下重排结果相同）；
2. 逐根计算 `close_time = time + duration`；
3. 首个满足以下任一条件的 bar 即 **cutoff**，其 OHLC 可缺（不读取），
   之后全部不可见：
   - 显式 `is_closed is False`（数据源明确未收盘）；
   - `close_time > as_of`（尚未收盘）；
4. 只消费 `bars[:cutoff]` 交给 M1 `precompute_market_states`；
   **过滤先于任何 OHLC 读取** → cutoff 处缺 OHLC 不产生 KeyError；
5. 边界精确：`close_time == as_of` 可用；`as_of - 1`（早 1 秒）不可用；
   晚 1 秒只影响下一根 bar 的可见性，不影响当前对齐结果。

as-of 契约：融合在时刻 `t` 只依赖每周期 `candles[:k+1]`（k 为截至
`t` 的最后已收盘 bar）。追加任何未来 K 线不得改变已产生的结果
（`test_prefix_history_immutable_across_asofs` 逐 as_of 守护）。

### 2.2 对齐输出 `AlignedPeriod`

```
role / timeframe / available / unavailable_reason /
bar_time（最后已收盘 bar 开盘秒）/ bar_close_time（= bar_time + duration）/
config_hash（M1 config）/ market_state（M1 MarketState，可空）/
reasons（消费摘要：cutoff@N（消费 bars=N，最后 bar_time=…））
```

受控不可用（不抛异常，缺周期 / 历史不足 / 重复时间）：
`available=False` + `unavailable_reason`；非法 timeframe（非
1week/1day/4h）或非法 as_of → `ValueError`；重复 time（可见区域内）
→ 受控不可用「冲突重复时间」。

> 审计 `reasons` 只含「消费摘要」而不含 cutoff 触发条件：同一 as_of 下
> 「传入恰截至前缀」与「传入含未来 bars 的全量序列」必须逐字段一致
> （重放一致性），触发条件是输入形态差异而非对齐结果差异。

## 3. 角色融合（任务 2）

### 3.1 门控矩阵（少假设，任务书拍板）

| 层 | 角色 | 周期 | 门控 | 通过 | 拒绝 code |
|---|---|---|---|---|---|
| Context | 周线大环境 | 1week | 状态 ready；方向 up→long / down→short | `ctx_ok` | `ctx_not_ready`、`ctx_sideways`、`ctx_reject_direction` |
| Setup | 日线结构 | 1day | 状态 ready；方向映射后与周线及 Trigger **同向** | `setup_ok` | `setup_not_ready`、`setup_inconsistent` |
| Trigger | 4H 入场 | 4h | 状态 ready；事件来自**对齐后最新已收盘 4H bar**；方向归一 | `trigger_ok` | `trigger_not_ready`、`trigger_wrong_period`、`trigger_stale`、`trigger_bad_direction` |

- 任何一层不满足 → `eligible=False` 并说明具体层；拒绝也必须返回完整审计结果；
- 接受路径三层均明确 pass（reason_codes 恰为
  `ctx_ok, setup_ok, trigger_ok`）；
- 4H 方向 / 强度**不**额外过滤（只要求 ready）；
- sideways / unknown 方向一律拒绝（周线、日线均不映射 long/short）；
- **禁止对多周期信号做简单 OR**。

### 3.2 Trigger 事件契约

接入现有事件字典（`signals/monitor_signals._event` 同构）：
`event_type / direction / signal_time / reference_price / summary / payload`。
事件必须**证明**来自 4H（复验整改：防只校验外壳）：

- 方向归一：`long/buy → long`，`short/sell → short`（别名表由
  `MultiTimeframeConfig.buy_sell_map` 驱动）；其余 → `trigger_bad_direction`；
- 周期：顶层与 `payload.timeframe` **必须一致**且等于 Trigger 周期（默认 4h）；
  缺失 / 冲突 / 非 4H → `trigger_wrong_period`（不能只信顶层）；
- 时间：`signal_time` 必须为**合法整数秒**（缺失/NaN/Inf/负数 →
  `trigger_bad_time`）且等于对齐后的最新已收盘 4H `bar_time`
  （否则 → `trigger_stale`）；
- 参考价：`reference_price` 必须为**有限正数**且与对齐 4H MarketState.close
  一致（相对容差 1e-6）→ 否则 `trigger_bad_reference`（降级 None，无 NaN）；
- event_type 必须非空（`trigger_bad_event_type`）；
- payload 校验（存在字段必须与顶层/对齐契约一致，否则 `trigger_payload_conflict`）：
  `payload.direction` 存在就必须可归一且等于顶层；`payload.signal_type` 必须
  等于顶层 event_type；`payload.reference_price` 必须有限正数且等于顶层；
  `occurred_at`/`confirmed_at` 存在必须等于 `signal_time`；
  `bar_closed` 存在必须严格为 bool True（0/'true'/None/False/1 均拒绝）；
- **M2 输出不携带原始 payload**（Trigger 摘要只含经校验字段，避免未验证对象
  污染稳定 JSON）；to_dict 恒可 `json.dumps(out, allow_nan=False)`；
- 任何不可信事件字段受控拒绝/降级，不以未说明异常穿透。

### 3.3 reason codes（稳定，含中文 explanations）

| code | 层 | 含义 |
|---|---|---|
| `ctx_ok` | context | 周线就绪且方向明确（up→long / down→short） |
| `ctx_not_ready` | context | 周线缺数据 / 历史不足（含未收盘 cutoff） |
| `ctx_sideways` | context | 周线方向为 sideways/unknown，拒绝 |
| `ctx_reject_direction` | context | Context 逆势：周线方向与 Trigger 相反 |
| `setup_ok` | setup | 日线就绪且与周线及 Trigger 同向 |
| `setup_not_ready` | setup | 日线缺数据 / 历史不足 |
| `setup_inconsistent` | setup | 日线方向 ≠ 周线 或 ≠ Trigger（含无法映射） |
| `trigger_ok` | trigger | 4H 就绪，事件来自最新已收盘 4H bar 且方向合法 |
| `trigger_not_ready` | trigger | 4H 缺数据 / 历史不足 |
| `trigger_wrong_period` | trigger | 事件周期缺失 / 顶层与 payload 冲突 / 非 4H |
| `trigger_stale` | trigger | signal_time 不是最新已收盘 4H bar |
| `trigger_bad_time` | trigger | signal_time 非法（非整数秒 / NaN / Inf / 负数） |
| `trigger_bad_direction` | trigger | 方向非 long/buy/short/sell |
| `trigger_bad_reference` | trigger | reference_price 缺失/非法（已降级 None） |
| `trigger_payload_conflict` | trigger | payload 与顶层/对齐契约冲突 |

### 3.4 端到端输出 `FusionDecision.to_dict()`（schema_version=m2-v1）

```
schema_version / symbol / as_of / direction / eligible /
context / setup / trigger（AlignedPeriod.to_dict()）/
trigger_event（TriggerSnapshot 快照）/ reason_codes / explanations /
config_hash{market_state, fusion} / evidence{volatility, candle, volume}
```

- 稳定 JSON、无 NaN/Infinity（所有 float 经 safe 降级为 None；恒可
  `json.dumps(out, allow_nan=False)`）；
- Pydantic `MarketFusionOut` 对端到端 dict 严格校验（第二轮整改：全模型
  `extra='forbid', strict=True`；role/timeframe/direction/reason codes 全
  Literal 枚举；ConfigHash 恰有 market_state/fusion 两键且 16 位小写 hex；
  available=True ⇒ unavailable_reason=None、bar_time/close_time/state 非空、
  state.ready 且 is_closed、state.bar_time==period.bar_time、config_hash 链
  一致、close_time>bar_time；eligible=True ⇒ 方向 long/short、恰三 ok codes、
  三周期 available、trigger_event.timeframe=='4h'、normalized_direction==
  direction、signal_time==trigger.bar_time、reference_price 非空；
  eligible=False ⇒ direction=None；codes/explanations 等长；available=False
  不强求 state 为空；JSON roundtrip 后仍通过）；
- **第三轮整改（决策快照深不可变 + schema 语义等价）**：
  - FusionDecision 不保存原始 trigger_event，只存 frozen `TriggerSnapshot`
    （evaluate 时一次性复制规范化审计字段），调用者后续修改原事件不改变
    已生成决策；summary 受控字符串（None→None、str→原样、其他→str()）；
  - aligned 与 _direction_map 构造时复制并深冻结（MappingProxyType），
    修改 decision 内部（d.aligned[...]=、d._direction_map[...]=）均失败；
    to_dict 每次返回全新 dict/list，修改返回值不影响对象；
  - **eligible iff reason_codes 恰为三个 OK codes**；eligible=False 必须
    至少一个 reject code（不能全 OK/空）；direction 等价不变；
  - evidence 用严格 typed 角色模型（context/setup/trigger 三键固定、
    extra forbid、数值 finite），全模型 allow_inf_nan=False，嵌套 NaN/Inf
    一律 ValidationError；MarketState 状态字符串 Literal 化；
  - eligible=True ⇒ reference_price 必须与 trigger.market_state.features.
    close 在 1e-6 相对容差内（二者有限正数）；event_type/raw_direction 非空；
- TriggerEventOut **不携带原始 payload**；
- 证据（波动 level/phase、K 线 strength、volume_state）来自三周期
  MarketState，**只写证据**；未经 M3 统计不得宣称优势或预测行情大小。

### 3.5 配置即行为（复验整改，第二轮收紧）

`MultiTimeframeConfig` 的**每个字段都被实际消费**，不是只进 hash 的空转配置：

- `durations`（1week/1day/4h 三键正整数秒）→ 驱动 `align_period` 的 close_time 计算；
- `direction_map`（恰有 up/down → long/short）→ 驱动 Context/Setup 方向映射；
- `buy_sell_map`（事件方向别名，键规范化为非空小写，重复归一键拒绝）→
  驱动 `normalize_direction`。

第二轮整改：

- **roles 是固定业务法则，不是配置项**：运行统一使用 ROLE_TIMEFRAMES
  （context→1week、setup→1day、trigger→4h），MultiTimeframeConfig 不暴露
  roles 字段（传入即 ValueError），与 schema 固定三角色周期同源一致；
- **深冻结**：构造时复制 dict 并转 `MappingProxyType` 只读映射，调用者原
  dict 后改或 cfg 字段赋值都不改变 hash/行为；config_hash 显式序列化真实字段；
- schema_version 固定 m2-v1 不可任意输入；配置非法（三键/正时长/up-down/
  long-short/键规范）→ ValueError。
- 自定义配置必须有行为差异（`test_custom_config_drives_behavior` 守护）。

## 4. 降级与审计

- 缺周期 → 该角色受控不可用 → 对应 `*_not_ready`，其余角色照常审计；
- 历史不足（M1 ready=False）→ `*_not_ready`（含 missing_reason）；
- 重复时间 → `冲突重复时间`（受控不可用，不抛异常）；
- 非法 timeframe / as_of → `ValueError`（调用方错误，不属于数据降级）；
- 拒绝路径同样返回完整审计（三角色快照 + trigger 摘要 + evidence）。

## 5. 示例

### 5.1 成功路径（全 up 行情，long Trigger）

```
evaluate("BTCUSDT", candles, as_of, trigger_long, QUICK_CONFIG)
→ eligible=True, direction="long"
  reason_codes = [ctx_ok, setup_ok, trigger_ok]
  context:  1week bar_time=…, up（strong）    → long
  setup:    1day  bar_time=…, up（strong）    → long（与周线/Trigger 同向）
  trigger:  4h    bar_time=…（== signal_time）→ long 事件，ready
```

### 5.2 逆势拒绝（周线 down，Trigger long）

```
reason_codes = [ctx_reject_direction, setup_inconsistent, trigger_ok]
→ eligible=False；层 = context（Context 逆势 Trigger）
```

### 5.3 陈旧 Trigger（signal_time 为上一根 4H）

```
reason_codes = [ctx_ok, setup_ok, trigger_stale] → eligible=False；层 = trigger
```

## 6. 验收证据（2026-08-13，含两轮复验整改）

- 新增 `test_multi_timeframe_fusion.py` **57 项全绿**（覆盖：到达顺序不变、
  close 边界、未来/未收盘缺 OHLC 不消费、追加未来不改结果、三类状态不足、
  逆势拒绝、Setup 拒绝、buy/sell 归一、陈旧 Trigger、周期错误、方向非法、
  成功路径、证据不参与硬门控、schema/JSON/config 回放、
  **逐前缀/不同 as_of 历史不可变**（≥70 个 as_of，含成功路径）、
  非法 timeframe → ValueError、缺周期/重复时间受控不可用、周线 sideways；
  复验反例：Trigger 周期缺失/冲突拒绝、signal_time/reference_price 非法受控、
  NaN/Inf 拒绝、payload 冲突拒绝、配置真正驱动行为、非法配置 ValueError、
  schema 语义 ValidationError、非法 bar time 明确 ValueError；
  第二轮反例：roles 固定业务法则、配置深冻结（MappingProxyType 只读 +
  原 dict 后改不影响）、buy_sell_map 键规范化、输出无 payload +
  allow_nan=False 恒可序列化、payload.direction 必须可归一、bar_closed 严格
  True、event_type 非空、signal_type 匹配、payload reference 匹配顶层、
  reference 匹配对齐 4H close（容差 1e-6）、schema 拒绝伪造
  （state None / config_hash 任意键 / available 缺状态）、as_of 前置校验、
  trigger_event 非 dict 受控 ValueError、JSON roundtrip 后仍通过）；
  第三轮反例：决策快照不随调用者 ev 漂移、_direction_map/aligned 深冻结、
  to_dict 返回值修改不反写、eligible iff 三 OK codes（False+全 OK/空拒绝）、
  evidence 嵌套 NaN/Inf 拒绝、eligible reference 必须匹配 close（1e-6）、
  event_type/raw_direction 非空与 timeframe=='4h'）；
- 红→绿实验（临时破坏实现后新测试变红，还原后全绿）：
  - 破坏 close 边界（`close_time >= as_of`）→ **9 failed**；
  - 破坏方向门控（删除 ctx_reject_direction）→ **2 failed**；
  - 复验：破坏配置驱动（方向归一硬编码）→ **1 failed**；
  - 复验：破坏 schema 语义（跳过 eligible 校验）→ **1 failed**；
  - 第二轮：破坏 JSON 卫生（恢复输出 payload）→ **1 failed**；
  - 第二轮：破坏 schema 缺 state 校验 → **1 failed**；
  - 第三轮：破坏深不可变（保存原始引用 + 不冻结）→ **2 failed**；
  - 第三轮：破坏 evidence typed（dict[str,Any] 无 NaN 约束）→ **1 failed**；
- 回归：M1 三文件 52 passed、M0 六文件 101 passed、全量
  `pytest -q --ignore=tests/test_ma_strategy_scope.py` = 318 passed、
  前端 `npx tsc --noEmit` 通过、m2-pre 快照 verify/drill 通过；
- 范围 diff（source.tgz）：白名单外源码差异与开工一致。

## 7. 未证明的交易优势（诚实声明）

- 本文档**未**证明多周期融合优于单周期或任何基线：未经 M3 统计
  （30 天 + ≥100 事件 + 对照报告）不得宣称优势或预测行情大小；
- 波动 phase/path、K 线 strength、volume 只作为证据字段输出，
  未做硬门槛、未做参数寻优；
- 事件只作为**影子决策**存在：不持久化、不发送、不改变 V1 告警。

## 8. 延后事项（明确不在首版）

- 股票市场时区与 4H 交易时段对齐（先服务加密货币）；
- 1H 周期进入多周期体系；
- Pinbar 及其它新形态探测器接入；
- 参数寻优与自动调参；
- 任何形式的自动切换（切换依赖 M3 人工评估与 M0 回滚工具）。
