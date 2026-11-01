# 项目空间梳理与重组建议（2026-10-22）

> 范围：`/Users/zhangzheyu/market-replay` 全仓。
> 结论先行：**代码质量不差，乱在「边界」和「产物」**——两个产品 + 一个引擎 + 三类实验沙箱共处一仓，
> 且把 192MB 生成数据当源码提交。所以优先级是「先止血、再抽骨、后分区」，不是重写。

---

## 0. TL;DR（如果只做五件事）

| # | 动作 | 收益 | 工作量 |
|---|------|------|--------|
| 1 | 停止把生成数据入库（`apps/chanlun-web/data-view/*.js` 192MB、`*.bin` 15MB），改为构建产物 + 单一数据根 | 仓库 1.7G→~80MB，`git status` 立刻干净 | 0.5–1 天 |
| 2 | 把缠论引擎从 `apps/chanlun-web/chanlun.py` 抽成 `packages/chanlun-core`，后端去掉 `sys.path` 注入 | 解除「拆目录就崩后端」的锁 | 2–3 天 |
| 3 | 明确两个产品：`apps/platform-*`（盯盘/回测/告警）与 `apps/chanlun-web`（缠论练习/教学）；mobile 出仓 | 目录名即产品名，新人/未来的自己 5 分钟能懂 | 3–5 天 |
| 4 | 回测/实验收进 `labs/`，一份登记表 + 一个入口命令；MA 策略 4 套 API 收敛到 unified | 消灭 15 个脚本 + 4 套接口的漂移 | 3–5 天 |
| 5 | 文档单一事实源：`docs/README.md` 索引 + `STATUS.md` 现状；`PROGRESS.md`(105KB) 归档 | 不再出现 README 与架构文档互相矛盾 | 0.5 天 |

**不要现在做**：重写 `chanlun.py`、立刻拆成多个 Git 仓库、`git filter-repo` 重写历史、DB 迁移。理由见 §7。

---

## 1. 事实盘点（实测数字）

### 1.1 体积

| 路径 | 大小 | 性质 | 是否入库 |
|------|------|------|----------|
| `.git` | 149 MB | 历史 | — |
| `apps/chanlun-web/` | 672 MB | 产品 + 引擎 + 数据 + 研究沙箱 | 部分 |
| ├─ `data-view/` | 197 MB | **生成的** 前端数据 JS（33MB/个） | **192 MB 入库** |
| ├─ `research/` | 403 MB | 抓取素材（图片/v2/media 391MB 已 ignore） | 脚本+小文件入库 |
| ├─ `mobile/` | 36 MB | kpractice 的开发副本 | 已 ignore，但仍在本地 |
| └─ `intraday/` | 32 MB | 月度 `.bin` 分片 | **15 MB 入库**（109 个文件） |
| `backend/` | 333 MB | API + 引擎 | 源码入库 |
| └─ `backend/data/` | 325 MB | 回测生成物 | ignore |
| `apps/platform-web/` | 436 MB | Next.js | 源码入库 |
| `data/` | 91 MB | 行情源 | **39 MB 入库** |
| `reports/` | 26 MB | 研究/备份产物 | ignore |
| `experiments/` | 2.4 MB | 3 个实验 | 入库 |

仓库总计 1.7 GB；`git ls-files` 725 个文件，其中 **约 246 MB 是可再生成的行情/图表数据**。

### 1.2 代码体量

| 区域 | 规模 | 备注 |
|------|------|------|
| `backend/app` | 26,409 行 / 102 文件 | `core/` 18,059 行、48 个平铺文件 |
| `apps/platform-api/tests` | 625 个测试函数 / 38 文件 | 覆盖不错，是重组的安全网 |
| `frontend` | 页面 4,067 行 + 组件 5,594 行 + lib 892 行 | 11 个页面 |
| `fenxing` 前端 | JS 4,334 行 + CSS 459 行 | 桌面版 00–08 号文件 + 移动版另 8 个文件 |
| `fenxing` 引擎 | `chanlun.py` 1,969 行 + `fractals.py` 332 行 | 无包结构，靠 `sys.path` 被调用 |
| `scripts/backtests` | 15 个脚本 ≈ 6.6k 行 | 6–7 个同族 MA 脚本 |
| `apps/chanlun-web/tools` | 34 个文件 | 22 个 `verify_*` |

