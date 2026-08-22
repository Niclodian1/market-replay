# M3_SHADOW_RUNTIME_V1 — M3B 安全影子运行时（2026-08-14）

> 状态：**M3B-A 已交付（运行时上线并开始计时）**。M3B 完成门槛（满 30 天且
> ≥100 唯一 eligible 事件）**未完成**——本轮只证明「运行时上线并开始计时」，
> 不伪造门槛达成。M3C（对照报告与校准）未开始。
> 关联：`docs/architecture/ALERT_V2_ROADMAP.md`（3.2/3.4）、
> `docs/architecture/OUTCOME_LABELING_V1.md`（M3A）、`PROGRESS.md`（执行记录）。

## 0. 定位与红线

- M3B 影子运行时让 M2 决策与 M3A 标签**开始真实积累**：独立 worker 读取已
  持久化的 4H AlertEvent（**不改 monitor_engine**），计算 M2 融合决策并持久化，
  eligible 事件按 5/10/20/40 窗口边界生成 M3A 标签。
- **绝不改变 V1 告警/发送**：不写 V1 六表、不发送任何通知、不导入任何 sender。
- 独立表 `m3_shadow_runtime` / `m3_shadow_events` 对 V1 六表**不建外键**，
  确保 M0 回滚不被新行阻塞；删表仅由 `scripts/ops/m3_shadow_schema.sh rollback`
  在精确确认词下执行。

> ⚠️ 2026-08-14 第二轮整改后版本说明：本章描述与**当前实现一致**（ORM/DDL/
> 活库三表一致）。新增：label_bound 允许 pending censored、状态一致性三层
> 统一（DB CHECK / ShadowEventRow / validate_row_content）、缺数据/部分成熟
> backoff（label_retry_delay_seconds）、loader before 精确=end_ts、schema
> apply 显式单事务 + 原子回滚测试、from_settings 不吞 0/空配置。

## 1. 表结构（PostgreSQL，DDL 见 scripts/ops/m3_shadow_schema.sh）

### 1.1 m3_shadow_runtime（单例心跳）

| 列 | 类型 | 说明 |
|---|---|---|
| id | SMALLINT PK | 恒为 1（`CHECK (id = 1)` 单例） |
| started_at | BIGINT | **首次 enabled cycle 原子创建**的 epoch 秒；只设一次 |
| last_run_at | BIGINT | 最近一次 cycle 完成时刻 |
| last_run_ok | BOOLEAN | 最近一次 cycle 是否成功 |
| last_error | TEXT | 错误（脱敏截断 ≤300 字符） |
| created_at / updated_at | TIMESTAMP | 审计（DB 默认 now()，与 DDL 一致） |

原子性：`INSERT ... ON CONFLICT (id) DO NOTHING` → 并发/重跑下 started_at 只
首次设置；之后任何重跑只更新 last_run_*。

### 1.2 m3_shadow_events（事件表）

| 列 | 类型 | 说明 |
|---|---|---|
| id | BIGSERIAL PK | |
| source_dedupe_key | VARCHAR(240) **UNIQUE** | V1 alert_events.dedupe_key；同源至多一行 |
| source_event_id | BIGINT | V1 行 id（审计引用，**无外键**） |
| symbol / timeframe / signal_time | | 源事件标识（timeframe 恒 4h） |
| source_snapshot | TEXT | 只读 V1 源行完整快照（canonical JSON） |
| decision_json | TEXT | canonical M2 JSON（allow_nan=False，写前过 validate_decision）；**decision 失败态可空** |
| decision_digest | VARCHAR(16) | sha256[:16]（与 M3A decision_digest 同源）；失败态可空 |
| eligible | BOOLEAN | M2 是否 eligible；失败态可空 |
| reasons | TEXT | JSON `{codes, explanations}`；失败态可空 |
| config_hash | TEXT | JSON `{market_state, fusion}`（16 位 hex）；失败态可空 |
| status | VARCHAR(20) | 状态机（见 §2） |
| failure_stage | VARCHAR(20) | 失败阶段：`decision`（取数/计算失败，decision 字段可空）/ `label`（标签失败，不得丢 decision）；仅 failed 非空 |
| attempts / next_retry_at / last_error | | 失败退避重试（attempts ≥ 0） |
| label_json | TEXT | M3A 标签（canonical JSON，写前过 validate_outcome_label） |
| label_as_of | BIGINT | 最近一次标签计算的 as_of（恒为窗口边界） |
| maturity_window | INT | **标签中实际成熟窗口最大值**（5/10/20/40；无则 NULL，不虚报） |
| next_label_at | BIGINT | 下一次标签更新边界 |

