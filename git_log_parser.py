def parse_git_log(raw: str) -> list[dict]:
    """
    Parse raw git log output into structured commit dicts.

    Returns list of:
    {
        "hash": str,
        "author": str,
        "email": str,
        "timestamp": int,        # unix epoch
        "message": str,
        "changes": [
            {"status": "A", "path": str},
            {"status": "M", "path": str},
            {"status": "D", "path": str},
            {"status": "R", "old_path": str, "new_path": str, "score": int},
        ]
    }
    """
    commits = []
    blocks = raw.split("COMMIT_START\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        commit_hash = lines[0]
        author = lines[1]
        email = lines[2]
        timestamp = int(lines[3])
        message = lines[4]

        changes = []
        for line in lines[5:]:
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("R"):
                score = int(status[1:])
                changes.append({
                    "status": "R",
                    "old_path": _strip_quotes(parts[1]),
                    "new_path": _strip_quotes(parts[2]),
                    "score": score,
                })
            elif status in ("A", "M", "D"):
                changes.append({
                    "status": status,
                    "path": _strip_quotes(parts[1]),
                })

        commits.append({
            "hash": commit_hash,
            "author": author,
            "email": email,
            "timestamp": timestamp,
            "message": message,
            "changes": changes,
        })
    return commits


def _strip_quotes(s: str) -> str:
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    return s