### 1.3 现状逻辑图（目录即职责）

```
market-replay/
├── backend/            # 产品A 后端（FastAPI + PG）
├── apps/platform-web/           # 产品A 前端（Next.js 11 页）
├── apps/chanlun-web/            # ← 一个目录，七种职责
│   ├── chanlun.py      #   ① 引擎（被后端 sys.path 反向依赖）
│   ├── index.html js/ css/  # ② 产品B 桌面前端（静态站）
│   ├── mobile/         #   ③ 产品C 移动端（另一仓库的开发副本）
│   ├── data-view/      #   ④ 生成的数据产物（192MB 入库）
│   ├── tools/          #   ⑤ 构建 + 探针 + 22 个浏览器验证
│   ├── research/       #   ⑥ 外部方法论调研沙箱（403MB）
│   └── docs/           #   ⑦ 缠论规范 + 教学页
├── experiments/        # 产品A 的实验（3 个，各自为政）
├── scripts/backtests/  # 产品A 的历史回测脚本（15 个）
├── data/ backend/data/ reports/  # 三处数据/产物
└── docs/               # 架构/runbook/策略/PROGRESS
```

**一句话**：目录结构表达的是「文件是什么时候加的」，不是「它属于哪个产品」。

---

## 2. 九个具体问题（现象 → 证据 → 后果 → 方向）

### P1 · `apps/chanlun-web/` 一目录七职责，名字还只指「分型」

- 证据：`chanlun.py`（引擎）、`index.html/js/css`（桌面产品）、`mobile/`（另一个产品）、`data-view/`（产物）、`research/`（调研）、`tools/`（22 个验证）、`docs/`（规范+教学）全部平级。
- 后果：任何一次改动都要先判断「改的是引擎还是产品」；`ruff` 直接把整个 `fenxing` 排除；CI 完全不覆盖它。
- 方向：拆为 `apps/chanlun-web`（前端）/ `packages/chanlun-core`（引擎）/ `labs/research`（调研）/ `data/`（产物）。

### P2 · 缠论逻辑三份实现 + 运行时 `sys.path` 耦合

- 证据：
  - 引擎：`apps/chanlun-web/chanlun.py`（1,969 行，唯一事实源）
  - 后端适配：`apps/platform-api/app/core/chanlun_service.py`（848 行）靠 `sys.path.insert(0, _FENXING_DIR)` 再 `import chanlun`
  - 前端复刻：`apps/platform-web/lib/chanlunStats.ts`(270) + `chanlunStrategy.ts`(97) + `chanlunOverlay.ts`，只能靠 `apps/platform-web/tests/` 的 23 个用例守口径
- 后果：`fenxing` 目录一动后端就崩；TS 与 Python 口径漂移只能事后发现；`pyproject.toml` 必须把 `fenxing` 排除在 lint 之外。
- 方向：先抽 `packages/chanlun-core`（可 `pip install -e`），后端改 `import chanlun_core`；TS 侧保留但把「口径契约」写成 golden JSON（Python 生成 → TS 测试比对），而不是各写一遍。

### P3 · 回测/仿真四套引擎并存

