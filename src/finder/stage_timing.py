"""Fixed-key, anonymous wall-time observations, independent of work-budget clocks."""

from contextlib import contextmanager
from time import perf_counter_ns

STAGES = (
    "setup",
    "catalog",
    "search",
    "cached_queue",
    "cached_read",
    "cached_review",
    "cached_disposition",
    "detail_queue",
    "detail",
    "finalize",
)


class StageTimings:
    """Exclusive nested spans: each nanosecond belongs only to the innermost stage.

    Calls count entered spans; completed counts normal returns, not evaluated rows or
    successful provider requests. Failed spans still contribute time. No data from the
    measured work enters the report. Measure only; never read or extend a work deadline.
    """

    def __init__(self):
        self.elapsed = dict.fromkeys(STAGES, 0)
        self.calls = dict.fromkeys(STAGES, 0)
        self.completed = dict.fromkeys(STAGES, 0)
        self.children = []

    @contextmanager
    def measure(self, stage):
        self.calls[stage] += 1
        started = perf_counter_ns()
        self.children.append(0)
        try:
            yield
            self.completed[stage] += 1
        finally:
            elapsed = perf_counter_ns() - started
            self.elapsed[stage] += elapsed - self.children.pop()
            if self.children:
                self.children[-1] += elapsed

    def call(self, stage, function, *args, **kwargs):
        with self.measure(stage):
            return function(*args, **kwargs)

    def report(self):
        return {
            "timing_total_ms": sum(self.elapsed.values()) // 1_000_000,
            **{f"timing_{stage}_ms": self.elapsed[stage] // 1_000_000 for stage in STAGES},
            **{f"timing_{stage}_calls": self.calls[stage] for stage in STAGES},
            **{f"timing_{stage}_completed": self.completed[stage] for stage in STAGES},
        }
