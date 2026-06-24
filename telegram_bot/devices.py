import json
from pathlib import Path

# Mirrors rooms.py: a small JSON store under the shared data/ folder.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEVICES_FILE = DATA_DIR / "devices.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)

# Firmware defaults (see esp32_firmware/main_dht11.py). Shown when the bot has
# never pushed a config to a device, so the menu always has values to display.
DEFAULT_CONFIG = {"read_interval": 1, "read_processing": 10, "active": True}


def _load():
    if not DEVICES_FILE.exists():
        return {}
    try:
        with open(DEVICES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        # corrupted file fallback (mirrors rooms.py)
        return {}


def _save(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(DEVICES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# -------------------------
# PUBLIC API
# -------------------------

def has_device_config(device_id):
    """True if the bot has previously saved a config for this device."""
    return device_id in _load()


def get_device_config(device_id):
    """Last config the bot pushed to a device, merged over firmware defaults.

    The firmware does not report its live config back, so this reflects the most
    recent config the bot sent (or the firmware defaults if none was ever sent).
    """
    merged = dict(DEFAULT_CONFIG)
    stored = _load().get(device_id)
    if stored:
        merged.update(stored)
    return merged


def set_device_config(device_id, read_interval, read_processing, active):
    """Persist the config the bot is pushing to a device."""
    data = _load()
    data[device_id] = {
        "read_interval": int(read_interval),
        "read_processing": int(read_processing),
        "active": bool(active),
    }
    _save(data)
