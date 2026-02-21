# Webapp — ClawGraph Visualization

## Architecture

- **Backend**: Zero-dependency Python stdlib HTTP server (`webapp/backend/server.py`)
  - Serves static files from `webapp/frontend/`
  - Serves `output/knowledge_graph.json` at `/knowledge_graph.json`
  - SSE live-reload (`/events`) — auto-reloads browser when frontend files change
  - SSE remote commands (`/commands`) — push commands to frontend
  - State API (`GET/POST /state`) — read/write frontend visualization state
  - Command API (`POST /command`) — send commands to frontend
- **Frontend**: Single-file `webapp/frontend/index.html` (canvas-based D3 force graph)

## Server Modes

The server requires exactly one of two mutually exclusive flags:

- **`--public`** — Read-only mode. Control endpoints (`/state` POST, `/command`, `/open-in-cursor`, `/cursor-running`) are disabled (403). Anyone can view the graph.
- **`--password SECRET`** — Full-access mode gated behind a URL password. Visitors must open `/?password=SECRET` to authenticate; the server sets a session cookie and the frontend immediately clears the password from the URL bar.

## Servers (Linux — systemd)

Two systemd user services run on Linux, auto-starting on login:

| Service | Port | Mode | URL |
|---------|------|------|-----|
| `knowledge-graph` | 13337 | `--public` | http://localhost:13337 |
| `knowledge-graph-private` | 21337 | `--password` | http://localhost:21337/?password=i7iS0wZ032g44kinP_XGCAJZIC7h5lV9lipbOhI0ZKk |

### Key Commands

```bash
# Check status
systemctl --user status knowledge-graph
systemctl --user status knowledge-graph-private

# Restart (do this after changing server.py)
systemctl --user restart knowledge-graph knowledge-graph-private

# Stop / Start
systemctl --user stop knowledge-graph-private
systemctl --user start knowledge-graph-private

# Tail live logs
journalctl --user -u knowledge-graph -f
journalctl --user -u knowledge-graph-private -f

# Recent logs
journalctl --user -u knowledge-graph --since "5 min ago"
```

### After Editing `server.py`

Always restart both services to pick up changes:
```bash
systemctl --user restart knowledge-graph knowledge-graph-private
```

### After Editing Frontend Files

No restart needed — the file watcher detects changes and pushes live-reload to connected browsers automatically.

### Service Definitions

- `~/.config/systemd/user/knowledge-graph.service` (public)
- `~/.config/systemd/user/knowledge-graph-private.service` (password-protected)

If you edit either, reload with:
```bash
systemctl --user daemon-reload && systemctl --user restart knowledge-graph knowledge-graph-private
```

### Logging

- **journalctl**: `journalctl --user -u knowledge-graph` / `-u knowledge-graph-private` (primary, structured)
- **Log file**: `webapp/server.log` (rotating, 2MB max, 3 backups, gitignored)

### macOS — Manual Process

On macOS, there is no systemd. Run the server manually:

```bash
# Public (read-only)
python webapp/backend/server.py --public --port 8080

# Password-protected (full access)
python webapp/backend/server.py --password YOUR_SECRET --port 8080
```

### Verifying the Servers are Up

```bash
# Public
curl -s -o /dev/null -w "%{http_code}" http://localhost:13337/
# Should return 200

# Private (without password → 403, with password → 200)
curl -s -o /dev/null -w "%{http_code}" http://localhost:21337/
curl -s -o /dev/null -w "%{http_code}" "http://localhost:21337/?password=i7iS0wZ032g44kinP_XGCAJZIC7h5lV9lipbOhI0ZKk"
```