- 证据：`backtest_engine.py`(707)、`strategy_backtest_engine.py`(822)、`matrix_backtest.py`、`mtf_historical_backtest.py`(1,129)、`outcome_labeling.py`(870)；另有 `apps/chanlun-web/backtest.py`、`multilevel_backtest.py`、`sanmai_hold_test.py`；`scripts/backtests/` 15 个脚本；`experiments/` 3 个。
- 后果：同一个「回测」有 10+ 个入口，结果不可比；新人无法判断哪个是权威口径；黄金测试只锁了其中两条路径。
- 方向：明确「一条主路径 + 若干研究脚本」。主路径 = `strategy_backtest_engine`（快慢双路径）+ `mtf_historical_backtest`（多周期研究）；其余进 `labs/` 并登记状态。

### P4 · MA 策略族：4 套 API × 6–7 个脚本 × 4 个页面

- 证据：`ma_strategies_api` / `-3x` / `-c` / `-unified` 四个 router；`scripts/backtests` 里 `backtest_ma_strategies{,_3x,_c}`、`backtest_ma_3x_exp`、`backtest_ma_breakout{,_v2}`；前端 `/ma-strategies`、`/strategy-lab`、`/strategy-overview`、`/backtest` 四页。
- 后果：杠杆/参数只在新接口修，旧接口继续长草；前端 4 页各自实现统计与图表。
- 方向：`unified` 是唯一对外接口，其余标 `deprecated` 并冻结；脚本合并为一个 `labs/backtests/ma.py --variant {base,3x,c} --leverage N`；前端保留 `/ma-strategies`（操作）+ `/strategy-overview`（总览），`/strategy-lab` 归入实验台。

### P5 · 数据六处存放，生成物当源码

- 证据：`data/`(91M)、`backend/data/`(325M)、`apps/chanlun-web/data-view/`(197M，192M 入库)、`apps/chanlun-web/intraday/`(32M，15M 入库)、`apps/chanlun-web/mobile/data/`(36M)、`reports/`(26M)。`apps/chanlun-web/data-view/data.js` 单文件 33MB，`index.html` 首屏就加载它。
- 后果：每次更新行情 = 一次几十 MB 的 commit；git 历史只会更大；首屏解析 33MB JSON；换机/CI 都要拉全量数据。
- 方向：**数据出库**（生成物 gitignore），保留「源 parquet + 一个构建命令」；前端改为按需 manifest（分片加载 `intraday/` 已有成熟机制，日线/4H 照抄）；产物发布到静态托管/对象存储。
- 注意：数据「可再生成」的前提是**源数据与生成脚本都在**。`build_all_symbols.py` 依赖 `data/` 的 parquet，这条链必须先固化成一条命令再删产物。

### P6 · 22 个验证脚本游离在 CI 之外

- 证据：`apps/chanlun-web/tools/verify_*` 22 个（mjs/cjs/py），CI 只有 backend `ruff+pytest` 和 frontend `tsc+lint+test`；`ruff` 配置 `exclude=["fenxing", ...]`。
- 后果：产品 B 的质量完全靠手动跑；改动无人守；脚本本身也在漂移（`verify_mobile_bias_browser.mjs` 这类还在增长）。
- 方向：`apps/chanlun-web/tests/` 统一入口（`npm test` 或 `node --test`），CI 加一个 job；纯浏览器回归拆成「本地手动 + 夜间可选」。

### P7 · 文档没有单一事实源，且已互相矛盾

- 证据：`README.md` 说「表在首次启动自动创建，开发无需迁移工具」，但仓里有 `alembic/`、`0001_baseline`、`docs/runbooks/DATABASE_MIGRATIONS.md`；README 完全没提缠论、告警 V2、M3B；`docs/architecture/PROJECT_OVERVIEW.md` 才是现状。另有 `BLOCKED.md`、`apps/chanlun-web/docs/TODO.md`、105KB 追加式 `docs/PROGRESS.md`。
- 后果：读文档要靠运气；状态散落 4 处。
- 方向：`docs/README.md` 做索引；`STATUS.md` 做唯一状态页（当前里程碑/阻塞/下一步）；`PROGRESS.md` 移入 `docs/archive/`；`BLOCKED.md` 并入 STATUS；架构文档保留但加「最后校对日期」。

