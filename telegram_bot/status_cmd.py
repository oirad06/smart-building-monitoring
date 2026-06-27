"""/status — riepilogo salute del sistema (comando one-shot)."""
import asyncio
import time

from telegram import Update, ReplyKeyboardRemove
from telegram.ext import CommandHandler, ContextTypes

from rooms import get_room_names

FRESH_SECS = 300


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot  # lazy: avoid import cycle / use live module state

    mqtt_line = "connesso" if bot.mqtt_client is not None else "non disponibile"

    now = time.time()
    known = bot.get_known_devices()
    total = len(known)
    fresh = sum(1 for t in bot.known_devices.values() if now - t < FRESH_SECS)
    stale = total - fresh

    rows = await asyncio.to_thread(bot.read_sensors)
    last_ts = 0.0
    for r in rows:
        ts = bot._parse_ts(r.get("timestamp"))
        if ts > last_ts:
            last_ts = ts
    if last_ts > 0:
        age = int(now - last_ts)
        sensori_line = f"{age}s fa"
    else:
        sensori_line = "nessun dato"

    n_rooms = len(get_room_names())

    text = (
        "📊 Stato del sistema\n"
        f"• MQTT: {mqtt_line}\n"
        f"• Dispositivi noti: {total} (freschi: {fresh}, inattivi: {stale})\n"
        f"• Ultimo dato sensori: {sensori_line}\n"
        f"• Stanze configurate: {n_rooms}"
    )
    await update.message.reply_text(text, reply_markup=ReplyKeyboardRemove())


def install(app):
    app.add_handler(CommandHandler("status", status))
