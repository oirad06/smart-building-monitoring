# Smart Building Monitoring — Requirements & Implementation Guide

> This guide is written for a high school student. It tells you **what** to build and **how to think about each piece**. Read an entire section before coding it.

---

## Overview

Three components talking over MQTT:

```
ESP32 sensors ──MQTT──> MQTT Consumer ──> sensors.csv
                              │
                         rooms.json
                              │
Telegram Bot ──MQTT──> ESP32 configs
    │
    └──> actions.csv
```

- **Firmware** runs on ESP32, reads DHT11, publishes to MQTT
- **Consumer** (Python) subscribes to MQTT, writes CSV files
- **Bot** (Python/Telegram) manages rooms, collects manual events, downloads data

---

## Implementation Roadmap (build in this order)

| Phase | What | Why first |
|-------|------|-----------|
| 0 | Project setup — shared `data/` folder, `.env`, `requirements.txt` | Everything depends on this |
| 1 | Firmware — device ID, new topics, new payload | Consumer needs data to test against |
| 2 | Consumer — wildcard subscribe, sensors.csv, device discovery MQTT topic | Bot needs to discover devices |
| 3 | Bot — data layer (rooms.json, actions.csv readers/writers) | Bot features need storage |
| 4 | Bot — `/setup` and `/rooms` commands | Device assignment is prerequisite for events |
| 5 | Bot — `/event` and `/events` commands | Core data entry |
| 6 | Bot — `/devices` command | ESP32 config management |
| 7 | Bot — `/show`, `/sensors`, `/actions`, `/config` downloads | Reporting (easiest to debug) |

---

## 0. Project Setup (shared between consumer and bot)

Both the consumer and the bot need to read/write the same files in `data/`.
The consumer and bot can run on the same machine or different machines — they just need access to a shared `data/` folder (can be a network share, but simplest is same machine).

**Folder layout:**
```
project/
├── data/               ← shared between consumer and bot
│   ├── rooms.json      ← bot writes, consumer reads
│   └── sensors.csv     ← consumer writes
│   └── actions.csv     ← bot writes
│
├── mqtt_consumer/
│   └── consumer.py
│
├── telegram_bot/
│   └── bot.py
│
├── esp32_firmware/
│   ├── main.py
│   ├── secrets.py
│   ├── mp-esp32-v1.28.0.bin
│   └── mp-esp32s3-v1.28.0.bin
│
├── requirements.txt    (paho-mqtt, python-telegram-bot, python-dotenv)
└── .env                (shared env vars: MQTT_BROKER, MQTT_PORT, MQTT_USER, MQTT_PASS, TELEGRAM_BOT_TOKEN)
```

> **Key insight for the student**: The consumer and bot read/write `data/rooms.json` *concurrently*. This means both must handle the case where the file doesn't exist yet (bot creates it, consumer treats missing device as "room is empty string"). The consumer never modifies `rooms.json` — it only reads it.

---

## 1. Firmware (`esp32_firmware/main.py`)

### What changes from the current code

Current code has:
```python
ID = 0  # line 22 — hardcoded
client_id = ubinascii.hexlify(machine.unique_id())  # line 27 — already computed!

datatemp = { "id": 0, "room": "open space", ... }   # hardcoded
datahum = { "id": 0, "room": "open space", ... }    # hardcoded

client.publish(b"sensor/temperature", json_datatemp)     # flat topic
client.publish(b"sensor/humidity", json_datahum)          # flat topic
client.subscribe("sensor/config_" + str(ID))              # hardcoded topic
```

### 1.1 Device ID — the base of everything

`machine.unique_id()` returns bytes like `b'\xa1\xb2\xc3\xd4\xe5\xf6'`.  
`ubinascii.hexlify(...)` gives `b'a1b2c3d4e5f6'` (bytes).  
You need a **string**: call `.decode()` on it once at the top.

```python
device_id = ubinascii.hexlify(machine.unique_id()).decode()
```

