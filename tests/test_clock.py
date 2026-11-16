from household import clock
from household.clock import Clock


def test_parts_add_up_and_the_line_names_the_slow_ones(monkeypatch):
    now = iter([0.0, 2.0, 2.0, 2.4, 5.0])
    monkeypatch.setattr(clock.time, "perf_counter", lambda: next(now))
    c = Clock()  # 0
    c.lap("reading")  # 2: two seconds
    with c.part("comparing"):  # 2 to 2.4
        pass
    c.add("writing", 1.0)  # 1 to 5
    assert c.seconds == {"reading": 2.0, "comparing": 0.4, "writing": 4.0}
    assert c.said() == "writing 4s, reading 2s"  # a part under a second is left out of the line


def test_a_part_that_fails_is_still_timed():
    c = Clock()
    try:
        with c.part("reading"):
            raise ValueError("no")
    except ValueError:
        pass
    assert "reading" in c.seconds
    assert Clock().said() == "every part under a second"