### P8 · 仓库垃圾与游离物

- 证据：`apps/chanlun-web/2026-08-08-...txt`（181KB 会话转储）、根目录 `.pytest_cache/`、`.ruff_cache/`、各处 `.DS_Store`（含 `backend/.DS_Store` 6KB、`apps/platform-web/.DS_Store` 8KB）、`backend/tmp_parquet/`（空）、两份 `lightweight-charts*.js` 直接躺在 `apps/chanlun-web/` 根、`apps/chanlun-web/research/baoshen.../ocr_images.swift`。
- 后果：根目录 23 项，视觉噪声；大文件误入库风险（pre-commit 有 1MB 限制，但已有 33MB 文件在库里，说明是绕过或历史遗留）。
- 方向：P0 一次性清理；vendor 库移到 `vendor/`；会话转储不入库。

### P9 · 分支与命名

- 证据：`refactor/strategy-spine`、`main`、`HEAD` 三者同为 `2f6a212`——长期分支已与 main 同步但名字还在，且工作区有 6 个未提交改动 + 2 个未跟踪研究目录。仓库名 `market-replay` 只描述产品 A，却装着产品 B/C。
- 方向：先提交/归档未完成改动，删掉已同步的长期分支，回到「短分支 + 里程碑 tag」；仓库在 README 首行声明「monorepo：平台 + 缠论实验室」，或择机改名为 `trading-lab`（改名成本低，链接成本高，可缓）。

---

## 3. 目标架构

### 3.1 推荐：单仓分域（方案 A）

```
market-replay/
├── README.md                  # 5 分钟看懂：两个产品 + 一个引擎 + 一条数据链
├── STATUS.md                  # 唯一状态源（里程碑/阻塞/下一步）
├── Makefile                   # make data / make web / make test / make verify
├── apps/
│   ├── platform-api/          # ← backend/（FastAPI）
│   ├── platform-web/          # ← apps/platform-web/（Next.js）
│   └── chanlun-web/           # ← apps/chanlun-web/{index.html,js,css,docs/*.html}
│       ├── src/  tests/  public/
├── packages/
│   ├── chanlun-core/          # ← apps/chanlun-web/{chanlun.py,fractals.py,backtest.py}
│   │   ├── pyproject.toml
│   │   └── tests/
│   └── market-data/           # ← 数据读取/构建（build_all_symbols, ops/*）
├── labs/
│   ├── experiments/           # ← experiments/ + scripts/backtests/（带登记表）
│   │   └── registry.yaml      #   id/状态/数据/命令/产物/结论
│   ├── research/              # ← apps/chanlun-web/research/
│   └── ideas/                 # ← 「小想法」一页纸模板
├── data/                      # 唯一数据根（全部 gitignore 或 LFS）
│   ├── raw/                   #   parquet 源
│   ├── derived/               #   生成的分片/JS（构建产物）
│   └── reports/               #   ← reports/（gitignore）
├── docs/
│   ├── README.md              # 索引
│   ├── architecture/  runbooks/  strategies/  refactor/
│   └── archive/PROGRESS.md
└── .github/workflows/ci.yml   # backend + frontend + chanlun-web 三 job
```

**为什么不是一上来拆多仓**：后端运行时依赖 `apps/chanlun-web/chanlun.py`（`sys.path`），产品 B 的数据又是产品 A 数据的子集。拆仓前必须先有 `chanlun-core` 包与独立数据链，否则会把「目录耦合」升级成「仓库耦合 + 版本地狱」。

### 3.2 备选：拆仓（方案 B，条件成熟后）

| 仓库 | 内容 | 前置条件 |
|------|------|----------|
| `market-replay`（平台） | platform-api + platform-web + labs | `chanlun-core` 以 path/git 依赖引入 |
| `chanlun-web`（桌面练习） | 静态站 + 数据构建 | 数据链独立、可一键构建发布 |
| `kpractice`（移动） | 已有独立仓，保持 | 删除本仓 `apps/chanlun-web/mobile/`，只留发布脚本 |

