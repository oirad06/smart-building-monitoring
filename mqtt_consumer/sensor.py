import csv
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ROOMS_FILE = DATA_DIR / "rooms.json"
SENSORS_FILE = DATA_DIR / "sensors.csv"

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
    """Append one row to sensors.csv. Creates file with headers if it doesn't exist."""
    file_exists = SENSORS_FILE.exists()
    with open(SENSORS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "device_id", "room", "type", "min", "max", "media", "varianza"])
        writer.writerow([timestamp, device_id, room, measurement_type, min_val, max_val, mean, variance])