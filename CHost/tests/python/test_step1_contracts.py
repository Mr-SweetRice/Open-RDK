import json
import unittest
from pathlib import Path


TESTS_ROOT = Path(__file__).resolve().parents[1]
FRAME_FIXTURE = TESTS_ROOT / "fixtures" / "protocol_frames" / "v1.json"
LIFECYCLE_FIXTURE = TESTS_ROOT / "fixtures" / "lifecycle" / "v1.json"
SYNC = bytes.fromhex("aa55aa55")


def build_frame(payload: str, message_type: int, sequence: int) -> bytes:
    body = payload.encode("utf-8")
    if not 1 <= len(body) <= 200:
        raise ValueError("fixture payload length is outside protocol bounds")
    if not 0 <= sequence <= 0xFFFFFF:
        raise ValueError("fixture sequence is outside uint24")
    return (
        SYNC
        + bytes([len(body)])
        + body
        + bytes([message_type])
        + sequence.to_bytes(3, "big")
    )


class Step1FixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = json.loads(FRAME_FIXTURE.read_text(encoding="utf-8"))
        cls.lifecycle = json.loads(LIFECYCLE_FIXTURE.read_text(encoding="utf-8"))

    def test_schema_versions(self):
        self.assertEqual(self.frames["schema_version"], 1)
        self.assertEqual(self.lifecycle["schema_version"], 1)

    def test_protocol_constants(self):
        protocol = self.frames["protocol"]
        self.assertEqual(bytes.fromhex(protocol["sync_hex"]), SYNC)
        self.assertEqual(protocol["max_payload"], 200)
        self.assertEqual(protocol["sequence_bytes"], 3)
        self.assertEqual(
            protocol["message_types"],
            {"CMD": 1, "TEST": 2, "TELEMETRY": 3, "CONTROL": 4},
        )

    def test_explicit_stream_frame_hex(self):
        types = self.frames["protocol"]["message_types"]
        explicit = [
            item for item in self.frames["stream_frames"] if "frame_hex" in item
        ]
        self.assertTrue(explicit)
        for item in explicit:
            actual = build_frame(
                item["payload_utf8"],
                types[item["message_type"]],
                item["sequence"],
            )
            self.assertEqual(actual.hex(), item["frame_hex"], item["id"])

    def test_all_supported_modules_have_frames(self):
        present = {item["module_type"] for item in self.frames["stream_frames"]}
        self.assertEqual(
            present,
            {
                "traction_module",
                "line_sensor_module",
                "color_module",
                "distance_sensor_module",
            },
        )

    def test_malformed_coverage(self):
        ids = {item["id"] for item in self.frames["malformed_cases"]}
        self.assertTrue(
            {
                "noise_before_sync",
                "partial_sync",
                "zero_length",
                "length_above_max",
                "truncated_payload",
                "unknown_message_type",
                "duplicate_sequence",
            }.issubset(ids)
        )

    def test_lifecycle_ids_are_unique(self):
        ids = [item["id"] for item in self.lifecycle["cases"]]
        self.assertEqual(len(ids), len(set(ids)))
        for case in self.lifecycle["cases"]:
            self.assertEqual(len(case["events"]), len(case["states"]))


if __name__ == "__main__":
    unittest.main()
