"""前端静态托管与 SPA fallback 集成测试（ADR-021 / Harness IT-1~IT-5）。"""

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


def _make_web_dir(tmp_path: Path) -> Path:
    """构造一个最小前端产物目录（index.html + assets 产物文件）。"""
    web_dir = tmp_path / "web"
    (web_dir / "assets").mkdir(parents=True)
    (web_dir / "index.html").write_text("<html><body>console</body></html>", encoding="utf-8")
    (web_dir / "favicon.ico").write_bytes(b"ico")
    (web_dir / "assets" / "app.js").write_text("console.log('app')", encoding="utf-8")
    return web_dir


def _create_client(web_dir: Path | None) -> TestClient:
    """在指定 web 目录（或 None）下创建 TestClient。

    不进入 with 语句：lifespan 不执行，纯路由测试无需数据库连接。
    """
    with patch(
        "app.main.resolve_web_dist_dir",
        return_value=web_dir,
    ):
        from app.main import create_app

        app = create_app()
        return TestClient(app)


@pytest.fixture
def web_client(tmp_path: Path) -> TestClient:
    """web 目录存在时的客户端（不进入 with，避免 lifespan 二次开销）。"""
    return _create_client(_make_web_dir(tmp_path))


def test_root_returns_index_html(web_client: TestClient) -> None:
    """IT-1：web 存在时根路径返回 index.html。"""
    resp = web_client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "console" in resp.text


def test_spa_history_route_fallback(web_client: TestClient) -> None:
    """IT-2：history 路由刷新回退 index.html。"""
    for path in ("/tasks", "/settings/llm"):
        resp = web_client.get(path)
        assert resp.status_code == 200
        assert "console" in resp.text


def test_api_route_priority_preserved(web_client: TestClient) -> None:
    """IT-3：API 路由优先；未知 /api 路径返回 404 而非 index.html。"""
    resp = web_client.get("/api/unknown-endpoint")
    assert resp.status_code == 404
    payload = resp.json()
    assert payload["code"] is not None


def test_static_asset_served(web_client: TestClient) -> None:
    """IT-4：assets 产物文件直接返回。"""
    resp = web_client.get("/assets/app.js")
    assert resp.status_code == 200
    assert "app" in resp.text


def test_real_file_in_web_dir_served(web_client: TestClient) -> None:
    """web/ 下真实文件（favicon）直接返回。"""
    resp = web_client.get("/favicon.ico")
    assert resp.status_code == 200


def test_no_web_dir_keeps_json_root(tmp_path: Path) -> None:
    """IT-5：web 缺失时根路径保持服务信息 JSON（纯后端行为）。"""
    client = _create_client(None)
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.json()["service"] == "opc-central-dispatch-hub"
