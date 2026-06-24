import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import alerts  # noqa: E402
import bot  # noqa: E402
import rooms  # noqa: E402


class FakeMsg:
    def __init__(self, text=None):
        self.text = text
        self.sent = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.sent.append((text, reply_markup))
        return self


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMsg()
        self.edits = []
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edits.append((text, reply_markup))


class FakeUpdate:
    def __init__(self, query=None, message=None):
        self.callback_query = query
        self.message = message


class Ctx:
    def __init__(self):
        self.user_data = {}


def measurement(media, mtype="temperature"):
    return json.dumps({
        "device_id": "dev",
        "measurement": {"type": mtype, "media": media},
    })


class AlertsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        self._orig = rooms.ROOMS_FILE
        rooms.ROOMS_FILE = d / "rooms.json"
        self.addCleanup(lambda: setattr(rooms, "ROOMS_FILE", self._orig))
        alerts._alert_state.clear()

    # ----- check_reading -----

    def test_above_max_returns_message(self):
        rooms.add_room("Lab", ["dev"], 1)
        rooms.update_room("Lab", temp_max=25.0)
        msg = alerts.check_reading("Lab", "temperature", 30.0)
        self.assertIsNotNone(msg)
        self.assertIn("Lab", msg)
        self.assertIn("sopra", msg)

    def test_below_min_returns_message(self):
        rooms.add_room("Lab", ["dev"], 1)
        rooms.update_room("Lab", hum_min=40.0)
        msg = alerts.check_reading("Lab", "humidity", 10.0)
        self.assertIsNotNone(msg)
        self.assertIn("sotto", msg)

    def test_within_range_returns_none(self):
        rooms.add_room("Lab", ["dev"], 1)
        rooms.update_room("Lab", temp_min=10.0, temp_max=25.0)
        self.assertIsNone(alerts.check_reading("Lab", "temperature", 20.0))

    def test_unset_thresholds_return_none(self):
        rooms.add_room("Lab", ["dev"], 1)
        self.assertIsNone(alerts.check_reading("Lab", "temperature", 999.0))

    def test_debounce_then_realert_after_recovery(self):
        rooms.add_room("Lab", ["dev"], 1)
        rooms.update_room("Lab", temp_max=25.0)
        # First breach alerts.
        self.assertIsNotNone(alerts.check_reading("Lab", "temperature", 30.0))
        # Sustained breach is debounced.
        self.assertIsNone(alerts.check_reading("Lab", "temperature", 31.0))
        # Return to range resets state (no message).
        self.assertIsNone(alerts.check_reading("Lab", "temperature", 20.0))
        # Fresh breach after recovery alerts again.
        self.assertIsNotNone(alerts.check_reading("Lab", "temperature", 30.0))

    # ----- listener -----

    def test_listener_schedules_alert_on_breach(self):
        rooms.add_room("Lab", ["dev"], 1)
        rooms.update_room("Lab", temp_max=25.0)
        scheduled = []
        orig = bot.run_on_bot_loop
        bot.run_on_bot_loop = lambda coro: scheduled.append(coro)
        self.addCleanup(lambda: setattr(bot, "run_on_bot_loop", orig))
        try:
            alerts._on_message(
                "sensor/dev/temperature",
                measurement(30.0),
                ["sensor", "dev", "temperature"],
            )
        finally:
            for c in scheduled:
                c.close()  # avoid un-awaited coroutine warnings
        self.assertEqual(len(scheduled), 1)

    def test_listener_skips_unknown_device(self):
        scheduled = []
        orig = bot.run_on_bot_loop
        bot.run_on_bot_loop = lambda coro: scheduled.append(coro)
        self.addCleanup(lambda: setattr(bot, "run_on_bot_loop", orig))
        alerts._on_message(
            "sensor/ghost/temperature",
            measurement(99.0),
            ["sensor", "ghost", "temperature"],
        )
        self.assertEqual(scheduled, [])

    # ----- conversation -----

    async def test_conversation_set_temp_max(self):
        rooms.add_room("Lab", ["dev"], 1)
        ctx = Ctx()

        # Pick room.
        q = FakeQuery("al_room_Lab")
        state = await alerts.alerts_pick_room(FakeUpdate(query=q), ctx)
        self.assertEqual(state, alerts.AL_MENU)
        self.assertEqual(ctx.user_data["al_room"], "Lab")

        # Press "set temp max".
        q2 = FakeQuery("al_set_tmax")
        state = await alerts.alerts_menu_action(FakeUpdate(query=q2), ctx)
        self.assertEqual(state, alerts.AL_VALUE)
        self.assertEqual(ctx.user_data["al_key"], "temp_max")

        # Type the value.
        msg = FakeMsg("27.5")
        state = await alerts.alerts_set_value(FakeUpdate(message=msg), ctx)
        self.assertEqual(state, alerts.AL_MENU)
        self.assertEqual(rooms.get_room("Lab")["temp_max"], 27.5)

    async def test_conversation_clear_threshold(self):
        rooms.add_room("Lab", ["dev"], 1)
        rooms.update_room("Lab", temp_max=25.0)
        ctx = Ctx()
        ctx.user_data["al_room"] = "Lab"
        q = FakeQuery("al_clear_temp_max")
        state = await alerts.alerts_menu_action(FakeUpdate(query=q), ctx)
        self.assertEqual(state, alerts.AL_MENU)
        self.assertIsNone(rooms.get_room("Lab")["temp_max"])


if __name__ == "__main__":
    unittest.main()
