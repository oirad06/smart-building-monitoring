import asyncio
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "telegram_bot"))

# bot.py guards on these at import time; set dummy values before importing.
os.environ.setdefault("MQTT_BROKER", "localhost")
os.environ.setdefault("MQTT_USER", "u")
os.environ.setdefault("MQTT_PASS", "p")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123:dummy")

import auth  # noqa: E402
from telegram.ext import ApplicationHandlerStop  # noqa: E402


class FakeMsg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, **kw):
        self.sent.append(text)
        return self


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeUpdate:
    def __init__(self, uid, message=None, callback_query=None):
        self.effective_user = FakeUser(uid) if uid is not None else None
        self.effective_message = message
        self.callback_query = callback_query


def run(coro):
    return asyncio.run(coro)


class TestAllowlistParsing(unittest.TestCase):
    def setUp(self):
        self._orig = os.environ.get("ALLOWED_USER_IDS")

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("ALLOWED_USER_IDS", None)
        else:
            os.environ["ALLOWED_USER_IDS"] = self._orig

    def test_unset_empty(self):
        os.environ.pop("ALLOWED_USER_IDS", None)
        self.assertEqual(auth.allowed_user_ids(), set())

    def test_empty_string(self):
        os.environ["ALLOWED_USER_IDS"] = "   "
        self.assertEqual(auth.allowed_user_ids(), set())

    def test_comma_and_space(self):
        os.environ["ALLOWED_USER_IDS"] = "1, 2 3,  4"
        self.assertEqual(auth.allowed_user_ids(), {1, 2, 3, 4})

    def test_ignores_junk(self):
        os.environ["ALLOWED_USER_IDS"] = "1,abc,2"
        self.assertEqual(auth.allowed_user_ids(), {1, 2})


class TestIsAllowed(unittest.TestCase):
    def setUp(self):
        self._orig = os.environ.get("ALLOWED_USER_IDS")

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("ALLOWED_USER_IDS", None)
        else:
            os.environ["ALLOWED_USER_IDS"] = self._orig

    def test_open_when_unset(self):
        os.environ.pop("ALLOWED_USER_IDS", None)
        self.assertTrue(auth.is_allowed(999))

    def test_restricts_when_set(self):
        os.environ["ALLOWED_USER_IDS"] = "42, 7"
        self.assertTrue(auth.is_allowed(42))
        self.assertTrue(auth.is_allowed(7))
        self.assertFalse(auth.is_allowed(99))


class TestGate(unittest.TestCase):
    def setUp(self):
        self._orig = os.environ.get("ALLOWED_USER_IDS")

    def tearDown(self):
        if self._orig is None:
            os.environ.pop("ALLOWED_USER_IDS", None)
        else:
            os.environ["ALLOWED_USER_IDS"] = self._orig

    def test_blocks_disallowed(self):
        os.environ["ALLOWED_USER_IDS"] = "42"
        msg = FakeMsg()
        upd = FakeUpdate(99, message=msg)
        with self.assertRaises(ApplicationHandlerStop):
            run(auth._gate(upd, None))
        self.assertEqual(len(msg.sent), 1)
        self.assertIn("autorizzato", msg.sent[0])

    def test_allows_allowed(self):
        os.environ["ALLOWED_USER_IDS"] = "42"
        msg = FakeMsg()
        upd = FakeUpdate(42, message=msg)
        # Must NOT raise.
        run(auth._gate(upd, None))
        self.assertEqual(msg.sent, [])

    def test_open_lets_through(self):
        os.environ.pop("ALLOWED_USER_IDS", None)
        msg = FakeMsg()
        upd = FakeUpdate(12345, message=msg)
        run(auth._gate(upd, None))
        self.assertEqual(msg.sent, [])


if __name__ == "__main__":
    unittest.main()
