# python 3.12
import time
import random
import json
import csv
import os

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

seen_devices = set()  # Keep track of seen devices to avoid duplicate processing

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


def subscribe(client: mqtt_client, topic):
    def on_message(client, userdata, msg):
        print(f"Received `{msg.payload.decode()}` from `{msg.topic}` topic")
        try:
            data = json.loads(msg.payload.decode())
        except Exception as e:
            print(f"Bad payload: {e}")
            return
        measurement = data["measurement"]
        parts = msg.topic.split("/")
        device_id = parts[1]    # always the second part
        measurement_type = parts[2]  # "temperature" or "humidity"
        timemsg = time.time()
        
        
        room = get_room_for_device(device_id)
        append_sensor_row(time.time(), device_id, room, measurement_type,
                        measurement["min"], measurement["max"],
                        measurement["media"], measurement["varianza"])
        print("invio") 
        # In on_message:
        if device_id not in seen_devices:
            seen_devices.add(device_id)
            client.publish(
                "discovery/devices",
                json.dumps(list(seen_devices)),
                retain=True
            )
    client.subscribe(topic)
    client.on_message = on_message


def run():
    client = connect_mqtt()
    for cont in topics:
      subscribe(client, cont)
    client.publish("discovery/devices", json.dumps(list(seen_devices)))
    client.loop_forever()


if __name__ == '__main__':
    run()

