from fastapi import APIRouter
from app.api import health, market, indicators_api, watchlist_api, replay_notes_api, alerts_api, backtest_api, analysis_api, macro_api

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(market.router, prefix="/market", tags=["market"])
api_router.include_router(indicators_api.router, prefix="/indicators", tags=["indicators"])
api_router.include_router(watchlist_api.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(replay_notes_api.router, prefix="/replay/notes", tags=["replay_notes"])
api_router.include_router(alerts_api.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(backtest_api.router, prefix="/backtest", tags=["backtest"])
api_router.include_router(analysis_api.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(macro_api.router, prefix="/macro", tags=["macro"])
