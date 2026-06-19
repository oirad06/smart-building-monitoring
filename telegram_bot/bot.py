import csv
import io
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from actions import (
    append_action,
    delete_action_row,
    read_actions,
    update_action_row,
)
from rooms import (
    add_room,
    delete_room,
    get_device_room,
    get_room,
    get_room_names,
    remove_device_from_all_rooms,
    room_exists,
    update_room,
)

from dotenv import load_dotenv
from paho.mqtt import client as mqtt_client
from telegram import (
    BotCommand,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

load_dotenv()

# ─────────────────────────────────────────────────────────────────────────────
# Paths & MQTT
# ─────────────────────────────────────────────────────────────────────────────
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

broker = os.getenv("MQTT_BROKER", "130.136.2.70")
port = int(os.getenv("MQTT_PORT", "8080"))
username = os.getenv("MQTT_USER")
password = os.getenv("MQTT_PASS")

# Known ESP32 devices: {device_id: last_seen_epoch}, populated live from the bus.
known_devices: dict[str, float] = {}
mqtt_client: mqtt_client | None = None


def connect_mqtt() -> mqtt_client:
    cid = f"telegram-bot-{os.getpid()}"
    client = mqtt_client.Client(
        client_id=cid,
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
    )
    if username and password:
        client.username_pw_set(username, password)
    client.connect(broker, port)
    return client


def _on_mqtt_message(client, userdata, msg):
    """Live device discovery: learn device IDs from any sensor message + the
    retained discovery snapshot published by the consumer."""
    if msg.topic == "discovery/devices":
        try:
            for dev in json.loads(msg.payload.decode()):
                known_devices.setdefault(dev, time.time())
        except Exception:
            pass
        return
    parts = msg.topic.split("/")
    if len(parts) == 3 and parts[0] == "sensor":
        known_devices[parts[1]] = time.time()


def send_device_config(device_id, read_interval, read_processing, active):
    if mqtt_client is None:
        return
    payload = json.dumps({
        "read_interval": read_interval,
        "read_processing": read_processing,
        "active": active,
    })
    mqtt_client.publish(f"sensor/{device_id}/config", payload)


# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Conversation states (non-overlapping ranges per handler)
# ─────────────────────────────────────────────────────────────────────────────
# /setup
ROOM_NAME, AC_COUNT, DEVICE_SELECTION = range(3)
# /event
EVENT_ROOM, EVENT_PEOPLE, EVENT_COOL, EVENT_HEAT = range(10, 14)
# /devices
DEV_SELECT, DEV_INTERVAL, DEV_PROCESSING, DEV_ACTIVE = range(20, 24)
# /rooms
RM_PICK, RM_MENU, RM_RENAME, RM_AC, RM_DEVICES, RM_DELETE = range(30, 36)
# /events
EV_PAGE, EV_EDIT_PEOPLE, EV_EDIT_COOL, EV_EDIT_HEAT = range(40, 44)

ROOMS_PER_PAGE = 0  # unused sentinel
EVENTS_PER_PAGE = 10


# ─────────────────────────────────────────────────────────────────────────────
# UI helpers
# ─────────────────────────────────────────────────────────────────────────────
def number_keyboard(placeholder="Numero (>= 0)"):
    return ReplyKeyboardMarkup(
        [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["0"]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder=placeholder,
    )


def room_buttons(prefix="room_", extra=None):
    rows = [[InlineKeyboardButton(name, callback_data=f"{prefix}{name}")] for name in get_room_names()]
    if extra:
        rows += extra
    return InlineKeyboardMarkup(rows)


def get_known_devices():
    """Return device IDs seen recently (last 5 min) first, then stale ones."""
    now = time.time()
    fresh = [d for d, t in known_devices.items() if now - t < 300]
    stale = [d for d in known_devices if d not in fresh]
    return fresh + stale


def device_keyboard(devices, assigned, selected):
    rows = []
    for dev in devices:
        room = assigned.get(dev)
        mark = "✅ " if dev in selected else ""
        label = mark + dev + (f" ({room})" if room else "")
        rows.append([InlineKeyboardButton(label, callback_data=dev)])
    rows.append([InlineKeyboardButton("Done", callback_data="done")])
    return InlineKeyboardMarkup(rows)


def _parse_ts(s):
    try:
        return float(s)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromisoformat(str(s)).timestamp()
    except Exception:
        return 0.0


def _fmt_time(s):
    ts = _parse_ts(s)
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S") if ts else "?"


def read_sensors():
    path = DATA_DIR / "sensors.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ─────────────────────────────────────────────────────────────────────────────
# /start, /help, /cancel
# ─────────────────────────────────────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Benvenuto in Smart Building Monitor. Usa /help per la lista comandi."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comandi disponibili:\n"
        "/setup — crea una nuova stanza\n"
        "/rooms — gestisci le stanze (rinomina, AC, dispositivi, elimina)\n"
        "/event — registra un evento (persone + condizionatori)\n"
        "/events — modifica o elimina eventi recenti\n"
        "/devices — configura i sensori ESP32\n"
        "/show — mostra dati sensori + eventi\n"
        "/sensors — scarica sensors.csv\n"
        "/actions — scarica actions.csv\n"
        "/config — scarica rooms.json\n"
        "/cancel — annulla l'operazione in corso"
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("Operazione annullata.")
    else:
        await update.message.reply_text("Operazione annullata.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /setup — create room (name → AC count → device multi-select → save)
# ─────────────────────────────────────────────────────────────────────────────
async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Inserisci il nome della stanza:")
    return ROOM_NAME


async def save_room_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Il nome non può essere vuoto.")
        return ROOM_NAME
    if room_exists(name):
        await update.message.reply_text("Esiste già una stanza con questo nome.")
        return ROOM_NAME
    context.user_data["room_name"] = name
    await update.message.reply_text("Quanti condizionatori ci sono?", reply_markup=ForceReply())
    return AC_COUNT


async def save_ac_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        if count < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Inserisci un numero valido >= 0.")
        return AC_COUNT

    context.user_data["num_ac"] = count
    devices = get_known_devices()
    assigned = {d: get_device_room(d) for d in devices}
    context.user_data["devices"] = devices
    context.user_data["assigned"] = assigned
    context.user_data["selected_devices"] = []

    await update.message.reply_text(
        "Seleziona i dispositivi per questa stanza:",
        reply_markup=device_keyboard(devices, assigned, []),
    )
    return DEVICE_SELECTION


async def save_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.setdefault("selected_devices", [])
    devices = context.user_data["devices"]
    assigned = context.user_data["assigned"]

    if query.data == "done":
        room_name = context.user_data["room_name"]

        # Reassignment guard: warn once, then on confirm strip devices from old rooms.
        warnings = []
        for dev in selected:
            current = get_device_room(dev)
            if current:
                warnings.append(f"{dev} è già assegnato a '{current}'")

        if warnings and not context.user_data.get("confirmed"):
            context.user_data["confirmed"] = True
            await query.message.reply_text(
                "ATTENZIONE:\n" + "\n".join(warnings) + "\n\nPremi di nuovo Done per confermare."
            )
            return DEVICE_SELECTION

        for dev in selected:
            remove_device_from_all_rooms(dev, except_room=room_name)
        add_room(room_name, selected, context.user_data["num_ac"])
        await query.edit_message_text(
            f"✅ Stanza '{room_name}' creata.\n"
            f"Dispositivi: {', '.join(selected) if selected else 'nessuno'}"
        )
        context.user_data.clear()
        return ConversationHandler.END

    # toggle a device
    device = query.data
    if device in selected:
        selected.remove(device)
    else:
        selected.append(device)
    await query.edit_message_reply_markup(
        reply_markup=device_keyboard(devices, assigned, selected)
    )
    return DEVICE_SELECTION


# ─────────────────────────────────────────────────────────────────────────────
# /rooms — manage existing rooms
# ─────────────────────────────────────────────────────────────────────────────
async def rooms_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = get_room_names()
    if not names:
        await update.message.reply_text("Nessuna stanza. Creane una con /setup.")
        return ConversationHandler.END
    await update.message.reply_text("Scegli una stanza:", reply_markup=room_buttons())
    return RM_PICK


async def rooms_show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data[len("room_"):]
    if not room_exists(name):
        await query.edit_message_text("Stanza non più esistente.")
        return ConversationHandler.END
    context.user_data["room"] = name
    room = get_room(name)
    text = (
        f"Stanza: {name}\n"
        f"AC totali: {room['num_ac']}\n"
        f"Dispositivi: {', '.join(room.get('device_ids', [])) or 'nessuno'}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Rinomina", callback_data="rm_rename"),
         InlineKeyboardButton("Cambia AC", callback_data="rm_ac")],
        [InlineKeyboardButton("Assegna dispositivi", callback_data="rm_devices"),
         InlineKeyboardButton("Elimina stanza", callback_data="rm_delete")],
        [InlineKeyboardButton("« Indietro", callback_data="rm_back")],
    ])
    await query.edit_message_text(text, reply_markup=kb)
    return RM_MENU