拆分收益：部署与权限独立、CI 更快。成本：跨仓改引擎要发版；`chanlun-core` 需语义化版本。**建议在方案 A 稳定运行 2–4 周后再评估。**

### 3.3 归属映射表（现状 → 目标）

| 现状 | 归属 | 目标位置 | 处置 |
|------|------|----------|------|
| `backend/app` | 产品A | `apps/platform-api/app` | 移动；`core/` 内按域再分子包（见 §4.3） |
| `apps/platform-api/tests` | 产品A | `apps/platform-api/tests` | 移动 |
| `apps/platform-web/*` | 产品A | `apps/platform-web` | 移动 |
| `apps/chanlun-web/{index.html,js,css}` | 产品B | `apps/chanlun-web/src` | 移动 |
| `apps/chanlun-web/tools/verify_*` | 产品B | `apps/chanlun-web/tests` | 合并为测试入口 |
| `apps/chanlun-web/{chanlun.py,fractals.py,backtest.py}` | 引擎 | `packages/chanlun-core` | 抽包 + 保留 shim |
| `apps/chanlun-web/build_all_symbols.py` | 数据 | `packages/market-data` | 移动 + 参数化 |
| `apps/chanlun-web/tools/build_*.py` | 数据/产品B | 按调用方分 | 拆分 |
| `apps/chanlun-web/mobile/` | 产品C | 无（kpractice 仓） | 删除本地副本，保留 `publish_kpractice.sh` |
| `apps/chanlun-web/data-view/`、`intraday/` | 产物 | `data/derived/` | 出库 |
| `apps/chanlun-web/research/` | 调研 | `labs/research` | 移动 |
| `apps/chanlun-web/docs/*.html` | 产品B | `apps/chanlun-web/public` | 移动 |
| `apps/chanlun-web/docs/CHAN_FORMAL_SPEC.md` | 规范 | `docs/architecture/chanlun/` | 移动 |
| `experiments/`、`scripts/backtests/` | 实验 | `labs/experiments` | 合并 + 登记 |
| `scripts/ops/` | 数据/运维 | `packages/market-data/ops` + `docs/runbooks` | 拆分 |
| `reports/` | 产物 | `data/reports/` | 移动（continue ignore） |
| `data/`、`backend/data/` | 数据 | `data/raw`、`data/derived` | 合并 |
| `docs/architecture`、`runbooks`、`strategies` | 文档 | 原地 | 保留 |
| `docs/PROGRESS.md`、`BLOCKED.md` | 历史 | `docs/archive/` + `STATUS.md` | 归档/合并 |
| 根 `.pytest_cache`、`.ruff_cache`、`.DS_Store` | 垃圾 | 删除 | 删除 |
| `apps/chanlun-web/2026-08-08-*.txt` | 垃圾 | 删除 | 删除 |
| `lightweight-charts*.js` | vendor | `apps/chanlun-web/vendor` | 移动 |

---

## 4. 分阶段执行计划

> 原则：每一步都可独立回滚；先做「不改行为」的清理；每阶段结束跑全量测试。

**执行状态（2026-10-22 第二十轮，全部完成）**

