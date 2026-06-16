# MQTT Consumer

Subscribes to MQTT sensor topics and appends readings to a CSV file. Also publishes configuration updates back to the sensors.

## Topics

| Direction | Topic | Payload |
|---|---|---|
| Inbound | `sensor/temperature` | `{ id, room, measurement: { type, min, max, media, varianza } }` |
| Inbound | `sensor/humidity` | Same structure as temperature |
| Outbound | `sensor/config_0` | `{ readI, readP, activate }` JSON config |

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file (copy from `.env.example`) with `MQTT_BROKER`, `MQTT_USER`, and `MQTT_PASS` set. The consumer will exit with an error if any required variable is missing. `MQTT_BROKER` and `MQTT_PORT` have built-in defaults.

```bash
python consumer.py
```

## Output

Readings are appended to `data.csv` in the current directory.
