"""Ask the real VCMI client to load every map and campaign and export their
texts as it decoded them ("translate maps" console command).

    PYTHONIOENCODING=utf-8 python -X utf8 tools/vcmi_texts.py <mod> [--show]

Proves three things at once: every map/campaign of the mod parses, the
campaign (.vcmp) header and scenarios load, and the text came out in the right
encoding. Prints problems from the log and the number of strings per file;
--show prints the strings themselves. Opens the game window for ~20 s.
Uses the official VCMI install (vcmi_dir) and its user data (vcmi_data), see
tools/h3tools/config.py.
"""
import json
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h3tools import config  # noqa: E402

DATA = config.vcmi_data()
EXPORT = DATA / "cache" / "extracted" / "translation"
LOG = DATA / "logs" / "VCMI_Client_log.txt"


def main():
    if "-h" in sys.argv or "--help" in sys.argv:
        print(__doc__.strip())
        return 0
    vcmi = config.vcmi_dir()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__.strip())
        return 2
    mod = args[0]
    out = EXPORT / f"{mod}.json"
    out.unlink(missing_ok=True)
    p = subprocess.Popen([str(vcmi / "VCMI_client.exe"), "--nointro", "--disable-video"], cwd=vcmi,
                         stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(10)
        p.stdin.write(b"translate maps\n")
        p.stdin.flush()
        for _ in range(120):
            time.sleep(1)
            if out.exists():
                time.sleep(3)
                break
    finally:
        p.kill()
        p.wait()

    log = LOG.read_text(encoding="utf-8", errors="replace") if LOG.exists() else ""
    problems = [l for l in log.splitlines() if re.search(r"is invalid|\] (WARN|ERROR) ", l) and mod in l]
    for l in problems:
        print("PROBLEM:", l[:240])
    if not out.exists():
        print(f"no export for mod {mod!r}")
        return 1
    strings = json.loads(out.read_text(encoding="utf-8"))
    per_file = Counter(re.match(r"(map|campaign)\.([^.]+)", k).group(2) for k in strings
                       if re.match(r"(map|campaign)\.", k))
    for name, n in sorted(per_file.items()):
        print(f"{name}: {n} strings")
    garbled = [k for k, v in strings.items() if "\ufffd" in str(v)]
    for k in garbled:
        print("GARBLED:", k)
    if "--show" in sys.argv:
        for k, v in strings.items():
            print(k, "=>", str(v).replace("\n", " / ")[:100])
    return 1 if problems or garbled or not per_file else 0


if __name__ == "__main__":
    sys.exit(main())
