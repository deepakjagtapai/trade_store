"""Browser smoke tests for the manual-testing console.

Run with ``make e2e``. Uses the locally installed Chrome by default
(``E2E_BROWSER_CHANNEL=chromium`` after ``playwright install chromium`` to use the bundled one).
"""

import os
import socket
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e

playwright_api = pytest.importorskip("playwright.sync_api")
Page = playwright_api.Page
expect = playwright_api.expect

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@pytest.fixture(scope="module")
def start_server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Callable[[bool], str]]:
    processes: list[subprocess.Popen[bytes]] = []

    def start(demo_mode: bool) -> str:
        port = free_port()
        db = tmp_path_factory.mktemp("db") / "trades.db"
        env = {
            **os.environ,
            "DEMO_MODE": str(demo_mode).lower(),
            "ENABLE_SCHEDULER": "false",
            "DATABASE_URL": f"sqlite:///{db}",
        }
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(proc)
        base_url = f"http://127.0.0.1:{port}"
        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{base_url}/health", timeout=1):
                    return base_url
            except OSError:
                time.sleep(0.1)
        pytest.fail("server did not start")

    yield start
    for proc in processes:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    channel = os.environ.get("E2E_BROWSER_CHANNEL", "chrome")
    with playwright_api.sync_playwright() as pw:
        browser = pw.chromium.launch(channel=None if channel == "chromium" else channel)
        yield browser
        browser.close()


@pytest.fixture
def open_ui(browser: object, start_server: Callable[[bool], str]) -> Iterator[Callable[..., Page]]:
    pages: list[Page] = []

    def open_page(demo_mode: bool) -> Page:
        page: Page = browser.new_page()  # type: ignore[attr-defined]
        page.goto(f"{start_server(demo_mode)}/ui")
        expect(page.locator("#scenario-select option")).not_to_have_count(0)
        expect(page.locator("#busy")).to_be_hidden()
        pages.append(page)
        return page

    yield open_page
    for page in pages:
        page.close()


def store_rows(page: Page) -> list[list[str]]:
    rows = page.locator("#store-body tr")
    return [rows.nth(i).locator("td").all_inner_texts() for i in range(rows.count())]


def test_lower_version_scenario_shows_one_accepted_and_one_rejected(
    open_ui: Callable[..., Page],
) -> None:
    page = open_ui(demo_mode=True)

    page.select_option("#scenario-select", "lower-version")
    page.click("#load-scenario-btn")
    expect(page.locator("#today-badge")).to_have_text("Simulated")
    expect(page.locator("#today-value")).to_have_text("15/03/2015")
    page.click("#submit-btn")

    expect(page.locator("#results-summary")).to_contain_text("matches the scenario")
    expect(page.locator("#results-body .badge.accepted")).to_have_count(1)
    expect(page.locator("#results-body .badge.rejected")).to_have_count(1)
    expect(page.locator("#results-body")).to_contain_text("LOWER_VERSION")
    assert store_rows(page) == [["T2", "2", "CP-2", "B1", "20/05/2021", "15/03/2015", "N"]]


def test_guided_demo_rebuilds_the_sample_table(open_ui: Callable[..., Page]) -> None:
    page = open_ui(demo_mode=True)

    page.click("#guided-demo-btn")

    expect(page.locator("#results-summary")).to_contain_text("matches the sample table")
    assert store_rows(page) == [
        ["T1", "1", "CP-1", "B1", "20/05/2020", "15/03/2015", "N"],
        ["T2", "2", "CP-2", "B1", "20/05/2021", "15/03/2015", "N"],
        ["T2", "1", "CP-1", "B1", "20/05/2021", "14/03/2015", "N"],
        ["T3", "3", "CP-3", "B2", "20/05/2014", "01/05/2014", "Y"],
    ]
    expect(page.locator("#store-body tr.expired")).to_have_count(1)
    expect(page.locator("#store-count")).to_have_text("(4 trades)")


def test_invalid_json_is_caught_and_nothing_is_sent(open_ui: Callable[..., Page]) -> None:
    page = open_ui(demo_mode=True)
    posted: list[str] = []
    page.on("request", lambda r: posted.append(r.url) if r.method == "POST" else None)

    page.fill("#json-input", '{"trade_id": "T1",')
    page.click("#submit-btn")

    expect(page.locator("#json-error")).to_contain_text("Invalid JSON")
    page.wait_for_timeout(300)
    assert posted == []


def test_demo_controls_are_hidden_when_demo_mode_is_off(open_ui: Callable[..., Page]) -> None:
    page = open_ui(demo_mode=False)

    expect(page.locator("#date-bar")).to_be_hidden()
    expect(page.locator("#reset-btn")).to_be_hidden()
    expect(page.locator("#guided-demo-btn")).to_be_hidden()
    expect(page.locator("#date-note")).to_be_visible()

    page.select_option("#scenario-select", "lower-version")
    page.click("#load-scenario-btn")
    expect(page.locator("#scenario-note")).to_contain_text("could not be simulated")
