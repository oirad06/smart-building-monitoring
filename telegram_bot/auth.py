"""Access control gate.

Restricts bot usage to an allowlist of Telegram user IDs read from the
ALLOWED_USER_IDS env var (comma/space separated integers). If the allowlist
is empty/unset the bot stays open (backward compatible).
"""
import os

from telegram import Update
from telegram.ext import ApplicationHandlerStop, TypeHandler

DENIED_MESSAGE = "⛔ Non sei autorizzato a usare questo bot."


def allowed_user_ids() -> set[int]:
    """Parse ALLOWED_USER_IDS (comma/space separated ints). Empty/unset -> set()."""
    raw = os.getenv("ALLOWED_USER_IDS", "") or ""
    ids: set[int] = set()
    for tok in raw.replace(",", " ").split():
        try:
            ids.add(int(tok))
        except ValueError:
            continue
    return ids


def is_allowed(user_id) -> bool:
    """True if the allowlist is empty (open) or user_id is in it."""
    ids = allowed_user_ids()
    if not ids:
        return True
    try:
        return int(user_id) in ids
    except (TypeError, ValueError):
        return False


async def _gate(update, context):
    user = update.effective_user
    if user is not None and not is_allowed(user.id):
        query = update.callback_query
        if query is not None:
            await query.answer(DENIED_MESSAGE, show_alert=True)
            try:
                await query.edit_message_text(DENIED_MESSAGE)
            except Exception:
                if query.message is not None:
                    await query.message.reply_text(DENIED_MESSAGE)
        elif update.effective_message is not None:
            await update.effective_message.reply_text(DENIED_MESSAGE)
        raise ApplicationHandlerStop
    return


def install(app):
    """Register the access-control gate before all other handlers."""
    app.add_handler(TypeHandler(Update, _gate), group=-1)
