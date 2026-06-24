"""Regression test for the production-only `__main__`-vs-`bot` module split.

When the bot is launched as `python bot.py` it is the `__main__` module; every
feature plugin does `import bot`, which — without an alias — loads a SECOND,
empty copy (mqtt_client=None, empty known_devices, a separate _message_listeners
list). That made presence/alerts listeners register into a module the live
_on_mqtt_message never iterates, and /status read the empty copy.

This test reproduces that launch in a subprocess (run_polling stubbed so nothing
blocks or hits the network) and asserts `import bot` resolves to the running
module and that plugin listeners landed in it.
"""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT_DIR = ROOT / "telegram_bot"
BOT_PY = BOT_DIR / "bot.py"

DRIVER = '''
import os, sys, runpy
from unittest import mock
os.environ.update(MQTT_BROKER="localhost", MQTT_USER="u", MQTT_PASS="p", TELEGRAM_BOT_TOKEN="123:dummy")
sys.path.insert(0, {bot_dir!r})
import telegram.ext
captured = {{}}
def fake_run_polling(self, *a, **k):
    # Runs while bot.py is still '__main__'; capture the live module.
    captured["mod"] = sys.modules["__main__"]
with mock.patch.object(telegram.ext.Application, "run_polling", fake_run_polling):
    runpy.run_path({bot_py!r}, run_name="__main__")
mod = captured.get("mod")
assert mod is not None, "run_polling was never reached"
assert sys.modules.get("bot") is mod, "PROD BUG: `import bot` != __main__ (duplicate module copy)"
n = len(getattr(mod, "_message_listeners", []))
assert n >= 2, "feature listeners registered into the wrong module copy: %d" % n
print("IDENTITY_OK")
'''


class ModuleIdentityTest(unittest.TestCase):
    def test_import_bot_resolves_to_main_when_run_as_script(self):
        driver = DRIVER.format(bot_dir=str(BOT_DIR), bot_py=str(BOT_PY))
        r = subprocess.run([sys.executable, "-c", driver], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, msg="STDOUT:\n" + r.stdout + "\nSTDERR:\n" + r.stderr)
        self.assertIn("IDENTITY_OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