> **Why this matters**: Every ESP32 has a unique silicon ID burned into the chip. You cannot change it. This is your device's permanent identity. Everything else (room name, friendly name) is managed in the bot.

### 1.2 MQTT Topics — new format

| Old | New |
|-----|-----|
| `sensor/temperature` | `sensor/{device_id}/temperature` |
| `sensor/humidity` | `sensor/{device_id}/humidity` |
| `sensor/config_0` | `sensor/{device_id}/config` |

In Python with f-strings:
```python
topic_temp = f"sensor/{device_id}/temperature"
topic_hum = f"sensor/{device_id}/humidity"
topic_config = f"sensor/{device_id}/config"
```

> **Common mistake**: Forgetting `b"..."` (bytes) for MQTT topic strings. MicroPython's MQTT library expects bytes, not str. Use `.encode()` on the f-string or write `b"sensor/" + device_id.encode() + b"/temperature"`.

### 1.3 JSON Payload — no more room or id

```python
payload = {
    "device_id": device_id,
    "measurement": {
        "type": "temperature",
        "min": minsensor,
        "max": maxsensor,
        "media": media,
        "varianza": varianza
    }
}
json_payload = json.dumps(payload)
```

Remove the two dicts `datatemp` and `datahum` entirely. Build the dict fresh each time before publishing.

### 1.4 Config callback — update subscription topic

Change:
```python
client.subscribe("sensor/config_" + str(ID))
```
To:
```python
client.subscribe(f"sensor/{device_id}/config")
```

### 1.5 What stays the same

- The DHT11 reading loop
- The statistics computation (min/max/mean/variance)
- WiFi connection code
- The `READ_INTERVAL`, `READ_PROCESSING`, `ACTIVE` globals and `sub_cb` handler

### 1.6 Testing your firmware changes

Flash the ESP32, then open the REPL with `mpremote`. You should see:
```
network config: ('192.168.x.x', ...)
-----STATISTICS-TEMPERATURE-----
...
```

On the MQTT broker side (use `mosquitto_sub` or `mqttx`), subscribe to `sensor/#` and verify you see messages on topics like:
```
sensor/a1b2c3d4e5f6/temperature
sensor/a1b2c3d4e5f6/humidity
```

The payload should be JSON with `device_id` matching the topic's device ID.

### 1.7 Secrets template

```python
# secrets.py
mqtt_server = "your_broker_address"
mqtt_port = 8080
mqtt_user = "your_mqtt_user"
mqtt_pass = "your_mqtt_password"
```

---

## 2. MQTT Consumer (`mqtt_consumer/consumer.py`)

### 2.1 New file: `sensors.py` (utility functions)

Create a helper file `sensors.py` with these functions:

```python
import csv
import json
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path("data")
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
        if device_id in config["device_ids"]:
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
```

### 2.2 Consumer changes

The consumer currently looks like:

```python
broker = os.getenv("MQTT_BROKER", "130.136.2.70")
port = int(os.getenv("MQTT_PORT", "8080"))
topics = ["sensor/temperature", "sensor/humidity"]
```

**Changes:**
1. Subscribe to wildcard topics: `["sensor/+/temperature", "sensor/+/humidity"]`
2. In `on_message`, extract `device_id` from the topic:
   ```python
   # topic looks like "sensor/a1b2c3d4/temperature"
   parts = msg.topic.split("/")
   device_id = parts[1]    # always the second part
   measurement_type = parts[2]  # "temperature" or "humidity"
   ```
3. Parse payload JSON:
   ```python
   data = json.loads(msg.payload.decode())
   measurement = data["measurement"]
   ```
4. Look up room and write to CSV:
   ```python
   room = get_room_for_device(device_id)
   append_sensor_row(time.time(), device_id, room, measurement_type,
                     measurement["min"], measurement["max"],
                     measurement["media"], measurement["varianza"])
   ```

### 2.3 Device Discovery

