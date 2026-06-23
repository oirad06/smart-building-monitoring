import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "setup_esp32"


class SetupEsp32ScriptTest(unittest.TestCase):
    def run_setup(self, *args, env_port="/dev/testUSB", mpremote_list=None):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            log = tmp_path / "commands.log"
            fakebin = tmp_path / "bin"
            fakebin.mkdir()

            esptool = fakebin / "esptool"
            esptool.write_text(
                "#!/usr/bin/env bash\n"
                f"printf '%s ' 'esptool' >> {str(log)!r}\n"
                f"printf '%q ' \"$@\" >> {str(log)!r}\n"
                f"printf '\\n' >> {str(log)!r}\n"
            )
            esptool.chmod(0o755)

            mpremote = fakebin / "mpremote"
            mpremote.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"$1\" == 'connect' && \"$2\" == 'list' ]]; then\n"
                f"  printf '%b\\n' {((mpremote_list if mpremote_list is not None else '/dev/autoUSB 0001 10c4:ea60 Silicon Labs CP2102 USB to UART Bridge Controller'))!r}\n"
                "  exit 0\n"
                "fi\n"
                f"printf '%s ' 'mpremote' >> {str(log)!r}\n"
                f"printf '%q ' \"$@\" >> {str(log)!r}\n"
                f"printf '\\n' >> {str(log)!r}\n"
            )
            mpremote.chmod(0o755)

            env = os.environ.copy()
            env["PATH"] = str(fakebin) + os.pathsep + env["PATH"]
            if env_port is None:
                env.pop("ESP32_PORT", None)
            else:
                env["ESP32_PORT"] = env_port
            env["SETUP_ESP32_POST_FLASH_WAIT"] = "0"
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
            "mpremote connect /dev/testUSB cp "
            + str(ROOT / "esp32_firmware" / "main_dht11.py")
            + " :main.py",
            log,
        )
        self.assertNotIn("mpremote connect /dev/testUSB sleep", log)

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
            "mpremote connect /dev/testUSB cp "
            + str(ROOT / "esp32_firmware" / "main_dht22.py")
            + " :main.py",
            log,
        )
        self.assertNotIn("mpremote connect /dev/testUSB sleep", log)

    def test_help_does_not_require_detected_port(self):
        result, log = self.run_setup("--help", env_port=None, mpremote_list="")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Usage: scripts/setup_esp32", result.stderr)
        self.assertEqual(log, "")

    def test_auto_detects_single_serial_port_when_env_missing(self):
        result, log = self.run_setup(
            env_port=None,
            mpremote_list="/dev/ttyUSB7 0001 10c4:ea60 Silicon Labs CP2102 USB to UART Bridge Controller",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("esptool --chip esp32 --port /dev/ttyUSB7 erase-flash", log)
        self.assertIn("mpremote connect /dev/ttyUSB7 cp", log)

    def test_auto_detect_rejects_no_serial_ports(self):
        result, _ = self.run_setup(env_port=None, mpremote_list="")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No ESP32 serial port detected", result.stderr)

    def test_auto_detect_rejects_multiple_serial_ports(self):
        result, _ = self.run_setup(
            env_port=None,
            mpremote_list=(
                "/dev/ttyUSB0 0001 10c4:ea60 Silicon Labs CP2102 USB to UART Bridge Controller\n"
                "/dev/ttyUSB1 0002 10c4:ea60 Silicon Labs CP2102 USB to UART Bridge Controller"
            ),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Multiple ESP32 serial ports detected", result.stderr)

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
