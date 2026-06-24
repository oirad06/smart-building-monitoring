import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

# bot.py guards on these at import time; set dummy values before importing.
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import bot  # noqa: E402
import devices  # noqa: E402
import rooms  # noqa: E402
from telegram.ext import ConversationHandler  # noqa: E402


class FakeMsg:
    def __init__(self, text=None):
        self.text = text
        self.sent = []

    async def reply_text(self, text, reply_markup=None, **kw):
        self.sent.append((text, reply_markup))
        return self


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.message = FakeMsg()
        self.edits = []
        self.answered = 0

    async def answer(self, *a, **kw):
        self.answered += 1

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, reply_markup=None, **kw):
        self.edits.append((None, reply_markup))


class FakeUpdate:
    def __init__(self, query=None, message=None):
        self.callback_query = query
        self.message = message


class Ctx:
    def __init__(self):
        self.user_data = {}


def callbacks(markup):
    if markup is None or not hasattr(markup, "inline_keyboard"):
        return []
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class BotFlowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        d = Path(self._tmp.name)
        self._orig = (rooms.ROOMS_FILE, devices.DEVICES_FILE)
        rooms.ROOMS_FILE = d / "rooms.json"
        devices.DEVICES_FILE = d / "devices.json"
        self.addCleanup(
            lambda: (setattr(rooms, "ROOMS_FILE", self._orig[0]),
                     setattr(devices, "DEVICES_FILE", self._orig[1]))
        )
        self._orig_send = bot.send_device_config
        self.published = []
        bot.send_device_config = lambda *a: self.published.append(a)
        self.addCleanup(lambda: setattr(bot, "send_device_config", self._orig_send))
        bot.known_devices.clear()

    async def test_device_menu_shows_current_config_and_room(self):
        bot.known_devices["dev"] = time.time()
        rooms.add_room("Lab", ["dev"], 2)
        devices.set_device_config("dev", 5, 20, False)
        ctx = Ctx()
        q = FakeQuery("devcfg_dev")
        state = await bot.devices_pick(FakeUpdate(query=q), ctx)
        self.assertEqual(state, bot.DEV_MENU)
        text = q.edits[-1][0]
        self.assertIn("Intervallo lettura: 5 s", text)
        self.assertIn("Letture per finestra: 20", text)
        self.assertIn("Stato: Spento", text)
        self.assertIn("Stanza: Lab", text)
        self.assertNotIn("predefiniti", text)  # config was saved

    async def test_unconfigured_device_shows_not_confirmed_note(self):
        bot.known_devices["fresh"] = time.time()
        ctx = Ctx()
        q = FakeQuery("devcfg_fresh")
        await bot.devices_pick(FakeUpdate(query=q), ctx)
        text = q.edits[-1][0]
        self.assertIn("Intervallo lettura: 1 s", text)
        self.assertIn("non confermati", text)  # device never echoed config_state

    async def test_full_edit_and_save_publishes_persists_and_assigns_room(self):
        bot.known_devices["dev"] = time.time()
        rooms.add_room("Lab", [], 1)
        ctx = Ctx()
        await bot.devices_pick(FakeUpdate(query=FakeQuery("devcfg_dev")), ctx)
        # edit interval
        await bot.devices_menu_action(FakeUpdate(query=FakeQuery("devset_interval")), ctx)
        await bot.devices_interval(FakeUpdate(message=FakeMsg("8")), ctx)
        # edit processing
        await bot.devices_menu_action(FakeUpdate(query=FakeQuery("devset_processing")), ctx)
        await bot.devices_processing(FakeUpdate(message=FakeMsg("4")), ctx)
        # toggle active off
        await bot.devices_menu_action(FakeUpdate(query=FakeQuery("devset_toggle")), ctx)
        # assign room
        await bot.devices_menu_action(FakeUpdate(query=FakeQuery("devset_room")), ctx)
        await bot.devices_room_action(FakeUpdate(query=FakeQuery("devroom_pick_Lab")), ctx)
        # save
        state = await bot.devices_menu_action(FakeUpdate(query=FakeQuery("devset_save")), ctx)
        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(self.published, [("dev", 8, 4, False)])
        self.assertEqual(
            devices.get_device_config("dev"),
            {"read_interval": 8, "read_processing": 4, "active": False},
        )
        self.assertEqual(rooms.get_room("Lab")["device_ids"], ["dev"])

    async def test_device_room_removal_detaches(self):
        bot.known_devices["dev"] = time.time()
        rooms.add_room("Lab", ["dev"], 1)
        devices.set_device_config("dev", 1, 10, True)
        ctx = Ctx()
        await bot.devices_pick(FakeUpdate(query=FakeQuery("devcfg_dev")), ctx)
        await bot.devices_menu_action(FakeUpdate(query=FakeQuery("devset_room")), ctx)
        await bot.devices_room_action(FakeUpdate(query=FakeQuery("devroom_rm")), ctx)
        await bot.devices_menu_action(FakeUpdate(query=FakeQuery("devset_save")), ctx)
        self.assertIsNone(rooms.get_device_room("dev"))
        self.assertEqual(rooms.get_room("Lab")["device_ids"], [])

    async def test_invalid_interval_keeps_state(self):
        bot.known_devices["dev"] = time.time()
        ctx = Ctx()
        await bot.devices_pick(FakeUpdate(query=FakeQuery("devcfg_dev")), ctx)
        state = await bot.devices_interval(FakeUpdate(message=FakeMsg("0")), ctx)
        self.assertEqual(state, bot.DEV_INTERVAL)
        # cfg unchanged at default
        self.assertEqual(ctx.user_data["dev_cfg"]["read_interval"], 1)

    async def test_rooms_remove_lists_offline_devices_and_removes(self):
        # 'offline' is assigned to the room but never seen on the bus.
        rooms.add_room("Lab", ["online", "offline"], 1)
        bot.known_devices["online"] = time.time()
        ctx = Ctx()
        ctx.user_data["room"] = "Lab"
        q = FakeQuery("rm_remove")
        state = await bot.rooms_menu_action(FakeUpdate(query=q), ctx)
        self.assertEqual(state, bot.RM_REMOVE)
        cbs = callbacks(q.edits[-1][1])
        self.assertIn("rmrm_online", cbs)
        self.assertIn("rmrm_offline", cbs)  # removable even though offline
        # remove the offline one
        state = await bot.rooms_remove_device(FakeUpdate(query=FakeQuery("rmrm_offline")), ctx)
        self.assertEqual(state, bot.RM_REMOVE)
        self.assertEqual(rooms.get_room("Lab")["device_ids"], ["online"])

    async def test_rooms_remove_back_returns_to_menu(self):
        rooms.add_room("Lab", ["x"], 1)
        ctx = Ctx()
        ctx.user_data["room"] = "Lab"
        state = await bot.rooms_remove_device(FakeUpdate(query=FakeQuery("rmrm_back")), ctx)
        self.assertEqual(state, bot.RM_MENU)

    async def test_inline_cancel_ends_setup_device_selection(self):
        ctx = Ctx()
        ctx.user_data.update(
            selected_devices=[], devices=["dev"], assigned={}, room_name="X", num_ac=1
        )
        state = await bot.save_devices(FakeUpdate(query=FakeQuery(bot.CANCEL_DATA)), ctx)
        self.assertEqual(state, ConversationHandler.END)
        self.assertEqual(ctx.user_data, {})

    async def test_every_conversation_has_three_cancel_fallbacks(self):
        app = bot._build_application()
        convs = [h for grp in app.handlers.values() for h in grp
                 if isinstance(h, ConversationHandler)]
        self.assertEqual(len(convs), 7)  # setup, rooms, event, events, devices, alerts, chart
        for c in convs:
            kinds = [type(f).__name__ for f in c.fallbacks]
            self.assertEqual(
                sorted(kinds),
                ["CallbackQueryHandler", "CommandHandler", "MessageHandler"],
            )


if __name__ == "__main__":
    unittest.main()
