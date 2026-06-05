# 策略库模块 (Strategy Library) — 开发文档

> 版本: v0.1 Draft
> 状态: 设计待实施
> 所属系统: market-replay (自我感知告警系统)
> 关联模块: `signal_registry` / `opportunity_backtester` / `edge_service`

---

## 1. 模块目标 (Why)

为用户提供一个**可视化的策略浏览与表现透视**入口:

1. **策略卡片墙** — 把系统中所有交易策略 (如 "MA30 突破"、"趋势线突破"、"Pinbar @ 200EMA"...) 以**卡片**形式陈列,一眼看清当前系统具备哪些 edge。
2. **策略详情页** — 点击任一卡片,进入该策略的**多品种表现透视表**,展示每个交易品种在此策略上的:
   - 累计收益 (Total Return / Avg R)
   - 胜率 (Win Rate)
   - 最大回撤 (Max Drawdown)
   - 交易次数 (Trades / Sample Size)
   - 期望值 (Expectancy, R)
   - 最近一次触发时间
3. **默认按胜率从高到低排序**,支持切换排序字段。

> 与现有 `opportunities` 模块的差异: opportunities 是 "**当前**有什么机会",strategy-library 是 "**这个策略历史上**在哪些品种上最强"。前者面向决策,后者面向选股/策略筛选。

---

## 2. 用户故事 (User Stories)

| # | 角色 | 想要 | 以便 |
|---|---|---|---|
| US-1 | 交易员 | 看到所有可用策略的概览卡片 | 快速了解系统有哪些 edge 可用 |
| US-2 | 交易员 | 点击 "MA30 突破" 卡片 | 查看该策略在 BTC/ETH/SOL/AAPL... 上的表现 |
| US-3 | 交易员 | 默认按胜率排序 | 第一眼看到最值得交易的品种 |
| US-4 | 交易员 | 切换排序为 "期望值 R" / "样本数" | 评估稳定性而非偶然胜率 |
| US-5 | 交易员 | 看到样本量警告 (n<30 标灰) | 避免被小样本胜率误导 |
| US-6 | 交易员 | 点击某行品种 | 跳转到 `chart/<symbol>` 并叠加该策略的历史信号点 |

---

## 3. 整体架构 (Where it fits)

```
┌──────────────── Frontend ────────────────┐
│  /strategies            策略卡片墙        │
│  /strategies/[id]       策略详情(品种表)  │
└─────────────────┬────────────────────────┘
                  │ REST
┌─────────────────▼────────────────────────┐
│  backend/app/api/strategy_library_api.py │
└─────────────────┬────────────────────────┘
                  │
   ┌──────────────┼──────────────┐
   ▼              ▼              ▼
signal_registry  edge_service  opportunity_backtester
(策略元数据)     (历史 edge)    (按需补算)
                  │
                  ▼
            EdgeStat / SignalEvent (PG)
```

复用已有组件,**不引入新数据表**:
- 策略元数据 → 直接来自 `signal_registry`
- 品种表现统计 → 聚合 `EdgeStat` (现有表)
- 缺失统计 → 触发一次 `opportunity_backtester` 异步回填

---

## 4. 数据模型 (Schema)

### 4.1 复用已有表

```python
# backend/app/models/models.py (已存在)
class EdgeStat(Base):
    id: int
    signal_combo_key: str    # 如 "ma_breakout|ma=30"
    symbol: str
    timeframe: str
    direction: str           # long / short
    sample_size: int
    win_rate: float
    avg_r: float
    expectancy: float
    max_drawdown: float
    last_updated_at: datetime
    last_fired_at: datetime
```

### 4.2 新增 Pydantic Schemas

```python
# backend/app/schemas/strategies.py (新建)

class StrategyCard(BaseModel):
    id: str                  # signal_registry name, e.g. "ma_breakout"
    name: str                # 中文名, "MA 突破"
    category: str            # indicator / pattern / structure
    description: str         # 一句话说明
    direction_support: list[str]   # ["long", "short"]
    params_schema: dict      # 该策略的参数,如 {"ma_period": 30}
    coverage: int            # 已有统计的品种数
    avg_win_rate: float | None     # 跨品种平均胜率
    top_symbol: str | None         # 表现最佳品种 (按胜率)

class StrategySymbolPerf(BaseModel):
    symbol: str
    timeframe: str
    direction: str
    sample_size: int
    win_rate: float
    avg_r: float
    expectancy: float
    max_drawdown: float
    last_fired_at: datetime | None
    confidence: Literal["low", "mid", "high"]   # 由 sample_size 推导

class StrategyDetail(BaseModel):
    card: StrategyCard
    rows: list[StrategySymbolPerf]
    sort_by: str
    sort_dir: str
    generated_at: datetime
```

---

## 5. 后端 API 设计

