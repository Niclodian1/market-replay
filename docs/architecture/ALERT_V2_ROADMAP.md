# ALERT_V2_ROADMAP — 告警体系 V2 路线图（M0 已冻结）

> 状态：M0（备份与因果性）已交付，本文冻结 M1—M3 路线。本文档为架构实施文档，
> 不包含实现；任何里程碑的实现都不得超出各自边界（见「延后事项」与「红线」）。
> 关联文档：`docs/runbooks/ALERT_V1_BACKUP_RESTORE.md`（V1 快照与恢复）、
> `PROGRESS.md`（M0 执行记录）。

## 0. 背景与原则

V1 现状：monitor 按 symbol×timeframe 独立判断（ma/macd/structure 三探测器），
回测侧另有 trend/momentum/reversal 三策略探测器；趋势（trend_classifier 5 态）、
Regime（regime_classifier 6 态）、日报阶段（daily_report_analysis 的 stage）三套
状态口径分裂；M0 已修复回测侧未来数据污染。

V2 原则（M0 起即生效，后续里程碑必须遵守）：
1. **as-of 契约**：时刻 t 的状态/信号只能依赖 `candles[:t+1]`；中心摆动点只有经过
   右侧 pivot 根后才可使用；信号只记在确认/决策 bar。追加任何未来 K 线不得改变
   已产生的结果（由 `tests/test_asof_causality.py` 持续守护）。
2. **只表达分布，不输出确定性预测**：「可能有多大级别行情」必须表达为历史条件分布
   和时间窗口（如 5/10/20/40 根后 MFE/MAE 分布），禁止输出确定性预测。
3. **V2 只做 shadow annotation**：不直接发新告警；至少运行 30 天且积累 100 个 V2
   事件后才评估切换，不自动切换。
4. **多周期角色固定**：周线定大环境（Context）、日线定 Setup、4H 给 Trigger；
   1H 暂不进入首版。先服务加密货币；股票市场时区与 4H 交易时段对齐延后处理。
5. **可配置、可回放**：指标窗口、阈值、缺失数据降级、状态版本号、解释字段、审计
   字段必须可配置并可回放（M0 不实现这些能力，M1 起逐步落地）。

## 1. M1 — 单一 MarketState（单周期状态化）

> 状态：**M1 已落地（2026-08-13）**。核心引擎 `market_state.py` + 适配器
> `market_state_adapters.py` 已交付；三个旧入口与回测/日报 golden 零差异；
> 细节见 `docs/architecture/MARKET_STATE_V1.md`。以下为路线原始定义与验收结果。

目标：把三套分裂的状态口径（趋势 5 态 / Regime 6 态 / 日报 stage）收口为一个
**单一 MarketState 对象**，作为所有信号/回测/告警的公共状态输入。

### 1.1 范围

- **趋势方向/强度**：方向（up/down/sideways）与强度（弱/中/强），统一取自一组
  MA 排列 + ADX/摆动结构，输出单一结构（含版本号与解释字段）。
- **波动率路径**：`contraction → transition → expansion` 三态迁移，由 ATR 百分位
  或等效指标驱动；状态迁移必须显式记录（供 M3 状态转移统计）。
- **质量分层**（K 线/波动质量，用于信号加权或过滤）：
  - `body/ATR`（实体占比）
  - `range/ATR`（振幅占比）
  - `body/range`（实体/振幅比）
  - `close-location`（收盘在振幅中的位置）
  - 可选 `volume`（成交量状态，仅适用于有量品种）
  每项输出分层标签（如 high/normal/low），全部为纯函数、确定性、可回放。
- **适配器收口**：三套旧状态通过适配器逐步映射到 MarketState，**不一次删除**；
  每个适配器必须带 golden 测试锁定映射关系，V1 消费者（monitor、回测、日报）
  逐个切换后旧实现才允许下线。

### 1.2 验收门槛（M1 完成条件）

