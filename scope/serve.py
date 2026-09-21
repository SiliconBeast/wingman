"""`python -m scope serve`: run the dashboard against the files on this computer (no GitHub token)."""
from __future__ import annotations

import json
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .util import ROOT

READABLE = ("data/", "resume/", "config/")
WRITABLE = {"data/applications.json"}


def _resolve(rel: str, allowed) -> Path | None:
    rel = rel.lstrip("/")
    if ".." in Path(rel).parts or not rel.startswith(allowed):
        return None
    path = (ROOT / rel).resolve()
    return path if str(path).startswith(str(ROOT.resolve())) else None


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "docs"), **kwargs)

    def log_message(self, fmt, *args):  # keep the terminal quiet
        pass

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")          # blocks DNS-rebinding tricks

    def _reply(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._host_ok():
            return self._reply(403, b"forbidden")
        url = urlparse(self.path)
        if url.path != "/api/file":
            return super().do_GET()
        rel = parse_qs(url.query).get("path", [""])[0]
        path = _resolve(rel, READABLE)
        if path is None:
            return self._reply(403, b"path not allowed")
        if not path.is_file():
            return self._reply(404, b"not found")
        self._reply(200, path.read_bytes())

    def do_PUT(self):
        if not self._host_ok():
            return self._reply(403, b"forbidden")
        url = urlparse(self.path)
        rel = parse_qs(url.query).get("path", [""])[0].lstrip("/")
        if url.path != "/api/file" or rel not in WRITABLE:
            return self._reply(403, b"only data/applications.json can be written")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        try:
            json.loads(body)
        except json.JSONDecodeError:
            return self._reply(400, b"body is not valid JSON")
        path = ROOT / rel
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        self._reply(200, b"saved")


def serve(port: int = 8765, open_browser: bool = True) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Dashboard running at {url}  (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
