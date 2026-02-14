"""Unit tests for crate_extractor using fixture data (no cargo needed)."""

from crate_extractor import (
    _parse_metadata,
    _build_dependency_edges_from_metadata,
    map_files_to_crates,
    enrich_crate_created_at,
    build_contributor_crate_edges,
)


def _make_metadata(packages, workspace_members=None, workspace_root="/ws"):
    """Build a minimal cargo metadata fixture."""
    if workspace_members is None:
        workspace_members = [p["id"] for p in packages]
    return {
        "packages": packages,
        "workspace_members": workspace_members,
        "workspace_root": workspace_root,
    }


def _make_package(name, manifest_path, deps=None, targets=None, edition="2021",
                  pkg_id=None):
    """Build a minimal cargo package entry."""
    if deps is None:
        deps = []
    if targets is None:
        targets = [{"kind": ["lib"], "name": name, "src_path": ""}]
    if pkg_id is None:
        pkg_id = f"{name} 0.1.0 (path+file://{manifest_path.rsplit('/', 1)[0]})"
    return {
        "id": pkg_id,
        "name": name,
        "manifest_path": manifest_path,
        "dependencies": deps,
        "targets": targets,
        "edition": edition,
    }


# --- Test 1: Parse cargo metadata output ---

def test_parse_cargo_metadata_output():
    pkg_a = _make_package(
        "alpha",
        "/ws/alpha/Cargo.toml",
        targets=[
            {"kind": ["lib"], "name": "alpha", "src_path": "/ws/alpha/src/lib.rs"},
        ],
        edition="2021",
    )
    pkg_b = _make_package(
        "beta",
        "/ws/beta/Cargo.toml",
        targets=[
            {"kind": ["lib"], "name": "beta", "src_path": "/ws/beta/src/lib.rs"},
            {"kind": ["bin"], "name": "beta-cli", "src_path": "/ws/beta/src/main.rs"},
        ],
        edition="2024",
    )
    pkg_c = _make_package(
        "gamma",
        "/ws/tools/gamma/Cargo.toml",
        targets=[
            {"kind": ["proc-macro"], "name": "gamma", "src_path": "/ws/tools/gamma/src/lib.rs"},
        ],
    )

    metadata = _make_metadata([pkg_a, pkg_b, pkg_c])
    crates = _parse_metadata(metadata)

    assert len(crates) == 3

    # Sorted by name: alpha, beta, gamma
    alpha = crates[0]
    assert alpha["id"] == "crate_1"
    assert alpha["name"] == "alpha"
    assert alpha["root_dir"] == "alpha/"
    assert alpha["manifest_path"] == "alpha/Cargo.toml"
    assert alpha["edition"] == "2021"
    assert alpha["has_lib"] is True
    assert alpha["has_bin"] is False
    assert alpha["created_at"] is None

    beta = crates[1]
    assert beta["id"] == "crate_2"
    assert beta["name"] == "beta"
    assert beta["root_dir"] == "beta/"
    assert beta["edition"] == "2024"
    assert beta["has_lib"] is True
    assert beta["has_bin"] is True

    gamma = crates[2]
    assert gamma["id"] == "crate_3"
    assert gamma["name"] == "gamma"
    assert gamma["root_dir"] == "tools/gamma/"
    assert gamma["manifest_path"] == "tools/gamma/Cargo.toml"
    assert gamma["has_lib"] is True  # proc-macro counts as lib


# --- Test 2: Crate dependency edges ---

def test_crate_dependency_edges():
    pkg_a = _make_package("alpha", "/ws/alpha/Cargo.toml", deps=[
        {"name": "beta", "kind": None},
    ])
    pkg_b = _make_package("beta", "/ws/beta/Cargo.toml", deps=[
        {"name": "gamma", "kind": None},
        {"name": "serde", "kind": None},  # external dep
    ])
    pkg_c = _make_package("gamma", "/ws/gamma/Cargo.toml")

    metadata = _make_metadata([pkg_a, pkg_b, pkg_c])
    crate_nodes = _parse_metadata(metadata)
    edges = _build_dependency_edges_from_metadata(metadata, crate_nodes)

    assert len(edges) == 2

    # alpha -> beta
    ab = [e for e in edges if e["source"] == "crate_1"]
    assert len(ab) == 1
    assert ab[0]["target"] == "crate_2"
    assert ab[0]["type"] == "depends_on"

    # beta -> gamma
    bg = [e for e in edges if e["source"] == "crate_2"]
    assert len(bg) == 1
    assert bg[0]["target"] == "crate_3"
    assert bg[0]["type"] == "depends_on"

    # No edge for serde (external)
    targets = {e["target"] for e in edges}
    assert all(t.startswith("crate_") for t in targets)