- MarketState 在给定 candles 前缀上是 as-of 因果的（沿用 `test_asof_causality.py`
  的逐前缀口径，不一致数 = 0）。✅ `test_market_state.py` 逐前缀每个历史位置不一致数 = 0
- 三套旧状态各自的 golden 测试不回退；至少两套 V1 消费者已切换到适配器。
  ✅ 回测（regime 入口）与日报已迁移；四组序列 golden 逐字段一致
- 质量分层在确定性序列上有固定 golden 摘要。✅ `test_market_state_legacy_parity.py`
- 状态版本号 + 解释字段 + 审计字段字段已定义并通过 schema 校验。
  ✅ schema_version / config_hash / reasons / to_dict
- 未改变生产告警文案/去重键/发送渠道；未接入 Pinbar；未做参数寻优。✅
- 验收附加（任务 3）：临时改错适配映射 → parity 红（保存输出）→ 还原全绿 ✅；
  后端全量 249 passed（>209）、M0 101 项通过、前端 tsc 通过、生产 monitor/
  API/schema/DB/前端改动为 0 ✅

## 2. M2 — 周/日/4H 多周期融合

> 状态：**M2 已落地（2026-08-13，三轮复验整改完成）**。核心引擎
> `multi_timeframe.py` + 数据契约 `schemas/market_fusion.py` + 57 项测试已交付；
> 纯计算影子决策，不持久化、不发送、不接生产 monitor；V1 零变化。
> 两轮退回反例（Trigger 契约 / 配置空转与深冻结 / payload 污染 / schema 语义）
> 已全部修复并红→绿验证。细节见
> `docs/architecture/MULTI_TIMEFRAME_V1.md`。以下为路线原始定义与验收结果。

目标：从「各周期独立判断后简单 OR」改为**角色化融合**：周线定大环境（Context）、
日线定 Setup、4H 给 Trigger。

### 2.1 范围

- **统一 as-of 时间**：融合只使用各周期「最近一根已收盘 bar」，统一取当前时刻
  `t` 之前各周期最后一个完整 bar；跨周期数据必须对齐到同一 `t`（用 bar 的
  close_time 判定），杜绝「用未来 4H bar 判断今日日线」。
- **角色语义**：
  - 周线 Context：决定市场大环境（只允许顺势方向 / 禁止逆势）
  - 日线 Setup：决定可交易结构（进入候选池的条件）
  - 4H Trigger：决定入场时机（满足 Setup 后的触发信号）
- **融合规则**：Context 过滤方向 → Setup 过滤结构 → Trigger 决定入场；任何一层
  不满足即不产生 V2 事件。**禁止对多周期信号做简单 OR**。
- 1H 暂不进入首版；股票市场时区与 4H 交易时段对齐延后。

### 2.2 验收门槛（M2 完成条件）

- 跨周期对齐逻辑有确定性测试：同一 `t` 下不同 K 线到达顺序产生相同融合结果。
  ✅ `test_reorder_same_result`（乱序重排逐字段一致）
- 融合结果逐前缀因果（沿用 M0 口径）。
  ✅ `test_prefix_history_immutable_across_asofs`（≥70 个 as_of 前后一致）
- 有「Context 拒绝逆势 Trigger」与「Setup 未满足时 Trigger 不触发」的定向测试。
  ✅ `test_context_rejects_opposite_trigger` / `test_setup_rejects_inconsistent_direction`
- V2 事件结构（含解释字段：为什么该 Trigger 有效/无效）已定义并落 schema。
  ✅ `market_fusion.py`（m2-v1）reason_codes + 中文 explanations + 三角色审计
