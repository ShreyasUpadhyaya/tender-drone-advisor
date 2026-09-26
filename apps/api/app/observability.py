"""Small, dependency-free observability primitives suitable for the case study."""

import json
import logging
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Lock

SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^,\s]+"
)
LOGGER = logging.getLogger("tender_advisor")


def redact(value: object) -> str:
    """Return bounded safe text; never log request bodies or provider payloads."""
    text = str(value)
    return SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)[:500]


class Metrics:
    def __init__(self) -> None:
        self._lock = Lock()
        self.counters: Counter[str] = Counter()
        self.durations: dict[str, list[float]] = defaultdict(list)

    def increment(self, name: str, **labels: str) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self.counters[key] += 1

    def observe(self, name: str, seconds: float, **labels: str) -> None:
        key = _metric_key(name, labels)
        with self._lock:
            self.durations[key].append(seconds)
            self.durations[key] = self.durations[key][-1000:]

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return {
                "counters": dict(sorted(self.counters.items())),
                "duration_ms": {
                    name: {
                        "count": len(values),
                        "avg": round(sum(values) * 1000 / len(values), 2) if values else 0,
                        "max": round(max(values) * 1000, 2) if values else 0,
                    }
                    for name, values in sorted(self.durations.items())
                },
            }


def _metric_key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    safe = ",".join(f'{key}="{redact(value)}"' for key, value in sorted(labels.items()))
    return f"{name}{{{safe}}}"


metrics = Metrics()


@contextmanager
def stage_timer(
    stage: str, *, trace_id: str | None = None, job_id: str | None = None
) -> Iterator[None]:
    started = time.perf_counter()
    outcome = "success"
    try:
        yield
    except Exception:
        outcome = "failure"
        raise
    finally:
        duration = time.perf_counter() - started
        metrics.observe("tda_stage_duration_seconds", duration, stage=stage, outcome=outcome)
        LOGGER.info(
            json.dumps(
                {
                    "event": "stage_finished",
                    "stage": stage,
                    "trace_id": trace_id,
                    "job_id": job_id,
                    "duration_ms": round(duration * 1000, 2),
                    "outcome": outcome,
                }
            )
        )