# --- Test 3: Map files to crates (basic) ---

def test_map_files_to_crates_basic():
    crate_nodes = [
        {"id": "crate_1", "name": "core", "root_dir": "core/"},
        {"id": "crate_2", "name": "cli", "root_dir": "cli/"},
    ]
    file_nodes = [
        {"id": "file_1", "name": "core/src/lib.rs"},
        {"id": "file_2", "name": "cli/src/main.rs"},
    ]
    edges = map_files_to_crates(file_nodes, crate_nodes)

    assert len(edges) == 2
    core_edge = [e for e in edges if e["target"] == "file_1"][0]
    assert core_edge["source"] == "crate_1"
    assert core_edge["type"] == "contains"

    cli_edge = [e for e in edges if e["target"] == "file_2"][0]
    assert cli_edge["source"] == "crate_2"
    assert cli_edge["type"] == "contains"


# --- Test 4: Nested crate — longest prefix match ---

def test_map_files_to_crates_nested():
    crate_nodes = [
        {"id": "crate_1", "name": "utils", "root_dir": "utils/"},
        {"id": "crate_2", "name": "utils-git", "root_dir": "utils/git/"},
    ]
    file_nodes = [
        {"id": "file_1", "name": "utils/git/src/lib.rs"},
        {"id": "file_2", "name": "utils/src/lib.rs"},
    ]
    edges = map_files_to_crates(file_nodes, crate_nodes)

    assert len(edges) == 2
    git_edge = [e for e in edges if e["target"] == "file_1"][0]
    assert git_edge["source"] == "crate_2"  # utils/git/ not utils/

    utils_edge = [e for e in edges if e["target"] == "file_2"][0]
    assert utils_edge["source"] == "crate_1"


# --- Test 5: Unmapped files ---

def test_map_files_to_crates_unmapped():
    crate_nodes = [
        {"id": "crate_1", "name": "core", "root_dir": "core/"},
    ]
    file_nodes = [
        {"id": "file_1", "name": "Cargo.lock"},
        {"id": "file_2", "name": "README.md"},
        {"id": "file_3", "name": ".github/ci.yml"},
    ]
    edges = map_files_to_crates(file_nodes, crate_nodes)
    assert len(edges) == 0


# --- Test 6: Stable deterministic IDs ---

def test_crate_nodes_have_stable_ids():
    pkg_b = _make_package("beta", "/ws/beta/Cargo.toml")
    pkg_a = _make_package("alpha", "/ws/alpha/Cargo.toml")

    # Pass in non-sorted order
    metadata = _make_metadata([pkg_b, pkg_a])

    run1 = _parse_metadata(metadata)
    run2 = _parse_metadata(metadata)

    assert run1 == run2
    # alpha comes first alphabetically
    assert run1[0]["name"] == "alpha"
    assert run1[0]["id"] == "crate_1"
    assert run1[1]["name"] == "beta"
    assert run1[1]["id"] == "crate_2"


# --- Test 7: All edges have type field ---

def test_edge_type_field_present():
    pkg_a = _make_package("alpha", "/ws/alpha/Cargo.toml", deps=[
        {"name": "beta", "kind": None},
    ])
    pkg_b = _make_package("beta", "/ws/beta/Cargo.toml")

    metadata = _make_metadata([pkg_a, pkg_b])
    crate_nodes = _parse_metadata(metadata)
    dep_edges = _build_dependency_edges_from_metadata(metadata, crate_nodes)

    file_nodes = [{"id": "file_1", "name": "alpha/src/lib.rs"}]
    contains_edges = map_files_to_crates(file_nodes, crate_nodes)

    all_edges = dep_edges + contains_edges
    assert len(all_edges) > 0
    for edge in all_edges:
        assert "type" in edge
        assert edge["type"] in ("depends_on", "contains")


# --- Test 8: Enrich crate created_at from file nodes ---

