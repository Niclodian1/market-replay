# 浏览器端到端验证（Runbook）

> 最后校对：2026-10-22。**构建通过 ≠ 页面能用**：本轮靠这套流程抓出两个真 bug
> （前端调用不存在的接口、练习页揭晓永远 0/N），所以每次改前端/接口后都跑一遍。

## 1. 起环境

```bash
# ① 后端（本地 Postgres 或 compose）
cd apps/platform-api && uvicorn app.main:app --port 8000

# ② 前端（必须先用生产构建起，验证的才是真产物）
cd apps/platform-web && npm run build && npx next start -p 3100

# ③ headless Chrome（CDP 调试端口）
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --headless=new --disable-gpu --no-sandbox --remote-debugging-port=9222 \
  --user-data-dir=/tmp/chrome-cdp about:blank
```

> 没有 Postgres / Docker 时，可用 SQLite 冒烟后端跑（跳过 Postgres 专属的 `m3_shadow_*` 表），
> 见 `docs/refactor/IMPLEMENTATION_2026-10-22.md` §28.1；这条路径只用于验证，不代表生产配置。

## 2. 跑验证

```bash
node scripts/verify_browser.mjs --base http://127.0.0.1:3100 --shots reports/browser-verify
```

覆盖 30 项断言：

| 分组 | 内容 |
| --- | --- |
| 页面渲染 | `/watch`、`/chart/BTC-USD`、`/stats`、`/library`、`/lab` 的 DOM 断言（标题/图表 canvas/表格行数） |
| 控制台 | 每页 **JS 异常 = 0、API 报错 = 0**（前端调不存在的接口会在这里暴露） |
| 练习交互 | 进场 → 1 条腿；加仓 → 2 条腿且权重 2；平仓结算；揭晓结果可点击且统计口径含截尾 |
| PWA | `Page.getAppManifest` 无错、`Page.getInstallabilityErrors` 为空、SW 注册+激活+接管、manifest 声明 |
| 离线 | 断网后导航 `/practice` 仍渲染且由 SW 接管 |

退出码非 0 即失败，可直接接进 CI（需要先起服务，见 §3）。

## 3. 判定口径

- **胜负**：与后端 `indicator_core.stats.summarize` 一致 —— `r_multiple > 0` 记胜，
  **截尾（censored）样本单独计数**，不进胜负、不进命中率与均值 R。
  前端实现在 `apps/platform-web/lib/practiceCore.ts` 的 `isWinningOutcome` / `revealedStats`。
- **事件窗口**：`/api/events` 按 `confirmed_at` 升序且带 `limit` 截断，**必须带窗口**
  （`start`/`end`，按 `bar_time` 过滤）；不带窗口 + 库里事件超过 limit 会把最新事件截掉。
- **可安装性**：以 Chrome 的 `installabilityErrors` 为空为准，不以「有 manifest」为准。

## 4. 已知限制

- 本流程跑在 headless Chrome 上，**不覆盖**真机 iOS/Android 安装与手势。
- Docker/Postgres 路径未在此脚本内验证；`docker compose up` 需另行冒烟。
