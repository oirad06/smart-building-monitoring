# python 3.12
import time
import random
import json
import csv
import os

from paho.mqtt import client as mqtt_client


broker = os.getenv("MQTT_BROKER", "130.136.2.70")
port = int(os.getenv("MQTT_PORT", "8080"))
topics = ["sensor/temperature", "sensor/humidity"]
# Generate a Client ID with the subscribe prefix.
client_id = f'subscribe-{random.randint(0, 100)}'
username = os.getenv("MQTT_USER")
password = os.getenv("MQTT_PASS")
if not all([broker, username, password]):
    raise SystemExit("ERROR: MQTT_BROKER, MQTT_USER, and MQTT_PASS must be set. Copy .env.example to .env and configure your MQTT credentials.")

#configuration
config = {
    "readI": 1,
    "readP": 10,
    "activate": True
}

json_config_0 = json.dumps(config)

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
        data = json.loads(msg.payload.decode())
        timemsg = time.time()
        row = [
            timemsg, 
            data.get("id"), 
            data.get("room"), 
            data["measurement"]["type"], 
            data["measurement"]["min"], 
            data["measurement"]["max"], 
            data["measurement"]["media"], 
            data["measurement"]["varianza"]
        ]

        # write CSV
        with open("data.csv", "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(row)
        print("invio")
        client.publish("sensor/config_0", json_config_0)
    client.subscribe(topic)
    client.on_message = on_message


def run():
    client = connect_mqtt()
    for cont in topics:
      subscribe(client, cont)
    client.loop_forever()


if __name__ == '__main__':
    run()

