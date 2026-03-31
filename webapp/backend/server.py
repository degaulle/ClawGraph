"""
Live-reload dev server for the knowledge graph webapp.

Zero dependencies — uses only Python stdlib.

Serves static files from webapp/frontend/, serves knowledge_graph.json
from the project root, and pushes reload events via SSE when any
frontend file changes.

Usage:
    python webapp/backend/server.py --public [--port PORT]
    python webapp/backend/server.py --password SECRET [--port PORT]
"""

import http.server
import json
import logging
import logging.handlers
import os
import re
import secrets
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import argparse
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent.parent.parent  # knowledge-graph/
FRONTEND = ROOT / "webapp" / "frontend"

# Load .env file from project root (if it exists) — simple key=value, no quotes needed
_env_file = ROOT / ".env"
if _env_file.is_file():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
LOG_FILE = ROOT / "webapp" / "server.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=3,
        ),
    ],
)
log = logging.getLogger("devserver")
POLL_INTERVAL = 0.5  # seconds

# ── File watcher ──────────────────────────────────────────────────────

_clients: list = []  # SSE client wfile handles
_clients_lock = threading.Lock()

_frontend_state: dict = {}
_state_lock = threading.Lock()

_command_clients: list = []  # SSE client wfile handles for /commands
_command_clients_lock = threading.Lock()

_valid_sessions: set = set()  # session tokens for password auth

# ── Dynamic repo build state ─────────────────────────────────────────
# Ensure project root is importable (for repo_builder, git_log_parser, etc.)
sys.path.insert(0, str(ROOT))

from repo_builder import validate_repo, job_id_for, build_repo_graph

GENERATED_DIR = ROOT / "generated"
MAX_CONCURRENT_BUILDS = 2
GENERATED_TTL = 3600  # 1 hour

# _builds[job_id] = {
#     "status": "building" | "done" | "error",
#     "error": str | None,
#     "sse_clients": [wfile, ...],
#     "thread": Thread,
#     "last_event": {"stage": ..., "progress": ..., "message": ...},
# }
_builds: dict[str, dict] = {}
_builds_lock = threading.Lock()


def _build_notify(job_id: str, stage: str, progress: float, message: str):
    """Push an SSE event to all clients watching this build."""
    event = {"stage": stage, "progress": progress, "message": message}
    payload = ("data: " + json.dumps(event) + "\n\n").encode()
    with _builds_lock:
        build = _builds.get(job_id)
        if not build:
            return
        build["last_event"] = event
        dead = []
        for wfile in build["sse_clients"]:
            try:
                wfile.write(payload)
                wfile.flush()
            except Exception:
                dead.append(wfile)
        for d in dead:
            build["sse_clients"].remove(d)


def _run_build(job_id: str, owner: str, name: str, clone_url: str):
    """Background thread: clone + build, push SSE progress."""
    output_dir = str(GENERATED_DIR / job_id)
    try:
        build_repo_graph(
            owner, name, clone_url, output_dir,
            progress_cb=lambda stage, prog, msg: _build_notify(job_id, stage, prog, msg),
        )
        with _builds_lock:
            _builds[job_id]["status"] = "done"
    except Exception as e:
        log.error("Build failed for %s: %s", job_id, e)
        _build_notify(job_id, "error", 0, str(e))
        with _builds_lock:
            _builds[job_id]["status"] = "error"
            _builds[job_id]["error"] = str(e)


def _reaper():
    """Periodically delete generated graphs older than TTL."""
    while True:
        time.sleep(300)
        if not GENERATED_DIR.is_dir():
            continue
        now = time.time()
        for entry in GENERATED_DIR.iterdir():
            if not entry.is_dir():
                continue
            try:
                age = now - entry.stat().st_mtime
                if age > GENERATED_TTL:
                    # Don't reap if there's an active build
                    with _builds_lock:
                        b = _builds.get(entry.name)
                        if b and b["status"] == "building":
                            continue
                    shutil.rmtree(entry, ignore_errors=True)
                    with _builds_lock:
                        _builds.pop(entry.name, None)
                    log.info("Reaped old graph: %s (age: %ds)", entry.name, int(age))
            except OSError:
                pass


