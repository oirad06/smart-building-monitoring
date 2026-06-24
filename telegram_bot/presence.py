"""Device ground-truth tracking.

Listens on the MQTT bus (via bot.register_message_listener) for:
  - sensor/{id}/status        -> 'online'/'offline' birth/will
  - sensor/{id}/config_state  -> retained JSON of the config the firmware has
                                 actually applied

From these we expose:
  - is_online(id)    -> True/False/None (None = never heard)
  - status_icon(id)  -> 🟢 / 🔴 / ⚪
  - is_confirmed(id) -> True once the device echoed its applied config

The config_state message also persists into devices.py, so the bot's stored
config reflects what the device is really running, not just what was last pushed.
"""

import json
import time

# device_id -> 'online' | 'offline'
_status: dict[str, str] = {}
# device_id -> epoch seconds of the last config_state we processed
_confirmed: dict[str, float] = {}


def note_status(device_id, payload):
    """Record a status birth/will message ('online' / 'offline')."""
    state = (payload or "").strip().lower()
    if state in ("online", "offline"):
        _status[device_id] = state


def is_online(device_id):
    """True/False from the last status seen, or None if never heard."""
    state = _status.get(device_id)
    if state is None:
        return None
    return state == "online"


def status_icon(device_id):
    """🟢 online, 🔴 offline, ⚪ unknown."""
    state = is_online(device_id)
    if state is True:
        return "🟢"
    if state is False:
        return "🔴"
    return "⚪"


def is_confirmed(device_id):
    """True if the device has echoed its applied config at least once."""
    return device_id in _confirmed


def confirmed_at(device_id):
    """Epoch of the last config_state confirmation, or None."""
    return _confirmed.get(device_id)


def _on_message(topic, payload, parts):
    """MQTT listener (paho thread). parts = topic.split('/')."""
    if len(parts) != 3 or parts[0] != "sensor":
        return
    device_id, leaf = parts[1], parts[2]
    if leaf == "status":
        note_status(device_id, payload)
        return
    if leaf == "config_state":
        try:
            cfg = json.loads(payload)
            read_interval = int(cfg["read_interval"])
            read_processing = int(cfg["read_processing"])
            active = bool(cfg["active"])
        except (ValueError, KeyError, TypeError):
            return
        import devices
        devices.set_device_config(device_id, read_interval, read_processing, active)
        _confirmed[device_id] = time.time()


def install(app):
    import bot
    bot.register_message_listener(_on_message)