- 复验整改（三轮，2026-08-13，管理者独立验收退回项）**已完成**：
  ① Trigger 周期缺失→trigger_wrong_period；不可信事件字段
  （signal_time→trigger_bad_time、reference_price→trigger_bad_reference、
  payload 冲突→trigger_payload_conflict、event_type→trigger_bad_event_type）
  受控不崩溃；MultiTimeframeConfig 全部字段真正驱动行为；Pydantic 语义约束。
  ② roles 固定业务法则（不可配置）；配置深冻结（MappingProxyType 只读）；
  M2 输出不携带 payload（allow_nan=False 恒可序列化）；payload 强校验
  （direction/bar_closed/signal_type/reference）；schema strict=True +
  ConfigHash 16 位 hex + available/eligible 全语义校验；
  reference_price 必须等于对齐 4H close（容差 1e-6）。
  红→绿：配置空转 1 failed、schema 语义 1 failed、JSON 污染 1 failed、
  schema 缺 state 1 failed，还原全绿。
  ③ 决策快照深不可变（TriggerSnapshot 快照 + aligned/_direction_map 深冻结 +
  to_dict 全新返回）；eligible iff 三个 OK codes（False 必须含 reject code）；
  evidence typed 角色模型 + allow_inf_nan=False；eligible reference 必须匹配
  close（1e-6）；event_type/raw_direction 非空。
  红→绿：深不可变 2 failed、nested NaN 1 failed，还原全绿。
- 附加验收：红→绿实验（破坏 close 边界 9 failed、破坏方向门控 2 failed，还原全绿）
  ✅；M2 57 项 + M1 52 + M0 101 + 全量 318 passed、tsc 通过、
  m2-pre 快照 verify/drill 通过；白名单外零源码差异 ✅

## 3. M3 — 历史结果标注、影子运行与校准

> 状态：**M3A（离线结果标注与汇总）已落地（2026-08-13），第一轮/第二轮整改
> 完成，第三轮收口（最终候选审查）完成（2026-08-13）**。核心引擎
> `outcome_labeling.py` + 数据契约 `schemas/outcome_labeling.py` + 100 项测试
> 已交付；纯计算离线标注，不接生产影子流、不持久化、不发送；V1/V2 零变化。
> **M3B（影子运行）与 M3C（校准）未完成**——M3B 需独立评审 DB/运行风险后
> 另行实施（见下文 3.2/3.3）。细节见 `docs/architecture/OUTCOME_LABELING_V1.md`。
> 以下为路线原始定义与 M3A 验收结果。

### 3.1 范围（M3A 已完成 ✅）

