"""/chart feature — render sensor time-series PNGs and serve them via Telegram.

Plugs into bot.py through `install(app)` (called from `_install_features`).
Owns the `chart_` callback prefix.
"""
import io
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from telegram import Update  # noqa: E402
from telegram.ext import (  # noqa: E402
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

ALL_CALLBACK = "chart_all"
_TYPES = ("temperature", "humidity")
_LABELS = {"temperature": "Temperatura", "humidity": "Umidità"}


def _parse_ts(s):
    """timestamp -> datetime. ISO first, float epoch fallback, else None."""
    try:
        return datetime.fromisoformat(str(s))
    except (ValueError, TypeError):
        pass
    try:
        return datetime.fromtimestamp(float(s))
    except (ValueError, TypeError, OSError):
        return None


def render_timeseries(rows, title):
    """Render `media` over time for temperature & humidity from read_sensors()
    rows (list[dict]). Returns a seek-0 BytesIO PNG, or None if no usable rows.
    """
    series = {t: ([], []) for t in _TYPES}  # type -> (xs, ys)
    for r in rows:
        t = r.get("type")
        if t not in series:
            continue
        x = _parse_ts(r.get("timestamp"))
        if x is None:
            continue
        try:
            y = float(r.get("media"))
        except (ValueError, TypeError):
            continue
        series[t][0].append(x)
        series[t][1].append(y)

    present = [t for t in _TYPES if series[t][0]]
    if not present:
        return None

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for t in present:
        xs, ys = series[t]
        pts = sorted(zip(xs, ys), key=lambda p: p[0])
        ax.plot([p[0] for p in pts], [p[1] for p in pts],
                marker="o", markersize=3, label=_LABELS[t])
    ax.set_title(title)
    ax.set_xlabel("Tempo")
    ax.set_ylabel("Valore medio")
    ax.legend()
    ax.grid(True, alpha=0.3)
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf


def _rows_for_room(rows, room):
    """Filter read_sensors() rows to a room via its device_ids, or all rows
    when room is None."""
    if room is None:
        return rows
    import rooms as rooms_mod

    info = rooms_mod.get_room(room)
    ids = set(info.get("device_ids", [])) if info else set()
    return [r for r in rows if r.get("device_id") in ids]


async def chart_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot

    keyboard = bot.room_buttons(
        prefix="chart_",
        extra=[
            [__import__("telegram").InlineKeyboardButton(
                "Tutte le stanze", callback_data=ALL_CALLBACK)],
            [bot.cancel_button()],
        ],
    )
    await update.message.reply_text(
        "Scegli la stanza per il grafico:", reply_markup=keyboard
    )


async def charts_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot

    query = update.callback_query
    data = query.data
    if data == bot.CANCEL_DATA:
        await query.answer()
        await query.edit_message_text("Operazione annullata.")
        return
    await query.answer()

    if data == ALL_CALLBACK:
        room = None
        title = "Tutte le stanze"
    else:
        room = data[len("chart_"):]
        title = room

    rows = _rows_for_room(bot.read_sensors(), room)
    png = render_timeseries(rows, title)
    if png is None:
        await query.message.reply_text("Nessun dato disponibile per il grafico.")
        return
    await query.message.reply_photo(photo=png)


def install(app):
    import bot

    app.add_handler(CommandHandler("chart", chart_start))
    app.add_handler(CallbackQueryHandler(
        charts_cb, pattern=f"^(chart_|{bot.CANCEL_DATA}$)"))
