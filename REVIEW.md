# Student Work Review — Smart Building Monitoring

Fetched commit: `5aa90db` → `a76e183` (merge to `main`), *"Changes on how the file are organized, added new files for actions and rooms..."*

This review audits the student's implementation against `REQUIREMENTS.md`, section by section, and lists every defect found with severity.

Severity scale: **🔴 Critical** (feature broken / data corruption) · **🟠 Major** (requirement missing or wrong) · **🟡 Minor** (bug under edge conditions / code smell).

---

## TL;DR

The student built the **plumbing** (project structure, helper modules, MQTT wiring, device-id topics) but most of the **Telegram bot's user-facing feature set is missing or non-functional**. Two **critical** defects make the delivered system unusable as specified:

1. **Device config is broken end-to-end** — firmware and bot disagree on JSON key names, so `/config` crashes the ESP32 callback.
2. **`actions.csv` is structurally wrong** — Italian headers, no `device_ids` column, and a single event is split across two incomplete rows.

Of the 14 acceptance criteria, **~4 are met, ~3 are partially met, and ~7 are unmet.**

---

## 1. Firmware (`esp32_firmware/main.py`)

### ✅ Met

| Requirement | Where | Notes |
|---|---|---|
| `device_id = ubinascii.hexlify(machine.unique_id()).decode()` | `main.py:30-31` | Correct. |
| Topics `sensor/{device_id}/temperature`, `/humidity`, `/config` | `main.py:62,161-162` | Correct format, bytes used for topics. |
| JSON payload `{device_id, measurement:{type,min,max,media,varianza}}` | `main.py:114-154` | Matches spec exactly. Old `datatemp`/`datahum` dicts rebuilt fresh each publish. |
| Stats loop (min/max/mean/variance), `READ_INTERVAL`, `READ_PROCESSING`, `ACTIVE`, `sub_cb` | `main.py:65-164` | Preserved from original. |
| Secrets loaded from `secrets.py` with helpful error | `main.py:12-15` | Good. |

### ❌ Defects

| # | Sev | Defect | Evidence |
|---|---|---|---|
| F1 | 🔴 | **Config key mismatch — remote config can never work.** Firmware reads `config["readI"]`, `config["readP"]`, `config["activate"]`; the bot publishes `{"read_interval", "read_processing", "active"}`. `json.loads` succeeds, then `config["readI"]` raises `KeyError`, crashing `sub_cb`. Every `/config` invocation breaks the device. | `main.py:52-54,70-72` vs `bot.py:282-284` |
| F2 | 🟠 | **WiFi credentials hardcoded** in `do_connect()`, not in `secrets.py`. Not portable; `secrets.py.example` has no WiFi fields. | `main.py:42` |
| F3 | 🟡 | **Publishes str, not bytes.** `client.publish(topic, json_datatemp)` passes a `str` payload. Spec §5 warns to `.encode()` first; behaviour depends on `umqttsimple` tolerance. | `main.py:161-162` |
| F4 | 🟡 | **Dead/duplicated config block.** `msg = client.check_msg()` returns `None` (umqttsimple dispatches via the callback), so `if msg != None:` (`main.py:68-73`) never executes. The `Contatore` recompute inside it is unreachable — changing `READ_PROCESSING`/`READ_INTERVAL` at runtime won't rescale the counter until it naturally wraps. | `main.py:67-73` |
| F5 | 🟡 | **`json.dumps` without separators** on a memory-constrained ESP32 — minor, but payloads are larger than necessary. | `main.py:157-158` |
| F6 | 🟡 | **Leftover file `temp.py`** containing only `print("Hello, World!")` — junk committed to the repo. | `esp32_firmware/temp.py` |

---

## 2. MQTT Consumer (`mqtt_consumer/consumer.py`, `sensor.py`)

### ✅ Met

