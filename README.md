# 行情复盘助手

A full-stack web application for replaying and analyzing historical market data, built with Next.js 14, FastAPI, and PostgreSQL.

## Features

- **Multi-asset search** — stocks, ETFs, indices, crypto (powered by Yahoo Finance, no API key required)
- **Interactive candlestick charts** — 4-panel layout: Candlestick + MA20/MA60, Volume, RSI(14), MACD(12,26,9)
- **Dynamic historical loading** — scroll left to automatically load earlier candles
- **Log scale** — toggle logarithmic price axis
- **Replay mode** — pick a start date, then step candle-by-candle or auto-play at adjustable speed (80ms–2s per candle)
- **Replay notes** — record trend view, support/resistance, entry idea, stop-loss/take-profit, and summary per candle, stored in PostgreSQL
- **Watchlist** — add symbols with one click, persisted to the database

## Tech Stack

| Layer     | Technology |
|-----------|------------|
| Frontend  | Next.js 14 (App Router), TypeScript, Tailwind CSS, TradingView Lightweight Charts v4, React Query v5, Zustand |
| Backend   | FastAPI, SQLAlchemy 2.0 (async), asyncpg, Pydantic v2 |
| Database  | PostgreSQL 16 |
| Data      | Local crypto parquet + OKX + Yahoo Finance (`yfinance`) — see **Data Sources** below |
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
├── backend/
│   ├── app/
│   │   ├── api/              # FastAPI routers
│   │   │   ├── health.py
│   │   │   ├── market.py
│   │   │   ├── watchlist_api.py
│   │   │   └── replay_notes_api.py
│   │   ├── core/             # Config, indicator calculations
│   │   ├── db/               # SQLAlchemy engine & session
│   │   ├── models/           # ORM models (User, Watchlist, ReplayNote)
│   │   ├── providers/        # Data sources: local_crypto (parquet+OKX), binance (OKX), yahoo_finance
│   │   └── schemas/          # Pydantic request/response models
│   └── requirements.txt
└── frontend/
    ├── app/
    │   └── (main)/
    │       ├── chart/[symbol]/   # Chart page with dynamic loading
    │       ├── replay/[symbol]/  # Replay mode page
    │       └── notes/            # Replay notes listing
    ├── components/
    │   ├── chart/CandlestickChart.tsx
    │   ├── ReplayControls.tsx
    │   └── ReplayNoteForm.tsx
    ├── lib/
    │   ├── api.ts            # Axios API client
    │   └── indicators.ts     # Client-side MA/RSI/MACD calculations
    └── store/useAppStore.ts
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check with DB ping |
| GET | `/api/market/search?q=` | Symbol search |
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

Market data is routed per symbol/timeframe by `backend/app/providers/` and `core/candle_store.py`:

| Symbol / timeframe | Source | Notes |
|--------------------|--------|-------|
| Crypto **with a local parquet** (`1h` / `4h` / `1day`) | **Local hybrid** (`local_crypto.py`) | parquet history as the base + OKX to fill the gap from the parquet cutoff to now |
| Other crypto, `1h` / `4h` | **OKX** (`binance.py`, public API, no key) | history back to ~2018 |
| Everything else (stocks, ETFs, indices, weekly crypto, …) | **Yahoo Finance** (`yfinance`, no key) | |

### Local crypto data (hybrid)

Pre-downloaded Binance OHLCV lives in `data/crypto/{SYMBOL}_{tf}.parquet`:

| Symbol | Timeframes | Coverage |
|--------|-----------|----------|
| `BTCUSDT`, `ETHUSDT` | `1h`, `4h`, `1d` | 2017-08 → present |

- **Symbol mapping** — `BTC-USD` / `BTC/USD` / `BTC-USDT` all normalize to `BTCUSDT`.
- **Why hybrid** — parquet gives instant, reproducible, offline-capable history (no rate limits); OKX is only queried for the recent tail since the parquet cutoff, so charts/backtests never go stale. If OKX is unreachable (offline / rate-limited), it degrades gracefully to parquet-only.
- **Coverage gaps** — `15min` and `1week` have no parquet and fall back to the remote sources above; symbols other than BTC/ETH fall back to OKX (1h/4h) or Yahoo.
- **Adding more symbols** — drop a `{SYMBOL}USDT_{1h,4h,1d}.parquet` (columns: `open_time` UTC, `open/high/low/close/volume`) into `data/crypto/` and it's picked up automatically.

The local hybrid feeds both the **chart/indicator/analysis** endpoints (`get_time_series`, via `pick_series_provider`) and the **backtest candle store** (`get_full_history` / `get_time_series_since`, which sync into the `market_candles` PostgreSQL table).

## Supported Timeframes

`15min` · `1h` · `4h` · `1day` · `1week`

> **Note:** 4h is not natively supported by Yahoo Finance — the backend aggregates 1h candles into 4-hour UTC-aligned windows.

## Notes

- Single-user application. All data is stored under a default user (no authentication required).
- Market data is fetched live from Yahoo Finance. Rate limits may apply for rapid sequential requests.
- Tables are auto-created on first startup — no migration tool needed for development.
