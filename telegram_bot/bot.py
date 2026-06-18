import logging
import csv
import os
from datetime import datetime
from pathlib import Path
import json
import random 
import threading  

from actions import read_actions
from rooms import add_room, room_exists, get_room_names, get_device_room
from dotenv import load_dotenv  
from paho.mqtt import client as mqtt_client
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, ForceReply, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
    CallbackQueryHandler,
)


load_dotenv()

broker = os.getenv("MQTT_BROKER", "130.136.2.70")
port = int(os.getenv("MQTT_PORT", "8080"))

# Generate a Client ID with the subscribe prefix.
client_id = f'subscribe-{random.randint(0, 100)}'
username = os.getenv("MQTT_USER")
password = os.getenv("MQTT_PASS")
if not all([broker, username, password]):
    raise SystemExit("ERROR: MQTT_BROKER, MQTT_USER, and MQTT_PASS must be set. Copy .env.example to .env and configure your MQTT credentials.")

def connect_mqtt() -> mqtt_client:
    def on_connect(client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("Connected to MQTT Broker!")
        else:
            print(f"Failed to connect, return code {reason_code}")

    client = mqtt_client.Client(
        client_id=client_id,
        callback_api_version=mqtt_client.CallbackAPIVersion.VERSION2,
    )
    client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.connect(broker, port)
    return client

client = connect_mqtt()
client.subscribe("sensor/+/+")
client.loop_start()

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Conversation states
(ROOM_NAME, NCOND, DEVICE_SELECTION,STANZAP,NPERSONE,STANZAC,NCONDFREDDI,NCONDCALDI,STANZACLEAR,DELETEROOM,DEVICE_SELECTIONC,NREADINTERVAL,NREADPROCESSING,NACTIVE) = range(14)
DATA_DIR = Path("../data")
DATA_DIR.mkdir(exist_ok=True)
REGISTRY_FILE = DATA_DIR / "stanze.json"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    await update.message.reply_html(
        rf"Buondì {user.mention_html()}!\n"
        "Sono il temperature_events_bot.\n\n"
        "Usa /setup per inserire una nuova rilevazione.",
        reply_markup=ForceReply(selective=True),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    command_list = [["/setup", "/npersone", "/ncondizionatori", "/cancel", "/clearline",  "/help", "/deleteroom"]]
    await update.message.reply_text(
        "comandi:\n"
        "-/setup - nuova rilevazione (per creare nuovo file stanza)\n"
        " per aggiornare dati nelle stanze:\n"
        "-/npersone - numero persone in una stanza\n"
        "-/ncondizionatori - numero condizionatori freddi e caldi in una stanza\n"
        " per vedere i comandi:\n"
        "-/help - mostra i comandi (il comando più utile del mondo!!!)\n"
        " per annullare un'operazione:\n"
        "-/cancel - annulare un comando\n"
        "-/clearline - rimuovere una riga di un file o nel caso non ci sia scritto nulla eliminare il file\n"
        "-/deleteroom - elimina una stanza e il relativo file csv\n"
        "-/sensorsdownload - download il relativo file csv\n"
        "-/actionsdownload - download il relativo file csv\n"
        "-/roomsdownload - download il relativo file json\n"
        "-/config - config degli esp32",
        reply_markup=ReplyKeyboardMarkup(
          command_list, one_time_keyboard=True, input_field_placeholder="scegli un comando"
          ),
    )

def carica_stanze():
    if not REGISTRY_FILE.exists():
        return {}

    with open(REGISTRY_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salva_stanze(stanze):
    with open(REGISTRY_FILE, "w", encoding="utf-8") as f:
        json.dump(stanze, f, indent=4, ensure_ascii=False)


def registra_stanza(nome_stanza, ncond, csv_file):
    stanze = carica_stanze()

    stanze[nome_stanza] = {
        "csv_file": str(csv_file),
        "ncond": ncond
    }

    salva_stanze(stanze)


def get_reply_keyboard():
    rooms = get_room_names()

    if not rooms:
        return []  # IMPORTANT: never fake a button

    keyboard = []
    row = []

    for room in rooms:
        row.append(room)

        if len(row) == 2:
            keyboard.append(row)
            row = []

    if row:
        keyboard.append(row)

    return keyboard

def rimuovi_ultima_riga(csv_file):
    with open(csv_file, "r", encoding="utf-8") as f:
        righe = list(csv.reader(f))

    if len(righe) <= 1:
        return False

    righe.pop()

    with open(csv_file, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(righe)

    return True

async def clearline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Di quale stanza vuoi cancellare l'ultima rilevazione?",
        reply_markup=ReplyKeyboardMarkup(
            get_reply_keyboard(),
            one_time_keyboard=True
        )
    )
    return STANZACLEAR

async def salva_clearline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stanza = update.message.text.strip()
    csv_file = DATA_DIR / f"actions.csv"

    if not csv_file.exists():
        await update.message.reply_text(
            "File non trovato.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    if rimuovi_ultima_riga(csv_file):
        await update.message.reply_text(
            "Ultima riga eliminata.",
            reply_markup=ReplyKeyboardRemove()
        )
    else:
        await update.message.reply_text(
            "Il file è vuoto.",
            reply_markup=ReplyKeyboardRemove()
        )
        
    return ConversationHandler.END

def leggi_ultima_riga(csv_file):
    try:
        with open(csv_file, "r", encoding="utf-8") as file:
            righe = list(csv.DictReader(file))

        if not righe:
            return None

        return righe[-1]

    except Exception:
        return None

def elimina_stanza(nome_stanza):
    stanze = carica_stanze()

    if nome_stanza not in stanze:
        return False

    del stanze[nome_stanza]
    salva_stanze(stanze)

    return True

async def deleteroom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Quale stanza vuoi eliminare?",
        reply_markup=ReplyKeyboardMarkup(
            get_reply_keyboard(),
            one_time_keyboard=True
        )
    )

    return DELETEROOM

async def salva_deleteroom(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stanza = update.message.text.strip()

    stanze = carica_stanze()

    if stanza not in stanze:
        await update.message.reply_text(
            "Stanza non trovata.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    csv_file = Path(stanze[stanza]["csv_file"])

    if csv_file.exists():
        csv_file.unlink()  # elimina il csv

    elimina_stanza(stanza)

    await update.message.reply_text(
        f"Stanza '{stanza}' eliminata.",
        reply_markup=ReplyKeyboardRemove()
    )

    return ConversationHandler.END

async def sensors_download(update, context):
    if not (DATA_DIR / "sensors.csv").exists() or (DATA_DIR / "sensors.csv").stat().st_size == 0:
        await update.message.reply_text("Il file sensors.csv non esiste.")
    else:
        await update.message.reply_document(document=open("../data/sensors.csv", "rb"))

async def actions_download(update, context):
    if not (DATA_DIR / "actions.csv").exists() or (DATA_DIR / "actions.csv").stat().st_size == 0:
        await update.message.reply_text("Il file actions.csv non esiste.")
    else:
        await update.message.reply_document(document=open("../data/actions.csv", "rb"))

async def rooms_download(update, context):
    if not (DATA_DIR / "rooms.json").exists() or (DATA_DIR / "rooms.json").stat().st_size == 0:
        await update.message.reply_text("Il file rooms.json non esiste.")
    else:
        await update.message.reply_document(document=open("../data/rooms.json", "rb"))

def send_device_config(device_id, read_interval, read_processing, active):
    payload = json.dumps({
        "read_interval": read_interval,
        "read_processing": read_processing,
        "active": active
    })

    client.publish(f"sensor/{device_id}/config", payload)

async def rooms_list(update, context):
    rooms = get_room_names()
    keyboard = [[InlineKeyboardButton(name, callback_data=f"room_{name}")] for name in rooms]
    await update.message.reply_text("Scegli una stanza:", reply_markup=InlineKeyboardMarkup(keyboard))

def build_device_selection_keyboard(devices, assigned):
    keyboard = []
    for device in devices:
        room = assigned.get(device)
        label = f"{device} ({room})" if room else device
        keyboard.append([InlineKeyboardButton(label, callback_data=device)])
    keyboard.append([InlineKeyboardButton("Done", callback_data="done")])
    return InlineKeyboardMarkup(keyboard)

def get_known_devices():
    result = []
    event = threading.Event()

    def on_message(client, userdata, msg):
        nonlocal result
        result = json.loads(msg.payload.decode())
        event.set()

    c = connect_mqtt()
    c.subscribe("discovery/devices")
    c.on_message = on_message
    c.loop_start()

    event.wait(timeout=3)

    c.loop_stop()
    c.disconnect()

    return result
#setup 
async def setup_start(update, context):
    await update.message.reply_text("Inserisci il nome della stanza:")
    return ROOM_NAME

async def save_room_name(update, context):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Il nome non può essere vuoto.")
        return ROOM_NAME
    if room_exists(name):
        await update.message.reply_text("Esiste già una stanza con questo nome.")
        return ROOM_NAME
    
    context.user_data["room_name"] = name
    await update.message.reply_text("Quanti condizionatori ci sono?", reply_markup=ForceReply())
    return NCOND

async def save_ac_count(update, context):
    try:
        count = int(update.message.text)

        if count < 0:
            raise ValueError

        context.user_data["num_ac"] = count

        devices = get_known_devices()
        assigned = {dev: get_device_room(dev) for dev in devices}

        context.user_data["devices"] = devices
        context.user_data["assigned"] = assigned
        context.user_data["selected_devices"] = []

        keyboard = build_device_selection_keyboard(devices, assigned)

        await update.message.reply_text(
            "Seleziona i dispositivi per questa stanza:",
            reply_markup=keyboard
        )

        return DEVICE_SELECTION

    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NCOND

async def save_devices(update, context):
    query = update.callback_query
    await query.answer()

    selected = context.user_data.setdefault("selected_devices", [])
    devices = context.user_data["devices"]
    assigned = context.user_data["assigned"]

    if query.data == "done":

        room_name = context.user_data["room_name"]
        num_ac = context.user_data["num_ac"]

        warnings = []

        for dev in selected:
            current_room = get_device_room(dev)

            if current_room:
                warnings.append(
                    f"{dev} è già assegnato alla stanza '{current_room}'"
                )

        if warnings and not context.user_data.get("confirmed"):
            context.user_data["confirmed"] = True

            await query.message.reply_text(
                "ATTENZIONE:\n"
                + "\n".join(warnings)
                + "\n\nPremi di nuovo Done per confermare."
            )

            return DEVICE_SELECTION

        add_room(room_name, selected, num_ac)

        await query.edit_message_text(
            f"✅ Stanza '{room_name}' creata.\n"
            f"Dispositivi associati: {', '.join(selected) if selected else 'nessuno'}"
        )

        return ConversationHandler.END

    device = query.data

    if device in selected:
        selected.remove(device)
    else:
        selected.append(device)

    keyboard = []

    for dev in devices:
        room = assigned.get(dev)

        if dev in selected:
            prefix = "✅ "
        else:
            prefix = ""

        label = prefix + dev

        if room:
            label += f" ({room})"

        keyboard.append(
            [InlineKeyboardButton(label, callback_data=dev)]
        )

    keyboard.append(
        [InlineKeyboardButton("Done", callback_data="done")]
    )

    await query.edit_message_reply_markup(
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

    return DEVICE_SELECTION

async def salva_ncond(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valore = int(update.message.text)

        if valore < 0:
            raise ValueError

        context.user_data["ncond"] = valore

        csv_file_events = DATA_DIR / f"actions.csv"
        
        registra_stanza(
          context.user_data["stanza"],
          context.user_data["ncond"],
          csv_file_events
        )

        file_exists = csv_file_events.exists()
        
        with open(csv_file_events, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)

            if not file_exists:
                writer.writerow([
                    "timestamp",
                    "stanza",
                    "ncond",
                    "npersone",
                    "ncondfreddi",
                    "ncondcaldi",
                    "ncondspenti"
                ])

            writer.writerow([
                datetime.now().isoformat(),
                context.user_data["stanza"],
                context.user_data["ncond"],
                context.user_data["npersone"],
                context.user_data["ncondfreddi"],
                context.user_data["ncondcaldi"],
                context.user_data["ncond"] - (context.user_data["ncondfreddi"] + context.user_data["ncondcaldi"])
            ])

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NCOND

# STEP 1
async def npersone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Di quale stanza stiamo raccogliendo i dati?",
        reply_markup=ReplyKeyboardRemove()   # reset first
    )

    await update.message.reply_text(
        "Seleziona la stanza:",
        reply_markup=ReplyKeyboardMarkup(
            get_reply_keyboard(),
            resize_keyboard=True,
            one_time_keyboard=True
        ),
    )

    return STANZAP

# STEP 2
async def salva_stanzap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stanza = update.message.text.strip()

    rooms = get_room_names()

    if stanza not in rooms:
        await update.message.reply_text(
            "Stanza non valida. Usa una delle stanze disponibili.",
            reply_markup=ReplyKeyboardRemove()
        )
        return ConversationHandler.END

    context.user_data["stanza"] = stanza

    await update.message.reply_text(
        "Quante persone ci sono nella stanza?",
        reply_markup=ReplyKeyboardRemove()
    )

    return NPERSONE

async def salva_npersone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valore = int(update.message.text)

        if valore < 0:
            raise ValueError

        context.user_data["npersone"] = valore

        csv_file_events = DATA_DIR / f"actions.csv"
        
        if not csv_file_events.exists():
            open(csv_file_events, "w").close()  # create empty file if it doesn't exist

        ultima = leggi_ultima_riga(csv_file_events)

        with open(csv_file_events, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)

            writer.writerow([
                datetime.now().isoformat(),
                context.user_data["stanza"],
                -1,
                context.user_data["npersone"],
                -1,
                -1,
                -1,
            ])

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NPERSONE

async def ncondizionatori(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Di quale stanza stiamo raccogliendo i dati?",
        reply_markup=ReplyKeyboardMarkup(
          get_reply_keyboard(), one_time_keyboard=True, input_field_placeholder="Quale stanza?"
          ),
        )
    return STANZAC


# STEP 2
async def salva_stanzac(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stanza"] = update.message.text.strip()


    await update.message.reply_text(
        "Quanti condizionatori freddi ci sono nella stanza?",
        reply_markup=ReplyKeyboardRemove()
    )

    return NCONDFREDDI

async def salva_ncondfreddi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
      valore = int(update.message.text)

      if valore < 0:
        raise ValueError

      context.user_data["ncondfreddi"] = valore


      await update.message.reply_text(
        "Quanti condizionatori caldi ci sono nella stanza?"
      )
      return NCONDCALDI
    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NCONDFREDDI

async def salva_ncondcaldi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valore = int(update.message.text)

        if valore < 0:
            raise ValueError
        
        csv_file_events = DATA_DIR / f"actions.csv"

        if not csv_file_events.exists():
            open(csv_file_events, "w").close()  # create empty file if it doesn't exist

        ultima = leggi_ultima_riga(csv_file_events)

        if context.user_data["ncondfreddi"] + valore > int(ultima["ncond"]):
          await update.message.reply_text("Il numero di condizionatori accesi supera il totale.")
          return NCONDCALDI

        context.user_data["ncondcaldi"] = valore


        ultima = leggi_ultima_riga(csv_file_events)
        with open(csv_file_events, "a", newline="", encoding="utf-8") as file:
              writer = csv.writer(file)

              writer.writerow([
                datetime.now().isoformat(),
                context.user_data["stanza"],
                -1,
                -1,
                context.user_data["ncondfreddi"],
                context.user_data["ncondcaldi"],
                -1
              ])

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NCONDCALDI

async def config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    devices = get_known_devices()
    assigned = {dev: get_device_room(dev) for dev in devices}
    if not devices:
      await update.message.reply_text(
        "Nessun dispositivo trovato."
      )
      return ConversationHandler.END
    await update.message.reply_text(
        "Seleziona un device:",
        reply_markup=build_device_selection_keyboard(devices, assigned)
    )
    return DEVICE_SELECTIONC


# STEP 2
async def salva_device(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "done":
      await query.answer("Seleziona un dispositivo", show_alert=True)
      return DEVICE_SELECTIONC
    context.user_data["device"] = query.data

    await query.edit_message_text(
      f"Dispositivo selezionato: {query.data}"
    )

    return NREADINTERVAL

async def salva_nreadinterval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
      valore = int(update.message.text)

      if valore < 0:
        raise ValueError

      context.user_data["nreadinterval"] = valore


      await update.message.reply_text(
        "Ogni quanti readinterval scrivere dati?"
      )
      return NREADPROCESSING
    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NREADINTERVAL
    
async def salva_nreadprocessing(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
      valore = int(update.message.text)

      if valore < 0:
        raise ValueError

      context.user_data["nreadprocessing"] = valore


      await update.message.reply_text(
        "Acceso o spento?",
        reply_markup=ReplyKeyboardMarkup(
          [["Acceso", "Spento"]], one_time_keyboard=True, input_field_placeholder="Acceso o Spento?"
          )
      )
      return NACTIVE
    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NREADPROCESSING

async def salva_nactive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        text = update.message.text.strip().lower()

        if text == "acceso":
            active = True
        elif text == "spento":
            active = False
        else:
            await update.message.reply_text(
                "Scegli Acceso o Spento."
            )
            return NACTIVE
        
        context.user_data["active"] = active

        send_device_config(
            context.user_data["device"],
            context.user_data["nreadinterval"],
            context.user_data["nreadprocessing"],
            context.user_data["active"])
        await update.message.reply_text(
            "Configurazione inviata."
        )

        return ConversationHandler.END
    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NACTIVE

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Operazione annullata."
    )
    return ConversationHandler.END

async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Comando non riconosciuto. Usa /help."
    )


def main():
    # Read token from environment — set TELEGRAM_BOT_TOKEN in .env
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise SystemExit("ERROR: TELEGRAM_BOT_TOKEN not set. Copy .env.example to .env and fill in your bot token.")

    application = Application.builder().token(TOKEN).build()

    conv_handler_stanza = ConversationHandler(
        entry_points=[
            CommandHandler("setup", setup_start)
        ],
        states={
            ROOM_NAME: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_room_name
                )
            ],
            NCOND: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    save_ac_count
                )
            ],
            DEVICE_SELECTION: [
                CallbackQueryHandler(
                    save_devices
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )

    conv_handler_persone = ConversationHandler(
        entry_points=[
            CommandHandler("npersone", npersone)
        ],
        states={
            STANZAP: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_stanzap
                )
            ],
            NPERSONE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_npersone
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )

    conv_handler_condizionatori = ConversationHandler(
        entry_points=[
            CommandHandler("ncondizionatori", ncondizionatori)
        ],
        states={
            STANZAC: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_stanzac
                )
            ],
            NCONDFREDDI: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_ncondfreddi
                )
            ],
            NCONDCALDI: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_ncondcaldi
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )

    conv_handler_clearline = ConversationHandler(
        entry_points=[
            CommandHandler("clearline", clearline)
        ],
        states={
            STANZACLEAR: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_clearline
                )
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )
    conv_handler_deleteroom = ConversationHandler(
        entry_points=[
            CommandHandler("deleteroom", deleteroom)
        ],
        states={
            DELETEROOM: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_deleteroom
                )
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )
    conv_handler_config = ConversationHandler(
        entry_points=[
            CommandHandler("config", config)
        ],
        states={
            DEVICE_SELECTIONC: [
                CallbackQueryHandler(salva_device)
            ],
            NREADINTERVAL: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_nreadinterval
                )
            ],
            NREADPROCESSING: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_nreadprocessing
                )
            ],
            NACTIVE: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_nactive
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel)
        ],
    )
    


    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rooms", rooms_list))
    application.add_handler(CommandHandler("sensorsdownload", sensors_download))
    application.add_handler(CommandHandler("actionsdownload", actions_download))
    application.add_handler(CommandHandler("roomsdownload", rooms_download))
    application.add_handler(conv_handler_stanza)
    application.add_handler(conv_handler_persone)
    application.add_handler(conv_handler_condizionatori)
    application.add_handler(conv_handler_clearline)
    application.add_handler(conv_handler_deleteroom)
    application.add_handler(conv_handler_config)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))
    
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()