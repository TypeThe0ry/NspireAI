#!/usr/bin/env python3
"""Talk to a running bridge's control socket.

  pagectl.py status
  pagectl.py type 'hello\n'      C escapes: \n Enter, \b Del, \e Esc,
                                 \x0b scroll up, \x0c scroll down, \x0e think,
                                 \x0f keyboard, \x10 menu, \x11 chats
  pagectl.py dump out.png        the page's real framebuffer
  pagectl.py block 240           send a synthetic image block (throughput)
"""
import json
import os
import socket
import sys
from pathlib import Path


def call(request: dict, timeout: float = 60.0) -> dict:
    home = Path(os.environ.get("NSPIREAI_HOME", str(Path.home() / ".config" / "nspireai")))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        sock.connect(str(home / "bridge.sock"))
        sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        data = b""
        while not data.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command = argv[1]
    if command == "status":
        reply = call({"cmd": "status"})
    elif command == "type" and len(argv) == 3:
        text = argv[2].replace("\\e", "\x1b").encode("latin-1").decode("unicode_escape")
        reply = call({"cmd": "inject", "data": text})
    elif command == "dump" and len(argv) == 3:
        reply = call({"cmd": "dump", "path": str(Path(argv[2]).resolve())})
    elif command == "block" and len(argv) == 3:
        reply = call({"cmd": "block", "height": int(argv[2])})
    else:
        print(__doc__)
        return 2
    print(json.dumps(reply))
    return 0 if reply.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
