import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

import devices  # noqa: E402


class DeviceConfigStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = devices.DEVICES_FILE
        devices.DEVICES_FILE = Path(self._tmp.name) / "devices.json"
        self.addCleanup(lambda: setattr(devices, "DEVICES_FILE", self._orig))

    def test_unknown_device_returns_firmware_defaults(self):
        cfg = devices.get_device_config("nope")
        self.assertEqual(cfg, {"read_interval": 1, "read_processing": 10, "active": True})
        self.assertFalse(devices.has_device_config("nope"))

    def test_set_then_get_roundtrips(self):
        devices.set_device_config("dev1", 5, 20, False)
        self.assertTrue(devices.has_device_config("dev1"))
        self.assertEqual(
            devices.get_device_config("dev1"),
            {"read_interval": 5, "read_processing": 20, "active": False},
        )

    def test_set_coerces_types(self):
        devices.set_device_config("dev2", "7", "3", 1)
        cfg = devices.get_device_config("dev2")
        self.assertEqual(cfg["read_interval"], 7)
        self.assertEqual(cfg["read_processing"], 3)
        self.assertIs(cfg["active"], True)

    def test_partial_stored_config_merges_over_defaults(self):
        devices.DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
        devices.DEVICES_FILE.write_text('{"dev3": {"read_interval": 9}}', encoding="utf-8")
        cfg = devices.get_device_config("dev3")
        self.assertEqual(cfg["read_interval"], 9)
        # missing keys fall back to firmware defaults
        self.assertEqual(cfg["read_processing"], 10)
        self.assertIs(cfg["active"], True)

    def test_corrupted_file_falls_back_to_empty(self):
        devices.DEVICES_FILE.parent.mkdir(parents=True, exist_ok=True)
        devices.DEVICES_FILE.write_text("{ not json", encoding="utf-8")
        self.assertFalse(devices.has_device_config("anything"))
        self.assertEqual(devices.get_device_config("anything")["read_interval"], 1)

    def test_second_device_does_not_clobber_first(self):
        devices.set_device_config("a", 2, 2, True)
        devices.set_device_config("b", 3, 3, False)
        self.assertEqual(devices.get_device_config("a")["read_interval"], 2)
        self.assertEqual(devices.get_device_config("b")["read_interval"], 3)


if __name__ == "__main__":
    unittest.main()
