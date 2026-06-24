print("Starting ESP32 firmware...")

import json
import time

import dht
import machine
import network
import ubinascii
from umqttsimple import MQTTClient, MQTTException

try:
    from secrets import (
        mqtt_pass,
        mqtt_port,
        mqtt_server,
        mqtt_user,
        wifi_password,
        wifi_ssid,
    )
except ImportError:
    raise SystemExit(
        "ERROR: esp32_firmware/secrets.py not found. Copy secrets.py.example to secrets.py and configure WiFi + MQTT credentials."
    )

tempVal = []
humVal = []
READ_INTERVAL = 1
READ_PROCESSING = 10
ACTIVE = True
MIN_VALUE = -100
MAX_VALUE = 100

# Network/broker connect retry tuning.
WIFI_CONNECT_TIMEOUT = 30  # seconds to wait for WiFi before resetting
MQTT_RETRY_DELAY = 5  # seconds between broker reconnect attempts

# device_id is the chip's unique silicon ID as a hex string. It is the single
# source of identity: MQTT client_id, the {device_id} in every topic, and the
# "device_id" field in every payload all use this same string.
device_id = ubinascii.hexlify(machine.unique_id()).decode()

CONFIG_TOPIC = b"sensor/" + device_id.encode() + b"/config"
TEMP_TOPIC = b"sensor/" + device_id.encode() + b"/temperature"
HUM_TOPIC = b"sensor/" + device_id.encode() + b"/humidity"
# Birth/will topic for autodiscovery. The bot learns a device from any 3-part
# sensor/{id}/* message, so an "online" announce here registers this device
# immediately on connect — no need to wait for the first aggregation window.
STATUS_TOPIC = b"sensor/" + device_id.encode() + b"/status"
# Retained mirror of the device's CURRENT applied config. The bot reads this to
# show ground truth (not just what it last pushed) and to confirm a config took.
CONFIG_STATE_TOPIC = b"sensor/" + device_id.encode() + b"/config_state"


def reads_per_window():
    # Number of samples per aggregation window. Guarded against bad config:
    # at least one read, and never a division by zero.
    interval = READ_INTERVAL if READ_INTERVAL > 0 else 1
    return max(1, READ_PROCESSING // interval)


Contatore = reads_per_window()

# Pin for Data
d = dht.DHT22(machine.Pin(4))


# Network
def do_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print("connecting to network...")
        wlan.connect(wifi_ssid, wifi_password)
        deadline = time.time() + WIFI_CONNECT_TIMEOUT
        while not wlan.isconnected():
            if time.time() > deadline:
                print("WiFi connect timed out; resetting.")
                time.sleep(1)
                machine.reset()
            machine.idle()
    print("network config:", wlan.ipconfig("addr4"))


# configuration
def sub_cb(topic, msg):
    global READ_INTERVAL, READ_PROCESSING, ACTIVE, Contatore
    if msg is None:
        return
    try:
        config = json.loads(msg)
        interval = int(config["read_interval"])
        processing = int(config["read_processing"])
        active = bool(config["active"])
    except (ValueError, KeyError, TypeError) as e:
        print("Bad config payload, ignoring:", e)
        return
    if interval <= 0 or processing <= 0:
        print("Invalid config (read_interval/read_processing must be > 0), ignoring.")
        return
    READ_INTERVAL = interval
    READ_PROCESSING = processing
    ACTIVE = active
    Contatore = reads_per_window()
    print("config: ", config)
    print("actual config: ", READ_INTERVAL, READ_PROCESSING, ACTIVE)
    publish_config_state(client_ref)


def announce(client):
    # Autodiscovery: tell the bus this device is present. Retained so a bot that
    # connects later still sees us without waiting for the next sensor message.
    client.publish(STATUS_TOPIC, b"online", retain=True, qos=0)
    print(f"{device_id} announced online on {STATUS_TOPIC.decode()}.")


# Reference to the live client so sub_cb (called by client.check_msg) can publish.
client_ref = None


def publish_config_state(client):
    # Retained snapshot of the applied config, so the bot sees ground truth even
    # for a device that is currently offline.
    if client is None:
        return
    payload = json.dumps({
        "read_interval": READ_INTERVAL,
        "read_processing": READ_PROCESSING,
        "active": ACTIVE,
    })
    client.publish(CONFIG_STATE_TOPIC, payload.encode(), retain=True, qos=0)


def connect_mqtt():
    client = MQTTClient(
        device_id.encode(), mqtt_server, mqtt_port, user=mqtt_user, password=mqtt_pass
    )
    client.set_callback(sub_cb)
    # Last will: the broker publishes this (retained) if we drop off ungracefully,
    # so the bot can tell live devices from dead ones.
    client.set_last_will(STATUS_TOPIC, b"offline", retain=True, qos=0)
    client.connect()
    client.subscribe(CONFIG_TOPIC)
    global client_ref
    client_ref = client
    announce(client)
    # Publish the current applied config once on connect so the bot sees ground
    # truth immediately (retained), even before any new config is pushed.
    publish_config_state(client)
    print(f"{device_id} connected to MQTT broker and subscribed to config.")
    return client


def stats(values):
    # min, max, mean, variance over a non-empty list.
    n = len(values)
    minv = min(values)
    maxv = max(values)
    mean = sum(values) / n
    var = 0
    for v in values:
        var += (v - mean) * (v - mean)
    var = var / n
    return minv, maxv, mean, var


def publish_measurement(client, topic, mtype, values):
    minv, maxv, mean, var = stats(values)
    print("-----STATISTICS-" + mtype.upper() + "-----")
    print(
        mtype,
        "min:",
        minv,
        "max:",
        maxv,
        "media:",
        mean,
        "varianza:",
        var,
        "n:",
        len(values),
    )
    payload = {
        "device_id": device_id,
        "measurement": {
            "type": mtype,
            "min": minv,
            "max": maxv,
            "media": mean,
            "varianza": var,
        },
    }
    encoded = json.dumps(payload)
    print(encoded)
    client.publish(topic, encoded.encode())


do_connect()
client = connect_mqtt()

# Superloop
while True:
    try:
        # Poll for config messages; sub_cb applies them and rescales Contatore.
        client.check_msg()

        if ACTIVE is not True:
            time.sleep(READ_INTERVAL)
            continue

        # measurements from sensor
        try:
            d.measure()
            temp = d.temperature()
            hum = d.humidity()
        except OSError as e:
            # DHT22 reads fail intermittently (timing/checksum); skip this sample.
            print("DHT read failed, skipping:", e)
            time.sleep(READ_INTERVAL)
            continue

        if MIN_VALUE <= temp <= MAX_VALUE:
            tempVal.append(temp)
        if MIN_VALUE <= hum <= MAX_VALUE:
            humVal.append(hum)

        if Contatore > 1:
            Contatore -= 1
        else:
            Contatore = reads_per_window()
            # Publish each measurement type independently so one empty window
            # does not suppress the other.
            if tempVal:
                publish_measurement(client, TEMP_TOPIC, "temperature", tempVal)
                tempVal.clear()
            if humVal:
                publish_measurement(client, HUM_TOPIC, "humidity", humVal)
                humVal.clear()

        time.sleep(READ_INTERVAL)

    except (OSError, MQTTException) as e:
        # Lost the broker (or WiFi). Reconnect with backoff instead of dying.
        print("MQTT/network error, reconnecting:", e)
        while True:
            time.sleep(MQTT_RETRY_DELAY)
            try:
                do_connect()
                client = connect_mqtt()
                break
            except (OSError, MQTTException) as e2:
                print("Reconnect failed, retrying:", e2)
