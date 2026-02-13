from rename_tracker import RenameTracker


def test_new_file():
    tracker = RenameTracker()
    file_id = tracker.process_change({"status": "A", "path": "foo.rs"}, timestamp=100)
    nodes = tracker.get_file_nodes()
    assert len(nodes) == 1
    node = nodes[0]
    assert node["id"] == file_id
    assert node["name"] == "foo.rs"
    assert node["previous_names"] == []


def test_modify_existing():
    tracker = RenameTracker()
    id1 = tracker.process_change({"status": "A", "path": "foo.rs"}, timestamp=100)
    id2 = tracker.process_change({"status": "M", "path": "foo.rs"}, timestamp=200)
    assert id1 == id2


def test_rename():
    tracker = RenameTracker()
    id1 = tracker.process_change({"status": "A", "path": "foo.rs"}, timestamp=100)
    id2 = tracker.process_change(
        {"status": "R", "old_path": "foo.rs", "new_path": "bar.rs"}, timestamp=200
    )
    assert id1 == id2
    nodes = tracker.get_file_nodes()
    node = [n for n in nodes if n["id"] == id1][0]
    assert node["name"] == "bar.rs"
    assert node["previous_names"] == ["foo.rs"]


def test_rename_chain():
    tracker = RenameTracker()
    id1 = tracker.process_change({"status": "A", "path": "a.rs"}, timestamp=100)
    id2 = tracker.process_change(
        {"status": "R", "old_path": "a.rs", "new_path": "b.rs"}, timestamp=200
    )
    id3 = tracker.process_change(
        {"status": "R", "old_path": "b.rs", "new_path": "c.rs"}, timestamp=300
    )
    assert id1 == id2 == id3
    nodes = tracker.get_file_nodes()
    node = [n for n in nodes if n["id"] == id1][0]
    assert node["name"] == "c.rs"
    assert node["previous_names"] == ["a.rs", "b.rs"]


def test_delete():
    tracker = RenameTracker()
    id1 = tracker.process_change({"status": "A", "path": "foo.rs"}, timestamp=100)
    id2 = tracker.process_change({"status": "D", "path": "foo.rs"}, timestamp=200)
    assert id1 == id2
    nodes = tracker.get_file_nodes()
    node = [n for n in nodes if n["id"] == id1][0]
    assert node["deleted"] is True


def test_readd_after_delete():
    tracker = RenameTracker()
    id1 = tracker.process_change({"status": "A", "path": "foo.rs"}, timestamp=100)
    tracker.process_change({"status": "D", "path": "foo.rs"}, timestamp=200)
    id3 = tracker.process_change({"status": "A", "path": "foo.rs"}, timestamp=300)
    assert id1 != id3


def test_modify_unknown_path():
    tracker = RenameTracker()
    file_id = tracker.process_change(
        {"status": "M", "path": "unknown.rs"}, timestamp=100
    )
    nodes = tracker.get_file_nodes()
    assert len(nodes) == 1
    assert nodes[0]["id"] == file_id
    assert nodes[0]["name"] == "unknown.rs"


def test_timestamps():
    tracker = RenameTracker()
    tracker.process_change({"status": "A", "path": "foo.rs"}, timestamp=100)
    tracker.process_change({"status": "M", "path": "foo.rs"}, timestamp=200)
    nodes = tracker.get_file_nodes()
    assert nodes[0]["created_at"] == 100
    assert nodes[0]["last_modified_at"] == 200
