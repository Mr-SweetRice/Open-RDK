from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import statistics
import time
from typing import Callable


@dataclass(frozen=True)
class StreamLatencySample:
    number: int
    code_ms: float | None
    wait_ms: float
    loop_ms: float | None
    frame_gap_ms: float | None
    frame_age_ms: float | None
    estimated_skipped_frames: int


def _percentile(values: list[float], percent: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return math.nan
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


class StreamLatencyWatch:
    """Observe a normal streaming sensor read without creating another stream.

    ``code_ms`` is time spent by user code after the previous read returned and
    before the next read was requested. ``wait_ms`` is time blocked inside the
    getter waiting for a frame. Together they form the control-loop period.

    The default frame timestamp source works with ``LineSensorModule``. A custom
    monotonic timestamp callback can be supplied for another streaming module.
    """

    def __init__(
        self,
        sensor,
        *,
        name: str | None = None,
        getter: str | Callable = "get_data",
        expected_period_ms: float | None = None,
        report_every: int = 1,
        window: int = 500,
        output: Callable[[str], None] = print,
        frame_timestamp: Callable[[], float | None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.sensor = sensor
        self.name = str(name or getattr(sensor, "name", None) or "sensor")
        self.getter = getattr(sensor, getter) if isinstance(getter, str) else getter
        self.expected_period_ms = (
            float(expected_period_ms) if expected_period_ms is not None else None
        )
        self.report_every = max(0, int(report_every))
        self.output = output
        self._clock = clock
        self._frame_timestamp = frame_timestamp or self._default_frame_timestamp
        self._samples: deque[StreamLatencySample] = deque(maxlen=max(1, int(window)))
        self._count = 0
        self._previous_return_at: float | None = None
        self._previous_frame_at: float | None = None

    def _default_frame_timestamp(self) -> float | None:
        public_value = getattr(self.sensor, "last_data_received_monotonic", None)
        if callable(public_value):
            public_value = public_value()
        value = public_value
        if not isinstance(value, (int, float)):
            value = getattr(self.sensor, "_last_data_timestamp", None)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
        return None

    def read(self, *args, **kwargs):
        requested_at = self._clock()
        code_ms = (
            (requested_at - self._previous_return_at) * 1000.0
            if self._previous_return_at is not None
            else None
        )
        try:
            result = self.getter(*args, **kwargs)
        except Exception:
            failed_at = self._clock()
            self.output(
                f"[{self.name}] read failed after {(failed_at - requested_at) * 1000.0:.3f} ms"
            )
            raise

        returned_at = self._clock()
        wait_ms = (returned_at - requested_at) * 1000.0
        loop_ms = (
            (returned_at - self._previous_return_at) * 1000.0
            if self._previous_return_at is not None
            else None
        )
        frame_at = self._frame_timestamp()
        frame_gap_ms = (
            (frame_at - self._previous_frame_at) * 1000.0
            if frame_at is not None and self._previous_frame_at is not None
            else None
        )
        frame_age_ms = (
            max(0.0, (returned_at - frame_at) * 1000.0)
            if frame_at is not None
            else None
        )
        skipped = 0
        if (
            frame_gap_ms is not None
            and self.expected_period_ms is not None
            and self.expected_period_ms > 0
        ):
            skipped = max(0, round(frame_gap_ms / self.expected_period_ms) - 1)

        self._count += 1
        sample = StreamLatencySample(
            number=self._count,
            code_ms=code_ms,
            wait_ms=wait_ms,
            loop_ms=loop_ms,
            frame_gap_ms=frame_gap_ms,
            frame_age_ms=frame_age_ms,
            estimated_skipped_frames=skipped,
        )
        self._samples.append(sample)
        self._previous_return_at = returned_at
        if frame_at is not None:
            self._previous_frame_at = frame_at
        if self.report_every and self._count % self.report_every == 0:
            self.output(self.format_sample(sample))
        return result

    def get_data(self, *args, **kwargs):
        return self.read(*args, **kwargs)

    def format_sample(self, sample: StreamLatencySample) -> str:
        def value(number: float | None) -> str:
            return "   n/a" if number is None else f"{number:7.3f}"

        dominant = "startup"
        if sample.code_ms is not None:
            dominant = "code" if sample.code_ms > sample.wait_ms else "sensor wait"
        return (
            f"[{self.name}] #{sample.number:<6} "
            f"code={value(sample.code_ms)} ms  wait={value(sample.wait_ms)} ms  "
            f"loop={value(sample.loop_ms)} ms  frame_gap={value(sample.frame_gap_ms)} ms  "
            f"frame_age={value(sample.frame_age_ms)} ms  skipped~{sample.estimated_skipped_frames}  "
            f"dominant={dominant}"
        )

    def summary(self) -> str:
        samples = list(self._samples)
        if not samples:
            return f"[{self.name}] no samples"

        def stats(field: str) -> str:
            values = [
                float(value)
                for sample in samples
                if (value := getattr(sample, field)) is not None
            ]
            if not values:
                return "n/a"
            return (
                f"avg={statistics.fmean(values):.3f} "
                f"p50={_percentile(values, 50):.3f} "
                f"p95={_percentile(values, 95):.3f} "
                f"max={max(values):.3f} ms"
            )

        skipped = sum(sample.estimated_skipped_frames for sample in samples)
        return (
            f"[{self.name}] last {len(samples)} reads\n"
            f"  code:      {stats('code_ms')}\n"
            f"  wait:      {stats('wait_ms')}\n"
            f"  loop:      {stats('loop_ms')}\n"
            f"  frame gap: {stats('frame_gap_ms')}\n"
            f"  frame age: {stats('frame_age_ms')}\n"
            f"  estimated skipped frames: {skipped}"
        )
