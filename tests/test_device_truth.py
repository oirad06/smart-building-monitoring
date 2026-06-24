import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

# bot.py guards on these at import time; set dummy values before importing.
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import bot  # noqa: E402
import devices  # noqa: E402
import presence  # noqa: E402


class PresenceUnitTest(unittest.TestCase):
    def setUp(self):
        presence._status.clear()
        presence._confirmed.clear()

    def test_note_status_and_is_online(self):
        self.assertIsNone(presence.is_online("d"))
        presence.note_status("d", "online")
        self.assertTrue(presence.is_online("d"))
        presence.note_status("d", "offline")
        self.assertFalse(presence.is_online("d"))
        # garbage payloads are ignored, leaving last good state
        presence.note_status("d", "garbage")
        self.assertFalse(presence.is_online("d"))

    def test_status_icon(self):
        self.assertEqual(presence.status_icon("u"), "⚪")
        presence.note_status("u", "online")
        self.assertEqual(presence.status_icon("u"), "🟢")
        presence.note_status("u", "offline")
        self.assertEqual(presence.status_icon("u"), "🔴")


class ConfigStateListenerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._orig = devices.DEVICES_FILE
        devices.DEVICES_FILE = Path(self._tmp.name) / "devices.json"
        self.addCleanup(lambda: setattr(devices, "DEVICES_FILE", self._orig))
        presence._status.clear()
        presence._confirmed.clear()

    def _feed(self, topic, payload):
        presence._on_message(topic, payload, topic.split("/"))

    def test_status_message_marks_online(self):
        self._feed("sensor/abc/status", "online")
        self.assertTrue(presence.is_online("abc"))

    def test_config_state_persists_and_confirms(self):
        self.assertFalse(presence.is_confirmed("abc"))
        self._feed(
            "sensor/abc/config_state",
            '{"read_interval": 7, "read_processing": 42, "active": false}',
        )
        cfg = devices.get_device_config("abc")
        self.assertEqual(cfg["read_interval"], 7)
        self.assertEqual(cfg["read_processing"], 42)
        self.assertFalse(cfg["active"])
        self.assertTrue(presence.is_confirmed("abc"))

    def test_bad_config_state_ignored(self):
        self._feed("sensor/abc/config_state", "not json")
        self.assertFalse(presence.is_confirmed("abc"))
        self._feed("sensor/abc/config_state", '{"read_interval": 1}')
        self.assertFalse(presence.is_confirmed("abc"))

    def test_unrelated_topic_ignored(self):
        self._feed("discovery/devices", "[]")
        self.assertEqual(presence._status, {})
        self.assertEqual(presence._confirmed, {})


class DeviceListIconTest(unittest.TestCase):
    def setUp(self):
        bot.known_devices.clear()
        presence._status.clear()
        presence._confirmed.clear()

    def test_list_shows_online_icon(self):
        bot.known_devices["dev"] = time.time()
        presence.note_status("dev", "online")
        text, kb = bot._device_list_text_kb()
        self.assertIn("🟢", text)
        self.assertIn("dev", text)

    def test_list_shows_unknown_icon_by_default(self):
        bot.known_devices["dev"] = time.time()
        text, _ = bot._device_list_text_kb()
        self.assertIn("⚪", text)


class FirmwareStaticTest(unittest.TestCase):
    def test_firmware_publishes_retained_config_state(self):
        fw_dir = ROOT / "esp32_firmware"
        for name in ("main.py", "main_dht11.py", "main_dht22.py"):
            src = (fw_dir / name).read_text(encoding="utf-8")
            self.assertIn("CONFIG_STATE_TOPIC", src, name)
            self.assertIn("config_state", src, name)
            self.assertIn(
                "client.publish(CONFIG_STATE_TOPIC", src, name
            )
            self.assertIn("retain=True", src, name)


if __name__ == "__main__":
    unittest.main()
