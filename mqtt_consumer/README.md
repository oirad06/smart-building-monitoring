# MQTT Consumer

Subscribes to per-device sensor topics, enriches each reading with its assigned
room (from `rooms.json`), and appends it to `data/sensors.csv`. Also republishes
the known-device list (retained) so the bot can discover devices.

## Topics

| Direction | Topic | Payload |
|---|---|---|
| Inbound | `sensor/+/temperature` | `{ device_id, measurement: { type, min, max, media, varianza } }` |
| Inbound | `sensor/+/humidity` | Same structure as temperature |
| Inbound | `discovery/devices` | retained JSON list of device IDs (seeds the known set on restart) |
| Outbound | `discovery/devices` | retained JSON list of device IDs seen so far |

The `device_id` is parsed from the second topic segment (`sensor/{device_id}/…`).
Config publishing is handled by the bot's own MQTT connection — the consumer is
not a middleman.

## Setup

```bash
pip install -r requirements.txt
```

Create `.env` (copy from `.env.example`) with `MQTT_BROKER`, `MQTT_USER`, and
`MQTT_PASS`. The consumer exits with an error if any required variable is
missing. `MQTT_BROKER` and `MQTT_PORT` have built-in defaults.

```bash
python consumer.py
```

## Output

Readings are appended to `data/sensors.csv` with headers
`timestamp,device_id,room,type,min,max,media,varianza`. Timestamps are ISO 8601
(UTC). The file is created with headers if missing, so deleting it while the
consumer runs recreates it correctly.
