# Smart Building Monitoring

IoT-based environmental monitoring system with three components:

- **ESP32 firmware** — DHT11 sensor data collection and MQTT publishing (MicroPython)
- **MQTT consumer** — subscribes to sensor topics, persists readings to CSV (Python)
- **Telegram bot** — query sensor data, manage rooms, configure readings via chat (Python)

## Project structure

```
├── esp32_firmware/         MicroPython code for ESP32-S3
├── mqtt_consumer/          Python MQTT subscriber + CSV logger
├── telegram_bot/           Python Telegram bot (python-telegram-bot)
├── .gitignore
└── README.md
```

## Quick start

### Prerequisites

- Python 3.12+ for the consumer and bot
- An ESP32-S3 flashed with MicroPython v1.27+ for the firmware
- MQTT broker reachable at `130.136.2.70:8080` (configurable via env)

### Setup

Each component has its own `requirements.txt` and `README.md` with setup instructions. Secrets are configurable via environment variables — see `.env.example` files for the available options.

```bash
cd <component>
pip install -r requirements.txt
```