async def rooms_menu_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    name = context.user_data.get("room")

    if action == "rm_back":
        names = get_room_names()
        await query.edit_message_text("Scegli una stanza:", reply_markup=room_buttons())
        return RM_PICK

    if action == "rm_rename":
        await query.message.reply_text("Nuovo nome della stanza?", reply_markup=ForceReply())
        return RM_RENAME

    if action == "rm_ac":
        await query.message.reply_text("Quanti condizionatori?", reply_markup=number_keyboard())
        return RM_AC

    if action == "rm_devices":
        devices = get_known_devices()
        assigned = {d: get_device_room(d) for d in devices}
        existing = set(get_room(name).get("device_ids", []))
        context.user_data["devices"] = devices
        context.user_data["assigned"] = assigned
        context.user_data["selected_devices"] = list(existing)
        await query.message.reply_text(
            "Seleziona i dispositivi (quelli attivi sono preselezionati):",
            reply_markup=device_keyboard(devices, assigned, list(existing)),
        )
        return RM_DEVICES

    if action == "rm_delete":
        await query.edit_message_text(
            f"Eliminare '{name}'?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Sì", callback_data="rm_del_yes"),
                 InlineKeyboardButton("No", callback_data="rm_del_no")],
            ]),
        )
        return RM_DELETE

    return RM_MENU


