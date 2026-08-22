BLOCKED.md — M3B 安全影子运行时（M3B-A 第二轮主审整改，2026-08-14）

无阻塞。

第二轮主审 8 项全部完成：label_bound 允许 pending censored（三层统一）、
Sql list_label_due 覆盖 failed/label + next_label_at NULL 的 W5 重试、
scheduler 兜底 commit 完全隔离测试（不碰真实 DB/网络）、loader before 精确
=end_ts、schema apply 显式单事务 + 原子回滚、状态一致性三层统一（DB CHECK /
ShadowEventRow / validate_row_content + 交叉验证 + canonical 契约）、
from_settings 不吞 0/空、缺数据 backoff 不热循环/不饿死后排。
红→绿：修复前 10 failed / 72 passed；还原修复 18 failed / 75 passed；修复后
93 passed。全量 511 passed（0 skipped）、M3A 100、M0+M1+M2 210、tsc 0、
schema 临时库测试全绿（含原子回滚与新增 CHECK）。
活库迁移事务化完成：heartbeat.started_at=1786691348 保留、events=0、V1 六表
不变、非法行活库实测拒绝、合法 pending censored/decision 失败态可插（已清理）、
scheduler 心跳持续 last_run_ok=true、无历史回填/发送副作用。
M3B 完成门槛（满 30 天且 ≥100 唯一 eligible 事件）未完成——本轮如实只上线并
开始计时；M3C（对照报告与校准）未开始。

## MTF-BTC-V1（2026-08-16）

无阻塞。

MTF-BTC-V1（4H 告警入场位置 vs 周线门槛放宽 A/B 对照事件研究）研究交付
完成；生产规则切换明确延后到 walk-forward / 留出集 / 影子评审，属流程
决定而非技术阻塞；M3B 门槛（满 30 天且 ≥100 唯一 eligible）仍未达到。
