import json
from pathlib import Path

# Safer absolute path (relative to file location)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
ROOMS_FILE = DATA_DIR / "rooms.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def _load_rooms():
    if not ROOMS_FILE.exists():
        return {}

    try:
        with open(ROOMS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # corrupted file fallback
        return {}


def _save_rooms(rooms):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(ROOMS_FILE, "w", encoding="utf-8") as f:
        json.dump(rooms, f, indent=2, ensure_ascii=False)


# -------------------------
# PUBLIC API
# -------------------------

def get_room_names():
    return list(_load_rooms().keys())


def get_room(name):
    return _load_rooms().get(name)


def room_exists(name):
    return name in _load_rooms()


def add_room(name, device_ids, num_ac):
    rooms = _load_rooms()
    rooms[name] = {
        "device_ids": device_ids,
        "num_ac": num_ac
    }
    _save_rooms(rooms)


def delete_room(name):
    rooms = _load_rooms()
    rooms.pop(name, None)
    _save_rooms(rooms)


def update_room(name, **kwargs):
    rooms = _load_rooms()
    if name in rooms:
        rooms[name].update(kwargs)
        _save_rooms(rooms)


def get_device_room(device_id):
    rooms = _load_rooms()
    for name, config in rooms.items():
        if device_id in config.get("device_ids", []):
            return name
    return None

def remove_device_from_all_rooms(device_id, except_room=None):
    """Remove a device from every room's device_ids (except `except_room`). Returns names of rooms that changed."""
    rooms = _load_rooms()
    changed = []
    for name, config in rooms.items():
        if name == except_room:
            continue
        ids = config.get("device_ids", [])
        if device_id in ids:
            config["device_ids"] = [d for d in ids if d != device_id]
            changed.append(name)
    if changed:
        _save_rooms(rooms)
    return changed