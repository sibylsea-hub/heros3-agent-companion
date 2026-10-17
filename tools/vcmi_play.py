"""Start the patched VCMI 1.7.5 for real play, with the Claude bridge (see vcmi_say.py).

Usage: python -X utf8 tools/vcmi_play.py   (or double-click tools/vcmi_play.bat)

Same game, same version, same saves as the official VCMI 1.7.5 install: it uses the player's
own settings, mods and Saves folder (vcmi_data, see tools/h3tools/config.py), and saves from
either client load in the other. The only difference is the client patch
(tools/vcmi_src/client-testmap.patch): chat lines and popups from Claude appear in the game,
and what the player types in the in-game chat reaches Claude.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_local  # noqa: E402


def main() -> int:
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__.strip())
        return 0
    vcmi_local.prepare_play()
    vcmi_local.BRIDGE.mkdir(parents=True, exist_ok=True)
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([str(vcmi_local.CLIENT)], cwd=vcmi_local.INSTALL, creationflags=flags,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"started {vcmi_local.CLIENT}")
    print(f"bridge  {vcmi_local.BRIDGE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