- **事件后统计**（对每个 V2 事件，统计 5/10/20/40 根后的表现）：
  - MFE（最大有利偏移）与 MAE（最大不利偏移），以 R（初始风险倍数）表达
  - 先到 +1R 还是 -1R（方向先兑现）
  - 假突破率（未达到 +1R 前先破 -1R 的比例）
  - 状态转移（M1 的波动率路径/趋势强度迁移与事件胜率的关系）
  - ✅ 全部落地：`outcome_labeling.py`（label_decision / label_batch / summarize）
    + `schemas/outcome_labeling.py`（m3a-v1，严格 schema）+ 54 项测试
  - 1R = 事件 4H MarketState 的 Wilder ATR(14)，来自 M2 决策快照且
    config hash 匹配；config.atr_period 冻结为 14；risk 输出 method/
    period/atr_abs/atr_pct；同时输出相对参考价的百分比（R_pct / MFE_pct /
    MAE_pct）
  - 窗口 = 触发 bar 后 5/10/20/40 根完整连续已收盘 4H bar（不含触发 bar）；
    缺根/未收盘/label_as_of 未到 → censored；同 bar 双触 → ambiguous_same_bar
    （不假设 OHLC 内路径）；fake_breakout 仅 negative_first=true
  - MFE/MAE 从 0 起取非负极值；long/short 镜像；事件 bar 极值不计
  - 未来 K 线只生成标签，不得改变事件时点 decision/risk/condition 快照
  - 第一轮整改（暗卷）：风险口径硬化（atr_period==14）、同源重放（事件时点
    MarketState 在决策历史 slice 上重算并与决策逐字段一致，事件前 bar 篡改
    拒绝）、可验证审计（OutcomeLabel 保存深冻结 decision_snapshot，schema 用
    MarketFusionOut 重验证并 canonical 重算 digest，顶层/三层/event_state/
    risk 全一致）、标签与汇总 schema 语义（reference==trigger close、
    mfe_pct≈mfe_r*atr_pct、path 时序连续、end 与末步一致、touch 和==samples、
    fake_rate 公式、group/transition 样本和与 rate、行唯一）、条件分组补齐
    direction/event_type/Context/Setup/Trigger 维度、summarize 拒绝重复
    digest 与混合 config_hash、label_batch 拒绝混合 symbol
  - 第二轮整改（第二层暗卷）：PathStep 增加完整 OHLC（schema 从逐 bar OHLC
    独立复算 MFE/MAE 与首触及/fake，成套伪造也拒绝；文档记录体积取舍）、
    summarize 对 OutcomeLabel 对象先 to_dict→validate（不信任内存对象）、
    全删失报告合法（samples=0 允许 censored=n，空报告 n=0 区别清楚）、
    summarize 拒绝混合 label_as_of（统一观察截止，不取 max）、删失结构化
    稳定码 censor_code（label_as_of_not_reached/unclosed/missing_bars，判定
    先看 expected end close_time，同一 as_of 追加不可见未来 bar 完整 label
    逐字段不变）、summary 可审计元信息（统一 config_hash/唯一 decision_
    digests/统一 label_as_of）、groups/transitions 严格排序（倒序拒绝）、
    清理重复字典键
  - 第三轮收口（最终候选审查）：闭合公式修正（expected_end_close =
    signal_time+(w+1)*step，t+(w+1)*step-1 不成熟/精确边界成熟反例）、
    schema 跨窗口约束（成熟窗口连续前缀、短 path==长 path 前缀、MFE/MAE
    随窗口不减、短窗已触及则长窗 first_touch/fake 不变）、标签自包含可重放
    （market_state_config 完整字段快照 + hash 重算；replay_context 从原始
    起点到事件 bar；PathStep 带 volume；schema 构造 MarketStateConfig 对
    replay_context + 成熟窗口重跑 M1 precompute，逐 bar 验证状态路径与事件
    末状态——路径成套伪造拒绝；不修改 M1）、summary 诚实区分
    （validate_outcome_summary 只承诺结构/算术；新增 source-linked
    validator validate_outcome_summary_against_labels(summary, labels) 对源
    labels 全部严格校验后用 summarize 重算逐字段比较，拒绝 group 均值/计数/
    digests 伪造）、20 组固定 seed 随机 OHLC 属性测试、AST 重复键扫描通过
  - 验收：手算 golden 与全部因果/删失/歧义反例全绿（100 项，含首轮反向红→
    绿两次、暗卷 19+10 项红灯复现→修复绿灯、第三轮收口 7 项红灯复现→修复
    绿灯）；成熟标签在追加任意窗口后未来数据时逐字段不变；全量后端 418
    passed（0 skipped）、M2/M1/M0 210 无回归、tsc 通过、m3-pre 快照
    verify/drill/同构恢复通过

### 3.2 影子运行（shadow，M3B-A 已交付 ⏳ 完成门槛未到）

- **影子运行（shadow）**：V2 只做 annotation，不直接发新告警；与 V1 并行至少
  **30 天且积累 ≥100 个 V2 事件**（按唯一 eligible decision_digest 计）。
- **M3B-A（2026-08-14）**：运行时上线并开始计时——`m3_shadow_runtime`
  单例心跳 + `m3_shadow_events` 事件表已落地；独立 worker 读取已持久化的
  4H AlertEvent（不改 monitor_engine），M2 决策与 M3A 标签开始真实积累；
  allowlist 仅 `BINANCE:BTCUSDT`；拒绝决策也持久化，eligible 才排 M3A；
  标签仅在 5/10/20/40 下一闭合边界到期时更新，40 成熟后停止；默认
  `M3_SHADOW_ENABLED=false`，启用后以 DB heartbeat 首次启用时刻为准、
  不回填旧事件。细节见 `docs/architecture/M3_SHADOW_RUNTIME_V1.md`。
