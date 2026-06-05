# 股票商业模式分析模块（Stock Fundamentals & Equity Story）

> 面向 A 股 / 美股 / 港股，用普通投资者听得懂的语言，呈现公司的商业故事、盈利模式、经营状况与财报节奏。

---

## 1. 模块定位

当前系统聚焦行情、技术分析、回测、机会识别，缺乏面向"基本面 + 商业理解"的入口。本模块补齐这一层，作为独立菜单 **「商业模式」**（路由 `/fundamentals`）上线。

---

## 2. 四大功能模块（专业术语 + 通俗内容）

| # | 模块名 | 内容定位 | 数据来源 |
|---|---|---|---|
| 1 | **业务概览与权益叙事**（Business Overview & Equity Story） | 主营业务、行业赛道、竞争壁垒、增长逻辑 | Excel（LLM + 人工校对） |
| 2 | **盈利模式与收入结构**（Revenue Model & Profit Drivers） | 收入来源拆分、客户结构、毛利驱动、单位经济学 | Excel（LLM + 人工校对） |
| 3 | **经营业绩与财务快照**（Operating Performance Snapshot） | 最近季度营收 / 净利 / 增速、关键经营指标、同比 / 环比 | 外部 API（yfinance） |
| 4 | **财报日历与业绩前瞻**（Earnings Calendar & Guidance） | 下次财报日倒计时、上次回顾、市场一致预期、关注点 | 外部 API（yfinance） |

---

## 3. 数据策略

### 3.1 定性数据 —— 本地 Excel 知识库

- 文件路径：`backend/data/fundamentals/company_profiles.xlsx`
- 工作流：LLM 离线批量生成初稿 → 人工校对（标记 `reviewed=true`）→ 后端热加载
- 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| `symbol` | str | `AAPL` / `00700.HK` / `600519.SS` |
| `market` | str | `US` / `HK` / `CN` |
| `name_cn` / `name_en` | str | 公司中英文名 |
| `sector` / `industry` | str | 行业分类 |
| `business_overview` | str(~200字) | 业务概览（口语化） |
| `equity_story` | str(~200字) | 投资逻辑 / 增长故事 |
| `revenue_model` | str(~200字) | 盈利模式描述 |
| `revenue_breakdown` | json | `[{"segment":"iPhone","pct":52}, ...]`，前端饼图用 |
| `moat` | str | 护城河要点（分号分隔） |
| `key_risks` | str | 核心风险（分号分隔） |
| `updated_by` / `updated_at` | str | 维护信息 |
| `reviewed` | bool | 人工校对标记 |

- 首期播种 20–30 个高知名度标的（A / 美 / 港股各约 10 个）。

### 3.2 时效性数据 —— 外部 API（yfinance）

复用并扩展 `backend/app/providers/yahoo_finance.py`：

- 已有：`get_earnings_dates(symbol)` —— 下次财报日期
- **新增** `get_financial_snapshot(symbol)` —— 通过 `yfinance.Ticker.quarterly_financials` / `info` 取：
  - 最近季度营收、净利
  - YoY / QoQ 增速
  - 毛利率、市值、PE、ROE、股息率
- **新增** `get_earnings_history(symbol, n=4)` —— 最近 4 季度 EPS 实际 vs 预期

后端进程内缓存（TTL 1 小时）防止高频外呼。

---

## 4. 后端实施

### 4.1 新增文件

```
backend/app/api/fundamentals_api.py        # 路由
backend/app/core/fundamentals_service.py   # 合并 Excel + API 的业务逻辑
backend/app/core/profile_loader.py         # Excel 读取 + mtime 失效缓存
backend/app/schemas/fundamentals.py        # Pydantic 响应模型
backend/data/fundamentals/company_profiles.xlsx
```

### 4.2 修改文件

```
backend/app/api/router.py                  # 注册 fundamentals_api.router (prefix=/fundamentals)
backend/app/providers/yahoo_finance.py     # 增加 get_financial_snapshot / get_earnings_history
backend/requirements.txt                   # 加入 openpyxl
```

### 4.3 API 端点

| Method | Path | 说明 |
|---|---|---|
| GET | `/fundamentals/list?market=US\|HK\|CN&q=` | 列表页：返回 Excel 中所有标的的精简卡片 |
| GET | `/fundamentals/{symbol}` | 详情页：合并 Excel 定性 + API 实时，单一聚合响应 |
| GET | `/fundamentals/{symbol}/earnings` | 财报日历与历史（前端可独立刷新） |
| POST | `/fundamentals/reload` | Excel 热重载（开发 / 校对完手动调用） |

