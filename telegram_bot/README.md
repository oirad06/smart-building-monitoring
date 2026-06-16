# Telegram Bot

Conversational bot for managing room-based sensor configurations via Telegram.

## Features

- `/setup` — register a new room (name, AC unit count, CSV file path)
- `/npersone` — set occupant count for a room
- `/ncondizionatori` — set AC unit counts (hot/cold) for a room
- `/deleteroom` — remove a room and its data
- `/clearline` — delete the last CSV row for a room
- `/help` — list all commands

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in this directory (copy from `.env.example`) and set `TELEGRAM_BOT_TOKEN` to your bot token from [@BotFather](https://t.me/BotFather). The bot will exit with an error if the token is not set.

```bash
python bot.py
```

## Data

- Room registry stored in `data/stanze.json`
- Per-room CSV files stored in `data/`
- See `data/` for persisted readings
