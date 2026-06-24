import asyncio
import csv
import io
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))
sys.path.insert(0, str(ROOT / "mqtt_consumer"))

# bot.py guards on these at import time; set dummy values before importing.
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import bot  # noqa: E402
import sensor  # noqa: E402


class FakeMsg:
    def __init__(self):
        self.docs = []
        self.texts = []
        self.edited = []
        self.deleted = False

    async def reply_document(self, document=None, filename=None, **kw):
        self.docs.append((document, filename))

    async def reply_text(self, text, **kw):
        self.texts.append(text)
        return FakeMsg()  # the loading note

    async def edit_text(self, text, **kw):
        self.edited.append(text)

    async def delete(self):
        self.deleted = True


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMsg()

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text=None, reply_markup=None, **kw):
        self.message.texts.append(text)

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        pass


class FakeUpdate:
    def __init__(self, query):
        self.callback_query = query


class Ctx:
    pass


class SqliteStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "monitor.db"
        # point both the consumer-side writer and bot-side reader at the temp DB
        sensor.DB_FILE = self.db
        sensor.DATA_DIR = Path(self.tmp.name)
        bot.SENSORS_DB = self.db

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_and_read_string_values(self):
        sensor.append_sensor_row("100", "espA", "Aula1", "temperature", 1.0, 9.0, 5.0, 2.0)
        rows = bot.read_sensors()
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(
            set(r.keys()),
            {"timestamp", "device_id", "room", "type", "min", "max", "media", "varianza"},
        )
        for v in r.values():
            self.assertIsInstance(v, str)
        self.assertEqual(r["device_id"], "espA")
        self.assertEqual(r["type"], "temperature")
        self.assertEqual(r["media"], "5.0")

    def test_order_and_room_filter(self):
        sensor.append_sensor_row("300", "espB", "Aula2", "humidity", 0, 1, 0.5, 0.1)
        sensor.append_sensor_row("100", "espA", "Aula1", "temperature", 1, 9, 5, 2)
        sensor.append_sensor_row("200", "espA", "Aula1", "temperature", 2, 8, 5, 1)
        rows = bot.read_sensors()
        self.assertEqual([r["timestamp"] for r in rows], ["100", "200", "300"])
        aula1 = [r for r in rows if r["room"] == "Aula1"]
        self.assertEqual(len(aula1), 2)

    def test_read_sensors_empty_when_no_db(self):
        self.assertEqual(bot.read_sensors(), [])

    def test_csv_export_all(self):
        sensor.append_sensor_row("100", "espA", "Aula1", "temperature", 1, 9, 5, 2)
        sensor.append_sensor_row("200", "espB", "Aula2", "humidity", 0, 1, 0.5, 0.1)
        bio, n = bot._sensors_csv_bytes(None)
        self.assertEqual(n, 2)
        text = bio.getvalue().decode()
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        self.assertEqual(
            rows[0],
            ["timestamp", "device_id", "room", "type", "min", "max", "media", "varianza"],
        )
        self.assertEqual(len(rows), 3)  # header + 2

    def test_csv_export_room_filter(self):
        sensor.append_sensor_row("100", "espA", "Aula1", "temperature", 1, 9, 5, 2)
        sensor.append_sensor_row("200", "espB", "Aula2", "humidity", 0, 1, 0.5, 0.1)
        bio, n = bot._sensors_csv_bytes("Aula1")
        self.assertEqual(n, 1)
        rows = list(csv.reader(io.StringIO(bio.getvalue().decode())))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][2], "Aula1")

    def test_downloads_callback_sensors_all(self):
        sensor.append_sensor_row("100", "espA", "Aula1", "temperature", 1, 9, 5, 2)
        q = FakeQuery("sensors_all")
        asyncio.run(bot.downloads_callback(FakeUpdate(q), Ctx()))
        self.assertEqual(len(q.message.docs), 1)
        self.assertEqual(q.message.docs[0][1], "sensors.csv")

    def test_migrate_csv_idempotent(self):
        csv_path = Path(self.tmp.name) / "sensors.csv"
        sensor.SENSORS_FILE = csv_path
        with open(csv_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["timestamp", "device_id", "room", "type", "min", "max", "media", "varianza"])
            w.writerow(["100", "espA", "Aula1", "temperature", "1", "9", "5", "2"])
            w.writerow(["200", "espB", "Aula2", "humidity", "0", "1", "0.5", "0.1"])

        sensor.migrate_csv()
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0], 2)
        conn.close()

        # second call must be a no-op (table not empty)
        sensor.migrate_csv()
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0], 2)
        conn.close()

    def test_migrate_csv_no_file(self):
        sensor.SENSORS_FILE = Path(self.tmp.name) / "does_not_exist.csv"
        sensor.migrate_csv()  # must not raise
        self.assertEqual(bot.read_sensors(), [])


if __name__ == "__main__":
    unittest.main()
