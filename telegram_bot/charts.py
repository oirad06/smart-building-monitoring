"""/chart feature — sensor time-series PNGs with temporal filters.

Plugs into bot.py via `install(app)` (called from `_install_features`). Owns the
`c_` callback prefix and conversation-state range 60-61.

Design notes:
- Resampling + per-room mean / min-max bands are computed in SQLite (GROUP BY a
  time bucket), NOT in Python. The RPi cannot afford to pull ~100k+ rows per
  chart, so the DB returns only O(buckets x rooms x types) aggregated rows.
- The resample bucket is derived from the chosen horizon (last hour -> 1 min,
  day -> 10 min, week -> 1 h, month/all -> 1 day).
- One mean line per selected room with a shaded MIN-MAX envelope; rooms get
  distinct colors from the matplotlib cycle. Temperature and humidity render as
  two stacked, time-aligned subplots.
"""
import asyncio
import io
import sqlite3
import time
from datetime import datetime

import matplotlib

matplotlib.use("Agg")
from matplotlib.figure import Figure  # noqa: E402

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update  # noqa: E402
from telegram.ext import (  # noqa: E402
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
)

# Conversation states (owned range: 60-69).
CH_ROOMS = 60
CH_HORIZON = 61

# Plotted measurement types and their axis labels.
_TYPES = [("temperature", "Temperatura (°C)"), ("humidity", "Umidità (%)")]

# horizon key -> (label, window seconds or None=all, resample bucket seconds).
_HORIZON = {
    "hour": ("Ultima ora", 3600, 60),
    "day": ("Ultimo giorno", 86400, 600),
    "week": ("Ultima settimana", 604800, 3600),
    "month": ("Ultimo mese", 2592000, 86400),
    "all": ("Tutto lo storico", None, 86400),
}


# ---------------------------------------------------------------------------
# Data: time-bucketed aggregation in SQLite
# ---------------------------------------------------------------------------
def _aggregate(device_ids, since_epoch, bucket_secs):
    """Bucket sensor_readings by time and aggregate per measurement type.

    `device_ids`: list of ids to include, or None for every device. An empty
    list means "no devices" -> no data. Returns
    {type: ([datetime], [mean], [lo], [hi])} for types that have rows.
    """
    import bot

    if device_ids is not None and not device_ids:
        return {}
    if not bot.SENSORS_DB.exists():
        return {}

    q = (
        "SELECT (CAST(strftime('%s', timestamp) AS INT) / ?) * ? AS bucket, "
        "type, AVG(CAST(media AS REAL)), MIN(CAST(min AS REAL)), "
        "MAX(CAST(max AS REAL)) "
        "FROM sensor_readings WHERE 1=1"
    )
    params = [bucket_secs, bucket_secs]
    if since_epoch:
        q += " AND CAST(strftime('%s', timestamp) AS INT) >= ?"
        params.append(since_epoch)
    if device_ids is not None:
        q += " AND device_id IN (%s)" % ",".join("?" * len(device_ids))
        params.extend(device_ids)
    q += " GROUP BY bucket, type ORDER BY bucket"

    series = {}
    conn = sqlite3.connect(f"file:{bot.SENSORS_DB}?mode=ro", uri=True, timeout=30)
    try:
        try:
            cur = conn.execute(q, params)
        except sqlite3.OperationalError:
            return {}
        for bucket, mtype, mean, lo, hi in cur.fetchall():
            if bucket is None or mean is None:
                continue
            xs, ms, los, his = series.setdefault(mtype, ([], [], [], []))
            xs.append(datetime.fromtimestamp(int(bucket)))
            ms.append(mean)
            los.append(lo if lo is not None else mean)
            his.append(hi if hi is not None else mean)
    finally:
        conn.close()
    return series


def build_chart(rooms, horizon_key):
    """rooms: list of room names, or None for all devices. Returns PNG BytesIO."""
    import rooms as rooms_mod

    label, window, bucket = _HORIZON[horizon_key]
    since_epoch = int(time.time() - window) if window else 0

    series = []  # [(label, {type: (xs, mean, lo, hi)})]
    if rooms is None:
        agg = _aggregate(None, since_epoch, bucket)
        if agg:
            series.append(("Tutti i dispositivi", agg))
    else:
        for name in rooms:
            info = rooms_mod.get_room(name)
            ids = info.get("device_ids", []) if info else []
            agg = _aggregate(ids, since_epoch, bucket)
            if agg:
                series.append((name, agg))
    if not series:
        return None
    return render_chart(series, label)


