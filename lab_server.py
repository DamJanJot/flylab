"""Loopback-only FlyLab panel. No arbitrary paths, commands or external services."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
from urllib.parse import urlsplit, unquote
import uuid

from flylab import ROOT
from lab_data import Catalog, read_json, experiment_request, write_live_json as write_checkpoint

TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class Jobs:
    def __init__(self, catalog):
        self.catalog = catalog
        self.lock = threading.RLock()
        self.process = None
        self.active = None
        catalog.jobs.mkdir(parents=True, exist_ok=True)

    def status(self):
        with self.lock:
            if self.active is None:
                return {"state": "idle", "progress": 0}
            status = read_json(self.active / "status.json")
            if self.process.poll() is not None and status["state"] not in TERMINAL:
                status.update(state="interrupted", error="Proces zakonczyl sie bez kompletnego wyniku")
                write_checkpoint(self.active / "status.json", status)
            return {"id": self.active.name, **status}

    def start(self, request):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                raise RuntimeError("Inna proba jest jeszcze aktywna")
            if not isinstance(request, dict):
                raise ValueError("Niepoprawne zadanie")
            if request.get("kind") == "simulate" and set(request) == {"kind", "parameters"}:
                experiment_request(request["parameters"])
                key = "run_" + uuid.uuid4().hex
            elif request.get("kind") == "render" and set(request) == {"kind", "recording_id"}:
                self.catalog.locate(request["recording_id"])
                if self.catalog.media(request["recording_id"]):
                    raise ValueError("Nagranie kamery juz istnieje")
                key = "media_" + request["recording_id"]
            else:
                raise ValueError("Nieznany typ zadania")
            directory = self.catalog.jobs / key
            directory.mkdir(parents=True, exist_ok=request["kind"] == "render")
            write_checkpoint(directory / "request.json", request)
            write_checkpoint(directory / "control.json", {"action": "resume"})
            write_checkpoint(directory / "status.json", {"state": "preparing", "progress": 0})
            with (directory / "worker.log").open("w", encoding="utf-8") as log:
                self.process = subprocess.Popen([sys.executable, str(ROOT / "lab_worker.py"), str(directory)],
                                                cwd=ROOT, stdout=log, stderr=log,
                                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.active = directory
            return self.status()

    def control(self, action):
        with self.lock:
            if action not in ("pause", "resume", "cancel"):
                raise ValueError("Nieznana komenda")
            if self.active is None or self.status()["state"] in TERMINAL:
                raise RuntimeError("Brak aktywnej proby")
            write_checkpoint(self.active / "control.json", {"action": action})
            return {**self.status(), "requested": action}

    def close(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                self.control("cancel")
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=10)


def byte_range(header, size):
    if header is None:
        return 0, size - 1, False
    match = re.fullmatch(r"bytes=(\d*)-(\d*)", header)
    if not match or not any(match.groups()) or size <= 0:
        raise ValueError("Invalid range")
    start, end = match.groups()
    if not start:
        length = int(end)
        if length <= 0:
            raise ValueError("Invalid suffix")
        start, end = max(0, size - length), size - 1
    else:
        start, end = int(start), min(int(end), size - 1) if end else size - 1
    if start > end or start >= size:
        raise ValueError("Range outside file")
    return start, end, True


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, catalog=None):
        self.catalog = catalog or Catalog()
        self.jobs = Jobs(self.catalog)
        self.token = secrets.token_urlsafe(32)
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    def allowed_host(self):
        port = self.server.server_port
        return self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")

    def headers_for(self, status, mime, length):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")

    def json_response(self, data, status=200):
        raw = json.dumps(data, allow_nan=False, ensure_ascii=True).encode()
        self.headers_for(status, "application/json; charset=utf-8", len(raw))
        self.end_headers()
        self.wfile.write(raw)

    def serve_file(self, path):
        size = path.stat().st_size
        try:
            start, end, partial = byte_range(self.headers.get("Range"), size)
        except ValueError:
            self.headers_for(416, "text/plain", 0)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return
        self.headers_for(206 if partial else 200, mimetypes.guess_type(path.name)[0] or "application/octet-stream", end - start + 1)
        self.send_header("Accept-Ranges", "bytes")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as source:
            source.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = source.read(min(65536, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    def do_GET(self):
        if not self.allowed_host():
            return self.json_response({"error": "Niedozwolony host"}, 403)
        route = unquote(urlsplit(self.path).path)
        try:
            assets = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css", "/lucide.js": "lucide.min.js"}
            if route in assets:
                return self.serve_file(ROOT / "lab_web" / assets[route])
            if route == "/api/bootstrap":
                return self.json_response({"app": "flylab", "token": self.server.token, "version": 7})
            if route == "/api/catalog":
                return self.json_response(self.server.catalog.entries())
            if route == "/api/job":
                return self.json_response(self.server.jobs.status())
            if route.startswith("/api/recordings/"):
                return self.json_response(self.server.catalog.detail(route[len("/api/recordings/"):]))
            if route.startswith("/media/") and route.endswith(".mp4"):
                path = self.server.catalog.media(route[len("/media/"):-4])
                if path:
                    return self.serve_file(path)
            self.json_response({"error": "Nie znaleziono"}, 404)
        except (FileNotFoundError, KeyError):
            self.json_response({"error": "Brak kompletnego zapisu"}, 404)
        except ValueError:
            self.json_response({"error": "Niepoprawny identyfikator lub uszkodzony zapis"}, 422)
        except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
            pass

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected = f"http://{self.headers.get('Host')}"
        if not self.allowed_host() or origin != expected or self.headers.get("X-Lab-Token") != self.server.token:
            return self.json_response({"error": "Odrzucono obce zadanie"}, 403)
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 8192 or self.headers.get_content_type() != "application/json":
                return self.json_response({"error": "Niepoprawne dane"}, 400)
            request = json.loads(self.rfile.read(length))
            if self.path == "/api/jobs":
                return self.json_response(self.server.jobs.start(request), 202)
            if self.path == "/api/control" and isinstance(request, dict) and set(request) == {"action"}:
                return self.json_response(self.server.jobs.control(request["action"]))
            self.json_response({"error": "Nieznana operacja"}, 404)
        except (ValueError, TypeError) as error:
            self.json_response({"error": str(error)}, 400)
        except RuntimeError as error:
            self.json_response({"error": str(error)}, 409)
        except FileNotFoundError:
            self.json_response({"error": "Brak zapisu"}, 404)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()
    server = Server(args.port)
    print(f"FlyLab: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.jobs.close()
        server.server_close()


if __name__ == "__main__":
    main()