async def rooms_rename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    old = context.user_data["room"]
    if not new_name:
        await update.message.reply_text("Nome non valido.")
        return RM_RENAME
    if new_name != old and room_exists(new_name):
        await update.message.reply_text("Esiste già una stanza con questo nome.")
        return RM_RENAME
    room = get_room(old)
    delete_room(old)
    add_room(new_name, room.get("device_ids", []), room.get("num_ac", 0))
    context.user_data["room"] = new_name
    await update.message.reply_text(f"Rinominata in '{new_name}'.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def rooms_ac(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        count = int(update.message.text)
        if count < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido.", reply_markup=number_keyboard())
        return RM_AC
    update_room(context.user_data["room"], num_ac=count)
    await update.message.reply_text("Numero AC aggiornato.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


async def rooms_devices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    selected = context.user_data.setdefault("selected_devices", [])
    devices = context.user_data["devices"]
    assigned = context.user_data["assigned"]
    name = context.user_data["room"]

    if query.data == "done":
        for dev in selected:
            remove_device_from_all_rooms(dev, except_room=name)
        update_room(name, device_ids=selected)
        await query.edit_message_text(
            f"Dispositivi di '{name}': {', '.join(selected) if selected else 'nessuno'}"
        )
        return ConversationHandler.END

    device = query.data
    if device in selected:
        selected.remove(device)
    else:
        selected.append(device)
    await query.edit_message_reply_markup(reply_markup=device_keyboard(devices, assigned, selected))
    return RM_DEVICES


async def rooms_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = context.user_data.get("room")
    if query.data == "rm_del_yes":
        delete_room(name)
        await query.edit_message_text(f"Stanza '{name}' eliminata.")
    else:
        await query.edit_message_text("Operazione annullata.")
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /event — record one event (room → people → AC cool → AC heat → validate)
# ─────────────────────────────────────────────────────────────────────────────
async def event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = get_room_names()
    if not names:
        await update.message.reply_text("Nessuna stanza. Creane una con /setup.")
        return ConversationHandler.END
    await update.message.reply_text("Seleziona la stanza:", reply_markup=room_buttons(prefix="eroom_"))
    return EVENT_ROOM


async def event_room(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data[len("eroom_"):]
    if not room_exists(name):
        await query.edit_message_text("Stanza non valida.")
        return ConversationHandler.END
    context.user_data["room"] = name
    context.user_data["num_ac"] = get_room(name)["num_ac"]
    await query.message.reply_text("Quante persone ci sono?", reply_markup=number_keyboard())
    return EVENT_PEOPLE


async def event_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido.", reply_markup=number_keyboard())
        return EVENT_PEOPLE
    context.user_data["num_people"] = val
    await update.message.reply_text("Quanti AC freddi (cool)?", reply_markup=number_keyboard())
    return EVENT_COOL


async def event_cool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido.", reply_markup=number_keyboard())
        return EVENT_COOL
    context.user_data["num_ac_cool"] = val
    await update.message.reply_text("Quanti AC caldi (heat)?", reply_markup=number_keyboard())
    return EVENT_HEAT


async def event_heat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        heat = int(update.message.text)
        if heat < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido.", reply_markup=number_keyboard())
        return EVENT_HEAT

    room = context.user_data["room"]
    num_ac = context.user_data["num_ac"]
    cool = context.user_data["num_ac_cool"]
    if cool + heat > num_ac:
        await update.message.reply_text(
            f"Cool({cool}) + Heat({heat}) = {cool + heat} > AC totali ({num_ac}). Riprova.",
            reply_markup=number_keyboard(),
        )
        return EVENT_HEAT

    device_ids = get_room(room).get("device_ids", [])
    append_action(
        datetime.now(timezone.utc).isoformat(),
        room, num_ac, context.user_data["num_people"], cool, heat, device_ids,
    )
    off = num_ac - cool - heat
    await update.message.reply_text(
        f"✅ Evento registrato.\n{room}: persone {context.user_data['num_people']} | "
        f"AC {cool}C / {heat}H / {off}F",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /events — list with pagination, edit & delete
# ─────────────────────────────────────────────────────────────────────────────
def _events_page_rows(page=0):
    rows = read_actions()
    total = len(rows)
    start = page * EVENTS_PER_PAGE
    page_rows = rows[start:start + EVENTS_PER_PAGE]
    return page_rows, total, page


def _render_events(update_text_target, page_rows, total, page):
    lines = []
    base = page * EVENTS_PER_PAGE
    for i, r in enumerate(page_rows):
        idx = base + i
        lines.append(
            f"[{idx}] {_fmt_time(r.get('timestamp'))} | {r.get('room')} | "
            f"persone {r.get('num_people')} | AC {r.get('num_ac_cool')}C/"
            f"{r.get('num_ac_heat')}H/{r.get('num_ac_off')}F"
        )
    header = f"Eventi ({total} totali):\n\n" + ("\n".join(lines) if lines else "(nessuno)")
    return header


def _events_nav_keyboard(page, total):
    last_page = max(0, (total - 1) // EVENTS_PER_PAGE) if total else 0
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("« Prec", callback_data=f"ev_pg_{page - 1}"))
    if page < last_page:
        nav.append(InlineKeyboardButton("Succ »", callback_data=f"ev_pg_{page + 1}"))
    rows = [nav] if nav else []
    base = page * EVENTS_PER_PAGE
    for i, _ in enumerate(_events_page_rows(page)[0]):
        idx = base + i
        rows.append([
            InlineKeyboardButton("Modifica", callback_data=f"ev_edit_{idx}"),
            InlineKeyboardButton("Elimina", callback_data=f"ev_del_{idx}"),
        ])
    return InlineKeyboardMarkup(rows)


async def events_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _events_render(update, context, page=0, edit=False)


async def _events_render(update, context, page, edit):
    page_rows, total, page = _events_page_rows(page)
    text = _render_events(None, page_rows, total, page)
    kb = _events_nav_keyboard(page, total)
    if not page_rows:
        kb = None
    context.user_data["page"] = page
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)
    return EV_PAGE


async def events_page_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    if data.startswith("ev_pg_"):
        return await _events_render(update, context, int(data[len("ev_pg_"):]), edit=False)
    await query.answer()
    if data.startswith("ev_del_"):
        idx = int(data[len("ev_del_"):])
        context.user_data["del_idx"] = idx
        await query.edit_message_text(
            "Confermi l'eliminazione?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Sì", callback_data="ev_delc_yes"),
                 InlineKeyboardButton("No", callback_data="ev_delc_no")],
            ]),
        )
        return EV_PAGE
    if data == "ev_delc_yes":
        delete_action_row(context.user_data.pop("del_idx", -1))
        await query.edit_message_text("Riga eliminata.")
        return await _events_render(update, context, context.user_data.get("page", 0), edit=False)
    if data == "ev_delc_no":
        return await _events_render(update, context, context.user_data.get("page", 0), edit=False)
    if data.startswith("ev_edit_"):
        idx = int(data[len("ev_edit_"):])
        rows = read_actions()
        if not (0 <= idx < len(rows)):
            await query.edit_message_text("Riga non valida.")
            return ConversationHandler.END
        context.user_data["edit_idx"] = idx
        context.user_data["num_ac"] = int(rows[idx].get("num_ac", 0))
        await query.message.reply_text("Nuovo numero persone?", reply_markup=number_keyboard())
        return EV_EDIT_PEOPLE
    return EV_PAGE


