from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.api.router import api_router
from app.db.session import engine
from app.db.base import Base

# 必须在 create_all 之前导入所有 model，否则表不会被创建
import app.models  # noqa: F401


@asynccontextmanager
async def lifespan(application: FastAPI):
    # 启动时自动建表（开发阶段使用，生产环境换 alembic）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(
    title="Market Replay API",
    description="行情复盘助手后端接口",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api")
