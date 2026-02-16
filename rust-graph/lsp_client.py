#!/usr/bin/env python3
"""
Minimal LSP client for rust-analyzer.

Spawns rust-analyzer as a subprocess and communicates via JSON-RPC over
stdin/stdout, following the Language Server Protocol.

Usage:
    python lsp_client.py <command> [args...]

Commands:
    symbols <query>                     - Search workspace symbols
    document-symbols <file>             - List symbols in a file
    definition <file> <line> <col>      - Go to definition
    references <file> <line> <col>      - Find all references
    call-in <file> <line> <col>         - Incoming calls (who calls this?)
    call-out <file> <line> <col>        - Outgoing calls (what does this call?)
    hover <file> <line> <col>           - Hover info (type signature, docs)

<file> paths are relative to the project root.
<line> and <col> are 0-indexed.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

PROJECT_ROOT = os.environ.get(
    "RA_PROJECT_ROOT",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "codex", "codex-rs")
    ),
)

# Optional: restrict rust-analyzer to a single crate (relative Cargo.toml path).
# e.g. RA_SINGLE_CRATE="core/Cargo.toml"  — only indexes that crate + its deps.
RA_SINGLE_CRATE = os.environ.get("RA_SINGLE_CRATE", "")

RA_BINARY = os.environ.get("RA_BINARY", shutil.which("rust-analyzer") or "rust-analyzer")

# ---------------------------------------------------------------------------
# Low-level JSON-RPC / LSP transport
# ---------------------------------------------------------------------------


class LspClient:
    """Manages a rust-analyzer subprocess and speaks LSP over stdio."""

    def __init__(self, project_root: str, binary: str = RA_BINARY, single_crate: str = ""):
        self.project_root = os.path.abspath(project_root)
        self.root_uri = f"file://{self.project_root}"
        self.single_crate = single_crate
        self._id = 0
        self._pending: dict[int, threading.Event] = {}
        self._results: dict[int, object] = {}
        self._lock = threading.Lock()
        self._progress_tokens: set[str] = set()
        self._last_progress_end: float = 0.0
        self._any_progress_seen = threading.Event()

        self._proc = subprocess.Popen(
            [binary],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.project_root,
        )
        self._reader_thread = threading.Thread(target=self._read_loop, daemon=True)
        self._reader_thread.start()

    # -- transport ----------------------------------------------------------

    def _send(self, msg: dict):
        body = json.dumps(msg).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        self._proc.stdin.write(header + body)
        self._proc.stdin.flush()

    def _read_message(self) -> dict | None:
        """Read one LSP message (header + body) from stdout."""
        stdout = self._proc.stdout
        headers = {}
        while True:
            line = stdout.readline()
            if not line:
                return None
            line = line.decode("ascii").strip()
            if line == "":
                break
            key, _, value = line.partition(":")
            headers[key.strip()] = value.strip()

        length = int(headers.get("Content-Length", 0))
        if length == 0:
            return None
        body = stdout.read(length)
        return json.loads(body)

    def _read_loop(self):
        while True:
            msg = self._read_message()
            if msg is None:
                break
            method = msg.get("method")

            # Handle server-initiated requests that need a response.
            if method == "window/workDoneProgress/create":
                token = msg["params"]["token"]
                with self._lock:
                    self._progress_tokens.add(str(token))
                # Respond with success (required by the protocol).
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
                continue

            if method == "client/registerCapability":
                self._send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
                continue

            # Track progress notifications — when all progress tokens end,
            # indexing is done.
            if method == "$/progress":
                params = msg.get("params", {})
                token = str(params.get("token", ""))
                value = params.get("value", {})
                kind = value.get("kind")
                if kind == "begin":
                    with self._lock:
                        self._progress_tokens.add(token)
                    self._any_progress_seen.set()
                elif kind == "end":
                    with self._lock:
                        self._progress_tokens.discard(token)
                        self._last_progress_end = time.monotonic()
                pct = value.get("percentage", "")
                msg_text = value.get("message", value.get("title", ""))
                status = f" ({pct}%)" if pct != "" else ""
                if msg_text:
                    print(f"  [progress] {msg_text}{status}", file=sys.stderr)
                continue

            # Handle response to our requests.
            msg_id = msg.get("id")
            if msg_id is not None and msg_id in self._pending:
                with self._lock:
                    self._results[msg_id] = msg.get("result") if "result" in msg else msg.get("error")
                    self._pending[msg_id].set()

    # -- RPC helpers --------------------------------------------------------

    def request(self, method: str, params: dict, timeout: float = 120) -> object:
        with self._lock:
            self._id += 1
            req_id = self._id
            event = threading.Event()
            self._pending[req_id] = event

        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        if not event.wait(timeout=timeout):
            raise TimeoutError(f"LSP request {method} (id={req_id}) timed out after {timeout}s")
        with self._lock:
            return self._results.pop(req_id)

    def notify(self, method: str, params: dict):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    # -- LSP lifecycle ------------------------------------------------------

    def initialize(self, wait_for_indexing: bool = True, index_timeout: float = 300):
        cargo_opts: dict = {
            "buildScripts": {"enable": False},
            # Skip sysroot entirely (std types won't resolve but saves ~100 crates).
            "sysroot": None,
            "sysrootSrc": None,
        }

        # Exclude external dependency sources so RA only indexes workspace code.
        cargo_home = os.environ.get("CARGO_HOME", os.path.expanduser("~/.cargo"))
        exclude_dirs = [
            os.path.join(cargo_home, "registry"),
            os.path.join(cargo_home, "git"),
        ]

        init_options: dict = {
            # Skip proc-macro expansion (biggest memory saver).
            "procMacro": {"enable": False},
            "cargo": cargo_opts,
            # Cap syntax-tree LRU cache (default is 128).
            "lru": {"capacity": 32},
            # Disable background cargo-check (we only need queries).
            "checkOnSave": False,
            # Don't crawl external dependency sources.
            "files": {"excludeDirs": exclude_dirs},
        }

        # When single_crate is set, use linkedProjects to restrict indexing
        # to just that crate (+ its deps) instead of the full workspace.
        if self.single_crate:
            manifest = os.path.join(self.project_root, self.single_crate)
            init_options["linkedProjects"] = [manifest]

        result = self.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": self.root_uri,
                "capabilities": {
                    "textDocument": {
                        "callHierarchy": {"dynamicRegistration": False},
                        "definition": {"dynamicRegistration": False},
                        "references": {"dynamicRegistration": False},
                        "documentSymbol": {
                            "dynamicRegistration": False,
                            "hierarchicalDocumentSymbolSupport": True,
                        },
                        "hover": {"dynamicRegistration": False},
                    },
                    "workspace": {
                        "symbol": {"dynamicRegistration": False},
                    },
                    "window": {
                        "workDoneProgress": True,
                    },
                },
                "workspaceFolders": [{"uri": self.root_uri, "name": "codex-rs"}],
                "initializationOptions": init_options,
            },
        )
        self.notify("initialized", {})

        if wait_for_indexing:
            print("Waiting for rust-analyzer to finish indexing...", file=sys.stderr)
            self._wait_for_indexing(timeout=index_timeout)

        return result

    def _wait_for_indexing(self, timeout: float = 300, quiet_period: float = 3.0):
        """Wait until rust-analyzer has no active progress for `quiet_period` seconds."""
        deadline = time.monotonic() + timeout
        # First, wait until we see at least one progress notification.
        remaining = deadline - time.monotonic()
        if not self._any_progress_seen.wait(timeout=min(remaining, 30)):
            print("No progress notifications received; server may already be ready.", file=sys.stderr)
            return
        # Now poll until there's been a quiet period with no active tokens.
        while time.monotonic() < deadline:
            with self._lock:
                active = len(self._progress_tokens)
                last_end = self._last_progress_end
            if active == 0 and last_end > 0:
                elapsed_since_last = time.monotonic() - last_end
                if elapsed_since_last >= quiet_period:
                    print("Indexing complete.", file=sys.stderr)
                    return
            time.sleep(0.5)
        print("Warning: indexing timed out, results may be incomplete.", file=sys.stderr)

    def shutdown(self):
        self.request("shutdown", {})
        self.notify("exit", {})
        self._proc.wait(timeout=5)

    # -- file helpers -------------------------------------------------------

    def file_uri(self, relpath: str) -> str:
        abspath = os.path.join(self.project_root, relpath)
        return f"file://{os.path.abspath(abspath)}"

    def open_file(self, relpath: str):
        abspath = os.path.join(self.project_root, relpath)
        with open(abspath) as f:
            text = f.read()
        self.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": self.file_uri(relpath),
                    "languageId": "rust",
                    "version": 1,
                    "text": text,
                }
            },
        )

    def close_file(self, relpath: str):
        self.notify(
            "textDocument/didClose",
            {"textDocument": {"uri": self.file_uri(relpath)}},
        )

    # -- LSP queries --------------------------------------------------------

    def workspace_symbols(self, query: str) -> list:
        return self.request("workspace/symbol", {"query": query})

    def document_symbols(self, relpath: str) -> list:
        self.open_file(relpath)
        return self.request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": self.file_uri(relpath)}},
        )

    def definition(self, relpath: str, line: int, col: int):
        self.open_file(relpath)
        return self.request(
            "textDocument/definition",
            {
                "textDocument": {"uri": self.file_uri(relpath)},
                "position": {"line": line, "character": col},
            },
        )

    def references(self, relpath: str, line: int, col: int):
        self.open_file(relpath)
        return self.request(
            "textDocument/references",
            {
                "textDocument": {"uri": self.file_uri(relpath)},
                "position": {"line": line, "character": col},
                "context": {"includeDeclaration": True},
            },
        )

    def hover(self, relpath: str, line: int, col: int):
        self.open_file(relpath)
        return self.request(
            "textDocument/hover",
            {
                "textDocument": {"uri": self.file_uri(relpath)},
                "position": {"line": line, "character": col},
            },
        )

    def call_hierarchy_prepare(self, relpath: str, line: int, col: int):
        self.open_file(relpath)
        return self.request(
            "textDocument/prepareCallHierarchy",
            {
                "textDocument": {"uri": self.file_uri(relpath)},
                "position": {"line": line, "character": col},
            },
        )

    def call_hierarchy_incoming(self, item: dict):
        return self.request("callHierarchy/incomingCalls", {"item": item})

    def call_hierarchy_outgoing(self, item: dict):
        return self.request("callHierarchy/outgoingCalls", {"item": item})


# ---------------------------------------------------------------------------
# Pretty-printing helpers
# ---------------------------------------------------------------------------

SYMBOL_KINDS = {
    1: "File", 2: "Module", 3: "Namespace", 4: "Package", 5: "Class",
    6: "Method", 7: "Property", 8: "Field", 9: "Constructor", 10: "Enum",
    11: "Interface", 12: "Function", 13: "Variable", 14: "Constant",
    15: "String", 16: "Number", 17: "Boolean", 18: "Array", 19: "Object",
    20: "Key", 21: "Null", 22: "EnumMember", 23: "Struct", 24: "Event",
    25: "Operator", 26: "TypeParameter",
}


def uri_to_relpath(uri: str, root: str) -> str:
    prefix = f"file://{root}"
    if uri.startswith(prefix):
        return uri[len(prefix):].lstrip("/")
    return uri


def fmt_location(loc: dict, root: str) -> str:
    uri = loc.get("uri") or loc.get("targetUri", "")
    rng = loc.get("range") or loc.get("targetSelectionRange", {})
    start = rng.get("start", {})
    return f"{uri_to_relpath(uri, root)}:{start.get('line', 0)+1}:{start.get('character', 0)+1}"


def print_json(data):
    print(json.dumps(data, indent=2))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    scope = f" (crate: {RA_SINGLE_CRATE})" if RA_SINGLE_CRATE else " (full workspace)"
    print(f"Connecting to rust-analyzer for project: {PROJECT_ROOT}{scope}", file=sys.stderr)
    client = LspClient(PROJECT_ROOT, single_crate=RA_SINGLE_CRATE)
    client.initialize()
    print("Ready.", file=sys.stderr)

    try:
        if cmd == "symbols":
            query = sys.argv[2] if len(sys.argv) > 2 else ""
            results = client.workspace_symbols(query)
            if not results:
                print("No symbols found.")
                return
            for sym in results:
                kind = SYMBOL_KINDS.get(sym.get("kind", 0), "?")
                loc = fmt_location(sym.get("location", {}), client.project_root)
                container = sym.get("containerName", "")
                prefix = f"[{container}] " if container else ""
                print(f"  {kind:12s}  {prefix}{sym['name']:40s}  {loc}")

        elif cmd == "document-symbols":
            relpath = sys.argv[2]
            results = client.document_symbols(relpath)
            if not results:
                print("No symbols found.")
                return

            def print_symbols(symbols, indent=0):
                for sym in symbols:
                    kind = SYMBOL_KINDS.get(sym.get("kind", 0), "?")
                    # DocumentSymbol format: selectionRange / range at top level.
                    # SymbolInformation format: location.range.
                    rng = sym.get("selectionRange") or sym.get("range") or \
                        sym.get("location", {}).get("range", {})
                    start = rng.get("start", {})
                    loc = f"L{start.get('line', 0)+1}"
                    container = sym.get("containerName", "")
                    prefix = f"[{container}] " if container else ""
                    print(f"{'  ' * indent}  {kind:12s}  {prefix}{sym['name']:40s}  {loc}")
                    if "children" in sym:
                        print_symbols(sym["children"], indent + 1)

            print_symbols(results)

        elif cmd == "definition":
            relpath, line, col = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
            results = client.definition(relpath, line, col)
            if not results:
                print("No definition found.")
                return
            if isinstance(results, dict):
                results = [results]
            for loc in results:
                print(f"  {fmt_location(loc, client.project_root)}")

        elif cmd == "references":
            relpath, line, col = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
            results = client.references(relpath, line, col)
            if not results:
                print("No references found.")
                return
            for loc in results:
                print(f"  {fmt_location(loc, client.project_root)}")

        elif cmd in ("call-in", "call-out"):
            relpath, line, col = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
            items = client.call_hierarchy_prepare(relpath, line, col)
            if not items:
                print("No call hierarchy item at that position.")
                return
            item = items[0]
            print(f"Call hierarchy for: {item['name']}", file=sys.stderr)
            if cmd == "call-in":
                calls = client.call_hierarchy_incoming(item)
            else:
                calls = client.call_hierarchy_outgoing(item)
            if not calls:
                print("No calls found.")
                return
            for call in calls:
                caller = call.get("from") or call.get("to")
                kind = SYMBOL_KINDS.get(caller.get("kind", 0), "?")
                loc = fmt_location(caller, client.project_root)
                print(f"  {kind:12s}  {caller['name']:40s}  {loc}")

        elif cmd == "hover":
            relpath, line, col = sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
            result = client.hover(relpath, line, col)
            if not result:
                print("No hover info.")
                return
            contents = result.get("contents", {})
            if isinstance(contents, dict):
                print(contents.get("value", ""))
            elif isinstance(contents, str):
                print(contents)

        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)
            sys.exit(1)

    finally:
        client.shutdown()


if __name__ == "__main__":
    main()