| 阶段 | 状态 | 落地位置 / 证据 |
| --- | --- | --- |
| P0 卫生与止血 | ✅ | 缓存/临时文件清理、`.gitignore` 补齐、README 双产品说明；`git status` 无缓存目录 |
| P1 数据止血 | ✅ | `scripts/build_web_data.py` → `apps/platform-web/public/data`；`data-view/*.js`(192MB) 与 `intraday/*.bin`(32MB) `git rm --cached`；CI hygiene 守卫 |
| P2 抽 `chanlun-core` | ✅ | `packages/chanlun-core` + `apps/chanlun-web/{chanlun,fractals}.py` shim；后端无 `sys.path` 注入 |
| P3 分区成 `apps/` | ✅ | `apps/platform-api`、`apps/platform-web`、`apps/chanlun-web`；Docker/CI/Makefile/tsconfig 同步 |
| P4 回测与实验收口 | ✅ | `labs/experiments/registry.yaml`（三态登记 + 契约测试）、`labs/backtests/ma.py` 统一入口、旧 MA router 标 `deprecated` |
| P5 文档与状态 | ✅ | `docs/README.md` 索引、`STATUS.md`（含原 `BLOCKED.md`）、`PROGRESS.md` → `docs/archive/PROGRESS_2026H1.md`、`apps/chanlun-web/docs/TODO.md` → `labs/ideas/chanlun-p1-p5.md` |
| §5.3 `core/` 分域 | ⏳ 低优先 | 未做（审计原文即标注「P2 之后再做，低优先」），保留再导出 shim 方案 |

### P0 · 卫生与止血（0.5 天，零风险）

1. 先提交现有 6 个改动 + 2 个未跟踪目录（`apps/chanlun-web/research/btc_ma60_bias/`、两个 `verify_*bias*`），避免整理中丢失。
2. 删除：根 `.pytest_cache/`、`.ruff_cache/`、所有 `.DS_Store`、`backend/tmp_parquet/`、`apps/chanlun-web/__pycache__/`、`apps/chanlun-web/2026-08-08-*.txt`、`apps/chanlun-web/tools/render_check.png*`。
3. `.gitignore` 增补：`.pytest_cache/`、`.ruff_cache/`（已在，但根目录仍存在）、`*.tsbuildinfo`（已在）、`backend/tmp_parquet/`（已在）、`apps/chanlun-web/mobile/`（已在）——把缺口补齐即可。
4. 删除已与 main 同步的 `refactor/strategy-spine` 分支。
5. `README.md` 顶部加「本仓包含两个产品」的三行说明 + 指向 `STATUS.md`。

**验收**：`git status` 干净；根目录条目 ≤ 12；`pytest` / `tsc` 结果与整理前一致。

### P1 · 数据止血（1 天，可回滚）

1. 固化数据链：`packages/market-data` 暴露一条命令
   `python -m market_data.build --symbols all --tfs 1D,4H,1H --out data/derived`
   （现有 `build_all_symbols.py` + `build_mobile_data.py` + `ops/build_intraday_chunks.py` 收编）。
2. 验证「删掉产物 → 一条命令重建 → 页面正常」；记录耗时与体积。
3. `git rm --cached` 掉 `apps/chanlun-web/data-view/*.js`、`apps/chanlun-web/intraday/**/*.bin`，加 `.gitignore`。
4. 前端改为读 manifest（`data-view/manifest.json` 列出可用品种/周期），去掉 `index.html` 里硬编码的 `data.js`；BTC 不再首屏 33MB。
5. CI 加守卫：`git ls-files | grep -E 'data-view|\.bin$'` 非空即失败。

**验收**：仓库从 1.7G/246M tracked 降到 ~80M；`git clone` + `make data` 后页面可用。
**回滚**：`git revert` 该 commit（数据仍在历史里，不会丢）。

### P2 · 抽 `chanlun-core`（2–3 天，中等风险）

1. 新建 `packages/chanlun-core/`，把 `chanlun.py`、`fractals.py`、`backtest.py` 移入，补 `pyproject.toml`（`name=chanlun-core`）。
2. 在 `apps/chanlun-web/` 留兼容 shim：`chanlun.py` → `from chanlun_core.chanlun import *`（一个版本的过渡期）。
3. `chanlun_service.py` 删除 `sys.path.insert`，改 `import chanlun_core`；`pyproject.toml` 的 `exclude=["fenxing"]` 改为只排除研究目录；把 `packages/chanlun-core` 纳入 ruff/pytest。
4. 用现有 `test_chanlun_service.py` / `test_chanlun_causality.py` / `test_chanlun_scan.py` 做回归；再加一条 golden：同输入 → 同 `data-view` 摘要哈希。

