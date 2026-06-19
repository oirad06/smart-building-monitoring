print("Starting ESP32 firmware...")

import time
import dht
import machine
import math
import json
import network
from umqttsimple import MQTTClient
import ubinascii

try:
    from secrets import wifi_ssid, wifi_password, mqtt_server, mqtt_port, mqtt_user, mqtt_pass
except ImportError:
    raise SystemExit("ERROR: esp32_firmware/secrets.py not found. Copy secrets.py.example to secrets.py and configure WiFi + MQTT credentials.")

tempVal = []
humVal = []
READ_INTERVAL = 1
READ_PROCESSING = 10
ACTIVE = True
MIN_VALUE = -100
MAX_VALUE = 100

Contatore = READ_PROCESSING // READ_INTERVAL
minsensor = 0
maxsensor = 0
media = 0
varianza = 0
client_id = ubinascii.hexlify(machine.unique_id())
device_id =client_id.decode()

# Pin for Data
d = dht.DHT11(machine.Pin(4))

#Network
def do_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.connect(wifi_ssid, wifi_password)
        while not wlan.isconnected():
            machine.idle()
    print('network config:', wlan.ipconfig('addr4'))

#configuration
def sub_cb(topic, msg):
  global READ_INTERVAL, READ_PROCESSING, ACTIVE, Contatore
  if msg != None:
      config = json.loads(msg)
      READ_INTERVAL = int(config["read_interval"])
      READ_PROCESSING = int(config["read_processing"])
      ACTIVE = bool(config["active"])
      Contatore = READ_PROCESSING // READ_INTERVAL
      print("config: ", config)
      print("actual config: ", READ_INTERVAL, READ_PROCESSING, ACTIVE)

do_connect()
client = MQTTClient(client_id, mqtt_server, mqtt_port, user=mqtt_user, password=mqtt_pass)
client.set_callback(sub_cb)
client.connect()
client.subscribe(b"sensor/" + device_id.encode() + b"/config")

#Superloop
while True:
  # Poll for config messages; sub_cb applies them and rescales Contatore.
  client.check_msg()


  if ACTIVE != True:
    continue

  #measurments from sensor
  d.measure()
  temp = d.temperature()
  hum = d.humidity()

  if MIN_VALUE <= temp <= MAX_VALUE:
    tempVal.append(temp)
  if MIN_VALUE <= hum <= MAX_VALUE:
    humVal.append(hum)

  if Contatore > 1:
    Contatore -= 1
  else:
    Contatore = READ_PROCESSING // READ_INTERVAL
    

    if len(tempVal) * len(humVal) != 0:
      #temperature values
      varianza = 0
      minsensor = min(tempVal)
      maxsensor = max(tempVal)
      media = sum(tempVal) / len(tempVal)
      for cont in range(0, len(tempVal)):
        varianza += (tempVal[cont] - media) * (tempVal[cont] - media)
      varianza = varianza / len(tempVal)

      print("-----STATISTICS-TEMPERATURE-----")
      print("temperature min: ", minsensor)
      print("temperature max: ", maxsensor)
      print("temperature media: ", media)
      print("temperature varianza: ", varianza)
      print("length array:", len(tempVal))
      
      tempVal.clear()
      
      datatemp = {
        "device_id": device_id,
        "measurement" : 
          {
            "type" : "temperature",
            "min" : minsensor,
            "max" : maxsensor,
            "media" : media,
            "varianza" : varianza
          },
      } 
      
      #humidity values
      varianza = 0
      minsensor = min(humVal)
      maxsensor = max(humVal)
      media = sum(humVal) / len(humVal)
      for cont in range(0, len(humVal)):
        varianza += (humVal[cont] - media) * (humVal[cont] - media)
      varianza = varianza / len(humVal)
      
      print("-----STATISTICS-HUMIDITY-----")
      print("humidity min: ", minsensor)
      print("humidity max: ", maxsensor)
      print("humidity media: ", media)
      print("humidity varianza: ", varianza)
      print("length array:", len(humVal))

      humVal.clear()

      datahum = {
        "device_id": device_id,
        "measurement" : 
          {
            "type" : "humidity",
            "min" : minsensor,
            "max" : maxsensor,
            "media" : media,
            "varianza" : varianza
          }
      }

      # Publish json
      json_datatemp = json.dumps(datatemp)
      json_datahum = json.dumps(datahum)
      print(json_datatemp)
      print(json_datahum)
      client.publish(b"sensor/" + device_id.encode() + b"/temperature", json_datatemp.encode())
      client.publish(b"sensor/" + device_id.encode() + b"/humidity", json_datahum.encode())

  time.sleep(READ_INTERVAL)