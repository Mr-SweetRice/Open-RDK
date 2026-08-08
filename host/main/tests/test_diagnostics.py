from __future__ import annotations

import unittest

from openrdk.diagnostics import StreamLatencyWatch


class _Sensor:
    def __init__(self, clock):
        self.clock = clock
        self.last_data_received_monotonic = None

    def get_data(self):
        self.clock.value += 0.004
        self.last_data_received_monotonic = self.clock.value - 0.001
        return {"position": 0.0}


class _Clock:
    value = 10.0

    def __call__(self):
        return self.value


class StreamLatencyWatchTests(unittest.TestCase):
    def test_splits_code_time_from_sensor_wait_and_frame_age(self):
        clock = _Clock()
        sensor = _Sensor(clock)
        watch = StreamLatencyWatch(
            sensor,
            expected_period_ms=20,
            report_every=0,
            clock=clock,
        )

        self.assertEqual(watch.get_data(), {"position": 0.0})
        clock.value += 0.006
        self.assertEqual(watch.get_data(), {"position": 0.0})

        sample = list(watch._samples)[-1]
        self.assertAlmostEqual(sample.code_ms, 6.0)
        self.assertAlmostEqual(sample.wait_ms, 4.0)
        self.assertAlmostEqual(sample.loop_ms, 10.0)
        self.assertAlmostEqual(sample.frame_gap_ms, 10.0)
        self.assertAlmostEqual(sample.frame_age_ms, 1.0)
        self.assertIn("dominant=code", watch.format_sample(sample))


if __name__ == "__main__":
    unittest.main()
