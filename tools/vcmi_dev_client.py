"""Run a test copy of the patched VCMI out of sight, even while someone is playing.

Usage: python -X utf8 tools/vcmi_dev_client.py [--map Maps/my-map | --save Saves/x] [--hotseat Claude]
                                                [--claude blue] [-- more client args]
       python -X utf8 tools/vcmi_dev_client.py --menu     (main menu, for testing the lobby)
       python -X utf8 tools/vcmi_dev_client.py --kill

Without --map or --save it starts dev_map from tools/h3tools/config.py (h3mod.local.json or
$H3MOD_DEV_MAP); with none of the three set it stops and asks for --map.

The client is the separate test install <vcmi_build_root>/install-dev (build it with
`cmake --install <build dir> --prefix <vcmi_build_root>/install-dev`) with its own user data
<vcmi_build_root>/userdata-dev: config (the player's settings and mod list copied in, muted,
windowed), logs, saves and its own claude-bridge folder. It reads only game data, maps and mods
from the player's vcmi_data folder, so their settings, saves and bridge files are never touched.

It starts on a separate Windows desktop that is never shown (CreateDesktop + lpDesktop): its
window can't appear on the player's screen or in a game stream, and can't take the keyboard focus.
Sound is off. Default game: --testmap with a hotseat seat "Claude" (see vcmi_claude_player.py)
and --autoSkip, so the human seat ends its turns at once; `--hotseat "" --claude blue` instead gives
the computer colour blue to Claude, as the lobby's 颜色和 Claude window does. Point the Claude tools at the test:
  set VCMI_CLAUDE_BRIDGE=<vcmi_build_root>\\userdata-dev\\claude-bridge
(the script prints the exact folder when it starts).
Screenshots and clicks (the test client only, it runs with VCMI_CLAUDE_PLAYER_DEV): a file
userdata-dev\\claude-bridge\\player\\screenshot.request takes a screenshot anywhere, menus and
lobby included (into userdata-dev\\cache\\extracted\\screenshots; in a game also the `screenshot`
command of vcmi_claude_player.py); ui.request in the same folder with lines "click x y" clicks
there (window coordinates, 1600x900). Messages posted from outside don't arrive: the hidden
desktop never gives the window focus. Save paths with Chinese letters don't work on the VCMI
command line; copy the save to e.g. Saves/_claude_test/ first.
"""
from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_local  # noqa: E402
from h3tools import config  # noqa: E402

INSTALL = vcmi_local.BUILD_ROOT / "install-dev"
USERDATA = vcmi_local.BUILD_ROOT / "userdata-dev"
BRIDGE = USERDATA / "claude-bridge"
CLIENT = INSTALL / "VCMI_client.exe"
DESKTOP = "vcmi-claude-test"

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class STARTUPINFOW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
                ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
                ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD), ("dwXCountChars", wintypes.DWORD),
                ("dwYCountChars", wintypes.DWORD), ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", wintypes.HANDLE), ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, ENUM_PROC, wintypes.LPARAM]
user32.CloseDesktop.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.CreateDesktopW.restype = wintypes.HANDLE
user32.CreateDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
kernel32.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
                                    wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                                    ctypes.POINTER(PROCESS_INFORMATION)]


def prepare() -> None:
    if not CLIENT.exists():
        raise SystemExit(f"{CLIENT} not found -- install a build there first (see the docstring)")
    for sub in ("config", "logs", "cache", "Saves", "claude-bridge"):
        (USERDATA / sub).mkdir(parents=True, exist_ok=True)
    dirs = {"userDataPath": vcmi_local.GAME_DATA.as_posix(),
            "userConfigPath": (USERDATA / "config").as_posix(), "userLogsPath": (USERDATA / "logs").as_posix(),
            "userCachePath": (USERDATA / "cache").as_posix(), "userSavePath": (USERDATA / "Saves").as_posix()}
    (INSTALL / "config" / "dirs.json").write_text(json.dumps(dirs, indent=2), encoding="utf-8")
    src = vcmi_local.GAME_DATA / "config"
    if (src / "modSettings.json").exists():
        shutil.copyfile(src / "modSettings.json", USERDATA / "config" / "modSettings.json")
    path = USERDATA / "config" / "settings.json"
    settings = json.loads((src / "settings.json").read_text(encoding="utf-8")) if (src / "settings.json").exists() else {}
    settings.setdefault("general", {}).update(music=0, sound=0, lastDifficulty=1)  # normal: humans start with 20000 gold
    video = settings.setdefault("video", {})
    video.update(fullscreen=False, realFullscreen=False, showIntro=False)
    video["resolution"] = {"width": 1600, "height": 900, "scaling": 100}
    settings.setdefault("adventure", {}).update(heroMoveTime=0, enemyMoveTime=0)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def visible_windows(pid: int) -> list[int]:
    """Visible top-level windows of `pid` on the desktop this script runs on (the player's)."""
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    user32.EnumWindows(collect, 0)
    return found