async def events_edit_people(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido.", reply_markup=number_keyboard())
        return EV_EDIT_PEOPLE
    context.user_data["num_people"] = val
    await update.message.reply_text("Nuovi AC freddi (cool)?", reply_markup=number_keyboard())
    return EV_EDIT_COOL


async def events_edit_cool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido.", reply_markup=number_keyboard())
        return EV_EDIT_COOL
    context.user_data["num_ac_cool"] = val
    await update.message.reply_text("Nuovi AC caldi (heat)?", reply_markup=number_keyboard())
    return EV_EDIT_HEAT


async def events_edit_heat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        heat = int(update.message.text)
        if heat < 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido.", reply_markup=number_keyboard())
        return EV_EDIT_HEAT
    num_ac = context.user_data["num_ac"]
    cool = context.user_data["num_ac_cool"]
    if cool + heat > num_ac:
        await update.message.reply_text(
            f"Cool({cool}) + Heat({heat}) > AC totali ({num_ac}).", reply_markup=number_keyboard()
        )
        return EV_EDIT_HEAT
    off = num_ac - cool - heat
    update_action_row(
        context.user_data["edit_idx"],
        num_people=context.user_data["num_people"],
        num_ac_cool=cool, num_ac_heat=heat, num_ac_off=off,
    )
    await update.message.reply_text("✅ Riga aggiornata.", reply_markup=ReplyKeyboardRemove())
    context.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /devices — list ESP32s + push config
# ─────────────────────────────────────────────────────────────────────────────
async def devices_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    devices = get_known_devices()
    if not devices:
        await update.message.reply_text("Nessun dispositivo rilevato sui bus MQTT.")
        return ConversationHandler.END
    now = time.time()
    lines = []
    rows = []
    for dev in devices:
        room = get_device_room(dev) or "—"
        age = int(now - known_devices.get(dev, now))
        lines.append(f"• {dev} | stanza: {room} | ultimo segnale: {age}s fa")
        rows.append([InlineKeyboardButton(f"Configura {dev}", callback_data=f"devcfg_{dev}")])
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(rows))
    return DEV_SELECT


