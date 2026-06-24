import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import bot  # noqa: E402
import charts  # noqa: E402
import rooms  # noqa: E402

PNG_MAGIC = b"\x89PNG"


def _make_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sensor_readings (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT, device_id TEXT, room TEXT, type TEXT, "
        "min REAL, max REAL, media REAL, varianza REAL)"
    )
    conn.executemany(
        "INSERT INTO sensor_readings (timestamp, device_id, room, type, min, max, media, varianza) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


class FakeMsg:
    def __init__(self, text=None):
        self.text = text
        self.texts = []
        self.photos = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.texts.append((text, reply_markup))
        return FakeMsg()

    async def reply_photo(self, photo=None, caption=None, **kw):
        self.photos.append((photo, caption))


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMsg()
        self.edits = []
        self.answered = []

    async def answer(self, text=None, show_alert=False, **kw):
        self.answered.append((text, show_alert))

    async def edit_message_text(self, text=None, reply_markup=None, **kw):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        self.edits.append((None, reply_markup))


class FakeUpdate:
    def __init__(self, query=None, message=None):
        self.callback_query = query
        self.message = message


class Ctx:
    def __init__(self):
        self.user_data = {}


# ---------------------------------------------------------------------------
class RenderTest(unittest.TestCase):
    def test_render_chart_returns_png(self):
        xs = [datetime(2026, 6, 24, 10, 0), datetime(2026, 6, 24, 10, 1)]
        series = [("Lab", {"temperature": (xs, [21.0, 22.0], [20.0, 20.5], [23.0, 24.0])})]
        buf = charts.render_chart(series, "Ultima ora")
        self.assertIsNotNone(buf)
        self.assertEqual(buf.getvalue()[:4], PNG_MAGIC)

    def test_render_chart_multi_room_two_types(self):
        xs = [datetime(2026, 6, 24, 10, 0)]
        series = [
            ("R1", {"temperature": (xs, [21.0], [20.0], [22.0]),
                    "humidity": (xs, [40.0], [39.0], [41.0])}),
            ("R2", {"temperature": (xs, [25.0], [24.0], [26.0])}),
        ]
        buf = charts.render_chart(series, "Ultimo giorno")
        self.assertEqual(buf.getvalue()[:4], PNG_MAGIC)

    def test_render_empty_returns_none(self):
        self.assertIsNone(charts.render_chart([], "x"))
        self.assertIsNone(charts.render_chart([("Lab", {})], "x"))


# ---------------------------------------------------------------------------
class AggregateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "monitor.db"
        base = datetime(2020, 1, 1, 10, 0, 0, tzinfo=timezone.utc)  # old: outside any recent window
        rows = []
        for i in range(3):
            ts = (base + timedelta(seconds=i * 60)).isoformat()
            # two devices, same minute bucket -> mean averaged, band = min..max
            rows.append((ts, "devA", "Lab", "temperature", 20.0 + i, 22.0 + i, 21.0 + i, 0.1))
            rows.append((ts, "devB", "Lab", "temperature", 18.0 + i, 24.0 + i, 21.0 + i, 0.1))
        _make_db(self.db, rows)
        self._orig_db = bot.SENSORS_DB
        bot.SENSORS_DB = self.db
        self.addCleanup(setattr, bot, "SENSORS_DB", self._orig_db)

    def test_aggregate_buckets_mean_and_band(self):
        agg = charts._aggregate(["devA", "devB"], 0, 60)
        self.assertIn("temperature", agg)
        xs, mean, lo, hi = agg["temperature"]
        self.assertEqual(len(xs), 3)              # 3 one-minute buckets
        self.assertAlmostEqual(mean[0], 21.0)     # avg of two equal medias
        self.assertAlmostEqual(lo[0], 18.0)       # min of mins
        self.assertAlmostEqual(hi[0], 24.0)       # max of maxs (devB=24)

    def test_aggregate_device_filter_excludes(self):
        agg = charts._aggregate(["devA"], 0, 60)
        _, _, lo, hi = agg["temperature"]
        self.assertAlmostEqual(lo[0], 20.0)       # only devA's min
        self.assertAlmostEqual(hi[0], 22.0)

    def test_aggregate_empty_device_list(self):
        self.assertEqual(charts._aggregate([], 0, 60), {})

    def test_aggregate_missing_db(self):
        bot.SENSORS_DB = Path(self._tmp.name) / "nope.db"
        self.assertEqual(charts._aggregate(None, 0, 60), {})

    def test_build_chart_for_room(self):
        rooms_file = Path(self._tmp.name) / "rooms.json"
        orig = rooms.ROOMS_FILE
        rooms.ROOMS_FILE = rooms_file
        self.addCleanup(setattr, rooms, "ROOMS_FILE", orig)
        rooms.add_room("Lab", ["devA", "devB"], 1)
        buf = charts.build_chart(["Lab"], "all")
        self.assertEqual(buf.getvalue()[:4], PNG_MAGIC)

    def test_build_chart_no_data_returns_none(self):
        self.assertIsNone(charts.build_chart(None, "hour"))  # window excludes 2026 rows


# ---------------------------------------------------------------------------
class HandlerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.rooms_file = Path(self._tmp.name) / "rooms.json"
        self._orig_rooms = rooms.ROOMS_FILE
        rooms.ROOMS_FILE = self.rooms_file
        self.addCleanup(setattr, rooms, "ROOMS_FILE", self._orig_rooms)

        self.db = Path(self._tmp.name) / "monitor.db"
        self._orig_db = bot.SENSORS_DB
        bot.SENSORS_DB = self.db
        self.addCleanup(setattr, bot, "SENSORS_DB", self._orig_db)

    def _seed(self):
        now = datetime.now(timezone.utc)
        rows = [
            ((now - timedelta(seconds=30)).isoformat(), "devA", "Lab", "temperature", 20, 22, 21, 0.1),
            ((now - timedelta(seconds=30)).isoformat(), "devB", "Lab", "temperature", 19, 23, 21, 0.1),
        ]
        _make_db(self.db, rows)
        rooms.add_room("Lab", ["devA", "devB"], 1)

    async def test_start_lists_rooms(self):
        rooms.add_room("Lab", [], 1)
        upd = FakeUpdate(message=FakeMsg())
        state = await charts.chart_start(upd, Ctx())
        self.assertEqual(state, charts.CH_ROOMS)
        self.assertEqual(len(upd.message.texts), 1)

    async def test_start_without_rooms_goes_to_horizon(self):
        upd = FakeUpdate(message=FakeMsg())
        ctx = Ctx()
        state = await charts.chart_start(upd, ctx)
        self.assertEqual(state, charts.CH_HORIZON)
        self.assertIsNone(ctx.user_data["c_rooms"])

    async def test_toggle_then_go(self):
        rooms.add_room("Lab", [], 1)
        ctx = Ctx()
        ctx.user_data["c_rooms"] = []
        await charts.chart_rooms_action(FakeUpdate(query=FakeQuery("c_rm_Lab")), ctx)
        self.assertEqual(ctx.user_data["c_rooms"], ["Lab"])
        state = await charts.chart_rooms_action(FakeUpdate(query=FakeQuery("c_go")), ctx)
        self.assertEqual(state, charts.CH_HORIZON)

    async def test_go_without_selection_alerts(self):
        ctx = Ctx()
        ctx.user_data["c_rooms"] = []
        q = FakeQuery("c_go")
        state = await charts.chart_rooms_action(FakeUpdate(query=q), ctx)
        self.assertEqual(state, charts.CH_ROOMS)
        self.assertTrue(q.answered[-1][1])  # show_alert=True

    async def test_horizon_renders_photo(self):
        self._seed()
        ctx = Ctx()
        ctx.user_data["c_rooms"] = ["Lab"]
        q = FakeQuery("c_h_all")
        state = await charts.chart_horizon_action(FakeUpdate(query=q), ctx)
        self.assertEqual(state, -1)  # ConversationHandler.END
        self.assertEqual(len(q.message.photos), 1)
        self.assertEqual(q.message.photos[0][0].getvalue()[:4], PNG_MAGIC)

    async def test_horizon_no_data_edits_text(self):
        _make_db(self.db, [])
        rooms.add_room("Lab", ["devA"], 1)
        ctx = Ctx()
        ctx.user_data["c_rooms"] = ["Lab"]
        q = FakeQuery("c_h_all")
        await charts.chart_horizon_action(FakeUpdate(query=q), ctx)
        self.assertEqual(len(q.message.photos), 0)
        self.assertIn("Nessun dato", q.edits[-1][0])

    async def test_cancel_in_room_select(self):
        ctx = Ctx()
        ctx.user_data["c_rooms"] = []
        q = FakeQuery(bot.CANCEL_DATA)
        state = await charts.chart_rooms_action(FakeUpdate(query=q), ctx)
        self.assertEqual(state, -1)


if __name__ == "__main__":
    unittest.main()