For the bot to know which ESP32 devices exist, the consumer needs to publish a list of known device IDs somewhere the bot can read it.

**Simple approach**: The consumer keeps a Python `set()` of device IDs it has seen, and on startup (and every 60 seconds) publishes them to an MQTT topic:

```python
# In on_message:
seen_devices.add(device_id)

# Periodically (e.g., in a separate thread or with loop timers):
client.publish("discovery/devices", json.dumps(list(seen_devices)))
```

> **Alternative approach (even simpler)**: The bot subscribes to `sensor/+/+` and collects device IDs directly from MQTT topics — no need for discovery topic at all. The consumer doesn't need a device registry at all; it just writes CSV rows. The bot independently discovers devices by listening to MQTT. Consider which is simpler.

### 2.4 Config Publishing — relay from bot to device

The consumer needs an MQTT subscription that the bot can publish to. Simplest: subscribe to a topic like `config/to/{device_id}` where the bot sends config, and the consumer blindly relays it to `sensor/{device_id}/config`.

But actually, even simpler: the bot can publish **directly** to `sensor/{device_id}/config` using its own MQTT connection. The consumer doesn't need to be a middleman for config at all.

> **Decision to make**: Does the bot have its own MQTT connection (simpler), or does it send configs through the consumer (more centralized)?  
> **Recommendation**: Give the bot its own MQTT connection. It's 10 lines of code and avoids building a relay protocol.

### 2.5 Config Publishing (if bot has its own MQTT)

The bot:
1. Connects to the same MQTT broker (same env vars)
2. Publishes directly to `sensor/{device_id}/config` when the user configures a device
3. No relay needed in the consumer at all

Remove section 2.4 from the consumer entirely.

### 2.6 Testing the consumer

1. Run the consumer: `python consumer.py` (it should connect to MQTT and print "Connected!")
2. If an ESP32 is publishing, check `data/sensors.csv` — should have rows appearing every ~10s
3. Delete `data/sensors.csv` while the consumer runs — it should recreate the file with headers

---

## 3. Telegram Bot (`telegram_bot/bot.py`)

### 3.0 Bot Architecture — How to Think About It

The bot uses `python-telegram-bot` library. Key concepts:

- **Command handlers** — functions triggered by `/command` messages
- **ConversationHandler** — a multi-step flow (ask → wait for reply → ask more → save)
- **InlineKeyboard** — buttons that appear **inside** the chat, under a message
- **ReplyKeyboardMarkup** — buttons that replace the user's keyboard
- **Filters** — conditions on what kind of message to handle (TEXT, COMMAND, etc.)

**Flow of every feature:**

```
User types /command
        │
        ▼
entry_point handler runs → sends message with keyboard/buttons
        │
        ▼
(waits for user reply)
        │
        ▼
state handler receives reply → validates → saves data → asks next question OR finishes
```

### 3.1 Room Configuration Store

**File**: `data/rooms.json`

```json
{
  "open space": {
    "device_ids": ["a1b2c3d4e5f6", "e5f6a1b2c3d4"],
    "num_ac": 6
  },
  "server room": {
    "device_ids": ["i9j0k1l2m3n4"],
    "num_ac": 2
  }
}
```

**Helper functions** (put in a new file `rooms.py`):

```python
DATA_DIR = Path("data")
ROOMS_FILE = DATA_DIR / "rooms.json"

def load_rooms():
    if not ROOMS_FILE.exists():
        return {}
    with open(ROOMS_FILE) as f:
        return json.load(f)

def save_rooms(rooms):
    DATA_DIR.mkdir(exist_ok=True)
    with open(ROOMS_FILE, "w") as f:
        json.dump(rooms, f, indent=2)

def get_room_names():
    return list(load_rooms().keys())

def get_room(name):
    return load_rooms().get(name)

def room_exists(name):
    return name in load_rooms()

def add_room(name, device_ids, num_ac):
    rooms = load_rooms()
    rooms[name] = {"device_ids": device_ids, "num_ac": num_ac}
    save_rooms(rooms)

def delete_room(name):
    rooms = load_rooms()
    rooms.pop(name, None)
    save_rooms(rooms)

def update_room(name, **kwargs):
    rooms = load_rooms()
    if name in rooms:
        rooms[name].update(kwargs)
        save_rooms(rooms)

def get_device_room(device_id):
    """Returns the room name a device is assigned to, or None."""
    rooms = load_rooms()
    for name, config in rooms.items():
        if device_id in config["device_ids"]:
            return name
    return None
```

