class RenameTracker:
    """Processes file changes in chronological order, assigning stable file IDs
    and tracking rename chains."""

    def __init__(self):
        self._path_to_id: dict[str, str] = {}
        self._id_counter: int = 0
        self._files: dict[str, dict] = {}

    def _next_id(self) -> str:
        self._id_counter += 1
        return f"file_{self._id_counter}"

    def process_change(self, change: dict, timestamp: int) -> str:
        """Process a single file change, return the file_id."""
        status = change["status"]

        if status == "A":
            path = change["path"]
            file_id = self._next_id()
            self._path_to_id[path] = file_id
            self._files[file_id] = {
                "id": file_id,
                "name": path,
                "previous_names": [],
                "deleted": False,
                "created_at": timestamp,
                "last_modified_at": timestamp,
            }
            return file_id

        elif status == "M":
            path = change["path"]
            if path in self._path_to_id:
                file_id = self._path_to_id[path]
                self._files[file_id]["last_modified_at"] = timestamp
                return file_id
            else:
                # Defensive: unknown path, create new node
                file_id = self._next_id()
                self._path_to_id[path] = file_id
                self._files[file_id] = {
                    "id": file_id,
                    "name": path,
                    "previous_names": [],
                    "deleted": False,
                    "created_at": timestamp,
                    "last_modified_at": timestamp,
                }
                return file_id

        elif status == "D":
            path = change["path"]
            if path in self._path_to_id:
                file_id = self._path_to_id[path]
                self._files[file_id]["deleted"] = True
                self._files[file_id]["last_modified_at"] = timestamp
                del self._path_to_id[path]
                return file_id
            else:
                file_id = self._next_id()
                self._files[file_id] = {
                    "id": file_id,
                    "name": path,
                    "previous_names": [],
                    "deleted": True,
                    "created_at": timestamp,
                    "last_modified_at": timestamp,
                }
                return file_id

        elif status == "R":
            old_path = change["old_path"]
            new_path = change["new_path"]
            if old_path in self._path_to_id:
                file_id = self._path_to_id[old_path]
                del self._path_to_id[old_path]
                self._path_to_id[new_path] = file_id
                self._files[file_id]["previous_names"].append(old_path)
                self._files[file_id]["name"] = new_path
                self._files[file_id]["last_modified_at"] = timestamp
                return file_id
            else:
                file_id = self._next_id()
                self._path_to_id[new_path] = file_id
                self._files[file_id] = {
                    "id": file_id,
                    "name": new_path,
                    "previous_names": [old_path],
                    "deleted": False,
                    "created_at": timestamp,
                    "last_modified_at": timestamp,
                }
                return file_id

    def get_file_nodes(self) -> list[dict]:
        """Return all file node dicts (without line counts / file types)."""
        return list(self._files.values())
