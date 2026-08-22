from fastapi import APIRouter
from app.api import (
    alerts_api,
    analysis_api,
    backtest_api,
    chanlun_api,
    health,
    indicators_api,
    ma_strategies_api,
    ma_strategies_3x_api,
    ma_strategies_c_api,
    ma_strategies_unified_api,
    macro_api,
    market,
    monitoring_api,
    replay_notes_api,
    strategy_lab_api,
    watchlist_api,
)

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(market.router, prefix="/market", tags=["market"])
api_router.include_router(indicators_api.router, prefix="/indicators", tags=["indicators"])
api_router.include_router(watchlist_api.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(replay_notes_api.router, prefix="/replay/notes", tags=["replay_notes"])
api_router.include_router(alerts_api.router, prefix="/alerts", tags=["alerts"])
api_router.include_router(monitoring_api.router, prefix="/monitoring", tags=["monitoring"])
api_router.include_router(backtest_api.router, prefix="/backtest", tags=["backtest"])
api_router.include_router(chanlun_api.router, prefix="/chanlun", tags=["chanlun"])
api_router.include_router(analysis_api.router, prefix="/analysis", tags=["analysis"])
api_router.include_router(macro_api.router, prefix="/macro", tags=["macro"])
api_router.include_router(strategy_lab_api.router, prefix="/strategy-lab", tags=["strategy-lab"])
api_router.include_router(ma_strategies_api.router, prefix="/ma-strategies", tags=["ma-strategies"])
api_router.include_router(ma_strategies_3x_api.router, prefix="/ma-strategies-3x", tags=["ma-strategies-3x"])
api_router.include_router(ma_strategies_c_api.router, prefix="/ma-strategies-c", tags=["ma-strategies-c"])
api_router.include_router(ma_strategies_unified_api.router, prefix="/ma-strategies-unified", tags=["ma-strategies-unified"])