| Requirement | Where |
|---|---|
| Wildcard subscribe `sensor/+/temperature`, `sensor/+/humidity` | `consumer.py:16` |
| Extract `device_id`/`type` from topic parts | `consumer.py:52-54` |
| Parse `measurement` from payload, look up room, append row | `consumer.py:51-61` |
| `sensor.py` helpers (`load_rooms`, `get_room_for_device`, `append_sensor_row`) | matches spec §2.1 |
| CSV header `timestamp,device_id,room,type,min,max,media,varianza` | `sensor.py:31` |
| Device discovery via `discovery/devices` (retained) | `consumer.py:64-70` |
| Bot gets its own MQTT connection (spec §2.5 decision — no consumer relay) | consumer has no config relay ✓ |

### ❌ Defects

| # | Sev | Defect | Evidence |
|---|---|---|---|
| C1 | 🟠 | **Relative `DATA_DIR = Path("../data")`.** Breaks if the consumer is run from anywhere other than `mqtt_consumer/`. The bot's `rooms.py` correctly uses `Path(__file__).resolve()…`; the consumer does not. Two components writing the "shared" `data/` folder can silently target different directories. | `sensor.py:6` |
| C2 | 🟡 | **Stale retained discovery on restart.** `run()` publishes `discovery/devices = []` at startup (`consumer.py:79`) before any device is seen, wiping the retained list. A bot querying devices in that window sees an empty set. | `consumer.py:79` |
| C3 | 🟡 | **Unused `timemsg`**, and `subscribe()` reassigns `client.on_message` per topic (harmless but noisy). | `consumer.py:55,71-72` |
| C4 | 🟡 | **Timestamps are raw `time.time()` floats** with no timezone, contradicting spec §5 "Use ISO 8601 with UTC offset". Confirmed in `sensors.csv` (`1781791884.88…`). | `consumer.py:59`, `data/sensors.csv` |

---

## 3. Telegram Bot (`telegram_bot/bot.py`, `rooms.py`, `actions.py`)

### ✅ Met

| Requirement | Where |
|---|---|
| `rooms.py` helpers (`load/save/get/add/delete/update/get_device_room`) | `rooms.py` — faithful to spec §3.1, with a `.get("device_ids", [])` guard (better than spec) |
| `/setup` conversation: name → AC count → device multi-select → save | `bot.py:324-449` |
| Device multi-select toggles, reassignment warning shown | `bot.py:372-449` |
| Reassignment warning text | `bot.py:391-402` |
| `/config` publishes to `sensor/{device_id}/config` via bot's own MQTT client | `bot.py:280-287` |
| `send_device_config(device_id, read_interval, read_processing, active)` | `bot.py:280-287` |
| `/cancel` fallback on every conversation | `bot.py:769-773` + each handler's `fallbacks` |
| Conversation states use non-overlapping `range(14)` ints | `bot.py:68` |

### ❌ Defects