**验收**：后端无 `sys.path` 注入；`ruff check packages` 通过；625 个测试全绿。
**回滚**：shim 还在，删包即回旧路径。

### P3 · 分区成 `apps/`（3–5 天，低风险但机械）

1. `git mv backend apps/platform-api`、`frontend apps/platform-web`；同步改 `docker-compose.yml`、CI 路径、`pyproject` 的 `testpaths`、前端 `tsconfig` 相对路径。
2. `git mv apps/chanlun-web/{index.html,js,css,vendor}` → `apps/chanlun-web/`；HTML 内相对路径改 `src/`。
3. `apps/chanlun-web/tools/verify_*` → `apps/chanlun-web/tests`，写一个 `run.sh` + `README`，CI 加 job（先跑纯 node 的 `.cjs`，浏览器回归留手动）。
4. `apps/chanlun-web/mobile/` 删除（源头在 `~/kpractice`），`publish_kpractice.sh` 移到 `apps/chanlun-web/scripts/` 或根 `scripts/`。
5. `apps/chanlun-web/research` → `labs/research`；`apps/chanlun-web/docs/*.html` → `apps/chanlun-web/public/docs`。

**验收**：`docker compose up -d` 正常；两个产品各自 README 能独立跑起来；CI 三个 job 全绿。

### P4 · 回测与实验收口（3–5 天）

1. 建 `labs/experiments/registry.yaml`，字段：`id / 标题 / 假设 / 状态(active|promoted|archived) / 数据 / 命令 / 产物 / 结论日期`。
2. `scripts/backtests/` 15 个脚本按目的分三类：① 被 API 调用的（保留在 `apps/platform-api/tools`）② 一次性研究（进 `labs/experiments`）③ 死代码（归档）。
3. MA 同族 6 个脚本合并为 `labs/backtests/ma.py --variant {base,3x,c} --leverage N --tf 4h`；CI 加一条 smoke（小样本）。
4. API 收敛：`ma_strategies_api`/`-3x`/`-c` 冠 `deprecated=True`（OpenAPI 可见），前端只调 `-unified`；下一个里程碑删旧 router。
5. 前端 `/strategy-lab` 定位为实验台，`/strategy-overview` 为总览，`/ma-strategies` 为操作台——在导航里写清三者关系，避免第四页再长出来。

**验收**：`registry.yaml` 覆盖全部实验；`ma.py` 与旧脚本在同一输入上结果一致（抽 2 个样本对比）。

### P5 · 文档与状态（0.5–1 天）

1. `docs/README.md`：索引（架构/runbook/策略/实验/规范），每个链接标注「最后校对」。
2. `STATUS.md`：当前里程碑、阻塞、下一步、最近一次验收命令与结果。
3. `docs/PROGRESS.md` → `docs/archive/PROGRESS_2026H1.md`；`BLOCKED.md` 并入 `STATUS.md`；`apps/chanlun-web/docs/TODO.md` → `labs/ideas/chanlun-p1-p5.md`（它就是一组待验证假设，天然属于 labs）。
4. 修正 `README.md` 与 Alembic 的矛盾表述；补缠论/告警 V2 一节。
5. 加 `docs/adr/`（架构决策记录，一页一条）：如「数据产物不入库」「引擎独立成包」。

**验收**：新读者只读 `README.md` + `STATUS.md` + `docs/README.md` 就能知道全貌与下一步。

---

## 5. 关键设计建议（避免二次变乱）

### 5.1 「一条数据链」原则
任何数据从源到前端只允许一条路径：
`data/raw/*.parquet → packages/market-data build → data/derived/{manifest,chunks} → 前端按需加载`。
禁止脚本之间互相读对方的中间产物（现在 `build_divergence_data.py` 同时 `sys.path.insert` backend 和 fenxing，就是反例）。