### 3.2 Actions Data Store

**File**: `data/actions.csv`

| timestamp | room | num_ac | num_people | num_ac_cool | num_ac_heat | num_ac_off | device_ids |
|-----------|---|--------|------------|-------------|-------------|------------|------------|

**Helper functions** (put in `actions.py`):

```python
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
```

> **Important**: `_rewrite_actions` rewrites the ENTIRE file. For a school project with a few hundred rows this is fine. For thousands of rows you'd want a database.

### 3.3 Feature: Room Setup (`/setup`)

**Conversation structure**: 3 steps

```
/setup
  → ask: room name?        (free text, QWERTY)
  → ask: number of ACs?    (number only)
  → show devices to assign (inline buttons, multi-select)
  → save to rooms.json
```

**Step-by-step code outline:**

```python
# Conversation states
ROOM_NAME, AC_COUNT, DEVICE_SELECTION = range(3)

async def setup_start(update, context):
    await update.message.reply_text("Inserisci il nome della stanza:")
    return ROOM_NAME

async def save_room_name(update, context):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Il nome non può essere vuoto.")
        return ROOM_NAME
    if room_exists(name):
        await update.message.reply_text("Esiste già una stanza con questo nome.")
        return ROOM_NAME
    
    context.user_data["room_name"] = name
    await update.message.reply_text("Quanti condizionatori ci sono?", reply_markup=ForceReply())
    return AC_COUNT

async def save_ac_count(update, context):
    try:
        count = int(update.message.text)
        if count < 0: raise ValueError
        context.user_data["num_ac"] = count
        
        # Now show available devices
        devices = await get_known_devices()  # from MQTT discovery
        assigned = {dev: get_device_room(dev) for dev in devices}
        
        keyboard = build_device_selection_keyboard(devices, assigned)
        await update.message.reply_text("Seleziona i dispositivi per questa stanza:", reply_markup=keyboard)
        return DEVICE_SELECTION
    except ValueError:
        await update.message.reply_text("Inserisci un numero >= 0.")
        return AC_COUNT

async def save_devices(update, context):
    # This handles callback_query from inline buttons
    selected = context.user_data.get("selected_devices", [])
    
    # ... handle multi-select logic ...
    
    if update.callback_query.data == "done":
        # Check for reassignments
        warnings = []
        for dev in selected:
            current_room = get_device_room(dev)
            if current_room:
                warnings.append(f"{dev} è già assegnato a \"{current_room}\"")
        
        if warnings:
            # Show warnings and ask confirmation
            await update.callback_query.message.reply_text(
                "ATTENZIONE:\n" + "\n".join(warnings) + "\n\nConfermi?",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Sì", callback_data="confirm")]])
            )
        else:
            # Save directly
            save_room(context.user_data["room_name"], selected, context.user_data["num_ac"])
            append_initial_action(...)
            await update.callback_query.message.reply_text("Stanza creata!")
            return ConversationHandler.END
```

> **How to build inline button keyboards**: See the Telegram Bot API docs for `InlineKeyboardMarkup` and `InlineKeyboardButton`. Each button has `text` and `callback_data`. You handle button presses in a `CallbackQueryHandler` (a separate handler outside the ConversationHandler, or inside it if you use `CallbackQueryHandler` as a state handler).

### 3.4 Feature: Room Management (`/rooms`)

