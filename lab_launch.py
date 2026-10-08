"""Start/reuse the local panel without keeping a terminal session alive."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent


def is_lab(url):
    try:
        with urlopen(url + "/api/bootstrap", timeout=.5) as response:
            data = json.load(response)
        return data.get("app") == "flylab" and data.get("version") == 8
    except (OSError, ValueError):
        return False


def launch():
    logs = ROOT / "work/lab-stage7"
    logs.mkdir(parents=True, exist_ok=True)
    for port in range(8767, 8778):
        url = f"http://127.0.0.1:{port}"
        if is_lab(url):
            return url
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        name = datetime.now().strftime("server-%Y%m%d-%H%M%S-%f")
        with (logs / f"{name}.log").open("w", encoding="utf-8") as log:
            process = subprocess.Popen([sys.executable, str(ROOT / "lab_server.py"), "--port", str(port)],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, close_fds=True,
                                       creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        for _ in range(100):
            if is_lab(url):
                (logs / "server-info.json").write_text(json.dumps({"pid": process.pid, "url": url}), encoding="utf-8")
                return url
            if process.poll() is not None:
                break
            time.sleep(.1)
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
            raise RuntimeError("Panel nie odpowiedzial. Sprawdz work/lab-stage7/server-*.log")
    raise RuntimeError("Porty 8767..8777 sa zajete")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    url = launch()
    print(url, flush=True)
    if not args.no_open:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
