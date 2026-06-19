# python 3.12
import json
import os
import random
from datetime import datetime, timezone

from sensor import get_room_for_device, append_sensor_row
from dotenv import load_dotenv
from paho.mqtt import client as mqtt_client

load_dotenv()

broker = os.getenv("MQTT_BROKER", "130.136.2.70")
port = int(os.getenv("MQTT_PORT", "8080"))
topics = ["sensor/+/temperature", "sensor/+/humidity"]
# Generate a Client ID with the subscribe prefix.
client_id = f'subscribe-{random.randint(0, 100)}'
username = os.getenv("MQTT_USER")
password = os.getenv("MQTT_PASS")
if not all([broker, username, password]):
    raise SystemExit("ERROR: MQTT_BROKER, MQTT_USER, and MQTT_PASS must be set. Copy .env.example to .env and configure your MQTT credentials.")

seen_devices = set()  # Device IDs observed on the bus; republished (retained) for bot discovery.


def connect_mqtt() -> mqtt_client:
    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("Connected to MQTT Broker!")
        else:
            print(f"Failed to connect, return code {reason_code}")

    client = mqtt_client.Client(
        client_id=client_id,
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.connect(broker, port)
    return client


def on_message(client, userdata, msg):
    print(f"Received `{msg.payload.decode()}` from `{msg.topic}` topic")

    # Rehydrate the known-device set from the retained discovery message (restart-safe).
    if msg.topic == "discovery/devices":
        try:
            for dev in json.loads(msg.payload.decode()):
                seen_devices.add(dev)
        except Exception as e:
            print(f"Bad discovery payload: {e}")
        return

    try:
        data = json.loads(msg.payload.decode())
    except Exception as e:
        print(f"Bad payload: {e}")
        return

    measurement = data["measurement"]
    # topic looks like "sensor/{device_id}/temperature"
    parts = msg.topic.split("/")
    device_id = parts[1]          # always the second part
    measurement_type = parts[2]   # "temperature" or "humidity"

    room = get_room_for_device(device_id)
    append_sensor_row(
        datetime.now(timezone.utc).isoformat(),
        device_id, room, measurement_type,
        measurement["min"], measurement["max"],
        measurement["media"], measurement["varianza"],
    )
    print("row appended")

    # Republish the full known-device list (retained) so the bot can discover devices.
    if device_id not in seen_devices:
        seen_devices.add(device_id)
        client.publish("discovery/devices", json.dumps(sorted(seen_devices)), retain=True)


def run():
    client = connect_mqtt()
    client.on_message = on_message
    for topic in topics:
        client.subscribe(topic)
    client.subscribe("discovery/devices")  # retained: seeds seen_devices on restart
    client.loop_forever()


if __name__ == '__main__':
    run()