Simpler than setup — just show room buttons, then show what you can do with each:

```python
async def rooms_list(update, context):
    rooms = get_room_names()
    keyboard = [[InlineKeyboardButton(name, callback_data=f"room_{name}")] for name in rooms]
    await update.message.reply_text("Scegli una stanza:", reply_markup=InlineKeyboardMarkup(keyboard))
```

When a room button is tapped, show:
```
Stanza: open space
AC totali: 6
Dispositivi: a1b2c3d4, e5f6g7h8

[Rinomina] [Cambia AC] [Assegna dispositivi] [Elimina stanza]
```

Each is a `CallbackQueryHandler` that starts its own mini-conversation.

### 3.5 Feature: Event Reporting (`/event`)

**Conversation**: 3 steps

```
/event
  → select room (inline buttons)
  → enter people count (numeric keyboard)
  → enter AC cool count (numeric keyboard)
  → enter AC heat count (numeric keyboard)
  → validate and save
```

**Validation rules:**
- `num_people >= 0`
- `num_ac_cool >= 0`
- `num_ac_heat >= 0`
- `num_ac_cool + num_ac_heat <= room.num_ac` (from rooms.json)
- `num_ac_off = room.num_ac - num_ac_cool - num_ac_heat` (computed automatically)

> **How to show numeric keyboard**: Use `ReplyKeyboardMarkup` with `input_field_placeholder="0"` and a one-row keyboard like `[["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]]` — or just set `ForceReply()` since Telegram on mobile will show the numeric keyboard when the input field has `input_field_placeholder="Inserisci numero"` (Telegram infers keyboard type from placeholder).

Actually the simplest approach: just use `ReplyKeyboardMarkup` with a single button `[["0-9"]]` and `input_field_placeholder="Numero (>=0)"`. Or just rely on Telegram's keyboard auto-detection.

### 3.6 Feature: Event Edit (`/events`)

**No conversation handler** — just show paginated data with edit/delete buttons.

```
/events  → shows last 10 events with [Edit] [Delete] per row
           [< Precedenti] [Successivi >]
```

**Pagination trick:**

```python
def get_action_page(page=0, per_page=10, room_filter=None):
    rows = read_actions()
    if room_filter:
        rows = [r for r in rows if r["room"] == room_filter]
    total = len(rows)
    start = page * per_page
    end = start + per_page
    page_rows = rows[start:end]
    return page_rows, total, page
```

**Edit flow**: User taps [Edit] → bot asks for new values one by one (same as event reporting). Once all collected, call `update_action_row(index, ...)`.

**Delete flow**: User taps [Delete] → bot asks "Confermi?" with [Sì] [No] buttons. On confirm, call `delete_action_row(index)`.

### 3.7 Feature: Download Sensor Data (`/sensors`)

```python
async def sensors_download(update, context):
    keyboard = [
        [InlineKeyboardButton("Full export", callback_data="sensors_all")],
        [InlineKeyboardButton("Per room", callback_data="sensors_per_room")]
    ]
    await update.message.reply_text("Scegli:", reply_markup=InlineKeyboardMarkup(keyboard))
```

For "full export":
```python
await update.message.reply_document(document=open("data/sensors.csv", "rb"))
```

For "per room": show room list → when selected, filter sensors.csv rows → write temp file → send.

> **How `reply_document` works**: The bot sends a file to the user. Telegram supports CSV files natively — the user gets a download button. The file path must exist on disk.

### 3.8 Feature: Download Actions Data (`/actions`)

Same structure as `/sensors` but with `actions.csv`.

### 3.9 Feature: Download Room Config (`/config`)

"All rooms" = send `rooms.json` as a file.  
"Single room" = find the room entry, convert to JSON string, send as `.txt` or `.json` file.

```python
import io
text = json.dumps(rooms[room_name], indent=2)
await update.message.reply_document(
    document=io.BytesIO(text.encode()),
    filename=f"{room_name}_config.json"
)
```