索引：`ix_m3_shadow_events_status`、`ix_m3_shadow_events_symbol_signal`、
唯一索引（source_dedupe_key）。**无任何指向 V1 六表的外键**（schema 脚本
`test` 子命令断言为 0）。

DB CHECK 约束（ORM/DDL/活库一致，schema 脚本 `test` 断言非法行必拒绝）：
- `ck_m3_shadow_events_status`：status ∈ 五态
- `ck_m3_shadow_events_timeframe`：timeframe = '4h'
- `ck_m3_shadow_events_attempts`：attempts >= 0
- `ck_m3_shadow_events_digest`：digest 为空或 `^[0-9a-f]{16}$`
- `ck_m3_shadow_events_maturity`：maturity_window 为空或 ∈ {5,10,20,40}
- `ck_m3_shadow_events_failure_stage`：failure_stage 空 ⇒ 非 failed；非空 ⇒ failed 且 ∈ {decision,label}
- `ck_m3_shadow_events_decision_complete`：**failed/decision 必须决策五字段全
  NULL**；其余状态必须五字段完整
- `ck_m3_shadow_events_label_bound`：label 为空 ⇒ as_of/maturity 全空；label 非空
  ⇒ as_of 非空，且 **pending 允许 censored（maturity NULL）**、其余状态
  maturity 非空（maturity 非空却无 label_json/as_of 一律拒绝）
- `ck_m3_shadow_events_state_consistency`：状态机一致性（与 ShadowEventRow /
  validate_row_content 三层统一）——failed/decision 决策五字段全 NULL 且 label
  空；rejected eligible=false 且无 label；pending/labeled/completed/failed-label
  eligible=true；labeled 真实 maturity<40；completed maturity=40；pending 的
  label 只允许 censored
- `ck_m3_shadow_events_retry_state`：非 failed 必须 attempts=0 且 error 空；
  next_retry_at 仅 pending/labeled 缺数据 backoff 允许（rejected/completed 必须空）

（注：CHECK 用 COALESCE 显式处理 NULL——PostgreSQL 对 NULL 结果放行，避免 `NULL OR ...` 绕过约束。）

## 2. 状态机

```
                 ┌─ decision/取数失败 → failed(failure_stage=decision)
                 │   （decision 字段可空；到期重试成功 → 原行升级补全）
 摄取(源行) ──▶ decision 成功 → pending / rejected
                                │ (eligible)
                                ▼
             5 边界到期 → label 计算
                │
       ┌─ 数据未到齐（缺根/未收盘）→ 保持 pending（maturity_window=None，不虚报）
       │        │ 下轮重试同一窗口
       │        ▼
       └─ 有成熟窗口 → labeled（maturity_window=实际最大成熟值）
                        │ (10/20/40 边界到期，真实成熟才推进)
                        ▼
                  completed（40 真实成熟后停止，不改写）
        label 计算失败 → failed(failure_stage=label)（decision 不丢，backoff 重试）
```

- **pending**：已持久化 decision、eligible，等待 5 窗口边界（= signal_time+6*14400）；
  5 边界到期但数据缺根/未收盘 → 保持 pending 可重试，**maturity_window=None 不虚报**；
  此时写入 censored label + `next_retry_at = now + label_retry_delay_seconds`（缺数据
  backoff，不算 failed、不热循环），backoff 到期后再试同一窗口。
