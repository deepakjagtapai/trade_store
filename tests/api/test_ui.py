import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app
from app.config import Settings
from app.main import create_app

STATIC_DIR = Path(app.__file__).parent / "static"


def make_client(**settings: bool) -> TestClient:
    return TestClient(
        create_app(Settings(database_url="sqlite://", enable_scheduler=False, **settings))
    )


def test_ui_page_is_served_as_html(client: TestClient) -> None:
    response = client.get("/ui")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Trade Store \u2013 Manual Test Console" in response.text


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/static/app.js", "javascript"),
        ("/static/styles.css", "text/css"),
        ("/static/scenarios.json", "application/json"),
    ],
)
def test_static_assets_are_served(client: TestClient, path: str, content_type: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]


def test_root_redirects_to_the_ui(client: TestClient) -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui"


@pytest.mark.parametrize("path", ["/ui", "/", "/static/app.js", "/config/ui"])
def test_ui_is_not_served_when_disabled(path: str) -> None:
    with make_client(enable_ui=False) as client:
        assert client.get(path, follow_redirects=False).status_code == 404
        assert client.get("/health").status_code == 200


@pytest.mark.parametrize("demo_mode", [False, True])
def test_config_ui_reports_demo_mode(demo_mode: bool) -> None:
    with make_client(demo_mode=demo_mode) as client:
        response = client.get("/config/ui")

    assert response.status_code == 200
    assert response.json() == {"demo_mode": demo_mode}


def test_ui_routes_are_hidden_from_openapi(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    assert "/ui" not in paths
    assert "/" not in paths


# --- Static-asset guards (no CDN, no innerHTML) -------------------------------------------


@pytest.mark.parametrize("name", ["index.html", "app.js", "styles.css"])
def test_assets_load_nothing_from_external_hosts(name: str) -> None:
    source = (STATIC_DIR / name).read_text()

    assert re.findall(r"https?://", source) == []
    assert "@import" not in source


def test_script_never_assigns_html_from_data() -> None:
    source = (STATIC_DIR / "app.js").read_text()

    for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
        assert sink not in source
