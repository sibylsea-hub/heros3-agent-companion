"""Claude 中转站: lets VCMI on an iPad, iPhone or another computer talk to Claude on this PC.

The patched client (ClaudeBridge in tools/vcmi_src) talks to Claude through files in
<vcmi_data>/claude-bridge/ (tools/h3tools/config.py; $VCMI_CLAUDE_BRIDGE overrides it).
A phone or tablet can't reach those files, so when the
device's claude-bridge/config.json names this relay, the client speaks HTTP to it instead and
the relay reads and writes the very same files:

  GET  /to-game?offset=N   what Claude wrote (vcmi_say.py) after byte N of to-game.jsonl;
                           -1 = only what comes from now on. Header X-Next-Offset.
  POST /from-game          one JSON line the player said, appended to from-game.jsonl
  PUT  /save?name=REL      a save the device made, stored as remote-saves/REL, where
                           vcmi_watch.py picks it up like a save made on this PC

Every request needs the X-Bridge-Token header. The token is made on first run and kept in
claude-bridge/relay.json.

Usage:
  python -X utf8 tools/vcmi_relay.py          # this PC only (127.0.0.1), for testing
  python -X utf8 tools/vcmi_relay.py --lan    # reachable from the home network
On start it prints the config.json for the device and also writes it to
claude-bridge/device-config.json. On an iPad it goes in the Files app under
我的 iPad › VCMI › claude-bridge › config.json.
"""
from __future__ import annotations

import argparse
import hmac
import json
import os
import secrets
import socket
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import vcmi_say  # noqa: E402

DEFAULT_PORT = 8733
MAX_SAVE = 64 * 1024 * 1024


def log(text: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {text}", flush=True)


def lan_address() -> str:
    """The address other devices on the home network reach this PC at."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.168.0.1", 9))  # no packet is sent; this only picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return socket.gethostbyname(socket.gethostname())


class Relay:
    def __init__(self, bridge: Path):
        self.bridge = bridge
        self.to_game = bridge / "to-game.jsonl"
        self.from_game = bridge / "from-game.jsonl"
        self.saves = bridge / "remote-saves"
        self.lock = threading.Lock()
        bridge.mkdir(parents=True, exist_ok=True)
        conf_path = bridge / "relay.json"
        try:
            conf = json.loads(conf_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            conf = {}
        if not conf.get("token"):
            conf["token"] = secrets.token_urlsafe(16)
            conf_path.write_text(json.dumps(conf, indent=2), encoding="utf-8")
        self.token = conf["token"]

    def read_to_game(self, offset: int) -> tuple[int, bytes]:
        try:
            size = self.to_game.stat().st_size
        except FileNotFoundError:
            return 0, b""
        if offset < 0:
            return size, b""
        if offset > size:  # file was replaced; start over
            offset = 0
        with self.to_game.open("rb") as f:
            f.seek(offset)
            data = f.read(size - offset)
        complete = data[: data.rfind(b"\n") + 1]  # a half-written last line waits for the next call
        return offset + len(complete), complete

    def add_from_game(self, body: bytes) -> dict:
        message = json.loads(body.decode("utf-8"))
        if not isinstance(message, dict) or not isinstance(message.get("text"), str):
            raise ValueError("expected a JSON object with text")
        line = json.dumps(message, ensure_ascii=False) + "\n"
        with self.lock, self.from_game.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line)
        return message

    def store_save(self, name: str, body: bytes) -> Path:
        rel = PurePosixPath(name)
        if rel.is_absolute() or not rel.parts or any(p in ("", ".", "..") or ":" in p for p in rel.parts):
            raise ValueError(f"bad save name {name!r}")
        if rel.suffix.lower() != ".vsgm1":
            raise ValueError(f"not a save: {name!r}")
        dest = self.saves.joinpath(*rel.parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")  # vcmi_watch only looks at *.vsgm1
        tmp.write_bytes(body)
        os.replace(tmp, dest)
        return dest


def make_handler(relay: Relay):
    class Handler(BaseHTTPRequestHandler):
        server_version = "EchoRelay/1"

        def log_message(self, fmt, *args):  # quiet; we log what matters ourselves
            pass

        def reply(self, status: int, body: bytes = b"", headers: dict | None = None) -> None:
            self.send_response(status)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self) -> bool:
            token = self.headers.get("X-Bridge-Token", "")
            if hmac.compare_digest(token.encode(), relay.token.encode()):
                return True
            log(f"拒绝 {self.client_address[0]}：口令不对")
            self.reply(403, b"bad token")
            return False

        def body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_SAVE:
                raise ValueError("too large")
            return self.rfile.read(length)

        def do_GET(self):
            url = urlsplit(self.path)
            if not self.authorized():
                return
            if url.path == "/to-game":
                offset = int(parse_qs(url.query).get("offset", ["-1"])[0])
                nxt, data = relay.read_to_game(offset)
                self.reply(200, data, {"X-Next-Offset": str(nxt), "Content-Type": "application/x-ndjson; charset=utf-8"})
            elif url.path == "/ping":
                self.reply(200, b"ok")
            else:
                self.reply(404, b"not found")

        def do_POST(self):
            url = urlsplit(self.path)
            if not self.authorized():
                return
            try:
                if url.path != "/from-game":
                    return self.reply(404, b"not found")
                m = relay.add_from_game(self.body())
                log(f"{m.get('from', '?')}：{m['text']}")
                self.reply(200, b"ok")
            except (ValueError, UnicodeDecodeError) as e:
                self.reply(400, str(e).encode())

        def do_PUT(self):
            url = urlsplit(self.path)
            if not self.authorized():
                return
            try:
                if url.path != "/save":
                    return self.reply(404, b"not found")
                name = unquote(parse_qs(url.query).get("name", [""])[0])
                data = self.body()
                dest = relay.store_save(name, data)
                log(f"收到存档 {name}（{len(data) // 1024} KB）→ {dest}")
                self.reply(200, b"ok")
            except ValueError as e:
                self.reply(400, str(e).encode())

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lan", action="store_true", help="listen on the home network, not just this PC")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args()

    relay = Relay(vcmi_say.bridge_dir())
    host = "0.0.0.0" if a.lan else "127.0.0.1"
    server = ThreadingHTTPServer((host, a.port), make_handler(relay))
    address = lan_address() if a.lan else "127.0.0.1"
    device = {"relay": f"http://{address}:{a.port}", "token": relay.token}
    (relay.bridge / "device-config.json").write_text(json.dumps(device, indent=2), encoding="utf-8")

    log(f"Claude 中转站在 {host}:{a.port}，文件夹 {relay.bridge}")
    print("设备上的 claude-bridge/config.json 写成：")
    print(json.dumps(device, ensure_ascii=False, indent=2), flush=True)
    if a.lan:
        print("（要是设备连不上：Windows 防火墙得放行这个端口，见 docs/play-together.md）", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
