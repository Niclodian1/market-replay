"""旧接口仍可用，但必须带 Deprecation 头指向新出口。"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deprecation import install


def _client() -> TestClient:
    app = FastAPI()
    install(app)

    @app.get("/api/chanlun/analyze")
    def legacy_chanlun():
        return {"ok": True}

    @app.get("/api/ma-strategies/manifest")
    def legacy_ma():
        return {"ok": True}

    @app.get("/api/series/catalog")
    def new_series():
        return {"ok": True}

    return TestClient(app)


def test_legacy_routes_carry_deprecation_headers() -> None:
    client = _client()
    for path in ("/api/chanlun/analyze", "/api/ma-strategies/manifest"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["Deprecation"] == "true"
        assert "/api/series" in response.headers["Link"]
        assert "deprecated" in response.headers["Warning"]


def test_new_routes_do_not() -> None:
    response = _client().get("/api/series/catalog")
    assert "Deprecation" not in response.headers
