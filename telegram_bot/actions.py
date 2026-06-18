import csv
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("../data")
ACTIONS_FILE = DATA_DIR / "actions.csv"

def append_action(timestamp, room, num_ac, num_people, num_ac_cool, num_ac_heat, device_ids):
    """Write one row. Create header if file doesn't exist."""
    num_ac_off = num_ac - num_ac_cool - num_ac_heat
    file_exists = ACTIONS_FILE.exists()
    with open(ACTIONS_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["timestamp", "room", "num_ac", "num_people", "num_ac_cool", "num_ac_heat", "num_ac_off", "device_ids"])
        writer.writerow([timestamp, room, num_ac, num_people, num_ac_cool, num_ac_heat, num_ac_off, ",".join(device_ids)])

def read_actions():
    """Read all rows into list of dicts."""
    if not ACTIONS_FILE.exists():
        return []
    with open(ACTIONS_FILE) as f:
        return list(csv.DictReader(f))

def update_action_row(row_index, **fields):
    """Modify one row by index (0-based, excluding header). Rewrites the whole file."""
    rows = read_actions()
    if 0 <= row_index < len(rows):
        rows[row_index].update(fields)
        _rewrite_actions(rows)

def delete_action_row(row_index):
    """Remove one row by index."""
    rows = read_actions()
    if 0 <= row_index < len(rows):
        rows.pop(row_index)
        _rewrite_actions(rows)

def _rewrite_actions(rows):
    """Internal: write all rows back (header + data)."""
    with open(ACTIONS_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys() if rows else [])
        writer.writeheader()
        writer.writerows(rows)