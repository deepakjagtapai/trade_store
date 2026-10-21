import ast
import logging
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest

import app
from app.clock import Clock, FixedClock, SwitchableClock, SystemClock

WALL_CLOCK_CALLS = {"today", "now", "utcnow"}


def test_fixed_clock_returns_the_date_it_was_created_with() -> None:
    clock = FixedClock(date(2015, 3, 14))

    assert clock.today() == date(2015, 3, 14)


def test_fixed_clock_set_moves_time() -> None:
    clock = FixedClock(date(2015, 3, 14))

    clock.set(date(2020, 1, 1))

    assert clock.today() == date(2020, 1, 1)


def test_system_clock_returns_real_today() -> None:
    assert SystemClock().today() == date.today()


def test_clocks_satisfy_the_clock_protocol() -> None:
    clocks: list[Clock] = [SystemClock(), FixedClock(date(2020, 1, 1))]

    assert all(isinstance(c.today(), date) for c in clocks)


def test_no_module_other_than_clock_reads_the_wall_clock() -> None:
    package_dir = Path(app.__file__).parent
    offenders = []
    for path in package_dir.rglob("*.py"):
        if path.name == "clock.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in WALL_CLOCK_CALLS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"date", "datetime"}
            ):
                offenders.append(f"{path.relative_to(package_dir)}:{node.lineno}")

    assert offenders == []


def test_switchable_clock_falls_back_to_the_real_date() -> None:
    clock = SwitchableClock()

    assert clock.override is None
    assert clock.today() == SystemClock().today()


def test_switchable_clock_override_and_clear() -> None:
    clock = SwitchableClock()

    clock.set_override(date(2015, 3, 14))
    assert clock.override == date(2015, 3, 14)
    assert clock.today() == date(2015, 3, 14)

    clock.clear_override()
    assert clock.override is None
    assert clock.today() == SystemClock().today()


def test_switchable_clock_satisfies_the_clock_protocol() -> None:
    clock: Clock = SwitchableClock()

    assert isinstance(clock.today(), date)


def test_switchable_clock_logs_every_change(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="app.clock")
    clock = SwitchableClock()

    clock.set_override(date(2015, 3, 14))
    clock.clear_override()

    changes = [(r.levelno, getattr(r, "override", "missing")) for r in caplog.records]
    assert changes == [(logging.WARNING, "2015-03-14"), (logging.WARNING, None)]


def test_switchable_clock_is_safe_to_use_from_many_threads() -> None:
    clock = SwitchableClock()
    dates = [date(2014, 5, 1) + timedelta(days=i) for i in range(50)]
    seen: list[date] = []
    errors: list[BaseException] = []

    def worker(d: date) -> None:
        try:
            for _ in range(100):
                clock.set_override(d)
                seen.append(clock.today())
                clock.clear_override()
        except BaseException as exc:  # pragma: no cover - only reached on failure
            errors.append(exc)

    logging.getLogger("app.clock").disabled = True
    try:
        threads = [threading.Thread(target=worker, args=(d,)) for d in dates]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        logging.getLogger("app.clock").disabled = False

    assert errors == []
    assert set(seen) <= {*dates, SystemClock().today()}
    assert clock.override is None