| # | Sev | Defect | Evidence |
|---|---|---|---|
| B1 | 🔴 | **`actions.csv` schema is wrong and corrupts the data model.** The bot writes headers `timestamp,stanza,ncond,npersone,ncondfreddi,ncondcaldi,ncondspenti` (Italian), but spec requires `timestamp,room,num_ac,num_people,num_ac_cool,num_ac_heat,num_ac_off,device_ids` (English). The `device_ids` column is **entirely missing**, violating the "self-contained CSVs" acceptance criterion. The correct `actions.py` writer exists but is never used (see B2). | `bot.py:474-491,646-654` vs `actions.py:16` |
| B2 | 🔴 | **A single event is split across two rows.** `/npersone` writes a row with `ncond=-1, npersone=N, ncondfreddi=-1, ncondcaldi=-1`; `/ncondizionatori` writes a *separate* row with `npersone=-1`. Spec §3.5 requires **one** row per event with all fields. The two commands don't share state, so no row is ever complete. | `bot.py:561-569` & `646-654` |
| B3 | 🔴 | **`actions.py` is dead code.** `from actions import read_actions` is imported but `read_actions` is never called; `append_action`, `update_action_row`, `delete_action_row` are never imported or used. All CSV I/O is hand-rolled inline with the wrong schema. | `bot.py:10`; search finds zero call sites |
| B4 | 🟠 | **`/rooms` is non-functional.** `rooms_list` renders inline buttons with `callback_data="room_{name}"`, but **no `CallbackQueryHandler`** handles `room_` callbacks (the only query handlers live inside `/setup` and `/config` conversations). Tapping a room button does nothing. The Rename/Change-AC/Assign/Delete sub-menu (spec §3.4) is entirely missing. | `bot.py:289-292`; handlers list `bot.py:935-948` |
| B5 | 🟠 | **`/config` name collision.** Spec §3.9 reserves `/config` for *downloading room config*. The student wired `/config` to *ESP32 device configuration*, and named the room-config download `/roomsdownload`. Downloads are `/sensorsdownload`/`/actionsdownload` instead of spec's `/sensors`/`/actions`. | `bot.py:903,938-940` |
| B6 | 🟠 | **`/event` command missing.** Spec §3.5 specifies a single conversation (room → people → AC cool → AC heat → validate → save). Replaced by two disconnected commands (`/npersone`, `/ncondizionatori`); AC-count validation against `room.num_ac` is not enforced. | absent; `bot.py:503-662` |
| B7 | 🟠 | **`/events` (edit/delete with pagination) missing.** Only `/clearline` exists, which blindly pops the last row of `actions.csv` regardless of room (it ignores the `stanza` argument — `csv_file = DATA_DIR/"actions.csv"` hardcodes the file). No pagination, no per-row edit/delete. | `bot.py:167-199` |
| B8 | 🟠 | **`/show` unified view missing.** The sensor+action merge (spec §3.10) is not implemented at all. | absent |
| B9 | 🟠 | **`/devices` listing missing.** No command lists all known ESP32 devices with assigned room + config status (spec §3.11). Device config is reachable via `/config`, but there's no read-only listing. | absent |
| B10 | 🟠 | **Reassignment updates only the new room.** On confirm, `add_room` appends the device to the new room's `device_ids` **without removing it from the old room**, so the device is silently double-assigned. `get_device_room` returns the first match. **Proven in `data/rooms.json`:** device `cc8da20d471c` is listed under `qwerty`, `test`, `asdf`, *and* `1234` simultaneously. | `bot.py:406`; `data/rooms.json` |
| B11 | 🟠 | **Menu commands never registered.** No `set_my_commands()` call anywhere; the menu button exposes nothing (acceptance criterion unmet). | search: zero hits |
| B12 | 🟠 | **Global discovery client is inert.** `client.subscribe("sensor/+/+")` runs at import but no `on_message` is ever set on that client — discovered devices are never accumulated. `get_known_devices()` instead opens a *throwaway* MQTT client per call, subscribes to `discovery/devices`, and blocks 3 s. Inefficient and racy. | `bot.py:54-56` (no handler) vs `303-322` |
| B13 | 🟡 | **`salva_ncondcaldi` validation is broken.** `ultima = leggi_ultima_riga(...)` reads the last CSV row and does `int(ultima["ncond"])`. If the last row is a people-count row, `ncond == -1`, so `cool+heat > -1` is always true and the check spuriously fails; if the file is empty, `ultima is None` → `TypeError`. Validation against `room.num_ac` (the spec rule) is never performed. | `bot.py:633-637` |
| B14 | 🟡 | **`npersone`/`ncondizionatori` never write a header.** Only the `setup→salva_ncond` path emits a header row; the event paths append blindly. `actions.csv` can end up headerless or with the stale Italian header. | `bot.py:558-569,643-654` |
| B15 | 🟡 | **`salva_device` calls `query.answer()` twice** (once unconditionally, once with an alert on "Done") — the second call can raise / be ignored. | `bot.py:682,684` |
| B16 | 🟡 | **Legacy `stanze.json` registry still present.** `REGISTRY_FILE`, `carica_stanze`, `salva_stanze`, `registra_stanza` (`bot.py:71,107-128`) keep the old single-file `{"csv_file","ncond"}` schema alive alongside the new `rooms.json`. `npersone`/`ncondizionatori` call `registra_stanza`, writing stale state. Migration left half-done. | `bot.py:107-128,462` |
| B17 | 🟡 | **Numeric inputs don't use a numeric keyboard.** Spec §3.5/§3.12 wants a numeric layout for counts; the student uses plain `ForceReply()` / `ReplyKeyboardRemove()` with free-text handlers. | `bot.py:338,536,594` |
| B18 | 🟡 | **`DATA_DIR = Path("../data")` in bot.py** (relative) vs absolute in `rooms.py` — inconsistent resolution; depends on CWD being `telegram_bot/`. | `bot.py:69` vs `rooms.py:5` |
| B19 | 🟡 | **MQTT connects at import time.** `client = connect_mqtt()` runs on module load; if the broker is down, the bot crashes before `main()`. | `bot.py:54` |

