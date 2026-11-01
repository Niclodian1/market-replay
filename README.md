# 行情复盘助手

A full-stack web application for replaying and analyzing historical market data, built with Next.js 14, FastAPI, and PostgreSQL.

> **本仓包含两个产品 + 两个共享包**（详见 [STATUS.md](STATUS.md) 与 [docs/README.md](docs/README.md)）：
> - `apps/platform-api` + `apps/platform-web`：盯盘 / 统计 / 实验台 / 告警（主产品）
> - `apps/chanlun-web`：缠论练习与教学静态站
> - `packages/indicator-core`：统一指标层（Series / Event / Outcome 契约）
> - `packages/chanlun-core`：缠论引擎（`apps/chanlun-web/chanlun.py` 为兼容 shim）

## Features

- **Multi-asset search** — stocks, ETFs, indices, crypto spot, crypto perpetuals, and equity perpetuals
- **Interactive candlestick charts** — 4-panel layout: Candlestick + MA20/MA60, Volume, RSI(14), MACD(12,26,9)
- **Dynamic historical loading** — scroll left to automatically load earlier candles
- **Log scale** — toggle logarithmic price axis
- **Replay mode** — pick a start date, then step candle-by-candle or auto-play at adjustable speed (80ms–2s per candle)
- **Replay notes** — record trend view, support/resistance, entry idea, stop-loss/take-profit, and summary per candle, stored in PostgreSQL
- **Watchlist** — add symbols with one click, persisted to the database
- **Signal monitoring** — dynamic watchlist plans, 15m/1h/4h/1d closed-bar checks, parallel MA/MACD/structure detectors, and durable delivery
- **Daily market report** — 08:00 crypto, A-share, U.S. stock, and commodity report by email, with a focused Discord summary

## Tech Stack

| Layer     | Technology |
|-----------|------------|
| Frontend  | Next.js 14 (App Router), TypeScript, Tailwind CSS, TradingView Lightweight Charts v4, React Query v5, Zustand |
| Backend   | FastAPI, SQLAlchemy 2.0 (async), asyncpg, Pydantic v2 |
| Database  | PostgreSQL 16 |
| Data      | Binance Spot/USD-M, Hyperliquid, local Binance parquet, Dukascopy, and Yahoo Finance — see **Data Sources** below |
| Container | Docker Compose |

## Quick Start

### Prerequisites
- Docker Desktop (or Docker Engine + Compose plugin)

### Run

```bash
git clone <repo>
cd market-replay
docker compose up -d
```

- Frontend: http://localhost:3000
- Backend API docs: http://localhost:8000/docs

No configuration required — everything runs out of the box.

### Stop

```bash
docker compose down
```

## Project Structure

