import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import bot  # noqa: E402
import rooms  # noqa: E402
import status_cmd  # noqa: E402


class FakeMsg:
    def __init__(self, text=None):
        self.text = text
        self.sent = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.sent.append((text, reply_markup))
        return self


class FakeUpdate:
    def __init__(self, message=None):
        self.callback_query = None
        self.message = message


class Ctx:
    def __init__(self):
        self.user_data = {}


class StatusTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        self._orig_rooms = rooms.ROOMS_FILE
        rooms.ROOMS_FILE = d / "rooms.json"
        self.addCleanup(lambda: setattr(rooms, "ROOMS_FILE", self._orig_rooms))
        self._orig_mqtt = bot.mqtt_client
        self.addCleanup(lambda: setattr(bot, "mqtt_client", self._orig_mqtt))
        bot.known_devices.clear()
        self.addCleanup(bot.known_devices.clear)

    async def test_status_summary(self):
        now = time.time()
        # one fresh, one stale device
        bot.known_devices["dev-fresh"] = now - 10
        bot.known_devices["dev-stale"] = now - 1000
        rooms.add_room("Lab", ["dev-fresh"], 1)

        fixture = [
            {"timestamp": str(now - 60), "device_id": "dev-fresh", "room": "Lab",
             "type": "temperature", "min": "1", "max": "2", "media": "1.5", "varianza": "0.1"},
        ]
        self._orig_read = bot.read_sensors
        bot.read_sensors = lambda: fixture
        self.addCleanup(lambda: setattr(bot, "read_sensors", self._orig_read))

        bot.mqtt_client = object()  # connesso

        upd = FakeUpdate(message=FakeMsg())
        await status_cmd.status(upd, Ctx())

        self.assertEqual(len(upd.message.sent), 1)
        text = upd.message.sent[0][0]
        # device count
        self.assertIn("Dispositivi noti: 2", text)
        self.assertIn("freschi: 1", text)
        self.assertIn("inattivi: 1", text)
        # room count
        self.assertIn("Stanze configurate: 1", text)
        self.assertIn("connesso", text)

    async def test_status_no_data_no_mqtt(self):
        bot.mqtt_client = None
        self._orig_read = bot.read_sensors
        bot.read_sensors = lambda: []
        self.addCleanup(lambda: setattr(bot, "read_sensors", self._orig_read))

        upd = FakeUpdate(message=FakeMsg())
        await status_cmd.status(upd, Ctx())
        text = upd.message.sent[0][0]
        self.assertIn("non disponibile", text)
        self.assertIn("nessun dato", text)
        self.assertIn("Dispositivi noti: 0", text)
        self.assertIn("Stanze configurate: 0", text)


if __name__ == "__main__":
    unittest.main()