- **M3B-A 第二轮整改（2026-08-14）**：label_bound 允许 pending censored；
  状态一致性三层统一（DB CHECK / ShadowEventRow / validate_row_content）；
  缺数据/部分成熟 backoff（label_retry_delay_seconds，不热循环、不饿死后排）；
  Sql list_label_due 覆盖 failed/label + next_label_at NULL 的 W5 重试；loader
  before 精确=end_ts；schema apply 显式单事务 + 原子回滚；from_settings 不吞
  0/空配置。全量 511 passed，活库迁移事务化并保 heartbeat。
- **M3B 完成门槛未完成**：满 30 天且 ≥100 唯一 eligible 事件后才视为 M3B
  完成；本轮只上线并开始计时，不伪造。

### 3.3 对照报告与校准（M3C 未完成 ⏳）

- **对照报告**：V1 vs V2 事件对比（命中率、MFE/MAE、假突破率、信号密度、
  重复率），形成人工评审报告；**由人决定是否切换**，不自动切换。
- **校准**：用 M3A 标签校准 M2 影子决策（三层条件/状态迁移分组分布）。

### 3.4 验收门槛（M3 完成条件）

- ✅ M3A：统计模块在确定性回放数据上有 golden 摘要测试（`test_outcome_labeling.py`
  54 项全绿，含手算 golden 与反向红→绿证据）。
- ⏳ M3B：影子运行满 30 天且 ≥100 事件后，产出对照报告（含上表全部指标）。
- ⏳ M3C：切换评估文档由人签署；切换本身是一次受控发布（回滚依赖 M0 快照与恢复工具）。

## 4. 可配置与可回放（跨里程碑横切项，M0 不实现）

- 指标窗口、阈值、缺失数据降级、状态版本号、解释字段、审计字段全部可配置
  （配置随事件/状态快照持久化），保证任意历史时刻可用当时的配置精确重放。
- 重放能力是 M3 统计可信的前提；每个里程碑必须带上重放 golden 测试。
- M0 明确**不实现**上述能力（M0 只做备份与因果性修复）。

## 5. 延后事项（明确不在首版）

- 股票市场时区与 4H 交易时段对齐（先服务加密货币）。
- 1H 周期进入多周期体系。
- Pinbar 及其它新形态探测器接入。
- 参数寻优与自动调参。
- 任何形式的自动切换。

## 6. 红线（任何里程碑都不得违反）

1. 不得改变 V1 生产告警代码/API/schema/告警文案/去重键/发送渠道，除非该变更
   属于已验收里程碑的明确范围并附带 characterization 测试更新。
2. 不得输出确定性预测；只能表达历史条件分布与时间窗口。
3. 所有判断只用已收盘、已确认的 K 线；as-of 契约测试必须保持全绿。
4. 不得在未经对照报告与人工决策的情况下切换 V2。
5. 超出本文档范围的改动必须先写 `BLOCKED.md`，不得自行扩边界。

## 7. M0 交付物（已完成，供 M1 引用）

- `reports/alert-backups/<UTC时间>/`：V1 源码 + 告警六表 + 渠道配置快照，
  权限 700/600、SHA-256 校验、已实际演练恢复。
- `scripts/ops/alert_backup.sh`（create/verify）与 `scripts/ops/alert_restore.sh`
  （drill/restore）：恢复演练成功，篡改检测生效，现用库未受影响。
- 因果修复：trend_classifier 摆动点确认、Trend 摆动点 confirmed_at 检查、
  Reversal 确认 bar 发信号、回测快速路径真实 caution；四组件前缀不一致 = 0。
- `tests/test_asof_causality.py` + `tests/test_alert_v1_characterization.py` 全绿；
  原 71 项基线测试全绿；前端 tsc 通过。
