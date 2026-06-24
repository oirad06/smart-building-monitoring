import csv
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ROOMS_FILE = DATA_DIR / "rooms.json"
SENSORS_FILE = DATA_DIR / "sensors.csv"
DB_FILE = DATA_DIR / "monitor.db"


def _connect():
    """Open the monitor.db connection, creating the sensor_readings table if needed."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sensor_readings ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp TEXT, device_id TEXT, room TEXT, type TEXT, "
        "min REAL, max REAL, media REAL, varianza REAL)"
    )
    return conn

def load_rooms():
    """Returns dict of {room_name: {device_ids: [...], num_ac: N}} or empty dict if file missing."""
    if not ROOMS_FILE.exists():
        return {}
    with open(ROOMS_FILE) as f:
        return json.load(f)

def get_room_for_device(device_id):
    """Look up which room a device belongs to. Returns empty string if unassigned."""
    rooms = load_rooms()
    for room_name, config in rooms.items():
        if device_id in config.get("device_ids", []):
            return room_name
    return ""

def append_sensor_row(timestamp, device_id, room, measurement_type, min_val, max_val, mean, variance):
    """Insert one reading into the sensor_readings table in data/monitor.db."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO sensor_readings "
            "(timestamp, device_id, room, type, min, max, media, varianza) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp, device_id, room, measurement_type, min_val, max_val, mean, variance),
        )
        conn.commit()
    finally:
        conn.close()


def migrate_csv():
    """Import existing data/sensors.csv into the DB once, only if the table is empty."""
    conn = _connect()
    try:
        count = conn.execute("SELECT COUNT(*) FROM sensor_readings").fetchone()[0]
        if count > 0 or not SENSORS_FILE.exists():
            return
        with open(SENSORS_FILE, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                conn.execute(
                    "INSERT INTO sensor_readings "
                    "(timestamp, device_id, room, type, min, max, media, varianza) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        r.get("timestamp"), r.get("device_id"), r.get("room"), r.get("type"),
                        r.get("min"), r.get("max"), r.get("media"), r.get("varianza"),
                    ),
                )
        conn.commit()
    finally:
        conn.close()