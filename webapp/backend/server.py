"""
Live-reload dev server for the knowledge graph webapp.

Zero dependencies — uses only Python stdlib.

Serves static files from webapp/frontend/, serves knowledge_graph.json
from the project root, and pushes reload events via SSE when any
frontend file changes.

Usage:
    python webapp/backend/server.py [--port PORT]
"""

import http.server
import logging
import os
import socketserver
import threading
import time
import argparse
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("devserver")

ROOT = Path(__file__).resolve().parent.parent.parent  # knowledge-graph/
FRONTEND = ROOT / "webapp" / "frontend"
POLL_INTERVAL = 0.5  # seconds

# ── File watcher ──────────────────────────────────────────────────────

_clients: list = []  # SSE client wfile handles
_clients_lock = threading.Lock()


def _snapshot(directory: Path) -> dict[str, float]:
    """Return {relative_path: mtime} for all files under directory."""
    snap = {}
    for p in directory.rglob("*"):
        if p.is_file():
            snap[str(p)] = p.stat().st_mtime
    return snap


def _watcher():
    prev = _snapshot(FRONTEND)
    while True:
        time.sleep(POLL_INTERVAL)
        curr = _snapshot(FRONTEND)
        if curr != prev:
            prev = curr
            _notify_clients()


def _notify_clients():
    with _clients_lock:
        dead = []
        for wfile in _clients:
            try:
                wfile.write(b"data: reload\n\n")
                wfile.flush()
            except Exception:
                dead.append(wfile)
        for d in dead:
            _clients.remove(d)
        if _clients:
            log.info("Reload pushed to %d client(s)", len(_clients))


# ── Reload script injected into HTML ─────────────────────────────────

RELOAD_SCRIPT = b"""<script>
(function(){
  function connect() {
    var es = new EventSource('/events');
    es.onmessage = function() { location.reload(); };
    es.onerror = function() { es.close(); setTimeout(connect, 1000); };
  }
  connect();
})();
</script>"""

# ── HTTP handler ──────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]

        # SSE endpoint
        if path == "/events":
            self._handle_sse()
            return

        # Serve knowledge_graph.json from project root
        if path == "/knowledge_graph.json":
            self._serve_file(ROOT / "output" / "knowledge_graph.json", "application/json")
            return

        # Static files from frontend/
        if path == "/" or path == "":
            path = "/index.html"

        file_path = FRONTEND / path.lstrip("/")
        file_path = file_path.resolve()

        # Security: stay within FRONTEND
        if not str(file_path).startswith(str(FRONTEND)):
            self.send_error(403)
            return

        if file_path.is_file():
            self._serve_file(file_path)
        else:
            self.send_error(404)

    def _serve_file(self, file_path: Path, content_type: str | None = None):
        if content_type is None:
            content_type = self._guess_type(file_path)

        data = file_path.read_bytes()

        # Inject reload script into HTML
        if content_type.startswith("text/html"):
            data = data.replace(b"</body>", RELOAD_SCRIPT + b"\n</body>")

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data)

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        with _clients_lock:
            _clients.append(self.wfile)
        # Keep connection open
        try:
            while True:
                time.sleep(1)
        except Exception:
            pass
        finally:
            with _clients_lock:
                if self.wfile in _clients:
                    _clients.remove(self.wfile)

    @staticmethod
    def _guess_type(path: Path) -> str:
        ext = path.suffix.lower()
        return {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }.get(ext, "application/octet-stream")

    def log_message(self, format, *args):
        # Route HTTP request logging through the logging module
        # Skip SSE /events since those are long-lived connections
        msg = format % args if args else format
        if "/events" not in msg:
            log.info("%s %s", self.client_address[0], msg)

    def log_error(self, format, *args):
        msg = format % args if args else format
        log.error("%s %s", self.client_address[0], msg)


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Live-reload dev server")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()

    # Start file watcher thread
    t = threading.Thread(target=_watcher, daemon=True)
    t.start()

    server = ThreadedServer(("127.0.0.1", args.port), Handler)
    log.info("Dev server running at http://localhost:%d", args.port)
    log.info("Serving frontend from %s", FRONTEND)
    log.info("Live reload active (polling every %ss)", POLL_INTERVAL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
