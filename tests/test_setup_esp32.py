import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_esp32"


class SetupEsp32ScriptTest(unittest.TestCase):
    def run_setup(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "commands.log"
            fakebin = tmp_path / "bin"
            fakebin.mkdir()

            for name in ("esptool", "mpremote"):
                tool = fakebin / name
                tool.write_text(
                    "#!/usr/bin/env bash\n"
                    f"printf '%s ' {name!r} >> {str(log)!r}\n"
                    f"printf '%q ' \"$@\" >> {str(log)!r}\n"
                    f"printf '\\n' >> {str(log)!r}\n"
                )
                tool.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
            env["ESP32_PORT"] = "/dev/testUSB"
            result = subprocess.run(
                [str(SCRIPT), *args],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
            )
            return result, log.read_text() if log.exists() else ""

    def test_defaults_to_esp32_and_dht11(self):
        result, log = self.run_setup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("esptool --chip esp32 --port /dev/testUSB erase-flash", log)
        self.assertIn(
            "esptool --chip esp32 --port /dev/testUSB --baud 460800 "
            "write-flash 0x1000 "
            + str(ROOT / "esp32_firmware" / "mp-esp32-v1.28.0.bin"),
            log,
        )
        self.assertIn(
            "mpremote connect /dev/testUSB sleep 5 cp "
            + str(ROOT / "esp32_firmware" / "main_dht11.py")
            + " :main.py",
            log,
        )

    def test_dht22_on_esp32s3_uses_s3_firmware_and_offset(self):
        result, log = self.run_setup("--sensor", "dht22", "--board", "esp32s3")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("esptool --chip esp32s3 --port /dev/testUSB erase-flash", log)
        self.assertIn(
            "esptool --chip esp32s3 --port /dev/testUSB --baud 460800 "
            "write-flash 0x0 "
            + str(ROOT / "esp32_firmware" / "mp-esp32s3-v1.28.0.bin"),
            log,
        )
        self.assertIn(
            "mpremote connect /dev/testUSB sleep 5 cp "
            + str(ROOT / "esp32_firmware" / "main_dht22.py")
            + " :main.py",
            log,
        )

    def test_short_flags_select_sensor_and_board(self):
        result, log = self.run_setup("-s", "dht22", "-b", "esp32s3")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "write-flash 0x0 " + str(ROOT / "esp32_firmware" / "mp-esp32s3-v1.28.0.bin"),
            log,
        )
        self.assertIn(str(ROOT / "esp32_firmware" / "main_dht22.py") + " :main.py", log)

    def test_rejects_unknown_sensor(self):
        result, _ = self.run_setup("--sensor", "bmp280")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported sensor", result.stderr)

    def test_rejects_unknown_board(self):
        result, _ = self.run_setup("--board", "esp8266")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unsupported board", result.stderr)


if __name__ == "__main__":
    unittest.main()
