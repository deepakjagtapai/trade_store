"""Time source abstraction.

This module is the only place allowed to read the wall clock. Every business rule
receives "today" from an injected ``Clock`` so tests can control time.
"""

import logging
import threading
from datetime import date
from typing import Protocol

logger = logging.getLogger(__name__)


class Clock(Protocol):
    def today(self) -> date: ...


class SystemClock:
    def today(self) -> date:
        return date.today()


class FixedClock:
    def __init__(self, d: date) -> None:
        self._today = d

    def today(self) -> date:
        return self._today

    def set(self, d: date) -> None:
        self._today = d


class SwitchableClock:
    """Real date unless an override is set. Demo mode only.

    Shared by request threads and the scheduler thread, hence the lock.
    """

    def __init__(self) -> None:
        self._system = SystemClock()
        self._override: date | None = None
        self._lock = threading.Lock()

    def today(self) -> date:
        with self._lock:
            override = self._override
        return override if override is not None else self._system.today()

    @property
    def override(self) -> date | None:
        with self._lock:
            return self._override

    def set_override(self, d: date) -> None:
        with self._lock:
            self._override = d
        logger.warning("Simulated date set to %s", d.isoformat(), extra={"override": d.isoformat()})

    def clear_override(self) -> None:
        with self._lock:
            self._override = None
        logger.warning("Simulated date cleared; using the real date", extra={"override": None})