async def devices_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    dev = query.data[len("devcfg_"):]
    context.user_data["device"] = dev
    await query.message.reply_text("read_interval (secondi)?", reply_markup=number_keyboard())
    return DEV_INTERVAL


async def devices_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido (> 0).", reply_markup=number_keyboard())
        return DEV_INTERVAL
    context.user_data["read_interval"] = val
    await update.message.reply_text("read_processing (numero di letture)?", reply_markup=number_keyboard())
    return DEV_PROCESSING


async def devices_processing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        val = int(update.message.text)
        if val <= 0:
            raise ValueError
    except (ValueError, TypeError):
        await update.message.reply_text("Numero non valido (> 0).", reply_markup=number_keyboard())
        return DEV_PROCESSING
    context.user_data["read_processing"] = val
    await update.message.reply_text(
        "Attivo?",
        reply_markup=ReplyKeyboardMarkup(
            [["Acceso", "Spento"]], resize_keyboard=True, one_time_keyboard=True,
            input_field_placeholder="Acceso o Spento?",
        ),
    )
    return DEV_ACTIVE


async def devices_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text == "acceso":
        active = True
    elif text == "spento":
        active = False
    else:
        await update.message.reply_text("Scegli Acceso o Spento.")
        return DEV_ACTIVE
    send_device_config(
        context.user_data["device"],
        context.user_data["read_interval"],
        context.user_data["read_processing"],
        active,
    )
    await update.message.reply_text(
        f"✅ Configurazione inviata a {context.user_data['device']}.",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


# ─────────────────────────────────────────────────────────────────────────────
# /show, /sensors, /actions, /config — read-only views & downloads
# ─────────────────────────────────────────────────────────────────────────────
def _merge_rows(room_filter=None, limit=10):
    sensors = read_sensors()
    actions = read_actions()
    rows = []
    for r in sensors:
        if room_filter and r.get("room") != room_filter:
            continue
        r["_source"] = "sensor"
        rows.append(r)
    for r in actions:
        if room_filter and r.get("room") != room_filter:
            continue
        r["_source"] = "action"
        rows.append(r)
    rows.sort(key=lambda r: _parse_ts(r.get("timestamp")), reverse=True)
    return rows[:limit]


def _format_merged(rows):
    out = []
    for r in rows:
        t = _fmt_time(r.get("timestamp"))
        room = r.get("room", "")
        if r["_source"] == "sensor":
            out.append(f"{t} | {room} | {r.get('device_id')} | {r.get('type')}: "
                       f"media {r.get('media')} (min {r.get('min')}, max {r.get('max')})")
        else:
            out.append(f"{t} | {room} | persone {r.get('num_people')} | "
                       f"AC {r.get('num_ac_cool')}C/{r.get('num_ac_heat')}H/{r.get('num_ac_off')}F")
    return "\n".join(out) if out else "(nessun dato)"


async def show_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [[InlineKeyboardButton("Tutte le stanze", callback_data="show_all")]]
    for name in get_room_names():
        rows.append([InlineKeyboardButton(name, callback_data=f"show_room_{name}")])
    await update.message.reply_text("Mostra dati (ultimi 10):", reply_markup=InlineKeyboardMarkup(rows))


async def sensors_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [[InlineKeyboardButton("Export completo", callback_data="sensors_all")]]
    for name in get_room_names():
        rows.append([InlineKeyboardButton(name, callback_data=f"sensors_room_{name}")])
    await update.message.reply_text("Scarica sensors:", reply_markup=InlineKeyboardMarkup(rows))


async def actions_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [[InlineKeyboardButton("Export completo", callback_data="actions_all")]]
    for name in get_room_names():
        rows.append([InlineKeyboardButton(name, callback_data=f"actions_room_{name}")])
    await update.message.reply_text("Scarica actions:", reply_markup=InlineKeyboardMarkup(rows))


async def config_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = [[InlineKeyboardButton("Tutte le stanze", callback_data="config_all")]]
    for name in get_room_names():
        rows.append([InlineKeyboardButton(name, callback_data=f"config_room_{name}")])
    await update.message.reply_text("Scarica configurazione:", reply_markup=InlineKeyboardMarkup(rows))


def _csv_filtered_bytes(path, room):
    if not path.exists():
        return None, None
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = [r for r in reader if (room is None or r.get("room") == room)]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=fieldnames or [])
    writer.writeheader()
    writer.writerows(rows)
    return io.BytesIO(out.getvalue().encode()), len(rows)


