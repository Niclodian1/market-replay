# STATUS —— 当前状态（唯一状态页）

> 最后更新：2026-10-23（§30 参数实验室） · 分支 `refactor/strategy-spine`（与 main 同步起点 `2f6a212`）
> 本页替代旧的 `BLOCKED.md`（原内容见文末「历史阻塞」）。

## 1. 当前里程碑

**重构收口（REPO_AUDIT P0–P7 + 设计文档 P0–P7）**：全部清单项已完成，改动仍在工作区
（未提交），旧接口全部保留 deprecated 转发，随时可 `git checkout`/`git reset` 回滚。

| 阶段 | 状态 | 关键交付 |
| --- | --- | --- |
| P0 契约与护栏 | ✅ | `packages/indicator-core`、12 指标、因果审计 0 泄漏/0 漏报、golden fixture |
| P1 指标层 + 看盘 | ✅ | `POST /api/series` 统一出口、`chart-kit`、收盘增量刷新 |
| P2 练习内核 + 双端 | ✅ | `practice-core`（随机起点/错题重练/加仓）、离线缓存、PWA 壳 |
| P3 事件库 + 统计 | ✅ | `indicator_events`/`indicator_outcomes`、统计 API 与页面 |
| P4 实验台 | ✅ | spec/run/artifacts、买卖点、删除、后台执行 + 取消 + 超时、真实实验 spec 迁移 |
| P5 指标库与发布 | ✅ | 版本化、lab→prod 发布门禁、下线/删除、影响面 API |
| P6 告警 + 飞书 | ✅ | 指标维度告警、飞书卡片统计 + 深链、收盘对齐扫描、投递幂等与重试 |
| P7 数据链与出库 | ✅ | 单一数据链 `build_web_data.py`、仓库分区、生成数据出库（224 MB） |
| 审计 P4/P5 收尾 | ✅ | `labs/`（registry.yaml + 三态）、`docs/README.md`、本页、PROGRESS 归档 |
| 真机浏览器验证 | ✅ | `scripts/verify_browser.mjs` 30 项断言（页面/控制台/练习交互/PWA/离线）；修 3 个真 bug |
| 缠论因果性审计 | ✅ | 两条链口径澄清（信号链逐根前缀 / 结构链必读后续K）；1day/4h/1h 18 切点 0 泄漏/0 漏报；修复因果链缓存跨调用累积 |
| 缠论参数实验室 | ✅ | `apps/chanlun-web` 前端重算 包含→分型→笔→中枢→背驰→买卖点；滑动条实时调参；一买=绿柱首次缩短那根收盘价（点位），二/三买带结构区间；19 项 Node 测试 |

## 2. 阻塞

无。

## 3. 下一步（按收益排序）

1. 把工作区改动分批提交（建议按 P0→P7 顺序，每批跑一次验收命令）。
2. `labs/backtests/ma.py` 从「薄封装」升级为真正的单文件合并（base/3x/c 三脚本合一）。
3. 移动端独立构建（当前 PWA 复用 Next 壳）；练习端完整错题本页面。
   注：PWA 已用 Chrome `Page.getInstallabilityErrors` 验证「可安装」，但**未**在真机 iOS/Android 上安装验证。
4. Docker/Postgres 路径未冒烟：本机 Docker 守护进程未启动，浏览器验证走的是 SQLite 冒烟后端。
5. `/api/ma-strategies/*` 旧 router 退役（现已标 deprecated + Deprecation 头）。

## 4. 最近一次验收（2026-10-22 第二十一轮：含真机浏览器验证）

```bash
cd apps/platform-api && python3 -m pytest -q                     # 752 passed
python3 -m ruff check apps packages scripts experiments labs     # All checks passed
python3 -m pytest packages/chanlun-core/tests -q                 # 8 passed
python3 scripts/audit_causality.py                               # 0 泄漏 / 0 漏报（指标层）
python3 scripts/audit_chanlun_causality.py                       # 0 泄漏 / 0 漏报（缠论引擎，1day/4h/1h）
cd apps/platform-web && npm test                                 # 57 passed
npx tsc --noEmit && npm run build                                # TSC_OK / BUILD_OK
node scripts/verify_browser.mjs --base http://127.0.0.1:3100     # 30/30 通过（真机 Chrome）
git ls-files | grep -cE 'data-view/.*\.js$|intraday/.*\.bin$'    # 0（生成数据已出库）
```

真机验证抓出并修掉 3 个构建期看不出的问题：前端右栏调用不存在的
`/api/opportunity-notifications*`（每页 404）、练习页揭晓永远 0/N（`/api/events` 升序 + limit 截断
导致窗口内事件被截掉）、胜负口径前后端不一致（截尾样本被误记为负）。详见
[docs/refactor/IMPLEMENTATION_2026-10-22.md](docs/refactor/IMPLEMENTATION_2026-10-22.md) §28 与
[docs/runbooks/BROWSER_VERIFICATION.md](docs/runbooks/BROWSER_VERIFICATION.md)。

## 5. 环境开关（常用）

`DB_AUTO_DDL=true`（默认，开发自动建表；生产走 Alembic）、
`INDICATOR_ALERTS_ENABLED`、`INDICATOR_ALERT_TIMEFRAMES`、`INDICATOR_ALERT_GRACE_MIN`、
`INDICATOR_SCAN_INTERVAL_MIN`、`EXPERIMENT_RUN_TIMEOUT_SEC`、`EXPERIMENT_ARTIFACT_ROOT`。

## 6. 历史阻塞（原 BLOCKED.md，2026-08-14/16）

- **M3B 影子运行时**：无阻塞。第二轮主审 8 项整改全部完成（红→绿：10 failed/72 passed →
  93 passed；全量 511 passed），活库迁移事务化完成。**完成门槛未达**：需满 30 天且 ≥100
  唯一 eligible 事件，本轮只上线并开始计时；M3C（对照报告与校准）未开始。
- **MTF-BTC-V1**：无阻塞。研究交付完成，生产规则切换明确延后到 walk-forward / 留出集 /
  影子评审（流程决定，非技术阻塞）。
