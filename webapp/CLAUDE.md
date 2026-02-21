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

## Dev Server (port 8080)

### Linux — systemd Service (automated)

On Linux, the dev server runs as a **systemd user service**. It auto-starts on login and auto-restarts on failure.

### Key Commands

```bash
# Check status
systemctl --user status knowledge-graph

# Restart (do this after changing server.py)
systemctl --user restart knowledge-graph

# Stop
systemctl --user stop knowledge-graph

# Start
systemctl --user start knowledge-graph

# Tail live logs (journalctl)
journalctl --user -u knowledge-graph -f

# Recent logs
journalctl --user -u knowledge-graph --since "5 min ago"
```

### After Editing `server.py`

Always restart the service to pick up changes:
```bash
systemctl --user restart knowledge-graph
```

### After Editing Frontend Files

No restart needed — the file watcher detects changes and pushes live-reload to connected browsers automatically.

### Service Definition

Located at `~/.config/systemd/user/knowledge-graph.service`. If you edit it, reload with:
```bash
systemctl --user daemon-reload && systemctl --user restart knowledge-graph
```

### Logging

- **journalctl**: `journalctl --user -u knowledge-graph` (primary, structured)
- **Log file**: `webapp/server.log` (rotating, 2MB max, 3 backups, gitignored)

### macOS — Manual Process

On macOS, there is no systemd. The user is expected to run the server manually in a separate terminal tab:

```bash
python webapp/backend/server.py --port 8080
```

The server must be manually stopped (Ctrl+C) and restarted after changes to `server.py`. Frontend file changes still auto-reload without restart.

### Verifying the Server is Up

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/
# Should return 200
```

Or open http://localhost:8080 in a browser.
