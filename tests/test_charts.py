import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import bot  # noqa: E402
import charts  # noqa: E402


def _row(ts, typ, media, device_id="dev", room="Lab"):
    return {
        "timestamp": ts, "device_id": device_id, "room": room,
        "type": typ, "min": "0", "max": "1", "media": media, "varianza": "0",
    }


SAMPLE = [
    _row("2026-06-24T10:00:00", "temperature", "21.5"),
    _row("2026-06-24T10:01:00", "temperature", "22.0"),
    _row("2026-06-24T10:00:00", "humidity", "45.0"),
    _row("1750759260", "humidity", "47.0"),  # float-epoch timestamp
]


class FakeMsg:
    def __init__(self, text=None):
        self.text = text
        self.sent = []
        self.photos = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.sent.append((text, reply_markup))
        return self

    async def reply_photo(self, photo=None, **kw):
        self.photos.append(photo)
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


class RenderTest(unittest.TestCase):
    def test_render_returns_png_bytesio(self):
        buf = charts.render_timeseries(SAMPLE, "Lab")
        self.assertIsNotNone(buf)
        data = buf.read()
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_render_empty_returns_none(self):
        self.assertIsNone(charts.render_timeseries([], "x"))

    def test_render_unusable_rows_returns_none(self):
        rows = [_row("bad-ts", "temperature", "nan-ish?")]
        # bad media coerces to None -> dropped; no usable series
        rows[0]["media"] = "not-a-number"
        self.assertIsNone(charts.render_timeseries(rows, "x"))


class HandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = bot.read_sensors
        bot.read_sensors = lambda: SAMPLE
        self.addCleanup(lambda: setattr(bot, "read_sensors", self._orig))

    async def test_chart_all_replies_with_photo(self):
        q = FakeQuery("chart_all")
        ctx = Ctx()
        await charts.charts_cb(FakeUpdate(query=q), ctx)
        self.assertEqual(len(q.message.photos), 1)
        self.assertEqual(q.answered, 1)

    async def test_chart_no_data_replies_text(self):
        bot.read_sensors = lambda: []
        q = FakeQuery("chart_all")
        await charts.charts_cb(FakeUpdate(query=q), Ctx())
        self.assertEqual(len(q.message.photos), 0)
        self.assertEqual(len(q.message.sent), 1)

    async def test_chart_cancel_edits_message(self):
        q = FakeQuery(bot.CANCEL_DATA)
        await charts.charts_cb(FakeUpdate(query=q), Ctx())
        self.assertEqual(q.edits[-1][0], "Operazione annullata.")
        self.assertEqual(len(q.message.photos), 0)


if __name__ == "__main__":
    unittest.main()
