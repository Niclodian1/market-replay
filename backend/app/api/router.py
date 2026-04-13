from fastapi import APIRouter
from app.api import health, market, indicators_api, watchlist_api, replay_notes_api

api_router = APIRouter()

api_router.include_router(health.router, tags=["health"])
api_router.include_router(market.router, prefix="/market", tags=["market"])
api_router.include_router(indicators_api.router, prefix="/indicators", tags=["indicators"])
api_router.include_router(watchlist_api.router, prefix="/watchlist", tags=["watchlist"])
api_router.include_router(replay_notes_api.router, prefix="/replay/notes", tags=["replay_notes"])
