"""Machine-specific paths for the tools, in one place.

Every value comes from the first of:
  1. its environment variable (H3_GAME_DIR, VCMI_DIR, ...; see KEYS below),
  2. h3mod.local.json at the repository root (copy h3mod.example.json and edit it;
     the environment variable H3MOD_CONFIG can name another file),
  3. a default, for the keys that have one.

    python tools/h3tools/config.py              # every key, its value and where it came from
    python tools/h3tools/config.py vcmi_dir     # just that value (used by tools/vcmi_src/build.bat)

From Python (tools/ on sys.path):

    from h3tools import config
    config.game_dir()      # Path; stops with a one-line message if it isn't configured

Values are looked up when a tool asks for them, not on import, so a tool that never
needs, say, the original game folder runs fine without it. Relative paths in the file
are relative to the repository root; "~" means the user's home folder.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LOCAL_FILE = REPO / "h3mod.local.json"
CONFIG_ENV = "H3MOD_CONFIG"

# key: (environment variable, is a path, what it is)
KEYS: dict[str, tuple[str, bool, str]] = {
    "h3_game_dir": ("H3_GAME_DIR", True,
                    "original Heroes 3 install (the folder with Data/ and Maps/); only read"),
    "vcmi_dir": ("VCMI_DIR", True, "official VCMI install (the folder with VCMI_client.exe)"),
    "vcmi_data": ("VCMI_DATA", True,
                  "VCMI user data folder (Data, Maps, Mods, Saves, config, claude-bridge)"),
    "vcmi_build_root": ("VCMI_BUILD_ROOT", True,
                        "where the patched VCMI is cloned, built and installed (src, build, install, ...)"),
    "vs_root": ("VS_ROOT", True, "Visual Studio 2022 install, for tools/vcmi_src/build.bat"),
    "conan_home": ("CONAN_HOME", True, "Conan cache for the VCMI build"),
    "dev_map": ("H3MOD_DEV_MAP", False, "default map of tools/vcmi_dev_client.py, e.g. Maps/my-map"),
}


def config_file() -> Path:
    """The JSON file in use ($H3MOD_CONFIG, else <repo>/h3mod.local.json). It may not exist."""
    other = os.environ.get(CONFIG_ENV)
    return Path(other).expanduser() if other else LOCAL_FILE


_file_cache: dict[Path, dict] = {}


def _file_values() -> dict:
    path = config_file()
    if path not in _file_cache:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as e:
            raise SystemExit(f"{path}: can't read it as JSON ({e})")
        if not isinstance(data, dict):
            raise SystemExit(f"{path}: expected a JSON object with keys like \"h3_game_dir\"")
        _file_cache[path] = data
    return _file_cache[path]


def _default(key: str) -> str | None:
    if key == "vcmi_data":
        return str(Path.home() / "Documents" / "My Games" / "vcmi")  # VCMI's own default on Windows
    if key == "vcmi_build_root":
        return str(REPO / "build" / "vcmi")
    if key == "conan_home":
        return str(build_root() / "conan")
    if key == "vs_root":
        return _detect_vs()
    return None


def _detect_vs() -> str | None:
    """The newest Visual Studio, as found by vswhere (ships with every VS 2017+ install)."""
    base = os.environ.get("ProgramFiles(x86)")
    if not base:
        return None
    vswhere = Path(base) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        return None
    try:
        res = subprocess.run([str(vswhere), "-latest", "-property", "installationPath"],
                             capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    found = res.stdout.strip().splitlines()
    return found[0].strip() if res.returncode == 0 and found else None


def lookup(key: str) -> tuple[str | None, str]:
    """(raw value or None, where it came from: "env:NAME", "file", "default" or "missing")."""
    if key not in KEYS:
        raise SystemExit(f"unknown config key {key!r}; known keys: {', '.join(KEYS)}")
    env = KEYS[key][0]
    value = os.environ.get(env)
    if value:
        return value, f"env:{env}"
    value = _file_values().get(key)
    if value not in (None, ""):
        if not isinstance(value, str):
            raise SystemExit(f"{config_file()}: \"{key}\" must be a string")
        return value, "file"
    value = _default(key)
    return (value, "default") if value else (None, "missing")


def _missing(key: str) -> SystemExit:
    env, _, what = KEYS[key]
    path = config_file()
    where = ("h3mod.local.json at the repository root (copy h3mod.example.json)" if path == LOCAL_FILE
             else str(path))
    return SystemExit(f"{key} is not set -- {what}: add \"{key}\" to {where} or set the {env} "
                      f"environment variable")


def _as_path(value: str) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else REPO / p


def get(key: str, required: bool = True) -> str | Path | None:
    """The value of `key`: a Path for path keys, a str otherwise. A missing value stops the
    tool with a one-line message, or gives None when required=False."""
    value, _ = lookup(key)
    if value is None:
        if required:
            raise _missing(key)
        return None
    return _as_path(value) if KEYS[key][1] else value


# ---------------------------------------------------------------- named shortcuts

def game_dir() -> Path:
    """Original Heroes 3 install (Data/, Maps/). Read-only."""
    return get("h3_game_dir")


def game_data() -> Path:
    """The original game's Data folder (LOD archives): $H3_DATA, else <h3_game_dir>/Data."""
    other = os.environ.get("H3_DATA")
    return Path(other) if other else game_dir() / "Data"


def vcmi_dir() -> Path:
    """Official VCMI install (VCMI_client.exe)."""
    return get("vcmi_dir")


def vcmi_data() -> Path:
    """VCMI user data: Data, Maps, Mods, Saves, config, logs, claude-bridge."""
    return get("vcmi_data")


def build_root() -> Path:
    """Patched VCMI build tree: src, build, install, install-dev, userdata, userdata-dev."""
    return get("vcmi_build_root")


def bridge_dir() -> Path:
    """Claude bridge folder of the patched client: $VCMI_CLAUDE_BRIDGE, else <vcmi_data>/claude-bridge."""
    other = os.environ.get("VCMI_CLAUDE_BRIDGE")
    return Path(other) if other else vcmi_data() / "claude-bridge"


def vs_root() -> Path:
    return get("vs_root")


def conan_home() -> Path:
    return get("conan_home")


def dev_map() -> str | None:
    """Default map for tools/vcmi_dev_client.py (a VCMI resource name like Maps/x), or None."""
    return get("dev_map", required=False)


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return 0
    if argv:
        try:
            print(get(argv[0]))
        except SystemExit as e:
            print(e, file=sys.stderr)
            return 1
        return 0
    path = config_file()
    print(f"config file: {path}{'' if path.exists() else '  (not found)'}")
    width = max(map(len, KEYS))
    for key, (env, _, what) in KEYS.items():
        value, source = lookup(key)
        shown = str(_as_path(value)) if value and KEYS[key][1] else value or "-"
        print(f"  {key:{width}}  {source:20}  {shown}")
    unknown = [k for k in _file_values() if k not in KEYS and not k.startswith("_")]
    if unknown:
        print(f"unknown keys in {path.name} (ignored): {', '.join(unknown)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
