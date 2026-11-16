"""How long the parts of a stage took, for its log: Clock().lap(name) after each part, or a part timed with
`with clock.part(name):`, or clock.add(name, started) from a time.perf_counter() reading; then said()."""

import time
from typing import Any


class Clock:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}
        self._at = time.perf_counter()

    def lap(self, name: str) -> float:
        """The time since the last lap (or the start), kept under name (added to, if name is seen again)."""
        now = time.perf_counter()
        took, self._at = now - self._at, now
        self._keep(name, took)
        return took

    def add(self, name: str, started: float) -> float:
        """The time since started (a time.perf_counter() reading), kept under name. Laps are not moved."""
        took = time.perf_counter() - started
        self._keep(name, took)
        return took

    def part(self, name: str) -> "_Part":
        """For a with block: the time it takes, kept under name, however it ends. Laps are not moved."""
        return _Part(self, name)

    def said(self) -> str:
        """Each part of a second or more, the slowest first ("the old workbook 12s, comparing 1s")."""
        parts = sorted(((name, secs) for name, secs in self.seconds.items() if secs >= 1), key=lambda kv: -kv[1])
        return ", ".join(f"{name} {secs:.0f}s" for name, secs in parts) or "every part under a second"

    def _keep(self, name: str, took: float) -> None:
        self.seconds[name] = round(self.seconds.get(name, 0.0) + took, 2)


class _Part:
    def __init__(self, clock: Clock, name: str) -> None:
        self.clock, self.name, self.started = clock, name, 0.0

    def __enter__(self) -> None:
        self.started = time.perf_counter()

    def __exit__(self, *_: Any) -> None:
        self.clock.add(self.name, self.started)
