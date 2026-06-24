import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mqtt_consumer"))

import sensor  # noqa: E402


class GetRoomForDeviceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = sensor.ROOMS_FILE
        sensor.ROOMS_FILE = Path(self._tmp.name) / "rooms.json"
        self.addCleanup(lambda: setattr(sensor, "ROOMS_FILE", self._orig))
        sensor.ROOMS_FILE.write_text(json.dumps({
            "Lab": {"device_ids": ["esp-1", "esp-2"], "num_ac": 1},
            "Office": {"device_ids": ["esp-9"], "num_ac": 2},
        }))

    def test_assigned_device_returns_room(self):
        self.assertEqual(sensor.get_room_for_device("esp-1"), "Lab")
        self.assertEqual(sensor.get_room_for_device("esp-9"), "Office")

    def test_unassigned_device_returns_empty(self):
        self.assertEqual(sensor.get_room_for_device("unknown"), "")

    def test_missing_rooms_file_returns_empty(self):
        sensor.ROOMS_FILE = Path(self._tmp.name) / "nope.json"
        self.assertEqual(sensor.get_room_for_device("esp-1"), "")


if __name__ == "__main__":
    unittest.main()
