"""Soglie di allerta per stanza + notifiche Telegram in tempo reale.

Le soglie vivono in rooms.json per stanza (temp_min/temp_max/hum_min/hum_max,
None/assenti = non impostate) e si configurano con /alerts. Un listener MQTT
osserva le misure e invia un avviso a ALERT_CHAT_ID quando un valore esce dal
range, con debounce per non spammare durante una violazione sostenuta.
"""

import json
import logging
import os

from rooms import get_device_room, get_room, update_room
from telegram import Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
)

logger = logging.getLogger(__name__)

# Conversation states (owned range: 50-59).
AL_ROOM = 50
AL_MENU = 51
AL_VALUE = 52

# Threshold key per (measurement type, bound).
_KEYS = {
    "temperature": ("temp_min", "temp_max"),
    "humidity": ("hum_min", "hum_max"),
}

# Italian labels for each threshold key.
_LABELS = {
    "temp_min": "Temp. min",
    "temp_max": "Temp. max",
    "hum_min": "Umidità min",
    "hum_max": "Umidità max",
}

# Debounce state: (room, mtype) -> bool (True if currently in breach).
_alert_state = {}


def _unit(mtype):
    return "°C" if mtype == "temperature" else "%"


def check_reading(room, mtype, value):
    """Return an Italian breach message on transition ok->breach, else None.

    Resets internal state when the value returns within range, so a sustained
    breach does not spam but a fresh breach after recovery alerts again.
    """
    keys = _KEYS.get(mtype)
    if keys is None:
        return None
    cfg = get_room(room) or {}
    kmin, kmax = keys
    vmin = cfg.get(kmin)
    vmax = cfg.get(kmax)

    breached = None
    if vmin is not None and value < vmin:
        breached = ("min", vmin)
    elif vmax is not None and value > vmax:
        breached = ("max", vmax)

    state_key = (room, mtype)
    if breached is None:
        _alert_state[state_key] = False
        return None

    if _alert_state.get(state_key):
        # Already in breach — debounce.
        return None
    _alert_state[state_key] = True

    label = "temperatura" if mtype == "temperature" else "umidità"
    unit = _unit(mtype)
    bound, limit = breached
    if bound == "min":
        return (
            f"⚠️ Allerta {label} in «{room}»: {value}{unit} "
            f"sotto la soglia minima ({limit}{unit})."
        )
    return (
        f"⚠️ Allerta {label} in «{room}»: {value}{unit} "
        f"sopra la soglia massima ({limit}{unit})."
    )


def _on_message(topic, payload, parts):
    """MQTT listener (paho thread): detect breaches and schedule an alert."""
    # parts == ["sensor", device_id, kind]
    if len(parts) != 3 or parts[0] != "sensor":
        return
    mtype = parts[2]
    if mtype not in _KEYS:
        return
    device_id = parts[1]
    try:
        data = json.loads(payload)
        value = data["measurement"]["media"]
    except (ValueError, KeyError, TypeError):
        return
    room = get_device_room(device_id)
    if room is None:
        return
    message = check_reading(room, mtype, value)
    if message:
        import bot
        bot.run_on_bot_loop(_send_alert(message))


async def _send_alert(text):
    chat_id = os.getenv("ALERT_CHAT_ID")
    if not chat_id:
        logger.warning("ALERT_CHAT_ID non impostato; avviso non inviato: %s", text)
        return
    import bot
    await bot._app.bot.send_message(chat_id=int(chat_id), text=text)


# ---------------------------------------------------------------------------
# /alerts conversation
# ---------------------------------------------------------------------------

def _fmt(v, unit):
    return f"{v}{unit}" if v is not None else "—"


def _menu_text(room):
    cfg = get_room(room) or {}
    t = f"{_fmt(cfg.get('temp_min'), '')}–{_fmt(cfg.get('temp_max'), '')}"
    h = f"{_fmt(cfg.get('hum_min'), '')}–{_fmt(cfg.get('hum_max'), '')}"
    return (
        f"Soglie di allerta «{room}»\n"
        f"🌡️ {t} °C\n"
        f"💧 {h} %\n\n"
        "Tocca una soglia per impostarla o azzerarla."
    )


