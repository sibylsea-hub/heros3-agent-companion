"""Load a map in the real VCMI client and report whether it starts cleanly.

Usage: python -X utf8 tools/vcmi_check.py <map.h3m|map.vmap> [--seconds 25] [--screenshot out.png]

The map is copied to <VCMI data>/Maps/_check/, the client is started with
--testmap, and the client log is scanned for map warnings, errors and the
"game started" marker. The client is killed after the marker appears or the
timeout runs out.

Paths (official VCMI install, VCMI user data, patched build) come from
tools/h3tools/config.py (h3mod.local.json).

Default: the installed official client, every colour AI. Its spectator
interface crashes in VCMI 1.7.5 right after "Initializing the interface for
player invalid" -- after the map is fully loaded and the game state is built --
so that known crash is ignored and anything logged before it counts.
(--headless is worse: it crashes before the map loads.)

--screenshot: use the self-built, patched 1.7.5 (tools/vcmi_src) instead. It
keeps the first colour human (--testmap-human), waits until that player's
adventure map is up and saves a PNG of the game window (windowed 1600x900,
muted). Its config/logs/saves stay in <vcmi_build_root>/userdata.

The script also checks which map the log says was opened: the official
client's lobby can swap in settings.json's lastMap after --testmap picked the
map (the patched client sets lastMap first), and a run on the wrong map fails.
"""
from __future__ import annotations

import argparse
import ctypes
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_local  # noqa: E402
from h3tools import config  # noqa: E402
from h3tools.png import write_png  # noqa: E402

DATA_DIR = vcmi_local.GAME_DATA
LOG = DATA_DIR / "logs" / "VCMI_Client_log.txt"
STARTED = "Preparing interface for player"
HUMAN_READY = re.compile(r"Player \w+ will be lead by human")
SPECTATOR = "Initializing the interface for player invalid"
PROBLEM = re.compile(r"] (ERROR|WARN) |Disaster happened|Exception")
OPENED = re.compile(r"Open map file: (.+)")


def capture_window(pid: int, out: Path) -> bool:
    """Save the largest visible top-level window of `pid` as PNG (PrintWindow,
    so it works even when the window is covered by others)."""
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.SetProcessDPIAware()
    windows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def collect(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            windows.append(((r.right - r.left) * (r.bottom - r.top), hwnd, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(collect, 0)
    if not windows:
        return False
    _, hwnd, w, h = max(windows)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    window_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, w, h)
    gdi32.SelectObject(mem_dc, bitmap)
    user32.PrintWindow(hwnd, mem_dc, 2)  # PW_RENDERFULLCONTENT: needed for GPU-rendered windows
    header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down BGRA
    bgra = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem_dc, bitmap, 0, h, bgra, ctypes.byref(header), 0)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, window_dc)

    rgb = bytearray(w * h * 3)
    raw = bgra.raw
    rgb[0::3], rgb[1::3], rgb[2::3] = raw[2::4], raw[1::4], raw[0::4]
    out.parent.mkdir(parents=True, exist_ok=True)
    write_png(out, w, h, bytes(rgb), channels=3)
    return True


def check(map_path: Path, seconds: float, screenshot: Path | None = None) -> int:
    official = None if screenshot else config.vcmi_dir()  # before copying, so a missing key leaves nothing behind
    dest_dir = DATA_DIR / "Maps" / "_check"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / map_path.name
    shutil.copyfile(map_path, dest)
    resource = f"Maps/_check/{map_path.stem}"

    if screenshot:
        vcmi_local.prepare(quiet=True)
        exe_dir, log = vcmi_local.INSTALL, vcmi_local.CLIENT_LOG
        extra = ["--testmap-human"]
        ready = lambda text: HUMAN_READY.search(text) is not None  # noqa: E731
    else:
        exe_dir, log = official, LOG
        extra = []
        ready = lambda text: STARTED in text  # noqa: E731

    log.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [str(exe_dir / "VCMI_client.exe"), "--nointro", "--disable-video", "--testmap", resource, *extra],
        cwd=exe_dir, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    started = shot = False
    deadline = time.time() + seconds
    try:
        while time.time() < deadline and proc.poll() is None:
            time.sleep(1)
            if log.exists() and ready(log.read_text(encoding="utf-8", errors="replace")):
                started = True
                time.sleep(4 if screenshot else 2)  # let the map draw / late errors get logged
                if screenshot and proc.poll() is None:
                    shot = capture_window(proc.pid, screenshot)
                break
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        (exe_dir / "VCMI_client.exe_crashinfo.dmp").unlink(missing_ok=True)
        dest.unlink(missing_ok=True)          # keep test maps out of the in-game map list

    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    if SPECTATOR in text:
        text = text[: text.index(SPECTATOR)]
    problems = [line for line in text.splitlines() if PROBLEM.search(line)]
    opened = OPENED.findall(text)
    right_map = bool(opened) and opened[-1].strip().lower() == resource.lower()
    print(f"map: {resource}")
    if not right_map:
        print(f"WRONG MAP: the game opened {opened[-1].strip() if opened else 'nothing'}; run it again")
    print(f"game started: {started}")
    if screenshot:
        print(f"screenshot: {screenshot if shot else 'FAILED'}")
    for line in problems[:40]:
        print("  ", line[:240])
    if len(problems) > 40:
        print(f"   ... {len(problems) - 40} more")
    ok = started and right_map and not any("ERROR" in p or "Disaster" in p for p in problems)
    return 0 if ok and (shot or not screenshot) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("map", type=Path)
    ap.add_argument("--seconds", type=float, default=25)
    ap.add_argument("--screenshot", type=Path, help="save a PNG of the adventure map (self-built client)")
    a = ap.parse_args()
    sys.exit(check(a.map, a.seconds, a.screenshot))
