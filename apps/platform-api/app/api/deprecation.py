"""旧接口的 deprecated 标记：路径继续可用，但响应头告诉调用方新出口。

不直接删除旧路由：前端/脚本可能仍在用，先发 Deprecation 头 + OpenAPI 标记，
等新出口（``/api/series``、``/api/events``）覆盖完所有调用方再退役。
"""
from __future__ import annotations

from fastapi import FastAPI, Request

LEGACY_PREFIXES: tuple[str, ...] = (
    "/api/chanlun",
    "/api/ma-strategies",
    "/api/analysis",
)

SUCCESSOR = "</api/series>; rel=\"successor-version\""


async def legacy_deprecation_middleware(request: Request, call_next):
    response = await call_next(request)
    if any(request.url.path.startswith(prefix) for prefix in LEGACY_PREFIXES):
        response.headers.setdefault("Deprecation", "true")
        response.headers.setdefault("Link", SUCCESSOR)
        response.headers.setdefault(
            "Warning", '299 - "deprecated API, use /api/series"'
        )
    return response


def install(app: FastAPI) -> None:
    app.middleware("http")(legacy_deprecation_middleware)
