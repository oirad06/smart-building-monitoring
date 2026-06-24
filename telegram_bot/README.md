# Telegram Bot

Bot for managing rooms, recording events, configuring ESP32 devices, and
downloading data. Talks to MQTT directly (own connection) and reads/writes the
shared `data/` folder.

## Commands

| Command | Description |
|---|---|
| `/setup` | Create a new room (name → AC count → assign devices) |
| `/rooms` | Manage a room: rename, change AC, assign/remove devices, delete |
| `/event` | Record an event (room → people → AC cool → AC heat); one row per event |
| `/events` | Browse/edit/delete recent events with pagination |
| `/devices` | List known ESP32s, then a per-device menu to view the current config (interval, window, active) + assigned room, change individual values, reassign/remove the room, and save (pushes config over MQTT) |
| `/show` | Merged view of sensor + event data (last 10), filterable by room |
| `/chart` | Render a time-series chart of average sensor values (per room or all) |
| `/sensors` | Download `sensors.csv` (full export or per room) |
| `/actions` | Download `actions.csv` (full export or per room) |
| `/config` | Download `rooms.json` (all rooms or single room) |
| `/cancel` | Abort any in-progress conversation |
| `/status` | System health summary (MQTT, known devices, last sensor data, room count) |

Every conversation also carries an inline **❌ Annulla** button (and **« Indietro**
in menus); free-text prompts add a cancel key to the keyboard, so `/cancel` is
never required. The bot menu is registered automatically via `set_my_commands`.

## Access control

Set `ALLOWED_USER_IDS` in `.env` to a comma/space separated list of Telegram
user IDs to restrict the bot to those users; anyone else gets
`⛔ Non sei autorizzato a usare questo bot.` and is blocked. Leave it empty or
unset to keep the bot open to everyone (default, backward compatible).

## Setup

```bash
pip install -r requirements.txt
```

Create `.env` (copy from `.env.example`) with `TELEGRAM_BOT_TOKEN`,
`MQTT_BROKER`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS`. The bot exits with an
error if the token or MQTT credentials are missing.

```bash
python bot.py
```

## Data

- `data/rooms.json` — room registry (bot writes, consumer reads)
- `data/actions.csv` — manual events (bot writes)
- `data/sensors.csv` — sensor readings (consumer writes)
- `data/devices.json` — last config the bot pushed per device (bot-only,
  created on demand, gitignored); backs the `/devices` "current config" view

CSVs are self-contained: English headers, a `room` column, and `device_ids`
present so they can be joined without `rooms.json`.

## MQTT

The bot subscribes to `sensor/+/+` and `discovery/devices` to learn which ESP32s
are online, and publishes device config directly to
`sensor/{device_id}/config`:

```json
{ "read_interval": 1, "read_processing": 10, "active": true }
```