### 5.2 「实验三态」原则
每个想法只有三种归宿：`labs/ideas`（一页纸）→ `labs/experiments`（可复跑，有 registry）→ `apps/platform`（产品化）。
超过 30 天没动的实验自动标 `archived`，README 写结论。**没有结论的实验不许留在根目录。**

### 5.3 后端 `core/` 分域（P2 之后再做，低优先）
现在 48 个文件平铺。建议按域拆子包并**保留再导出 shim**，避免一次性改 100 处 import：
```
core/{market/{state,fusion,regime}, alerts/{engine,detectors,shadow}, chanlun/, backtest/, report/, notify/, common/}
```
每次只移一个域，跑全量测试。**不要**在同一个 commit 里既重构又改逻辑。

### 5.4 前端两产品边界
- 平台：盯盘、回测、告警、日报 → 需要 DB/后端。
- 练习：盲测、复盘、教学 → 纯静态、可离线。
平台里的 `/chart` 保留缠论 **overlay 图层**（只读分析），**不再**复制练习器/记账逻辑；练习器只在 `chanlun-web` 演进。

### 5.5 命名
`fenxing`（分型）只是缠论的一个概念，却承担了整个产品名。建议目录统一用 `chanlun`；仓库名若要改，`trading-lab` 比 `market-replay` 更准确（但会破坏外部链接，可后置）。

---

## 6. 验收指标（重组是否成功）

| 指标 | 现状 | 目标 |
|------|------|------|
| 仓库体积 / tracked 数据 | 1.7 GB / 246 MB | < 100 MB / 0 |
| 根目录条目 | 23 | ≤ 12 |
| 产品边界 | 靠 `fenxing` 一个目录 | `apps/` 下两个可独立启动的产品 |
| 缠论引擎副本 | 1 Python + 1 TS 复刻 + sys.path 注入 | 1 个包 + 契约测试 |
| 回测入口 | 10+ | 1 主路径 + labs 登记 |
| MA 对外 API | 4 | 1（+3 deprecated 过渡） |
| CI 覆盖 | backend + frontend | + chanlun-web |
| 文档状态源 | 4 处 | `STATUS.md` 1 处 |
| 数据重建 | 依赖手工多脚本 | `make data` 一条命令 |

---

## 7. 风险与「现在别做」

| 事项 | 为什么先别做 | 什么时候做 |
|------|--------------|------------|
| `git filter-repo` 删历史大文件 | 改写历史会影响所有克隆/备份；当前没有协作者，但不急 | P1 之后、数据出库稳定 1 个月 |
| 拆成多个 Git 仓库 | 后端硬依赖 `fenxing`，拆了就是版本地狱 | `chanlun-core` 出包后（P2 之后） |
| 重写 `chanlun.py` | 1,969 行且有正式规范与 625 个测试，重写收益低风险高 | 不做；只做包化 |
| 后端 `core/` 大搬迁 | 48 文件互相 import，一次搬完无法 review | P2 之后按域小步 |
| DB/表结构迁移 | 与本次整理无关 | 单独里程碑 |
| 删除 `data-view` 历史 commit | 产物虽可重建，但重建脚本需先验证 | P1 验证通过后 |

---

## 8. 建议的下一步（本周就能做）

1. **今天**：提交现有改动；跑一次全量 `pytest` + `tsc`，记录基线（这是后续所有整理的对照）。
2. **明天**：P0 清理 + 写 `STATUS.md` + README 三行说明（半天可完成）。
3. **本周内**：跑通「删产物 → 一条命令重建」的验证；这一条不通过，P1 不能动。
4. **下周**：抽 `chanlun-core`，这是杠杆最大的一步——它同时解锁「分区」「拆仓」「CI 覆盖产品 B」。

> 本文档只描述方案，未改动任何代码/数据。执行时建议每个阶段一个 PR（或一个独立 commit），并在 `STATUS.md` 记录验收命令与结果。