# ---------------------------------------------------------------------------
# Rendering: one colored mean line + min-max band per room, stacked by type
# ---------------------------------------------------------------------------
def render_chart(series, horizon_label):
    """series: [(room_label, {type: (xs, mean, lo, hi)})]. Returns PNG BytesIO
    or None when nothing is plottable."""
    present = [(t, lbl) for t, lbl in _TYPES if any(t in agg for _, agg in series)]
    if not present:
        return None

    fig = Figure(figsize=(9, 3.3 * len(present)))
    axes = fig.subplots(len(present), 1, squeeze=False, sharex=True)
    for row, (mtype, axis_label) in enumerate(present):
        ax = axes[row][0]
        for idx, (room_label, agg) in enumerate(series):
            if mtype not in agg:
                continue
            xs, mean, lo, hi = agg[mtype]
            color = f"C{idx % 10}"
            ax.plot(xs, mean, color=color, linewidth=1.6, label=room_label)
            ax.fill_between(xs, lo, hi, color=color, alpha=0.18)
        ax.set_ylabel(axis_label)
        ax.grid(True, alpha=0.3)
        if row == 0:
            ax.legend(fontsize=8, loc="best")
    axes[-1][0].set_xlabel("Tempo")
    fig.suptitle(f"Andamento sensori — {horizon_label}")
    fig.autofmt_xdate()
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Conversation: pick room(s) -> pick horizon -> render
# ---------------------------------------------------------------------------
def _rooms_keyboard(selected):
    import bot
    import rooms as rooms_mod

    rows = []
    for name in rooms_mod.get_room_names():
        mark = "☑️" if name in selected else "☐"
        rows.append([InlineKeyboardButton(f"{mark} {name}", callback_data=f"c_rm_{name}")])
    rows.append([InlineKeyboardButton("📈 Genera grafico", callback_data="c_go")])
    rows.append([bot.cancel_button()])
    return InlineKeyboardMarkup(rows)


def _horizon_keyboard():
    import bot

    rows = [
        [InlineKeyboardButton(_HORIZON[k][0], callback_data=f"c_h_{k}")]
        for k in ("hour", "day", "week", "month", "all")
    ]
    rows.append([bot.cancel_button()])
    return InlineKeyboardMarkup(rows)


async def chart_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import rooms as rooms_mod

    context.user_data.clear()
    if not rooms_mod.get_room_names():
        # No rooms configured: chart every device as one series, skip room pick.
        context.user_data["c_rooms"] = None
        await update.message.reply_text(
            "Nessuna stanza configurata: grafico di tutti i dispositivi.\nPeriodo:",
            reply_markup=_horizon_keyboard(),
        )
        return CH_HORIZON
    context.user_data["c_rooms"] = []
    await update.message.reply_text(
        "Scegli una o più stanze, poi «Genera grafico»:",
        reply_markup=_rooms_keyboard([]),
    )
    return CH_ROOMS


async def chart_rooms_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot

    query = update.callback_query
    if query.data == bot.CANCEL_DATA:
        return await bot.cancel(update, context)
    data = query.data
    selected = context.user_data.setdefault("c_rooms", [])

    if data == "c_go":
        if not selected:
            await query.answer("Seleziona almeno una stanza.", show_alert=True)
            return CH_ROOMS
        await query.answer()
        await query.edit_message_text("Periodo del grafico:", reply_markup=_horizon_keyboard())
        return CH_HORIZON

    await query.answer()
    if data.startswith("c_rm_"):
        name = data[len("c_rm_"):]
        if name in selected:
            selected.remove(name)
        else:
            selected.append(name)
        await query.edit_message_reply_markup(reply_markup=_rooms_keyboard(selected))
    return CH_ROOMS


async def chart_horizon_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot

    query = update.callback_query
    if query.data == bot.CANCEL_DATA:
        return await bot.cancel(update, context)
    await query.answer()
    key = query.data[len("c_h_"):]
    if key not in _HORIZON:
        return CH_HORIZON
    rooms_sel = context.user_data.get("c_rooms")
    label = _HORIZON[key][0]
    # Collapse the menu + show a loading note (rendering is slow on the RPi).
    await query.edit_message_text(f"⏳ Generazione del grafico ({label})…")
    png = await asyncio.to_thread(build_chart, rooms_sel, key)
    if png is None:
        await query.edit_message_text("Nessun dato disponibile per il periodo scelto.")
    else:
        title = ", ".join(rooms_sel) if rooms_sel else "Tutti i dispositivi"
        await query.message.reply_photo(photo=png, caption=f"{title} — {label}")
    context.user_data.clear()
    return ConversationHandler.END


def install(app):
    import bot

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler("chart", chart_start)],
        states={
            CH_ROOMS: [CallbackQueryHandler(chart_rooms_action, pattern="^c_(rm_|go)")],
            CH_HORIZON: [CallbackQueryHandler(chart_horizon_action, pattern="^c_h_")],
        },
        fallbacks=bot._cancel_fallbacks(),
    ))