降级策略：Excel 无对应 symbol 时仅返回 API 实时部分，不报 500。

---

## 5. 前端实施

### 5.1 新增页面

- `frontend/app/(main)/fundamentals/page.tsx` —— 列表页：市场 Tab（A / 美 / 港）+ 搜索 + 卡片网格
- `frontend/app/(main)/fundamentals/[symbol]/page.tsx` —— 详情页布局：
  1. 顶部：公司名 + 市场标签 + 当前价格（复用现有 quote API）
  2. **业务概览与权益叙事** —— 文本块 + 「护城河」标签
  3. **盈利模式与收入结构** —— 文本 + 收入构成饼图（recharts）
  4. **经营业绩与财务快照** —— 关键指标卡（营收 / 净利 / 增速 / 毛利率 / ROE / PE）+ 最近 4 季度趋势小图
  5. **财报日历与业绩前瞻** —— 下次财报日倒计时大卡 + 历史 EPS 实际 vs 预期对比表

### 5.2 新增 / 修改文件

```
frontend/lib/api/fundamentals.ts                          # axios + react-query 客户端
frontend/components/fundamentals/BusinessOverviewCard.tsx
frontend/components/fundamentals/RevenueModelCard.tsx
frontend/components/fundamentals/FinancialSnapshotCard.tsx
frontend/components/fundamentals/EarningsCalendarCard.tsx
frontend/components/layout/Rail.tsx                       # 追加 { href:'/fundamentals', label:'商业模式' }
```

### 5.3 复用的现有资产

- `frontend/lib/api.ts` —— 已有 axios 客户端
- `@tanstack/react-query` —— 参考 `opportunities/page.tsx` 缓存模式
- `recharts` —— 已在依赖中
- `Rail.tsx` —— `NAV_ITEMS` 模式
- `yahoo_finance.py` 的 symbol 归一化（A 股 `.SS`/`.SZ`、港股 `.HK`）

---

## 6. 实施分阶段

| 阶段 | 内容 |
|---|---|
| Phase 1 | 后端骨架：Excel loader + schemas + 静态 API（含 3 条种子数据） |
| Phase 2 | 实时数据接入：扩展 yahoo_finance，合并 API 字段 |
| Phase 3 | 前端列表 + 详情页（复用 opportunities 页面模式） |
| Phase 4 | 数据填充：脚本批量调 LLM 生成 20–30 标的，人工校对 `reviewed` |
| Phase 5 | 优化：缓存、错误兜底、UI 细节 |

---

## 7. 验证方式

### 7.1 后端

- `curl localhost:8000/fundamentals/list?market=US` → 返回美股列表
- `curl localhost:8000/fundamentals/AAPL` → 返回合并后完整结构
- 修改 Excel 后 `POST /fundamentals/reload`，再 GET 验证最新内容
- 请求未播种 symbol（如 `MSFT`）→ 仅返回 API 部分，不报 500

### 7.2 前端

- `npm run dev` → 浏览器访问 `/fundamentals`
- 切换 A / 美 / 港 Tab、搜索框过滤
- 进入 `AAPL` 详情页，四个卡片渲染、财报倒计时正确
- 切 A 股 `600519`、港股 `00700.HK` 验证 symbol 归一化与中文叙述

### 7.3 数据更新流程

手动改 Excel 中某行 `business_overview` → `POST /fundamentals/reload` → 前端刷新看到新文案。

---

## 8. 关键文件清单（汇总）

**新增（13 个）**

```
backend/app/api/fundamentals_api.py
backend/app/core/fundamentals_service.py
backend/app/core/profile_loader.py
backend/app/schemas/fundamentals.py
backend/data/fundamentals/company_profiles.xlsx
frontend/app/(main)/fundamentals/page.tsx
frontend/app/(main)/fundamentals/[symbol]/page.tsx
frontend/lib/api/fundamentals.ts
frontend/components/fundamentals/BusinessOverviewCard.tsx
frontend/components/fundamentals/RevenueModelCard.tsx
frontend/components/fundamentals/FinancialSnapshotCard.tsx
frontend/components/fundamentals/EarningsCalendarCard.tsx
stock.md   # 本文档
```

**修改（3 个）**

```
backend/app/api/router.py
backend/app/providers/yahoo_finance.py
frontend/components/layout/Rail.tsx
```