- **labeled**：已有标签（maturity_window=实际成熟最大值），等待下一边界
  （next_label_at）更新；maturity 单调不减；到期窗口缺根时 next_label_at 仍指
  该窗口（不跳过不虚报），同时设缺数据 backoff；目标窗真实成熟后清空 backoff。
- **completed**：40 窗口（= signal_time+41*14400）**真实成熟**后停止；
  重跑绝不改写已完成 label_json。
- **rejected**：decision 不 eligible（拒绝决策也持久化，供重复率等统计）。
- **failed + failure_stage='decision'**：取数/计算失败；以 source_dedupe_key
  唯一行持久化 source 快照 + 脱敏错误 + attempts + next_retry_at（**decision
  五字段必须全 NULL**，DB CHECK 强制）；到期重试成功后**原行升级**补全严格校验的
  decision 并进入 rejected/pending（不产生重复行）；达 max_attempts 停止。
- **failed + failure_stage='label'**：标签计算异常；decision 字段不丢；
  attempts 递增、backoff 后重试，恢复后回到 labeled/pending 继续；首次 W5
  标签异常行 `next_label_at=NULL`，backoff 到期后 **Sql list_label_due 按 W5
  边界选中重试**（不会永远不被选中）。

标签更新规则：`label_as_of` 恒为窗口边界（5→10→20→40），崩溃跳过多个边界
时一次推进到「已到期最大窗口」（≤40）；maturity_window = 标签中实际 matured
最大值（无则 None）；40 成熟后 `next_label_at=NULL` 停止。

公平轮转：`list_label_due` 排序为「新鲜（无 backoff）优先 → backoff 最早到期
优先 → signal_time」，被处理者跳到队尾——同批缺数据重试不会永远压住新鲜后排
（Memory 与 Sql 同口径）。

## 3. 计数口径（M3B 完成门槛）

- **同源至多一行**：`source_dedupe_key` 唯一；同一 V1 source 行全部留存以便
  量重复率（重复由唯一键约束自然防重）。
- **≥100 事件**：按**唯一 eligible decision_digest** 计数
  （`count(distinct decision_digest) where eligible`），同 bar 多策略事件各自
  成行但 digest 不同，均计入唯一数。
- **30 天**：从 heartbeat.started_at 起算；`days_remaining_30` 如实递减。
- `milestone_reached = (elapsed ≥ 30 天) and (唯一 eligible digest ≥ 100)`；
  本轮**不伪造**——CLI status 只报告进度。

## 4. 开关与启用流程

- 默认 `M3_SHADOW_ENABLED=false`（config.py / .env.example）。关闭时：
  scheduler 的 shadow job 直接 return（零 DB/零网络）；CLI run-once 同样不动作。
- 启用（生产 canary）：向 .env **仅追加**两行
  `M3_SHADOW_ENABLED=true`、`M3_SHADOW_SYMBOLS=BINANCE:BTCUSDT`，重建 backend；
  `started_at` 以 DB heartbeat 首次启用 cycle 为准（不回填旧事件）。
- 摄取条件：`created_at >= started_at AND timeframe='4h' AND symbol ∈ allowlist
  AND source_dedupe_key 未处理`；**NOT EXISTS（anti-join）在 LIMIT 前排除已处理
  源**（含 failed 行），>ingest_limit 两轮必吃下一页（分页不饥饿）。历史 4H
  事件（created_at 早于启用）不回填。
- 每周期最多取 `M3_SHADOW_MAX_BARS=700` 根（猜测值；BTC 4H 700 根 ≈ 117 天，
  日线可见 ~116 根 > M1 warmup 101 根，实测足够；若未来事件过老/周期不足，
  调大该常量即可，不改 M1/M2/M3A 口径）。
- M2 `as_of = signal_time + 14400`（4H 收盘时刻），只用该时点可见周/日/4H；
  未来 bar 追加不改变已产生 digest / 成熟 label（M0/M1/M2/M3A 因果契约延续）。