def _push_command(data: dict):
    """Broadcast a command to all connected /commands SSE clients."""
    payload = "data: " + json.dumps(data) + "\n\n"
    raw = payload.encode()
    with _command_clients_lock:
        dead = []
        for wfile in _command_clients:
            try:
                wfile.write(raw)
                wfile.flush()
            except Exception:
                dead.append(wfile)
        for d in dead:
            _command_clients.remove(d)


def _resolve_cursor_cli() -> Path | None:
    """Find the latest cursor-server remote CLI binary."""
    base = Path.home() / ".cursor-server" / "bin" / "linux-x64"
    if not base.is_dir():
        return None
    # Pick the most recently modified version directory
    versions = sorted(
        (d for d in base.iterdir() if d.is_dir()),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    for v in versions:
        cli = v / "bin" / "remote-cli" / "cursor"
        if cli.is_file():
            return cli
    return None


def _resolve_cursor_ipc() -> Path | None:
    """Find the most recent VS Code IPC socket for the Cursor connection."""
    ipc_dir = Path(f"/run/user/{os.getuid()}")
    if not ipc_dir.is_dir():
        return None
    socks = sorted(
        ipc_dir.glob("vscode-ipc-*.sock"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return socks[0] if socks else None


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
    public_mode: bool = False
    password_mode: bool = False
    _expected_password: str | None = None

    def end_headers(self):
        """Inject any pending Set-Cookie headers before finalising."""
        for cookie in getattr(self, '_pending_cookies', []):
            self.send_header('Set-Cookie', cookie)
        super().end_headers()

    def _check_auth(self) -> bool:
        """Verify request is authenticated.  Returns True if OK, else sends 403."""
        self._pending_cookies = []
        if not self.password_mode:
            return True
        # 1) Check session cookie
        cookie_header = self.headers.get('Cookie', '')
        for part in cookie_header.split(';'):
            part = part.strip()
            if part.startswith('kg_session='):
                token = part[len('kg_session='):]
                if token in _valid_sessions:
                    return True
        # 2) Check password query param
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        pw = params.get('password', [None])[0]
        if pw is not None and secrets.compare_digest(pw, self._expected_password):
            token = secrets.token_hex(32)
            _valid_sessions.add(token)
            self._pending_cookies.append(
                f'kg_session={token}; Path=/; HttpOnly; SameSite=Strict'
            )
            return True
        self.send_error(403, "Authentication required")
        return False

    def do_GET(self):
        if not self._check_auth():
            return

        path = self.path.split("?")[0]

        # SSE endpoint (live-reload)
        if path == "/events":
            self._handle_sse()
            return

        # SSE endpoint (remote commands)
        if path == "/commands":
            self._handle_command_sse()
            return

        # Frontend state endpoint
        if path == "/state":
            with _state_lock:
                body = json.dumps(_frontend_state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
            return

        # SSE endpoint (build progress)
        if path == "/api/build-events":
            self._handle_build_sse()
            return

        # Serve a generated graph
        if path.startswith("/api/graph/"):
            self._serve_generated_graph(path)
            return

        # Serve knowledge_graph.json from project root
        if path == "/knowledge_graph.json":
            self._serve_file(ROOT / "output" / "knowledge_graph.json", "application/json")
            return

        # Static files from frontend/
        if path == "/" or path == "":
            # Default: comparison view; /index.html for single graph
            path = "/compare.html"

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

    def do_POST(self):
        if not self._check_auth():
            return

        path = self.path.split("?")[0]

        # /api/build is allowed in all modes
        if path == "/api/build":
            self._handle_build_request()
            return

        if self.public_mode and path in ("/state", "/command", "/open-in-cursor", "/cursor-running"):
            self.send_error(403, "Endpoint disabled in public mode")
            return

        if path == "/state":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self.send_error(400, "Invalid JSON")
                return
            global _frontend_state
            with _state_lock:
                _frontend_state = data
            self.send_response(204)
            self.end_headers()
            return

        if path == "/command":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self.send_error(400, "Invalid JSON")
                return
            if "action" not in data:
                self.send_error(400, "Missing 'action' field")
                return
            _push_command(data)
            self.send_response(204)
            self.end_headers()
            return

        if path == "/cursor-running":
            cli = _resolve_cursor_cli()
            if cli is None:
                self._send_json(200, {"running": False, "error": "No cursor-server installation found"})
                return
            env = os.environ.copy()
            if not env.get("VSCODE_IPC_HOOK_CLI"):
                sock = _resolve_cursor_ipc()
                if sock is None:
                    self._send_json(200, {"running": False, "error": "No active IPC socket found"})
                    return
                env["VSCODE_IPC_HOOK_CLI"] = str(sock)
            try:
                result = subprocess.run(
                    [str(cli), "--list-extensions"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    timeout=5,
                )
                self._send_json(200, {"running": result.returncode == 0})
            except (OSError, subprocess.TimeoutExpired):
                self._send_json(200, {"running": False, "error": "CLI check failed"})
            return

        if path == "/open-in-cursor":
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self.send_error(400, "Invalid JSON")
                return
            file_arg = data.get("file")
            if not file_arg:
                self.send_error(400, "Missing 'file' field")
                return
            # Resolve relative paths against the neighboring codex repo
            fp = Path(file_arg)
            if not fp.is_absolute():
                fp = ROOT.parent / "codex" / fp
            file_path = str(fp)
            cli = _resolve_cursor_cli()
            if cli is None:
                self._send_json(500, {"error": "No cursor-server installation found"})
                return
            # Resolve IPC socket if not already in the environment
            env = os.environ.copy()
            if not env.get("VSCODE_IPC_HOOK_CLI"):
                sock = _resolve_cursor_ipc()
                if sock is None:
                    self._send_json(500, {"error": "No active IPC socket found (is Cursor connected?)"})
                    return
                env["VSCODE_IPC_HOOK_CLI"] = str(sock)
            # Build the --goto argument: file:line:column
            target = file_path
            if "line" in data:
                target += f":{data['line']}"
                if "column" in data:
                    target += f":{data['column']}"
            try:
                subprocess.Popen(
                    [str(cli), "--goto", target],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
            except OSError as e:
                self._send_json(500, {"error": f"Failed to launch cursor: {e}"})
                return
            self.send_response(204)
            self.end_headers()
            return

        self.send_error(404)

    # ── Dynamic build endpoints ────────────────────────────────────

    def _handle_build_request(self):
        """POST /api/build — start or check a graph build."""
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "Invalid JSON"})
            return

        repo_str = data.get("repo", "").strip()
        if not repo_str:
            self._send_json(400, {"error": "Missing 'repo' field"})
            return

        try:
            owner, name, clone_url = validate_repo(repo_str)
        except ValueError as e:
            self._send_json(400, {"error": str(e)})
            return

        jid = job_id_for(owner, name)

        with _builds_lock:
            # Already built and cached on disk (generated/ or output/)?
            graph_path = GENERATED_DIR / jid / "knowledge_graph.json"
            presaved_path = ROOT / "output" / jid / "knowledge_graph.json"
            if graph_path.is_file() or presaved_path.is_file():
                existing = _builds.get(jid)
                if not existing or existing["status"] != "building":
                    self._send_json(200, {"job_id": jid, "status": "cached"})
                    return

            # Already building?
            existing = _builds.get(jid)
            if existing and existing["status"] == "building":
                self._send_json(200, {"job_id": jid, "status": "building"})
                return

            # Concurrency limit
            active = sum(1 for b in _builds.values() if b["status"] == "building")
            if active >= MAX_CONCURRENT_BUILDS:
                self._send_json(429, {"error": "Too many concurrent builds. Try again shortly."})
                return

            # Start build
            _builds[jid] = {
                "status": "building",
                "error": None,
                "sse_clients": [],
                "last_event": {"stage": "queued", "progress": 0, "message": "Starting..."},
            }
            t = threading.Thread(
                target=_run_build, args=(jid, owner, name, clone_url), daemon=True,
            )
            _builds[jid]["thread"] = t
            t.start()

        self._send_json(200, {"job_id": jid, "status": "started"})

    def _handle_build_sse(self):
        """GET /api/build-events?repo=job_id — SSE stream of build progress."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        jid = params.get("repo", [None])[0]
        if not jid:
            self.send_error(400, "Missing repo param")
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        with _builds_lock:
            build = _builds.get(jid)
            if build and build["status"] == "building":
                # Build in progress — send catch-up event and subscribe
                if build.get("last_event"):
                    payload = ("data: " + json.dumps(build["last_event"]) + "\n\n").encode()
                    try:
                        self.wfile.write(payload)
                        self.wfile.flush()
                    except Exception:
                        return
                build["sse_clients"].append(self.wfile)
            else:
                # Build finished or not found — send final event immediately
                graph_path = GENERATED_DIR / jid / "knowledge_graph.json"
                presaved_path = ROOT / "output" / jid / "knowledge_graph.json"
                if graph_path.is_file() or presaved_path.is_file():
                    event = {"stage": "done", "progress": 1.0, "message": "Graph ready (cached)"}
                elif build and build["status"] == "error":
                    event = {"stage": "error", "progress": 0, "message": build.get("error", "Build failed")}
                else:
                    event = {"stage": "error", "progress": 0, "message": "No build found"}
                payload = ("data: " + json.dumps(event) + "\n\n").encode()
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except Exception:
                    pass
                return

        # Keep connection open until build finishes
        try:
            while True:
                time.sleep(1)
                with _builds_lock:
                    build = _builds.get(jid)
                    if not build or build["status"] != "building":
                        break
        except Exception:
            pass
        finally:
            with _builds_lock:
                build = _builds.get(jid)
                if build and self.wfile in build["sse_clients"]:
                    build["sse_clients"].remove(self.wfile)

    def _serve_generated_graph(self, path: str):
        """GET /api/graph/{job_id} — serve a dynamically generated graph."""
        jid = path[len("/api/graph/"):]
        # Security: only allow safe characters
        if not re.match(r'^[a-zA-Z0-9._-]+$', jid):
            self.send_error(400, "Invalid job ID")
            return
        # Check generated/ first, then output/ for pre-saved graphs
        graph_path = GENERATED_DIR / jid / "knowledge_graph.json"
        if not graph_path.is_file():
            graph_path = ROOT / "output" / jid / "knowledge_graph.json"
        if graph_path.is_file():
            self._serve_file(graph_path, "application/json")
        else:
            self.send_error(404, "Graph not found")

    def _send_json(self, status: int, data: dict):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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

    def _handle_command_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        with _command_clients_lock:
            _command_clients.append(self.wfile)
        try:
            while True:
                time.sleep(1)
        except Exception:
            pass
        finally:
            with _command_clients_lock:
                if self.wfile in _command_clients:
                    _command_clients.remove(self.wfile)

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
        if "/events" not in msg and "/commands" not in msg and "/build-events" not in msg:
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
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--public", action="store_true",
        help="Read-only mode: disable control endpoints for safe public exposure",
    )
    mode.add_argument(
        "--password", type=str, metavar="SECRET",
        help="Full-access mode gated behind a URL password",
    )
    args = parser.parse_args()
    Handler.public_mode = args.public
    Handler.password_mode = args.password is not None
    Handler._expected_password = args.password

    # Start file watcher thread
    t = threading.Thread(target=_watcher, daemon=True)
    t.start()

    # Start generated graph reaper thread
    r = threading.Thread(target=_reaper, daemon=True)
    r.start()

    server = ThreadedServer(("0.0.0.0", args.port), Handler)
    log.info("Dev server running at http://localhost:%d", args.port)
    log.info("Serving frontend from %s", FRONTEND)
    log.info("Live reload active (polling every %ss)", POLL_INTERVAL)
    if args.password:
        log.info("Password-protected access: http://localhost:%d/?password=%s", args.port, args.password)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
