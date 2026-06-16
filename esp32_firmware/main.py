import time
import dht
import machine
import math
import json
import network
from umqttsimple import MQTTClient
import ubinascii
try:
    from secrets import mqtt_server, mqtt_port, mqtt_user, mqtt_pass
except ImportError:
    raise SystemExit("ERROR: esp32_firmware/secrets.py not found. Copy secrets.py.example to secrets.py and configure your MQTT credentials.")

tempVal = []
humVal = []
READ_INTERVAL = 1
READ_PROCESSING = 10
ACTIVE = True
MIN_VALUE = -100
MAX_VALUE = 100
Contatore = READ_PROCESSING // READ_INTERVAL
ID = 0
minsensor = 0
maxsensor = 0
media = 0
varianza = 0
client_id = ubinascii.hexlify(machine.unique_id())

# Pin for Data
d = dht.DHT11(machine.Pin(4))

# json
datatemp = {
  "id": 0,
  "room": "open space",
  "measurement" : 
    {
      "type" : "temperature",
      "min" : minsensor,
      "max" : maxsensor,
      "media" : media,
      "varianza" : varianza
    },
}    
    
datahum = {
  "id": 0,
  "room": "open space",
  "measurement" : 
    {
      "type" : "humidity",
      "min" : minsensor,
      "max" : maxsensor,
      "media" : media,
      "varianza" : varianza
    }
}

#Network
def do_connect():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        print('connecting to network...')
        wlan.connect('prismlab_guest', 'guest-123-prismlab')
        while not wlan.isconnected():
            machine.idle()
    print('network config:', wlan.ipconfig('addr4'))

#configuration
def sub_cb(topic, msg):
  global READ_INTERVAL, READ_PROCESSING, ACTIVE
  if msg != None:
      config = json.loads(msg)
      READ_INTERVAL = int(config["readI"])
      READ_PROCESSING = int(config["readP"])
      ACTIVE = bool(config["activate"])
      print("config: ", config)
      print("actual config: ", READ_INTERVAL, READ_PROCESSING, ACTIVE)

do_connect()
client = MQTTClient(client_id, mqtt_server, mqtt_port, user=mqtt_user, password=mqtt_pass)
client.set_callback(sub_cb)
client.connect()
client.subscribe("sensor/config_" + str(ID))

#Superloop
while True:
  msg = client.check_msg()
  if ACTIVE == True:
    #measurments from sensor
    d.measure()
    temp = d.temperature()
    if temp >= MIN_VALUE and temp <= MAX_VALUE:
      tempVal.append(temp)
      hum = d.humidity()
    if hum >= MIN_VALUE and hum <= MAX_VALUE:
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
        datatemp["measurement"]["min"] = minsensor
        datatemp["measurement"]["max"] = maxsensor
        datatemp["measurement"]["media"] = media
        print("-----STATISTICS-TEMPERATURE-----")
        print("temperature min: ", minsensor)
        print("temperature max: ", maxsensor)
        print("temperature media: ", media)
        for cont in range(0, len(tempVal)):
          varianza += (tempVal[cont] - media) * (tempVal[cont] - media)
        varianza = varianza / len(tempVal)
        print("temperature varianza: ", varianza)
        datatemp["measurement"]["varianza"] = varianza
        print("length array:", len(tempVal))
        tempVal.clear()
        #humidity values
        varianza = 0
        minsensor = min(humVal)
        maxsensor = max(humVal)
        media = sum(humVal) / len(humVal)
        datahum["measurement"]["min"] = minsensor
        datahum["measurement"]["max"] = maxsensor
        datahum["measurement"]["media"] = media
        print("-----STATISTICS-HUMIDITY-----")
        print("humidity min: ", minsensor)
        print("humidity max: ", maxsensor)
        print("humidity media: ", media)
        for cont in range(0, len(humVal)):
          varianza += (humVal[cont] - media) * (humVal[cont] - media)
        varianza = varianza / len(humVal)
        print("humidity varianza: ", varianza)
        datahum["measurement"]["varianza"] = varianza
        print("length array:", len(humVal))
        humVal.clear()
        json_datatemp = json.dumps(datatemp)
        json_datahum = json.dumps(datahum)
        print(json_datatemp)
        print(json_datahum)
        # Publish json
        client.publish(b"sensor/temperature", json_datatemp)
        client.publish(b"sensor/humidity", json_datahum)
  time.sleep(READ_INTERVAL)
 