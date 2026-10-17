"""Zip the PC's VCMI data (Data, Maps, Mp3, Mods) for copying to a phone / iPad.

    python -X utf8 tools/make_mobile_bundle.py [--out out/vcmi-data.zip]

The zip unpacks to four folders that go straight into VCMI's data folder on
the device (see docs/mobile.md). Test leftovers (Maps/_check) are skipped.
The data comes from vcmi_data (tools/h3tools/config.py); the zip goes to
<repo>/out/vcmi-data.zip unless --out says otherwise.
"""
import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from h3tools import config  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = config.vcmi_data()
FOLDERS = ["Data", "Maps", "Mp3", "Mods"]
SKIP_DIRS = {"_check"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=ROOT / "out" / "vcmi-data.zip")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with zipfile.ZipFile(a.out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for folder in FOLDERS:
            for f in sorted((DATA / folder).rglob("*")):
                if f.is_file() and not SKIP_DIRS & set(f.relative_to(DATA).parts):
                    z.write(f, f.relative_to(DATA))
                    total += f.stat().st_size
    print(f"{a.out}: {total / 2**20:.0f} MB of data -> {a.out.stat().st_size / 2**20:.0f} MB zip")


if __name__ == "__main__":
    main()