- 生产行情 loader 把 `end_ts` **精确**作为 provider 的 `before` 透传
  （`before=end_ts`，before 是开盘时点上界；不是 end_ts+duration，也不是拉
  最新再后过滤）——旧事件不会被未来数据挤出历史；保留 close_time<=end_ts
  二次过滤与最后 max_bars 截断；outputsize=max_bars+16 覆盖 provider 边界语义
  差异（spy 反例断言 before 相等 + 未来追加 bar 被过滤、结果不变）。
- 参数语义校验：timeframe 固定 4h、max_bars/ingest_limit/label_limit/
  max_attempts 为正、retry_delay_seconds/label_retry_delay_seconds≥0；错误
  配置直接 ValueError 不静默运行；`ShadowCore.from_settings` **不得用
  `value or default` 吞掉 0/空非法配置**（M3_SHADOW_MAX_BARS=0、
  M3_SHADOW_TIMEFRAME="" 原样进入 __post_init__ 校验抛错）；Sql repo 的
  max_attempts 取自 core（不硬编码）。
- 读回严格验证：get_by_key / list_* 读回行重新过 validate_decision /
  validate_outcome_label 并重算 digest 一致性；`validate_row_content` 三层统一
  （与 DB CHECK / ShadowEventRow）——交叉验证 label.decision_digest==行 digest、
  label_as_of 一致、实际 matured 最大值==行 maturity_window、reasons 与 decision
  reason_codes/explanations 一致、decision/label JSON 必须 canonical 字符串；
  交换另一条合法 label 必须拒绝；脏行拒绝（不信任）。

## 5. 运维

- `python -m app.tasks.m3_shadow_cli status`：心跳/计数/30 天与 100 唯一
  eligible 进度，**不输出 payload/秘密**。
- `python -m app.tasks.m3_shadow_cli run-once`：执行一轮 cycle（写库）。
- `python -m app.tasks.m3_shadow_cli dry-run-event <id>`：对既有 4h AlertEvent
  模拟处理（不写表），输出 eligible/digest/reason codes/标签预览。
- `scripts/ops/m3_shadow_schema.sh status|apply|rollback|test`：建表幂等；
  **apply 显式单事务（BEGIN/COMMIT + ON_ERROR_STOP），任一 DDL/迁移失败整体
  回滚**（临时库含故意失败反例证明原子回滚）；rollback 需精确确认词
  `ROLLBACK M3 SHADOW` 且只删两张 M3 表；`test` 在唯一命名临时库验证建表幂等/
  唯一键/无 V1 FK/CHECK 非法行必拒绝/原子回滚/回滚范围（不触碰活库）。
- 恢复：M0 快照（`alert_backup.sh`/`alert_restore.sh`）只覆盖 V1 六表与白名单
  源码；M3 两表可随时用 schema rollback 精确删除（先确认无未导出价值）。

## 6. 隔离与安全

- scheduler 独立 `m3_shadow` job；异常只写 shadow heartbeat/log，绝不冒泡
  影响 `_check_alerts_job`、V1 提交或发送（`run_cycle_safe` 永不抛出）。
- shadow 模块**禁止导入任何 sender**（源码级测试守护）。
- 错误脱敏：webhook/token/secret 打码 + 截断 300 字符；status 不吐原始
  payload/秘密。

## 7. 验收证据（2026-08-14）

- 68 项新测试全绿（`tests/test_m3_shadow_runtime.py`，≥35 要求，含主审整改
  反例 19 项）：
  flag/allowlist、start 边界、4H 过滤、as-of 未来追加不变、M2 拒绝持久化、
  eligible 标签分期、40 完成、同 bar 歧义、幂等/重试/崩溃恢复、同周期取数复用、
  心跳、错误脱敏、CLI 汇总、无发送、V1 失败隔离。
- 反向红→绿三组（实测输出见 PROGRESS）：
  a) 影子异常冒泡会打断 V1（2 failed → 修复 2 passed）；
  b) 同源重复产生两行（2 failed → 修复 2 passed）；
  c) 未来 bar 改写决策（2 failed → 修复 2 passed）。