### 3.10 Feature: Show Measurements & Actions (`/show`)

This is the hardest feature because it merges two CSV files.

```python
def merge_sensors_and_actions(room_filter=None, limit=10):
    """Return a list of unified rows sorted by timestamp descending."""
    sensors = read_sensors()  # similar to read_actions()
    actions = read_actions()
    
    # Tag rows by source
    for row in sensors:
        row["_source"] = "sensor"
    for row in actions:
        row["_source"] = "action"
    
    all_rows = sensors + actions
    if room_filter:
        all_rows = [r for r in all_rows if r["room"] == room_filter]
    
    all_rows.sort(key=lambda r: r["timestamp"], reverse=True)
    return all_rows[:limit]
```

For display, format differently depending on source:
- Sensor row: `"10:32:15 | open space | a1b2c3d4 | temp: 24.5°C"`
- Action row: `"10:35:00 | open space | people: 5 | AC: 2C/1H/3F"`

### 3.11 Feature: ESP32 Device Configuration (`/devices`)

This requires the bot to have its own MQTT connection.

**In `bot.py`, add:**

```python
import paho.mqtt.client as mqtt

# Connect to MQTT on bot startup
mqtt_client = mqtt.Client(client_id="telegram-bot")
mqtt_client.username_pw_set(username, password)
mqtt_client.connect(broker, port)
mqtt_client.loop_start()  # non-blocking loop

# Subscribe to discovery/devices to know which ESP32s exist
mqtt_client.subscribe("sensor/+/+")  # learn device IDs from any sensor message
```

**Discovery approach (simpler)**: Instead of a separate `discovery/devices` topic, the bot subscribes to `sensor/+/+` and collects the `device_id` from any message. It stores them in a set and updates a `last_seen` timestamp.

```python
known_devices = {}  # device_id -> last_seen_timestamp

def on_mqtt_message(client, userdata, msg):
    parts = msg.topic.split("/")
    if len(parts) == 3 and parts[0] == "sensor":
        device_id = parts[1]
        known_devices[device_id] = time.time()
```

**Config publishing:**

```python
def send_device_config(device_id, read_interval, read_processing, active):
    payload = json.dumps({
        "read_interval": read_interval,
        "read_processing": read_processing,
        "active": active
    })
    mqtt_client.publish(f"sensor/{device_id}/config", payload)
```

### 3.12 Telegram UI Guidelines

**Cheat sheet for bot UI components:**

| Input type | How to implement |
|------------|-----------------|
| **Free text** (room name) | `MessageHandler(filters.TEXT & ~filters.COMMAND, handler)` — Telegram shows QWERTY by default |
| **Number input** | Same handler, but validate with `int()` in try/except. Telegram shows numeric keyboard on mobile when field expects number. Optionally use `ForceReply(input_field_placeholder="0")`. |
| **Pick from list** (room, device) | `InlineKeyboardMarkup` with `InlineKeyboardButton(text, callback_data=value)` |
| **Multi-select** (assign devices) | Each tap toggles selection. Track in `context.user_data["selected"]`. "Done" button to finish. |
| **Pagination** | `[< Prev] [Next >]` buttons. Store current page in `context.user_data["page"]`. |
| **Menu button** | Set at startup with `await bot.set_my_commands([...])` |

**Menu commands to register:**

```python
commands = [
    ("setup", "Crea una nuova stanza"),
    ("rooms", "Gestisci le stanze"),
    ("event", "Registra evento (persone/AC)"),
    ("events", "Modifica o elimina eventi recenti"),
    ("devices", "Configura i sensori ESP32"),
    ("show", "Mostra dati sensori ed eventi"),
    ("sensors", "Scarica dati sensori"),
    ("actions", "Scarica dati eventi"),
    ("config", "Scarica configurazione stanze"),
]
```

**All conversation flows support `/cancel`:**

