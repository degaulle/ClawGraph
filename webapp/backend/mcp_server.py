"""
MCP server exposing knowledge graph UI controls as tools.

Talks to the webapp dev server via its /state and /command endpoints.
Authenticates once at startup using the KG_PASSWORD env var, then reuses
the session cookie for all subsequent requests.

Uses stdio transport — Claude Code launches it as a subprocess.

Usage:
    KG_PASSWORD=secret python webapp/backend/mcp_server.py
"""

import json
import os
import urllib.request
from mcp.server.fastmcp import FastMCP

BASE_URL = "http://localhost:21337"

# Authenticate once at startup — get a session cookie to reuse
_session_cookie: str | None = None
_password = os.environ.get("KG_PASSWORD", "")
if _password:
    try:
        req = urllib.request.Request(f"{BASE_URL}/?password={_password}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            for header in resp.headers.get_all("Set-Cookie") or []:
                if header.startswith("kg_session="):
                    _session_cookie = header.split(";")[0]
                    break
    except Exception:
        pass  # server may not be up yet; tools will fail gracefully


def _authed_request(url: str, **kwargs) -> urllib.request.Request:
    """Build a Request with the session cookie attached."""
    req = urllib.request.Request(url, **kwargs)
    if _session_cookie:
        req.add_header("Cookie", _session_cookie)
    return req


mcp = FastMCP("knowledge-graph")


@mcp.tool()
def kg_get_state() -> str:
    """Get the current frontend state of the knowledge graph visualization.

    Returns JSON with: mode, selection, viewport, visibility settings,
    timeline range, and nodes currently in view.
    """
    req = _authed_request(f"{BASE_URL}/state")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read().decode()


@mcp.tool()
def kg_command(action: str, params: str = "{}") -> str:
    """Send a command to the knowledge graph frontend.

    Args:
        action: The command action. One of:
            - select_node: Navigate to a node. params: {"node_id": "<id>"}
            - set_mode: Switch mode. params: {"mode": "explore"|"overview"}
            - set_visibility: Toggle layer. params: {"key": "<layer>", "value": "visible"|"hidden"|"off"}
            - go_back: Return to previous selection. No params needed.
            - set_timeline: Set time range. params: {"start": <ms>, "end": <ms>} (both optional)
            - set_viewport: Set camera. params: {"x": <float>, "y": <float>, "k": <float>}
            - center_on_node: Center view. params: {"node_id": "<id>", "k": <zoom>} (k optional)
            - reheat: Restart force simulation. No params needed.
            - stop_simulation: Freeze layout. No params needed.
        params: JSON string of action-specific parameters (default "{}").

    Returns:
        "ok" on success, or an error message.
    """
    extra = json.loads(params) if params else {}
    payload = {"action": action, **extra}
    data = json.dumps(payload).encode()
    req = _authed_request(
        f"{BASE_URL}/command",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 204:
                return "ok"
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.reason}"


@mcp.tool()
def kg_open_in_cursor(file: str, line: int = 0, column: int = 0) -> str:
    """Open a file in the remote Cursor editor connection.

    Args:
        file: Absolute path to the file to open.
        line: Optional line number to jump to (1-based).
        column: Optional column number (1-based).

    Returns:
        "ok" on success, or an error message.
    """
    payload: dict = {"file": file}
    if line:
        payload["line"] = line
    if column:
        payload["column"] = column
    data = json.dumps(payload).encode()
    req = _authed_request(
        f"{BASE_URL}/open-in-cursor",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 204:
                return "ok"
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            return json.loads(body).get("error", f"HTTP {e.code}: {e.reason}")
        except (json.JSONDecodeError, ValueError):
            return f"HTTP {e.code}: {e.reason}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