- schema 脚本临时库测试全绿；disabled 全量后端 + M3A100 + M0/M1/M2 210 +
  tsc 全绿；快照 verify/drill 通过。
- **30 天/100 唯一 eligible 门槛未完成**：本轮只上线并开始计时（heartbeat
  started_at 已固定），进度由 CLI status 如实报告。

## 8. 第二轮整改验收证据（2026-08-14，管理者主审 8 项）

1. **label_bound**：pending censored（label_json+label_as_of 非空、maturity NULL）
   在 DB CHECK / ShadowEventRow / validate_row_content 三层均合法；maturity 非空
   却无 label_json/label_as_of 仍拒绝；真实 SQL（schema 临时库 + 活库实测）与
   SqlRepo/Schema/validate 层测试覆盖（非仅 Memory）。
2. **首次 W5 标签异常**：failed/label + next_label_at NULL 行在 backoff 到期后
   被 Sql list_label_due 按 W5 边界选中重试（SQL 结构断言 + Memory 行为测试）。
3. **scheduler 兜底 commit 完全隔离**：patch runtime.run_production_cycle 强制抛
   + patch sched.AsyncSessionLocal 与 app.db.session.AsyncSessionLocal 可观测
   fake（主路径与兜底均不产生真实 session/连接），断言兜底 touch 后 commit
   恰好 1 次；commit 失败不冒泡；settings try/finally 自动还原。
4. **loader before**：production_market_loader 把 end_ts 精确传给 provider.before
   （不得 end_ts+duration）；保留 outputsize padding 与 close_time<=end_ts 二次
   过滤；spy 断言相等 + provider 返回未来追加 bar 被过滤（结果不变）。
5. **schema apply 原子性**：建表+迁移显式单事务（BEGIN/COMMIT + ON_ERROR_STOP）；
   临时库故意失败反例证明整体回滚（无半成品表）；活库修正同样事务化并保留
   heartbeat.started_at=1786691348。
6. **状态一致性三层统一**：DB CHECK / ShadowEventRow / validate_row_content
   同语义（failed/decision 决策五字段全 NULL 且 label 空；rejected
   eligible=false 且无 label；pending/labeled/completed/failed-label
   eligible=true；labeled 真实 maturity<40；completed maturity=40；非 failed
   attempts=0、error 空）；validate_row_content 交叉验证 label digest/as_of/
   成熟最大值、reasons、canonical 字符串，交换他行 label 拒绝；关键非法行逐一
   测试（含 ShadowEventRow 同层）。
7. **from_settings 不吞 0/空**：M3_SHADOW_MAX_BARS=0、M3_SHADOW_TIMEFRAME=""
   原样进入 __post_init__ 抛 ValueError（0/空测试）。
8. **缺数据 backoff**：0/部分成熟未到目标窗时设 now+label_retry_delay_seconds
   （不算 failed、不热循环），list_label_due 让 pending/labeled 尊重；成功清空；
   >label_limit 前排缺数据多轮仍处理后排（公平轮转排序：新鲜优先 → backoff 最早
   到期优先）。

红→绿证据（均实测）：
- 修复前（A–H 反例已入测试文件）：**10 failed / 72 passed**（保存于
  /tmp/m3b_red_before.txt）；
- 临时整体还原修复（含新补反例）：**18 failed / 75 passed**（保存于
  /tmp/m3b_red_after.txt）；
- 修复后：**93 passed**（M3B 定向）。

第二轮整改后全量验收：M3B 定向 **93 passed**（0 skip/xfail）；M3A 100；
M0+M1+M2 210；全后端 **511 passed**（0 skipped）；tsc exit 0；schema 临时库
测试全绿（含原子回滚与新增 CHECK）；活库迁移事务化完成（heartbeat
started_at=1786691348 保留、events=0、V1 六表不变、非法行活库实测拒绝、
合法 pending censored/decision 失败态可插且已清理、scheduler 心跳持续
last_run_ok=true、无历史回填/发送副作用）；30 天/100 门槛如实报告未达成。