```python
async def cancel(update, context):
    await update.message.reply_text("Operazione annullata.")
    return ConversationHandler.END
```

---

## 4. Data Joining

Both CSVs have `room` column — this is how you connect them:

### Example: find temperature when someone reported 5 people in "open space"

```python
# Filter actions for room="open space", num_people >= 5
# For each matching action, find sensors.csv rows with same room and close timestamp
# (within ~10 seconds, since sensor readings come every 10s)
```

The `/show` unified view does this automatically. The CSV files are designed to be loaded directly into Excel or pandas without needing `rooms.json`.

---

## 5. Common Pitfalls to Watch For

### Firmware
- **Bytes vs strings**: MQTT topics and payloads need bytes (`b"..."`), but `json.dumps()` produces a string. Call `.encode()` before publishing.
- **`ubinascii.hexlify` returns bytes**: Don't forget `.decode()`.
- **Indentation**: MicroPython on ESP32 has limited memory. Deeply nested loops are fine. Just don't create huge lists.

### Consumer
- **File locking**: On Windows, writing to a CSV while the bot reads it can cause errors. Use simple file operations (open, write, close) — don't hold files open.
- **Timezones**: Use ISO 8601 with UTC offset for timestamps to avoid confusion.

### Bot
- **`context.user_data` vs `context.chat_data`**: `user_data` persists per user across conversations. Use it for temporary state during a multi-step flow.
- **Callback queries expire**: Inline button `callback_data` is stored by Telegram. For sensitive operations (delete confirmation), always re-verify state at the time the button is pressed.
- **ConversationHandler ordering**: State numbers must be unique across ALL conversation handlers in your bot. Use `range(10)`, `range(10, 20)`, `range(20, 30)` etc.
- **`callback_query.answer()`**: Always call this after handling an inline button press to acknowledge it.

---

## 6. Migration from Current State

This table shows what changes in each component:

| Component | Current → Target |
|-----------|-----------------|
| **Firmware** | Hardcoded `id:0`, `room:"open space"`, flat topics → `device_id` in topic + payload, no room |
| **Consumer** | Flat `data.csv`, no device/room → single `sensors.csv` with `device_id` + `room`, reads `rooms.json` |
| **Bot** | Italian-only, `stanze.json` (single room), per-room event CSVs → English commands, `rooms.json` (multi-room), single `actions.csv`, device assignment, inline keyboards, downloads, unified view |

**Migration steps:**
1. Stop the old consumer and bot
2. Update firmware on all ESP32s (they'll use new topics)
3. Update consumer — it starts writing `data/sensors.csv` (old `data.csv` can be kept as backup)
4. Start new bot — configure rooms via `/setup` (old events in per-room CSVs can be manually merged into `actions.csv`)
5. Delete old files once everything works

---

## 7. Acceptance Criteria (checklist)

When you finish, go through this list:

- [ ] Multiple ESP32 devices publish to unique MQTT topics and are distinguishable by `device_id`
- [ ] The bot can discover devices currently sending MQTT data
- [ ] A new device starts unassigned and can be assigned to any room via `/setup`
- [ ] Reassigning a device from room A to room B shows a warning and updates both rooms
- [ ] `/devices` command lists all known ESP32 devices with assigned room and config status
- [ ] Device config changes (interval, window, active) are published from the bot to `sensor/{device_id}/config`
- [ ] Event reporting (`/event`) validates AC counts against room config
- [ ] All numeric inputs use numeric keyboard layout
- [ ] Room name is the only free-text input requiring QWERTY
- [ ] `/show` unified view merges sensor + event data on room + timestamp
- [ ] All downloads produce CSV/JSON files with proper headers
- [ ] Event edit/delete (`/events`) works with pagination
- [ ] `/cancel` aborts any in-progress conversation
- [ ] Menu button exposes all top-level commands
- [ ] `sensors.csv` and `actions.csv` are self-contained (room column filled in, device IDs present, usable without any other file)