---

## 4. Acceptance Criteria (spec §7)

| # | Criterion | Status | Note |
|---|---|---|---|
| 1 | Multiple ESP32s publish to unique topics, distinguishable by `device_id` | ✅ Met | Topics + payload correct. |
| 2 | Bot can discover devices currently sending MQTT data | ⚠️ Partial | Works only via throwaway client subscribing to `discovery/devices`; global `sensor/+/+` listener is inert (B12). |
| 3 | New device starts unassigned, assignable via `/setup` | ✅ Met | `/setup` flow works. |
| 4 | Reassigning device A→B warns and updates **both** rooms | ❌ Unmet | Warning shown, but old room keeps the device (B10). |
| 5 | `/devices` lists all known ESP32s with room + config status | ❌ Unmet | No `/devices` command (B9). |
| 6 | Config changes published to `sensor/{device_id}/config` | ❌ Unmet | Published, but firmware rejects the payload (F1) → crashes callback. |
| 7 | `/event` validates AC counts against room config | ❌ Unmet | No `/event`; validation broken (B6, B13). |
| 8 | Numeric inputs use numeric keyboard | ❌ Unmet | Plain text handlers (B17). |
| 9 | Room name is the only QWERTY free-text input | ⚠️ Partial | Most inputs are free text; not deliberately constrained. |
| 10 | `/show` unified view merges sensor + event data | ❌ Unmet | Not implemented (B8). |
| 11 | All downloads produce CSV/JSON with proper headers | ⚠️ Partial | `sensors.csv` ✓; `actions.csv` wrong schema, often headerless (B1, B14). |
| 12 | `/events` edit/delete with pagination | ❌ Unmet | Not implemented (B7). |
| 13 | `/cancel` aborts any conversation | ✅ Met | Fallback on every handler. |
| 14 | Menu button exposes all top-level commands | ❌ Unmet | No `set_my_commands` (B11). |
| 15 | `sensors.csv` & `actions.csv` self-contained (room + device IDs) | ❌ Unmet | `sensors.csv` ✓; `actions.csv` has no `device_ids` col + Italian headers (B1). |

**Tally: 3 fully met · 3 partial · 9 unmet** (of 15).

---

## 5. Top-priority fixes (ordered)

1. **F1 — Align config keys.** Pick one schema (recommend the bot's `read_interval`/`read_processing`/`active`) and update `sub_cb` + the superloop block in `main.py`. This is a 3-line fix that unblocks the entire device-config feature.
2. **B1/B2/B3 — Fix `actions.csv`.** Delete the inline CSV writers; import and use `actions.py`'s `append_action` for every event. Implement the single `/event` conversation so one row carries `room, num_ac, num_people, num_ac_cool, num_ac_heat, device_ids`.
3. **B10 — Remove reassigned devices from their old room** in the setup confirm path (`update_room`/`delete` from old `device_ids`).
4. **B4/B6/B7/B8/B9 — Implement the missing bot commands** (`/event`, `/events`, `/show`, `/devices`, and a real `/rooms` callback).
5. **B11 — Call `set_my_commands`** in a `post_init` hook.
6. **C1/B18 — Make `DATA_DIR` absolute everywhere** (`Path(__file__).resolve()…`), matching `rooms.py`.
7. **F2 — Move WiFi credentials into `secrets.py`** and update `secrets.py.example`.

---

*Generated from commit `a76e183`. All line references are to the current `main`.*