def _menu_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    import bot
    rows = [
        [
            InlineKeyboardButton("🌡️ ⬇ imposta", callback_data="al_set_tmin"),
            InlineKeyboardButton("🌡️ ⬆ imposta", callback_data="al_set_tmax"),
        ],
        [
            InlineKeyboardButton("💧 ⬇ imposta", callback_data="al_set_hmin"),
            InlineKeyboardButton("💧 ⬆ imposta", callback_data="al_set_hmax"),
        ],
        [
            InlineKeyboardButton("🗑️ 🌡️⬇", callback_data="al_clear_temp_min"),
            InlineKeyboardButton("🗑️ 🌡️⬆", callback_data="al_clear_temp_max"),
        ],
        [
            InlineKeyboardButton("🗑️ 💧⬇", callback_data="al_clear_hum_min"),
            InlineKeyboardButton("🗑️ 💧⬆", callback_data="al_clear_hum_max"),
        ],
        [bot.back_button("al_back_rooms")],
        [bot.cancel_button()],
    ]
    return InlineKeyboardMarkup(rows)


# callback al_set_* -> threshold key being edited.
_SET_KEY = {
    "al_set_tmin": "temp_min",
    "al_set_tmax": "temp_max",
    "al_set_hmin": "hum_min",
    "al_set_hmax": "hum_max",
}


async def alerts_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    context.user_data.clear()
    await update.message.reply_text(
        "Per quale stanza vuoi configurare le soglie di allerta?",
        reply_markup=bot.room_buttons(prefix="al_room_", extra=[[bot.cancel_button()]]),
    )
    return AL_ROOM


async def alerts_pick_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    query = update.callback_query
    if query.data == bot.CANCEL_DATA:
        return await bot.cancel(update, context)
    await query.answer()
    room = query.data[len("al_room_"):]
    context.user_data["al_room"] = room
    await query.edit_message_text(_menu_text(room), reply_markup=_menu_keyboard())
    return AL_MENU


async def alerts_menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    query = update.callback_query
    if query.data == bot.CANCEL_DATA:
        return await bot.cancel(update, context)
    await query.answer()
    data = query.data
    room = context.user_data.get("al_room")

    if data == "al_back_rooms":
        context.user_data.pop("al_key", None)
        await query.edit_message_text(
            "Per quale stanza vuoi configurare le soglie di allerta?",
            reply_markup=bot.room_buttons(prefix="al_room_", extra=[[bot.cancel_button()]]),
        )
        return AL_ROOM

    if data.startswith("al_clear_"):
        key = data[len("al_clear_"):]
        update_room(room, **{key: None})
        await query.edit_message_text(
            f"{_LABELS.get(key, key)} azzerata.\n\n" + _menu_text(room),
            reply_markup=_menu_keyboard(),
        )
        return AL_MENU

    if data in _SET_KEY:
        key = _SET_KEY[data]
        context.user_data["al_key"] = key
        await query.edit_message_text(
            f"Inserisci il nuovo valore per «{_LABELS[key]}» (numero):"
        )
        await query.message.reply_text(
            "In attesa del valore…",
            reply_markup=bot.number_keyboard(),
        )
        return AL_VALUE

    return AL_MENU


async def alerts_set_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot
    room = context.user_data.get("al_room")
    key = context.user_data.get("al_key")
    text = (update.message.text or "").strip().replace(",", ".")
    try:
        value = float(text)
    except ValueError:
        await update.message.reply_text(
            "Valore non valido. Inserisci un numero:",
            reply_markup=bot.number_keyboard(),
        )
        return AL_VALUE
    update_room(room, **{key: value})
    context.user_data.pop("al_key", None)
    await update.message.reply_text(
        f"{_LABELS[key]} impostata a {value}.",
        reply_markup=bot.ReplyKeyboardRemove(),
    )
    await update.message.reply_text(_menu_text(room), reply_markup=_menu_keyboard())
    return AL_MENU


def install(app):
    import bot
    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("alerts", alerts_start)],
        states={
            AL_ROOM: [CallbackQueryHandler(alerts_pick_room, pattern="^al_room_")],
            AL_MENU: [CallbackQueryHandler(alerts_menu_action, pattern="^al_")],
            AL_VALUE: [MessageHandler(bot.TEXT_INPUT, alerts_set_value)],
        },
        fallbacks=bot._cancel_fallbacks(),
    ))
    bot.register_message_listener(_on_message)