def test_enrich_crate_created_at():
    crate_nodes = [
        {"id": "crate_1", "name": "alpha", "root_dir": "alpha/",
         "manifest_path": "alpha/Cargo.toml", "created_at": None},
        {"id": "crate_2", "name": "beta", "root_dir": "beta/",
         "manifest_path": "beta/Cargo.toml", "created_at": None},
        {"id": "crate_3", "name": "gamma", "root_dir": "gamma/",
         "manifest_path": "gamma/Cargo.toml", "created_at": None},
    ]
    file_nodes = [
        {"id": "file_1", "name": "alpha/Cargo.toml",
         "created_at": "2025-05-01T10:00:00+00:00"},
        {"id": "file_2", "name": "beta/Cargo.toml",
         "created_at": "2025-06-15T12:30:00+00:00"},
        # gamma/Cargo.toml is missing from file nodes (e.g. deleted and re-added)
        {"id": "file_3", "name": "alpha/src/lib.rs",
         "created_at": "2025-05-02T09:00:00+00:00"},
    ]

    enrich_crate_created_at(crate_nodes, file_nodes)

    assert crate_nodes[0]["created_at"] == "2025-05-01T10:00:00+00:00"
    assert crate_nodes[1]["created_at"] == "2025-06-15T12:30:00+00:00"
    assert crate_nodes[2]["created_at"] is None  # no matching file node


# --- Test 9: Build contributor-crate edges ---

def test_build_contributor_crate_edges():
    """Verify contributed_to edges with deduplication and correct aggregation."""
    # Two crates, three files
    contains_edges = [
        {"source": "crate_1", "target": "file_1", "type": "contains"},
        {"source": "crate_1", "target": "file_2", "type": "contains"},
        {"source": "crate_2", "target": "file_3", "type": "contains"},
    ]

    # Contributor_1 touched file_1 and file_2 in the SAME commit (commit_a) —
    # should deduplicate to 1 commit for crate_1.
    # Contributor_1 also touched file_3 in commit_b → 1 commit for crate_2.
    # Contributor_2 touched file_1 in commit_c.
    authored_edges = [
        {"source": "contributor_1", "target": "file_1", "type": "authored",
         "commits": ["commit_a"]},
        {"source": "contributor_1", "target": "file_2", "type": "authored",
         "commits": ["commit_a", "commit_b"]},
        {"source": "contributor_1", "target": "file_3", "type": "authored",
         "commits": ["commit_b"]},
        {"source": "contributor_2", "target": "file_1", "type": "authored",
         "commits": ["commit_c"]},
    ]

    commits_lookup = {
        "commit_a": {"timestamp": "2025-06-01T10:00:00+00:00", "message": "a", "author": "contributor_1"},
        "commit_b": {"timestamp": "2025-05-01T08:00:00+00:00", "message": "b", "author": "contributor_1"},
        "commit_c": {"timestamp": "2025-07-01T12:00:00+00:00", "message": "c", "author": "contributor_2"},
    }

    edges = build_contributor_crate_edges(authored_edges, contains_edges, commits_lookup)

    # 3 pairs: (contributor_1, crate_1), (contributor_1, crate_2), (contributor_2, crate_1)
    assert len(edges) == 3

    for e in edges:
        assert e["type"] == "contributed_to"
        assert "total_commits" in e
        assert "first_contribution_at" in e

    # contributor_1 → crate_1: commits {commit_a, commit_b} = 2 distinct commits
    c1_cr1 = [e for e in edges if e["source"] == "contributor_1" and e["target"] == "crate_1"]
    assert len(c1_cr1) == 1
    assert c1_cr1[0]["total_commits"] == 2
    # commit_b is earlier
    assert c1_cr1[0]["first_contribution_at"] == "2025-05-01T08:00:00+00:00"

    # contributor_1 → crate_2: commits {commit_b} = 1 commit
    c1_cr2 = [e for e in edges if e["source"] == "contributor_1" and e["target"] == "crate_2"]
    assert len(c1_cr2) == 1
    assert c1_cr2[0]["total_commits"] == 1
    assert c1_cr2[0]["first_contribution_at"] == "2025-05-01T08:00:00+00:00"

    # contributor_2 → crate_1: commits {commit_c} = 1 commit
    c2_cr1 = [e for e in edges if e["source"] == "contributor_2" and e["target"] == "crate_1"]
    assert len(c2_cr1) == 1
    assert c2_cr1[0]["total_commits"] == 1
    assert c2_cr1[0]["first_contribution_at"] == "2025-07-01T12:00:00+00:00"


def test_build_contributor_crate_edges_unmapped_files():
    """Files not in any crate should not produce contributed_to edges."""
    contains_edges = [
        {"source": "crate_1", "target": "file_1", "type": "contains"},
    ]
    authored_edges = [
        {"source": "contributor_1", "target": "file_2", "type": "authored",
         "commits": ["commit_a"]},
    ]
    commits_lookup = {
        "commit_a": {"timestamp": "2025-06-01T10:00:00+00:00", "message": "a", "author": "contributor_1"},
    }

    edges = build_contributor_crate_edges(authored_edges, contains_edges, commits_lookup)
    assert len(edges) == 0
