"""Paths and setup for the self-built, patched VCMI 1.7.5 (see tools/vcmi_src/README.md).

The self-built copy reads the player's real VCMI user data (H3 files, maps, mods) from
the vcmi_data folder, but everything a test run writes -- config, logs, cache, saves --
goes to its own folder under vcmi_build_root, so tests never touch the player's settings
or saves. Both folders come from tools/h3tools/config.py (h3mod.local.json).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h3tools import config  # noqa: E402

BUILD_ROOT = config.build_root()
INSTALL = BUILD_ROOT / "install"            # cmake --install output: exes, DLLs, config/, Mods/vcmi
USERDATA = BUILD_ROOT / "userdata"          # what the self-built copy writes in test runs
GAME_DATA = config.vcmi_data()              # the player's VCMI user data; only read from here

CLIENT = INSTALL / "VCMI_client.exe"
SAVE_READER = INSTALL / "vcmi_save_reader.exe"
CLIENT_LOG = USERDATA / "logs" / "VCMI_Client_log.txt"
# Claude bridge of the patched client (to-game.jsonl / from-game.jsonl); the client
# also honours the VCMI_CLAUDE_BRIDGE environment variable (config.bridge_dir()), which tests use.
BRIDGE = GAME_DATA / "claude-bridge"


def prepare_play() -> None:
    """Point the self-built copy at the player's real user data, exactly like the official
    install's dirs.json: their settings, logs and saves. Used by vcmi_play.py."""
    if not CLIENT.exists():
        raise SystemExit(f"{CLIENT} not found -- build it first (tools/vcmi_src/README.md)")
    (INSTALL / "config" / "dirs.json").write_text(
        json.dumps({"userDataPath": GAME_DATA.as_posix()}, indent=2), encoding="utf-8")


def prepare(quiet: bool = False) -> None:
    """Write config/dirs.json for the self-built copy and mirror the player's
    settings and mod list into its private config folder (refreshed on every
    call, so it always loads the same mods and language as their game).
    quiet=True (for test launches) mutes music and sound and uses a 1600x900
    window instead of fullscreen, so a test run doesn't take over the screen."""
    if not CLIENT.exists():
        raise SystemExit(f"{CLIENT} not found -- build it first (tools/vcmi_src/README.md)")
    dirs = {
        "userDataPath": GAME_DATA.as_posix(),
        "userConfigPath": (USERDATA / "config").as_posix(),
        "userLogsPath": (USERDATA / "logs").as_posix(),
        "userCachePath": (USERDATA / "cache").as_posix(),
        "userSavePath": (USERDATA / "Saves").as_posix(),
    }
    (INSTALL / "config" / "dirs.json").write_text(json.dumps(dirs, indent=2), encoding="utf-8")
    for sub in ("config", "logs", "cache", "Saves"):
        (USERDATA / sub).mkdir(parents=True, exist_ok=True)
    for name in ("settings.json", "modSettings.json"):
        src = GAME_DATA / "config" / name
        if src.exists():
            shutil.copyfile(src, USERDATA / "config" / name)
    if quiet:
        path = USERDATA / "config" / "settings.json"
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        settings.setdefault("general", {}).update(music=0, sound=0)
        video = settings.setdefault("video", {})
        video.update(fullscreen=False, realFullscreen=False)
        video["resolution"] = {"width": 1600, "height": 900, "scaling": 0}
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