async def downloads_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Global handler for /show, /sensors, /actions, /config inline choices."""
    query = update.callback_query
    await query.answer()
    data = query.data

    # /show
    if data == "show_all":
        await query.message.reply_text(_format_merged(_merge_rows(None)))
        return
    if data.startswith("show_room_"):
        await query.message.reply_text(_format_merged(_merge_rows(data[len("show_room_"):])))

    # /sensors
    elif data == "sensors_all":
        path = DATA_DIR / "sensors.csv"
        if path.exists() and path.stat().st_size:
            await query.message.reply_document(document=open(path, "rb"), filename="sensors.csv")
        else:
            await query.message.reply_text("sensors.csv vuoto o assente.")
    elif data.startswith("sensors_room_"):
        room = data[len("sensors_room_"):]
        bio, n = _csv_filtered_bytes(DATA_DIR / "sensors.csv", room)
        if bio is None:
            await query.message.reply_text("sensors.csv assente.")
        else:
            await query.message.reply_document(document=bio, filename=f"sensors_{room}.csv")

    # /actions
    elif data == "actions_all":
        path = DATA_DIR / "actions.csv"
        if path.exists() and path.stat().st_size:
            await query.message.reply_document(document=open(path, "rb"), filename="actions.csv")
        else:
            await query.message.reply_text("actions.csv vuoto o assente.")
    elif data.startswith("actions_room_"):
        room = data[len("actions_room_"):]
        bio, n = _csv_filtered_bytes(DATA_DIR / "actions.csv", room)
        if bio is None:
            await query.message.reply_text("actions.csv assente.")
        else:
            await query.message.reply_document(document=bio, filename=f"actions_{room}.csv")

    # /config
    elif data == "config_all":
        path = DATA_DIR / "rooms.json"
        if path.exists() and path.stat().st_size:
            await query.message.reply_document(document=open(path, "rb"), filename="rooms.json")
        else:
            await query.message.reply_text("rooms.json vuoto o assente.")
    elif data.startswith("config_room_"):
        room = data[len("config_room_"):]
        room_cfg = get_room(room)
        if room_cfg is None:
            await query.message.reply_text("Stanza non trovata.")
        else:
            text = json.dumps({room: room_cfg}, indent=2, ensure_ascii=False)
            await query.message.reply_document(
                document=io.BytesIO(text.encode()), filename=f"{room}_config.json"
            )


async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Comando non riconosciuto. Usa /help.")


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────────────────────
async def post_init(application: Application):
    """Register the bot menu and bring up the MQTT discovery client."""
    await application.bot.set_my_commands([
        BotCommand("setup", "Crea una nuova stanza"),
        BotCommand("rooms", "Gestisci le stanze"),
        BotCommand("event", "Registra evento (persone/AC)"),
        BotCommand("events", "Modifica o elimina eventi recenti"),
        BotCommand("devices", "Configura i sensori ESP32"),
        BotCommand("show", "Mostra dati sensori ed eventi"),
        BotCommand("sensors", "Scarica dati sensori"),
        BotCommand("actions", "Scarica dati eventi"),
        BotCommand("config", "Scarica configurazione stanze"),
        BotCommand("cancel", "Annulla operazione"),
    ])
    global mqtt_client
    try:
        mqtt_client = connect_mqtt()
        mqtt_client.on_message = _on_mqtt_message
        mqtt_client.subscribe("sensor/+/+")
        mqtt_client.subscribe("discovery/devices")
        mqtt_client.loop_start()
        logger.info("MQTT discovery client started.")
    except Exception as e:
        logger.warning("MQTT unavailable (%s); device features disabilitate.", e)


def _build_application():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "ERROR: TELEGRAM_BOT_TOKEN not set. Copy .env.example to .env and fill in your bot token."
        )
    app = Application.builder().token(token).post_init(post_init).build()

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            ROOM_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_room_name)],
            AC_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_ac_count)],
            DEVICE_SELECTION: [CallbackQueryHandler(save_devices)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("rooms", rooms_list)],
        states={
            RM_PICK: [CallbackQueryHandler(rooms_show_menu, pattern="^room_")],
            RM_MENU: [CallbackQueryHandler(rooms_menu_action, pattern="^rm_")],
            RM_RENAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, rooms_rename)],
            RM_AC: [MessageHandler(filters.TEXT & ~filters.COMMAND, rooms_ac)],
            RM_DEVICES: [CallbackQueryHandler(rooms_devices)],
            RM_DELETE: [CallbackQueryHandler(rooms_delete, pattern="^rm_del")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("event", event_start)],
        states={
            EVENT_ROOM: [CallbackQueryHandler(event_room, pattern="^eroom_")],
            EVENT_PEOPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_people)],
            EVENT_COOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_cool)],
            EVENT_HEAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_heat)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("events", events_start)],
        states={
            EV_PAGE: [CallbackQueryHandler(events_page_action, pattern="^ev_")],
            EV_EDIT_PEOPLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, events_edit_people)],
            EV_EDIT_COOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, events_edit_cool)],
            EV_EDIT_HEAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, events_edit_heat)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("devices", devices_start)],
        states={
            DEV_SELECT: [CallbackQueryHandler(devices_pick, pattern="^devcfg_")],
            DEV_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, devices_interval)],
            DEV_PROCESSING: [MessageHandler(filters.TEXT & ~filters.COMMAND, devices_processing)],
            DEV_ACTIVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, devices_active)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    ))

    # Read-only views & downloads (no text state → plain handlers + global callback).
    app.add_handler(CommandHandler("show", show_start))
    app.add_handler(CommandHandler("sensors", sensors_download))
    app.add_handler(CommandHandler("actions", actions_download))
    app.add_handler(CommandHandler("config", config_download))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(
        downloads_callback,
        pattern="^(show|sensors|actions|config)_",
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    return app


def main():
    app = _build_application()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
