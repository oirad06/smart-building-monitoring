import logging
import csv
import os
from datetime import datetime
from pathlib import Path
import json

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove, Update, ForceReply
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Conversation states
(STANZA,NCOND,STANZAP,NPERSONE,STANZAC,NCONDFREDDI,NCONDCALDI,STANZACLEAR,DELETEROOM) = range(9)
DATA_DIR = Path("data")
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
        "-/deleteroom - elimina una stanza e il relativo file csv",
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
    stanze = carica_stanze()
    return [list(stanze.keys())] 

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
    csv_file = DATA_DIR / f"file_events_{stanza}.csv"

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
    with open(csv_file, "r", encoding="utf-8") as file:
        righe = list(csv.DictReader(file))

    if not righe:
        return None

    return righe[-1]

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

# STEP 1
async def setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Di quale stanza stiamo raccogliendo i dati?"
        )
    return STANZA


# STEP 2
async def salva_stanza(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stanza"] = update.message.text.strip()
    context.user_data["npersone"] = 0
    context.user_data["ncondfreddi"] = 0
    context.user_data["ncondcaldi"] = 0
    await update.message.reply_text(
        "Quanti condizionatori ci sono nella stanza?"
    )

    return NCOND

async def salva_ncond(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        valore = int(update.message.text)

        if valore < 0:
            raise ValueError

        context.user_data["ncond"] = valore

        csv_file_events = DATA_DIR / f"file_events_{context.user_data['stanza']}.csv"
        
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
        reply_markup=ReplyKeyboardMarkup(
          get_reply_keyboard(), one_time_keyboard=True, input_field_placeholder="Quale stanza?"
          ),
        )
    return STANZAP


# STEP 2
async def salva_stanzap(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["stanza"] = update.message.text.strip()


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

        csv_file_events = DATA_DIR / f"file_events_{context.user_data['stanza']}.csv"
        if not csv_file_events.exists():
          await update.message.reply_text( "fare prima /setup" )
          return ConversationHandler.END
        else:
          
          ultima = leggi_ultima_riga(csv_file_events)
          with open(csv_file_events, "a", newline="", encoding="utf-8") as file:
              writer = csv.writer(file)

              writer.writerow([
                datetime.now().isoformat(),
                context.user_data["stanza"],
                int(ultima["ncond"]),
                context.user_data["npersone"],
                int(ultima["ncondfreddi"]),
                int(ultima["ncondcaldi"]),
                int(ultima["ncond"]) - (int(ultima["ncondfreddi"]) + int(ultima["ncondcaldi"]))
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
        
        if not csv_file_events.exists():
          await update.message.reply_text( "fare prima /setup" )
          return ConversationHandler.END
        
        csv_file_events = DATA_DIR / f"file_events_{context.user_data['stanza']}.csv"
        ultima = leggi_ultima_riga(csv_file_events)

        if context.user_data["ncondfreddi"] + valore > int(ultima["ncond"]):
          await update.message.reply_text("Il numero di condizionatori accesi supera il totale.")
          return NCONDCALDI

        context.user_data["ncondcaldi"] = valore

        if not csv_file_events.exists():
          await update.message.reply_text( "fare prima /setup" )
          return ConversationHandler.END
        else:
          ultima = leggi_ultima_riga(csv_file_events)
          with open(csv_file_events, "a", newline="", encoding="utf-8") as file:
              writer = csv.writer(file)

              writer.writerow([
                datetime.now().isoformat(),
                context.user_data["stanza"],
                int(ultima["ncond"]),
                int(ultima["npersone"]),
                context.user_data["ncondfreddi"],
                context.user_data["ncondcaldi"],
                int(ultima["ncond"]) - (context.user_data["ncondfreddi"] + context.user_data["ncondcaldi"])
              ])

        return ConversationHandler.END

    except ValueError:
        await update.message.reply_text(
            "Inserisci un numero valido maggiore o uguale a 0."
        )
        return NCONDCALDI

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
            CommandHandler("setup", setup)
        ],
        states={
            STANZA: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_stanza
                )
            ],
            NCOND: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND,
                    salva_ncond
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


    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(conv_handler_stanza)
    application.add_handler(conv_handler_persone)
    application.add_handler(conv_handler_condizionatori)
    application.add_handler(conv_handler_clearline)
    application.add_handler(conv_handler_deleteroom)

    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))

    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()