### 5.1 路由

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/strategies` | 卡片墙列表 |
| GET | `/api/strategies/{strategy_id}` | 详情(品种表) |
| POST | `/api/strategies/{strategy_id}/refresh` | 触发该策略全市场回填(异步任务) |

### 5.2 查询参数

`GET /api/strategies/{strategy_id}`:

| 参数 | 默认 | 说明 |
|---|---|---|
| `timeframe` | `4h` | 限定时间周期 |
| `direction` | `all` | long / short / all |
| `sort_by` | `win_rate` | win_rate / expectancy / avg_r / sample_size / last_fired_at |
| `sort_dir` | `desc` | asc / desc |
| `min_samples` | `10` | 过滤样本太小的行 |
| `symbols` | `null` | 逗号分隔限定范围,默认全部 watchlist |

### 5.3 关键实现 (伪代码)

```python
# backend/app/api/strategy_library_api.py

@router.get("/strategies", response_model=list[StrategyCard])
async def list_strategies(db: Session = Depends(get_db)):
    cards = []
    for name in signal_registry.names:
        meta = signal_registry.get(name)
        stats = db.query(EdgeStat).filter(EdgeStat.signal_combo_key == name).all()
        cards.append(StrategyCard(
            id=name,
            name=meta.display_name,            # 需要在 detector 上加 display_name
            category=meta.category,            # 需要在 detector 上加 category
            description=meta.description,
            direction_support=meta.direction_support,
            params_schema=meta.params_schema,
            coverage=len({s.symbol for s in stats}),
            avg_win_rate=mean(s.win_rate for s in stats if s.sample_size >= 30) or None,
            top_symbol=max(stats, key=lambda s: s.win_rate, default=None) and ...,
        ))
    return cards


@router.get("/strategies/{strategy_id}", response_model=StrategyDetail)
async def strategy_detail(strategy_id: str, ...query params...):
    rows = db.query(EdgeStat).filter(
        EdgeStat.signal_combo_key == strategy_id,
        EdgeStat.timeframe == timeframe,
        EdgeStat.sample_size >= min_samples,
    ).all()
    rows = [_to_perf(r) for r in rows]
    rows.sort(key=lambda r: getattr(r, sort_by), reverse=(sort_dir == "desc"))
    return StrategyDetail(card=..., rows=rows, ...)


@router.post("/strategies/{strategy_id}/refresh", status_code=202)
async def refresh_strategy(strategy_id: str, bg: BackgroundTasks):
    bg.add_task(opportunity_backtester.sweep_strategy, strategy_id)
    return {"status": "queued"}
```

### 5.4 SignalDetector 元数据扩展

为支持卡片展示,需要在 `SignalDetector` Protocol 上扩展属性 (向后兼容,提供默认值):

```python
class SignalDetector(Protocol):
    name: str
    display_name: str = ""        # "MA 突破"
    category: str = "indicator"   # indicator / pattern / structure
    description: str = ""
    direction_support: list[str] = ["long", "short"]
    params_schema: dict = {}