```
market-replay/
├── apps/
│   ├── platform-api/          # FastAPI（原 backend/）
│   │   ├── app/
│   │   │   ├── api/           # REST routers（含 /api/series、/api/stats、/api/library、/api/experiments）
│   │   │   ├── core/          # 指标层、事件入库、告警、行情读取
│   │   │   ├── experiment_core/  # spec / runner / artifacts
│   │   │   ├── library/       # 指标库与发布门禁
│   │   │   └── models/        # SQLAlchemy ORM
│   │   └── tests/
│   ├── platform-web/          # Next.js（原 frontend/）
│   │   ├── app/(main)/        # watch / practice / stats / lab / library / replay / chart
│   │   ├── components/
│   │   ├── lib/               # api.ts、practiceCore.ts、staticData.ts
│   │   └── tests/             # node --test
│   └── chanlun-web/           # 缠论练习/教学静态站（原 fenxing/）
├── packages/indicator-core/   # 统一指标层（可独立安装）
├── packages/chanlun-core/     # 缠论引擎（分型/笔/线段/中枢/买卖点）
├── scripts/                   # 数据构建、因果审计、回填、告警 CLI
├── docs/refactor/             # 架构设计与实施记录
└── data/                      # 行情源与实验产物（产物 gitignore）
    └── store/useAppStore.ts
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check with DB ping |
| GET | `/api/market/search?q=` | Symbol search |
| GET | `/api/market/capabilities` | Supported sources, products, and timeframes |
| GET/POST | `/api/monitoring/plans` | Dynamic-watchlist monitor plans |
| GET/POST | `/api/monitoring/channels` | Server-side notification channels |
| GET | `/api/monitoring/events` | Durable, deduplicated signal events |
| POST | `/api/monitoring/run-now` | Run all active monitor plans immediately |
| GET | `/api/market/quote?symbol=` | Real-time quote |
| GET | `/api/market/candles?symbol=&timeframe=&outputsize=&before=` | OHLCV data |
| GET | `/api/watchlist` | List watchlist |
| POST | `/api/watchlist` | Add to watchlist |
| DELETE | `/api/watchlist/{id}` | Remove from watchlist |
| GET | `/api/replay/notes?symbol=` | List replay notes (optional symbol filter) |
| POST | `/api/replay/notes` | Create replay note |
| GET | `/api/replay/notes/{id}` | Get single note |
| DELETE | `/api/replay/notes/{id}` | Delete replay note |

## Data Sources

Market data is routed per symbol/timeframe by `apps/platform-api/app/providers/` and `core/candle_store.py`:

| Symbol / timeframe | Source | Notes |
|--------------------|--------|-------|
| `BTC-USD`, `ETH-USD`, etc. | **Binance Spot** | Official public API; native `15min` through `1week` candles |
| `BINANCE:BTCUSDT` | **Binance USD-M Futures** | Crypto perpetual OHLCV, mark/index price, funding, and open interest |
| `BINANCE:AAPLUSDT` | **Binance TradFi Perpetual** | Equity perpetual; intentionally distinct from the `AAPL` stock product |
| `HL:*` perpetuals, including HIP-3 markets such as `HL:XYZ:AAPL` | **Hyperliquid** | Quotes, derivatives metrics, recent OHLCV, and WebSocket mids across all perp DEXs |
| Crypto **with a local parquet** (`1h` / `4h` / `1day`) | **Binance single-market hybrid** (`local_crypto.py`) | Binance parquet history + Binance Spot tail; volumes are never spliced across exchanges |
| `XAUUSD` | **Dukascopy** | Spot-gold daily candles |
| Stocks, ETFs, and indices | **Yahoo Finance** (`yfinance`, no key) | Intraday history is subject to upstream window limits |

### Local crypto data (hybrid)

Pre-downloaded Binance OHLCV lives in `data/crypto/{SYMBOL}_{tf}.parquet`:

| Symbol | Timeframes | Coverage |
|--------|-----------|----------|
| `BTCUSDT`, `ETHUSDT` | `1h`, `4h`, `1d` | 2017-08 → present |

- **Symbol mapping** — `BTC-USD` / `BTC/USD` / `BTC-USDT` all normalize to `BTCUSDT`.
- **Why hybrid** — parquet gives instant, reproducible, offline-capable history; Binance Spot fills only the recent tail from the same market. If Binance is unreachable, it degrades gracefully to parquet-only.
- **Coverage gaps** — `15min` and `1week` have no parquet and use Binance Spot directly. Other crypto symbols also use Binance Spot rather than Yahoo.
- **Adding more symbols** — drop a `{SYMBOL}USDT_{1h,4h,1d}.parquet` (columns: `open_time` UTC, `open/high/low/close/volume`) into `data/crypto/` and it's picked up automatically.

The local hybrid feeds both the **chart/indicator/analysis** endpoints (`get_time_series`, via `pick_series_provider`) and the **backtest candle store** (`get_full_history` / `get_time_series_since`, which sync into the `market_candles` PostgreSQL table).

## Supported Timeframes

`15min` · `1h` · `4h` · `1day` · `1week`

> **Note:** 4h is not natively supported by Yahoo Finance — the backend aggregates 1h candles into 4-hour UTC-aligned windows.

## Signal monitoring

Monitor plans dynamically follow the user's watchlist. Each plan can select any
combination of `15min`, `1h`, `4h`, and `1day` (4h by default), plus any
combination of the following independent (OR) detectors. Every symbol/timeframe
pair is evaluated and deduplicated independently:

- **MA breakout/retest** — ATR-normalised candle strength with 5/10-bar volume evidence.
- **MACD momentum** — separate first-deceleration observation and confirmed price/area divergence events.
- **Structure** — causally confirmed swing points, three-touch trendlines, ATR fit tolerance, convergence, and close breakout.

Every event is written to `alert_events` before external delivery. Notification
destinations live in `notification_channels`; plans inherit the default channel
or reference an override. Discord is implemented now. Feishu and email can be
added as new sender adapters without changing signal detectors or monitor plans.
Discord embeds use green for long signals, red for short signals, amber for a
same-bar mixed batch, and show a symbol-specific logo or generated ticker badge.

Product identity is never changed as a fallback: `AAPL` remains the stock,
`BINANCE:AAPLUSDT` is a Binance TradFi perpetual, and `HL:XYZ:AAPL` is a
Hyperliquid perpetual.

## Notes

- Single-user application. All data is stored under a default user (no authentication required).
- Market data is fetched live from Yahoo Finance. Rate limits may apply for rapid sequential requests.
- Tables are auto-created on first startup — no migration tool needed for development.
- Daily-report rules, SMTP configuration, formulas, data fallbacks, and maintenance steps are documented in [`docs/runbooks/DAILY_EMAIL_REPORT.md`](docs/runbooks/DAILY_EMAIL_REPORT.md).
