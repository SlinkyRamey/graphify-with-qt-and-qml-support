"""INC-QML-133: semantic aliasing retains Qt cleanup and current producer state.

These production reconciliation inputs model saved semantic mentions beside a
new AST declaration. Their graph-owned cleanup restriction and optional transport
promise must survive upstream's alias remapping, without granting import proof.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import warnings

import pytest

from graphify import cache
from graphify.build import build_from_json
from graphify.export import to_json
from graphify.source_relationships import adopt_transport
from graphify.watch import _rebuild_code, _reconcile_existing_graph, _write_build_config

RETENTION = "qt_import_retention"
PROFILE = "graphify_source_relationships"


def fixture(root, *, promised):
    """A live YAML declaration collides with a saved, independently owned mention."""
    source = root / "config.yaml"
    source.write_text("shared: true\n", encoding="utf-8")
    (root / "readme.md").write_text("# Review\nShared configuration.\n", encoding="utf-8")
    out = root / "graphify-out"
    out.mkdir()
    graph = {RETENTION: {"schema": 1, "ids": ["missing"]}}
    if promised:
        graph[PROFILE] = {"contract_version": 1}
    saved = {
        "graph": graph,
        "multigraph": promised,
        "directed": False,
        "nodes": [
            {"id": "shared", "label": "Shared configuration", "file_type": "document",
             "source_file": "readme.md", "_origin": "semantic"},
            {"id": "review", "label": "Review", "file_type": "document",
             "source_file": "readme.md", "_origin": "semantic"},
            {"id": "missing", "label": "missing", "file_type": "concept",
             "source_file": "", "type": "external", "external": True},
        ],
        "links": [{"source": "review", "target": "shared", "relation": "describes",
                   "confidence": "EXTRACTED", "source_file": "readme.md",
                   "_origin": "semantic"}],
    }
    if promised:
        # A persisted source profile carries canonical logical direction and
        # its storage key; use the actual adapter to establish that baseline.
        saved["links"][0].update(_src="review", _tgt="shared")
        saved = adopt_transport(saved)
    path = out / "graph.json"
    path.write_text(json.dumps(saved), encoding="utf-8")
    fresh = {
        "nodes": [{"id": "shared", "label": "shared", "file_type": "code",
                   "source_file": "config.yaml", "source_location": "L1", "_origin": "ast"}],
        "edges": [], "hyperedges": [], "input_tokens": 9, "output_tokens": 3,
        "diagnostics": [{"code": "QML_EMPTY", "source_file": "Empty.qml"}],
        "qt_failures": [], "qml_failures": [], "failed_sources": [],
    }
    if promised:
        fresh["graph"] = {PROFILE: {"contract_version": 1}}
    return source, path, fresh


def reconcile(root, source, path, fresh, *, full):
    """The real owning reconciliation loads the persisted baseline itself."""
    return _reconcile_existing_graph(
        path, fresh, out=path.parent, project_root=root, watch_root=root,
        code_files=[source], extract_targets=[source], full_rebuild=full,
        deleted_paths=set(), deleted_source_identities=set(),
    )[0]


@pytest.mark.parametrize("full", [False, True], ids=["incremental", "full"])
@pytest.mark.parametrize("promised", [False, True], ids=["legacy", "source-profile"])
def test_REQ_QML_022_AC06_semantic_alias_retains_cleanup_and_current_diagnostics(
    tmp_path, full, promised,
):
    """Alias only the saved mention; retain diagnostics/promise and unresolved import."""
    source, path, fresh = fixture(tmp_path, promised=promised)
    accepted = path.read_bytes()
    actual = reconcile(tmp_path, source, path, fresh, full=full)
    by_id = {node["id"]: node for node in actual["nodes"]}
    assert by_id["shared"]["_origin"] == "ast"
    mention = next(node for node in actual["nodes"]
                   if node.get("label") == "Shared configuration")
    assert mention["id"] != "shared" and mention["_origin"] == "semantic"
    assert any(edge["source"] == "review" and edge["target"] == mention["id"]
               for edge in actual["edges"])
    assert actual["graph"][RETENTION] == {"schema": 1, "ids": ["missing"]}
    assert actual["diagnostics"] == fresh["diagnostics"]
    assert all(actual[key] == [] for key in ("qt_failures", "qml_failures", "failed_sources"))
    assert actual["input_tokens"] == actual["output_tokens"] == 0
    assert (PROFILE in actual["graph"]) is promised
    assert path.read_bytes() == accepted

    # Actual build/export/reload must preserve both stored contracts and the
    # semantic endpoint, rather than hiding the loss in an in-memory merge.
    exported = tmp_path / "reloaded.json"
    graph = build_from_json(actual)
    assert to_json(graph, {}, str(exported))
    reloaded = build_from_json(json.loads(exported.read_text(encoding="utf-8")))
    assert reloaded.graph[RETENTION] == actual["graph"][RETENTION]
    assert mention["id"] in reloaded and "shared" in reloaded
    assert reloaded.has_edge("review", mention["id"])


@pytest.mark.parametrize("full", [False, True])
def test_REQ_QML_012_AC02_alias_rejection_preserves_disk_and_allows_repair(tmp_path, full):
    """Invalid retained context rejects before mutation; repaired same input retries safely."""
    source, path, fresh = fixture(tmp_path, promised=True)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["graph"][RETENTION]["ids"] = ["absent"]
    path.write_text(json.dumps(saved), encoding="utf-8")
    invalid, original_input = path.read_bytes(), copy.deepcopy(fresh)
    with pytest.raises(ValueError, match="QT_IMPORT_RETENTION_INVALID"):
        reconcile(tmp_path, source, path, fresh, full=full)
    assert path.read_bytes() == invalid and fresh == original_input
    saved["graph"][RETENTION]["ids"] = ["missing"]
    path.write_text(json.dumps(saved), encoding="utf-8")
    repaired = path.read_bytes()
    first = reconcile(tmp_path, source, path, copy.deepcopy(fresh), full=full)
    second = reconcile(tmp_path, source, path, copy.deepcopy(fresh), full=full)
    assert first == second and path.read_bytes() == repaired


@pytest.mark.parametrize("predecessor", [40, 41, 45])
def test_REQ_QML_011_AC03_schema41_retires40_without_rekeying_semantics(
    tmp_path, monkeypatch, predecessor,
):
    """A predecessor AST entry cannot become current evidence; semantic lifetime is separate."""
    source = tmp_path / "Sample.py"
    source.write_text("def sample(): return 1\n", encoding="utf-8")
    payload = {"nodes": [{"id": "sample"}], "edges": [], "hyperedges": []}
    with monkeypatch.context() as old:
        old.setattr(cache, "_AST_CACHE_SCHEMA", predecessor)
        cache.save_cached(source, payload, root=tmp_path, kind="ast")
        cache.save_cached(source, payload, root=tmp_path, kind="semantic")
    assert cache._AST_CACHE_SCHEMA == 46
    assert cache.load_cached(source, tmp_path, kind="ast") is None
    assert cache.load_cached(source, tmp_path, kind="semantic") == payload
    cache.save_cached(source, payload, root=tmp_path, kind="ast")
    assert cache.load_cached(source, tmp_path, kind="ast") == payload


@pytest.mark.skipif(os.name != "nt", reason="Native extended Windows path spelling")
def test_REQ_CORE_006_AC04_physical_prefix_keeps_one_logical_stat_owner(tmp_path):
    """Hash/word-count readers share one memo and do not flush on prefix-only aliases."""
    root = tmp_path / "Repo"
    root.mkdir()
    source = root / "Sample.py"
    source.write_text("def sample(): return 1\n", encoding="utf-8")
    old = source.stat().st_mtime_ns - 10_000_000_000
    os.utime(source, ns=(old, old))
    digest = cache.file_hash(source, root, cache_root=root)
    assert cache.cached_word_count(source, root, lambda _: 4) == 4
    owner = cache._stat_index
    cache._flush_stat_index()
    index_path = cache._stat_index_path
    assert index_path is not None
    saved = index_path.read_bytes()
    with warnings.catch_warnings(record=True) as emitted:
        assert cache.file_hash(source, root, cache_root=root) == digest
        assert cache.cached_word_count(source, root, lambda _: pytest.fail("Recomputed count")) == 4
        cache._flush_stat_index()
    assert not any("switched from cache root" in str(item.message) for item in emitted)
    assert cache._stat_index_path == index_path
    assert cache._stat_index is owner and index_path.read_bytes() == saved


@pytest.mark.skipif(os.name != "nt", reason="Native UNC path spelling")
def test_REQ_CORE_006_AC04_unc_prefix_retains_absolute_share_identity():
    """Pure spelling normalization preserves a UNC anchor/case without opening a share."""
    ordinary = Path(r"\\fixture\share\Mixed\Sample.py")
    physical = Path(r"\\?\UNC\fixture\share\Mixed\Sample.py")
    actual = cache._normalize_path(physical)
    assert actual.is_absolute() and str(actual) == str(cache._normalize_path(ordinary))


def test_REQ_QML_012_AC02_code_only_qt_metadata_update_rejects_and_recovers(tmp_path):
    """The real Qt rebuild retains scope through metadata edits and rejected source."""
    source = tmp_path / "Main.qml"
    source.write_text("Item { property int count: 1 }\n", encoding="utf-8")
    metadata = tmp_path / "resources.qrc"
    resource = '<RCC><qresource><file alias="%s">Main.qml</file></qresource></RCC>'
    metadata.write_text(resource % "Root.qml", encoding="utf-8")
    document = tmp_path / "guide.md"
    document.write_text("# A separate document\n", encoding="utf-8")
    output = tmp_path / "graphify-out"
    _write_build_config(output, excludes=None, code_only=True)

    def rebuild(changes=None):
        return _rebuild_code(tmp_path, changed_paths=changes, no_cluster=True, acquire_lock=False)

    def cohort():
        return {name: (output / name).read_bytes() for name in (
            "graph.json", "manifest.json", ".graphify_root", ".qt_analysis.json")}

    assert rebuild()
    first = cohort()
    assert any(node.get("label") == "qrc:/Root.qml"
               for node in json.loads(first["graph.json"])["nodes"])
    metadata.write_text(resource % "Changed.qml", encoding="utf-8")
    document.write_text("# Changed document\n## Still excluded\n", encoding="utf-8")
    assert rebuild([metadata, document])
    accepted = cohort()
    data = json.loads(accepted["graph.json"])
    aliases = {node.get("label") for node in data["nodes"]
               if node.get("metadata", {}).get("qml", {}).get("kind") == "resource_alias"}
    assert "qrc:/Changed.qml" in aliases and "qrc:/Root.qml" not in aliases
    assert data["qt_failures"] == data["qml_failures"] == []
    assert not any(node.get("source_file") == "guide.md" for node in data["nodes"])

    # A real malformed QML contribution cannot become a successful metadata
    # update. All four accepted products survive, then repaired input retries.
    source.write_text("Item { property int count: ", encoding="utf-8")
    assert not rebuild([source])
    assert cohort() == accepted
    source.write_text("Item { property int count: 3 }\n", encoding="utf-8")
    assert rebuild([source])
    repaired = cohort()
    assert json.loads(repaired["graph.json"])["qml_failures"] == []
    assert rebuild([]) and cohort() == repaired


def test_REQ_QML_022_AC06_semantic_document_phase_preserves_dispatch_and_prior_layer(tmp_path, monkeypatch):
    """Real extraction excludes a semantic-backed document and retains its prior AST layer."""
    import graphify.extract as extraction

    unit = tmp_path / "unit.py"
    unit.write_text("def keep(): return 1\n", encoding="utf-8")
    note = tmp_path / "notes.md"
    note.write_text("# Original heading\nRetained document.\n", encoding="utf-8")
    assert _rebuild_code(tmp_path, no_cluster=True, acquire_lock=False)
    path = tmp_path / "graphify-out/graph.json"
    prior = json.loads(path.read_text(encoding="utf-8"))
    old_layer = {node["id"]: node for node in prior["nodes"]
                 if node.get("source_file") == "notes.md" and node.get("_origin") == "ast"}
    assert old_layer
    semantic = {"id": "reviewed_note", "label": "Reviewed note", "source_file": "notes.md",
                "file_type": "concept", "_origin": "semantic", "rationale": "Independent review"}
    prior["nodes"].append(semantic)
    path.write_text(json.dumps(prior), encoding="utf-8")
    unit.write_text("def keep(): return 2\ndef added(): return keep()\n", encoding="utf-8")
    note.write_text("# Changed heading\nThe saved semantic layer remains authoritative.\n", encoding="utf-8")
    dispatched = []
    original = extraction.extract

    def record(paths, *args, **kwargs):
        dispatched.extend(Path(item).resolve() for item in paths)
        return original(paths, *args, **kwargs)

    monkeypatch.setattr(extraction, "extract", record)
    assert _rebuild_code(tmp_path, changed_paths=[unit, note], no_cluster=True, acquire_lock=False)
    assert unit.resolve() in dispatched and note.resolve() not in dispatched
    actual = json.loads(path.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in actual["nodes"]}
    assert nodes["reviewed_note"] == semantic
    assert {identity: nodes[identity] for identity in old_layer} == old_layer
