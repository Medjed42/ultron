"""Developer transport: the same workspace in a normal browser.

    python -m core.workspace.devserver [--port 8790]

Serves core/workspace/web and the same request API the Qt bridge uses, on
127.0.0.1 only, guarded by a random token printed at start (pass it in the
URL fragment: http://127.0.0.1:8790/#token=…). Useful for responsive testing
and automation; the desktop app does not need it. Opening external links and
credentials dialogs are not available here.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent / "web"
TOKEN = os.environ.get("ULTRON_WS_TOKEN") or secrets.token_urlsafe(18)
_clients: list[queue.Queue] = []
_clients_lock = threading.Lock()


def _broadcast(ev: dict) -> None:
    data = json.dumps(ev, ensure_ascii=False, default=str)
    with _clients_lock:
        for q in list(_clients):
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


class Handler(BaseHTTPRequestHandler):
    server_version = "UltronWorkspaceDev/2.0"

    def log_message(self, fmt, *args):
        pass

    def _authorised(self) -> bool:
        return secrets.compare_digest(self.headers.get("X-Ultron-Token", ""), TOKEN) or \
            secrets.compare_digest(dict(p.split("=", 1) for p in (urlsplit(self.path).query or "x=").split("&")
                                        if "=" in p).get("token", ""), TOKEN)

    def _headers(self, status=200, ctype="application/json", extra=None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/events":
            if not self._authorised():
                return self._headers(403)
            self._headers(200, "text/event-stream")
            q: queue.Queue = queue.Queue(maxsize=500)
            with _clients_lock:
                _clients.append(q)
            try:
                self.wfile.write(b": connected\n\n")
                self.wfile.flush()
                while True:
                    try:
                        data = q.get(timeout=15)
                        self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                with _clients_lock:
                    if q in _clients:
                        _clients.remove(q)
            return
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        root = ROOT.resolve()
        target = (ROOT / rel).resolve()
        if root not in target.parents or not target.is_file():      # no path traversal out of web/
            return self._headers(404, "text/plain")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if target.suffix == ".js":
            ctype = "text/javascript; charset=utf-8"
        body = target.read_bytes()
        self._headers(200, ctype, {"Content-Length": str(len(body))})
        self.wfile.write(body)

    def do_POST(self):
        if urlsplit(self.path).path != "/api":
            return self._headers(404, "text/plain")
        if not self._authorised():
            return self._headers(403)
        length = min(int(self.headers.get("Content-Length") or 0), 1_000_000)
        try:
            req = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            req = None
        from core.workspace import service
        out = service.handle(req)
        body = json.dumps(out, ensure_ascii=False, default=str).encode("utf-8")
        self._headers(200, "application/json; charset=utf-8", {"Content-Length": str(len(body))})
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--data-dir", default="", help="keep test conversations out of config/workspace")
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from core.workspace import service, storage
    if args.data_dir:
        storage.set_data_dir(Path(args.data_dir))
    service.subscribe(_broadcast)
    service.orchestrator()
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"ULTRON Workspace dev server: http://127.0.0.1:{args.port}/#token={TOKEN}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