def desktop_windows(desk, pid: int) -> list[int]:
    """Top-level windows of `pid` on the hidden desktop."""
    found = []

    @ENUM_PROC
    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            found.append(hwnd)
        return True

    user32.EnumDesktopWindows(desk, collect, 0)
    return found


def launch(argv: list[str]) -> int:
    os.environ["VCMI_CLAUDE_BRIDGE"] = str(BRIDGE)
    os.environ["VCMI_CLAUDE_PLAYER_DEV"] = "1"
    desk = user32.CreateDesktopW(DESKTOP, None, None, 0, 0x10000000, None)  # GENERIC_ALL
    if not desk:
        raise SystemExit(f"CreateDesktopW failed: {ctypes.get_last_error()}")
    si = STARTUPINFOW()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = "WinSta0\\" + DESKTOP
    pi = PROCESS_INFORMATION()
    cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(CLIENT)] + argv))
    flags = 0x00004000 | 0x08000000  # BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
    if not kernel32.CreateProcessW(str(CLIENT), cmdline, None, None, False, flags, None, str(INSTALL),
                                   ctypes.byref(si), ctypes.byref(pi)):
        raise SystemExit(f"CreateProcessW failed: {ctypes.get_last_error()}")
    kernel32.CloseHandle(pi.hThread)
    # Keep our handle to the desktop until the client's window is on it: a desktop nobody holds
    # or uses is destroyed at once, and a client started onto it dies before writing its log.
    started = False
    for _ in range(120):
        time.sleep(0.5)
        if visible_windows(pi.dwProcessId):  # paranoia: it showed up on the visible desktop after all
            subprocess.run(["taskkill", "/PID", str(pi.dwProcessId), "/F"], capture_output=True)
            raise SystemExit("the test client showed a window on the visible desktop -- killed it")
        if kernel32.WaitForSingleObject(pi.hProcess, 0) == 0:
            raise SystemExit("the test client exited right away; see its log")
        if desktop_windows(desk, pi.dwProcessId):
            started = True
            break
    kernel32.CloseHandle(pi.hProcess)
    user32.CloseDesktop(desk)  # from now on the client's own window keeps the desktop alive
    if not started:
        print("warning: no window on the hidden desktop after 60 s")
    return pi.dwProcessId


def kill() -> None:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "Get-CimInstance Win32_Process -Filter \"Name='VCMI_client.exe'\" | "
                          "Where-Object { $_.ExecutablePath -like '*install-dev*' } | "
                          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; $_.ProcessId }"],
                         capture_output=True, text=True)
    print("stopped:", out.stdout.split() or "nothing")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--map", help="map for --testmap (default: dev_map from tools/h3tools/config.py)")
    ap.add_argument("--save", help="start from this save (--testsave) instead of a new map")
    ap.add_argument("--hotseat", default="Claude", help="hotseat names after the first, comma separated ('' for none)")
    ap.add_argument("--claude", default="", help="AI colours to give to Claude, comma separated (as the lobby's 颜色和 Claude window does)")
    ap.add_argument("--menu", action="store_true", help="start at the main menu instead of a --testmap game")
    ap.add_argument("--kill", action="store_true", help="stop running test clients and exit")
    ap.add_argument("extra", nargs="*", help="more client arguments (after --)")
    args = ap.parse_args()
    if args.kill:
        kill()
        return 0
    test_map = args.map or config.dev_map()
    if not (args.menu or args.save or test_map):
        ap.error("no map: pass --map Maps/<name> (or --save, --menu), or set dev_map in h3mod.local.json "
                 "or the H3MOD_DEV_MAP environment variable")
    prepare()
    if args.menu:
        argv = ["--nointro", "--disable-video"] + args.extra
    else:
        argv = ["--testsave", args.save] if args.save else ["--testmap", test_map]
        if args.hotseat:
            argv += ["--testmap-hotseat", args.hotseat]
        else:
            argv += ["--testmap-human"]
        if args.claude:
            argv += ["--testmap-claude", args.claude]
        argv += ["--autoSkip", "--nointro", "--disable-video"] + args.extra
    pid = launch(argv)
    print(f"test client {pid} on hidden desktop '{DESKTOP}': {' '.join(argv)}")
    print(f"logs   {USERDATA / 'logs' / 'VCMI_Client_log.txt'}")
    print(f"bridge {BRIDGE}  (set VCMI_CLAUDE_BRIDGE to this for vcmi_claude_player.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
