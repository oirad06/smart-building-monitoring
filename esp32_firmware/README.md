# ESP32 Firmware

MicroPython firmware for the ESP32 / ESP32-S3 that reads a DHT11
temperature/humidity sensor and publishes aggregated statistics over MQTT.

## Hardware

- ESP32 or ESP32-S3 board
- DHT11 sensor connected to GPIO pin 4

## Dependencies

- MicroPython v1.28+ (prebuilt images: `mp-esp32-v1.28.0.bin`, `mp-esp32s3-v1.28.0.bin`)
- `umqttsimple.py` — lightweight MQTT client (included in this directory)
- `secrets.py` — credentials file (must be created from `secrets.py.example`)

## Setup

1. Flash MicroPython firmware to the board:
   ```bash
   pip install mpremote esptool
   # ESP32-S3:
   mpremote connect <PORT> erase_flash
   esptool --chip esp32s3 --port <PORT> write_flash -z 0x0 mp-esp32s3-v1.28.0.bin
   ```

2. Copy the source files to the device:
   ```bash
   mpremote cp main.py :main.py
   mpremote cp umqttsimple.py :umqttsimple.py
   ```

3. Create `secrets.py` from the template and copy it to the device:
   ```bash
   cp secrets.py.example secrets.py
   # edit secrets.py with your WiFi + MQTT credentials
   mpremote cp secrets.py :secrets.py
   ```
   The firmware exits with an error if `secrets.py` is missing.

4. Reset the device — it runs `main.py` on boot.

## Behavior

- Connects to WiFi, then to the MQTT broker
- Device identity is the chip's unique silicon ID: `ubinascii.hexlify(machine.unique_id()).decode()`
- Reads the DHT11 at `READ_INTERVAL` (1s) intervals; computes
  min/max/mean/variance over a `READ_PROCESSING` (10-read) window
- Publishes JSON to per-device topics:
  - `sensor/{device_id}/temperature`
  - `sensor/{device_id}/humidity`
  - Payload: `{ device_id, measurement: { type, min, max, media, varianza } }`
- Subscribes to `sensor/{device_id}/config` for runtime config. The payload
  (sent by the bot) uses these keys:
  ```json
  { "read_interval": 1, "read_processing": 10, "active": true }
  ```
