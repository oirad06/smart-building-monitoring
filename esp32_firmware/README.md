# ESP32 Firmware

MicroPython firmware for the ESP32-S3 that reads DHT11 temperature/humidity sensor data and publishes it via MQTT.

## Hardware

- ESP32-S3 board
- DHT11 sensor connected to GPIO pin 4

## Dependencies

- MicroPython v1.27+ (see `firmware.bin` for the prebuilt image)
- `umqttsimple.py` — lightweight MQTT client (included in this directory)
- `secrets.py` — credentials file (must be created from `secrets.py.example`)

## Setup

1. Flash MicroPython firmware to the ESP32-S3:
   ```bash
   pip install mpremote
   mpremote flash firmware.bin
   ```

2. Copy the source files to the device:
   ```bash
   mpremote cp main.py :main.py
   mpremote cp umqttsimple.py :umqttsimple.py
   ```

3. Create `secrets.py` from the template and copy it to the device:
   ```bash
   cp secrets.py.example secrets.py
   # edit secrets.py with your MQTT credentials
   mpremote cp secrets.py :secrets.py
   ```
   The firmware will exit with an error if `secrets.py` is missing.

4. Reset the device — it runs `main.py` on boot.

## Behavior

- Connects to WiFi, then to the MQTT broker
- Reads DHT11 at `READ_INTERVAL` (1s) intervals
- Computes min/max/mean/variance over `READ_PROCESSING` (10s) windows
- Publishes aggregated readings to `sensor/temperature` and `sensor/humidity`
- Accepts runtime config changes via `sensor/config_0` subscription
