"""Unit tests for crate_extractor using fixture data (no cargo needed)."""

from crate_extractor import (
    _parse_metadata,
    _build_dependency_edges_from_metadata,
    map_files_to_crates,
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
