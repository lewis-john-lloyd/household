"""How long the parts of a stage took, for its log: Clock().lap(name) after each part, then said()."""

import time


class Clock:
    def __init__(self) -> None:
        self.seconds: dict[str, float] = {}
        self._at = time.perf_counter()

    def lap(self, name: str) -> float:
        """The time since the last lap (or the start), kept under name (added to, if name is seen again)."""
        now = time.perf_counter()
        took, self._at = now - self._at, now
        self.seconds[name] = round(self.seconds.get(name, 0.0) + took, 2)
        return took

    def said(self) -> str:
        """Each part's time, the slowest first ("the old workbook 12s, comparing 1s")."""
        parts = sorted(self.seconds.items(), key=lambda kv: -kv[1])
        return ", ".join(f"{name} {secs:.0f}s" for name, secs in parts) or "nothing timed"