```

每个 `signals/*.py` 中的 detector 实例补齐这些字段。

### 5.5 信任度 (confidence) 推导

```python
def _confidence(sample_size: int) -> str:
    if sample_size < 30: return "low"
    if sample_size < 100: return "mid"
    return "high"
```

前端按 confidence 给行加底色,提醒用户警惕小样本。

---

## 6. 前端设计

### 6.1 路由 & 文件结构

```
frontend/app/(main)/strategies/
  ├── page.tsx                 # 卡片墙
  └── [strategyId]/page.tsx    # 详情(品种表)
frontend/components/strategies/
  ├── StrategyCard.tsx
  ├── StrategyGrid.tsx
  ├── StrategyHeader.tsx
  ├── StrategyPerfTable.tsx
  └── ConfidenceBadge.tsx
frontend/lib/api/strategies.ts # fetch 封装
```

在 `Rail.tsx` 增加导航入口: `📚 策略库 → /strategies`。

### 6.2 卡片墙 (`/strategies`)

布局: CSS Grid `repeat(auto-fill, minmax(280px, 1fr))`,每张卡 ≈ 280×180。

每张卡显示:
- 顶部: 策略名 + 分类 chip (`indicator` 蓝 / `pattern` 紫 / `structure` 橙)
- 中部: 一句话描述
- 底部数据条:
  - `📊 覆盖品种 24`
  - `🏆 跨品种平均胜率 58%`
  - `⭐ 最佳: BTCUSDT 4h`
- Hover: 上浮 + 阴影
- Click: `router.push(/strategies/${id})`

支持顶部 filter: `分类 (all/indicator/pattern/structure)` + `搜索框`。

### 6.3 详情页 (`/strategies/[strategyId]`)

**Header**:
- 返回按钮 + 策略名 + 描述
- Toolbar: `周期(1h/4h/1d)` `方向(全部/多/空)` `最小样本(10/30/100)` `🔄 刷新统计`

**主表 `StrategyPerfTable`** (默认按 `win_rate desc`):

| 排名 | 品种 | TF | 方向 | 样本 | 胜率 | 平均R | 期望值 | 最大回撤 | 最近触发 | 操作 |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | BTCUSDT | 4h | long | 142 | **68.3%** | 1.52 | +0.71R | -8.2% | 2h ago | 📈 |

实现要点:
- 表头点击切换 `sort_by` (调用后端,而非纯前端排序,以保持参数化)
- `confidence=low` 行整行 opacity 0.5 + 提示 tooltip "样本不足"
- 胜率列用色阶: ≥60% 绿,40-60% 黄,<40% 红
- 最右 `📈` 按钮 → `/chart/${symbol}?strategy=${strategyId}&overlay=signals`(在图表页绘出该策略历史触发点)
- 空态: "尚无统计 → 点击右上 🔄 触发回填"

### 6.4 API 客户端

```ts
// frontend/lib/api/strategies.ts
export async function listStrategies(): Promise<StrategyCard[]> { ... }
export async function getStrategyDetail(
  id: string,
  params: { timeframe?: string; direction?: string; sort_by?: string; sort_dir?: 'asc'|'desc'; min_samples?: number }
): Promise<StrategyDetail> { ... }
export async function refreshStrategy(id: string): Promise<void> { ... }
```

### 6.5 类型 (`frontend/types/index.ts` 增补)

```ts
export interface StrategyCard {
  id: string; name: string; category: 'indicator'|'pattern'|'structure';
  description: string; direction_support: ('long'|'short')[];
  params_schema: Record<string, unknown>;
  coverage: number; avg_win_rate: number | null; top_symbol: string | null;
}
export interface StrategySymbolPerf {
  symbol: string; timeframe: string; direction: 'long'|'short';
  sample_size: number; win_rate: number; avg_r: number;
  expectancy: number; max_drawdown: number;
  last_fired_at: string | null; confidence: 'low'|'mid'|'high';
}
export interface StrategyDetail {
  card: StrategyCard; rows: StrategySymbolPerf[];
  sort_by: string; sort_dir: 'asc'|'desc'; generated_at: string;
}
```

---

## 7. 任务拆解 (Implementation Plan)

| # | 模块 | 任务 | 预估 |
|---|---|---|---|
| T1 | backend | `SignalDetector` 增加元数据字段并在所有 detector 上补齐 | 0.5d |
| T2 | backend | `schemas/strategies.py` Pydantic 模型 | 0.25d |
| T3 | backend | `api/strategy_library_api.py` 三个端点 | 1d |
| T4 | backend | `opportunity_backtester.sweep_strategy(strategy_id)` 接口 | 0.5d |
| T5 | backend | `router.py` 注册 + 单测 | 0.5d |
| T6 | frontend | `lib/api/strategies.ts` + types | 0.25d |
| T7 | frontend | `/strategies` 卡片墙页 + `StrategyCard` / `StrategyGrid` | 1d |
| T8 | frontend | `/strategies/[id]` 详情 + `StrategyPerfTable` 排序联动 | 1d |
| T9 | frontend | `Rail.tsx` 加导航入口,chart 页 `?strategy=` overlay 联动 | 0.5d |
| T10 | QA | E2E: 卡片→详情→排序→刷新→图表跳转 | 0.5d |

合计 ≈ **6 人天**。

---

## 8. 验收标准 (Acceptance Criteria)

- [ ] 进入 `/strategies` 看到至少一张卡片(基于 `signal_registry` 现有 detector)
- [ ] 卡片显示覆盖品种数、平均胜率、最佳品种
- [ ] 点击卡片进入详情页,默认按胜率倒序展示品种行
- [ ] 切换排序字段,后端返回对应顺序
- [ ] 样本不足 (`min_samples` 默认 10) 的行不展示;`confidence=low` 行(<30)灰显
- [ ] 点击 🔄 刷新统计返回 202,后台异步执行,前端 toast 提示
- [ ] 点击行内 📈 跳转 `/chart/<symbol>` 并在图表上画出该策略历史信号点
- [ ] 全部接口有单测,覆盖空 `EdgeStat`、缺失 strategy_id、非法排序参数三种边界

---

## 9. 风险与未来扩展

**风险**:
- `EdgeStat` 表当前可能没有按 `signal_combo_key=单一策略名` 的细粒度行(只有组合)。需要先确认 edge_service 的写入维度,必要时为单策略额外建一份聚合视图。
- 全市场扫一个新策略的耗时:`opportunity_backtester` 需要并发限速,避免压垮 provider。

**未来 (v0.2)**:
- 卡片支持组合策略 (Combo),从 `confluence` 输出消费
- 详情页增加 "权益曲线" 缩略图列
- 策略对比模式: 选 2-3 个策略 → 同表对比同一品种的表现
- 导出 CSV / 订阅: "BTCUSDT 在 MA 突破上胜率掉到 50% 以下时通知我"
