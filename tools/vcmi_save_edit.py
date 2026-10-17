"""Edit a VCMI 1.7.5 save without marking anyone as a cheater, and write it as a new save.

Usage:
  python -X utf8 tools/vcmi_save_edit.py [<save.vsgm1>] [-o <out.vsgm1>] <edits...>

Without a save, the newest one under <vcmi_data>/Saves is used. The input is never
changed: the result goes next to it as <name>-edited.vsgm1 (-edited-2, ... if taken), so it
shows up in the game's load list right below the original. Edits (run in order):
  --hero <id|name>  --player <colour>
  --give <creature> <count>        --resource <gold|wood|...> <n|+n|-n>
  --artifact <artifact> [slot|backpack]   --exp <n>   --levels <n>
  --primary <attack|defence|spellpower|knowledge> <n|+n|-n>
  --skill <skill> <0-3>   --spell <spell>   --mana <n|+n|-n>   --clear-cheated
Names can be VCMI identifiers (titan) or in-game names (泰坦巨人). Example:
  python -X utf8 tools/vcmi_save_edit.py --hero 孟斐拉 --give titan 10 --resource gold +5000

Uses tools/vcmi_src/save_edit.cpp (built against VCMI 1.7.5; see its README).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_local  # noqa: E402
import vcmi_save  # noqa: E402

EDITOR = vcmi_local.INSTALL / "vcmi_save_edit.exe"


def edited_name(src: Path) -> Path:
    for n in range(1, 100):
        suffix = "-edited" if n == 1 else f"-edited-{n}"
        candidate = src.with_name(src.stem + suffix + src.suffix)
        if not candidate.exists():
            return candidate
    raise SystemExit(f"too many edited copies of {src}")


def edit(src: Path, edits: list[str], out: Path | None = None) -> Path:
    out = out or edited_name(src)
    if out.exists():
        raise SystemExit(f"{out} already exists; not overwriting it")
    if out.resolve() == src.resolve():
        raise SystemExit("refusing to overwrite the input save")
    vcmi_local.prepare()
    res = subprocess.run([str(EDITOR), str(src), str(out), *edits], cwd=vcmi_local.INSTALL, capture_output=True)
    sys.stdout.write(res.stdout.decode("utf-8", "replace"))
    if res.returncode != 0:
        out.unlink(missing_ok=True)
        raise SystemExit(res.stderr.decode("utf-8", "replace").strip() or f"editor failed ({res.returncode})")
    return out


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    src = None
    if not args[0].startswith("-"):
        src = Path(args.pop(0))
    out = None
    if args[:1] == ["-o"]:
        out = Path(args[1])
        args = args[2:]
    src = src or vcmi_save.latest_save()
    print(f"input  {src}")
    result = edit(src, args, out)
    # read the new file back with the reader as a check
    data = vcmi_save.read_save(result)
    for p in data["players"]:
        if p["human"]:
            print(f"check  {p['color']}: cheated={p['cheated']}  " +
                  "  ".join(f"{h['name']}: " + "、".join(f"{s['name']}×{s['count']}" for s in h["army"])
                            for h in p["heroes"]))
    print(f"output {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
