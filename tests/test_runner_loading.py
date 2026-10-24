from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

from htmod import load_release
from htmod.runner import load_binary_mods


class HandlerIdentityTests(unittest.TestCase):
    def test_handler_id_and_target_must_match_manifest(self):
        cases = (
            ("wrong", "game/sys/game.dll", "id.*manifest"),
            ("camera", "sys/dx8.dll", "target.*manifest"),
        )
        for mod_id, target, message in cases:
            with self.subTest(mod_id=mod_id, target=target), tempfile.TemporaryDirectory() as temporary:
                modpack = Path(temporary) / "modpack"
                shutil.copytree(ROOT / "modpack", modpack)
                handler = modpack / "mods/camera/patch.py"
                with handler.open("a", encoding="utf-8") as output:
                    output.write(f"\nMOD = BinaryMod({mod_id!r}, {target!r}, ())\n")
                release = load_release(Path(temporary))
                with self.assertRaisesRegex(Exception, message):
                    load_binary_mods(release, ["camera"])


if __name__ == "__main__":
    unittest.main()
