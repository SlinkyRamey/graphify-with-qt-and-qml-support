"""INC133: incoming aliases/accounting preserve the accepted source frame."""
from __future__ import annotations

from copy import deepcopy
import errno
import json
import os
from pathlib import Path

import pytest

from graphify.build import build_from_json
from graphify.export import to_json
from graphify.paths import load_node_link_graph
from tests.source_relationship_projection_helpers import arcs, state, storage


def _file(identity, source):
    """Author public raw file facts; the production builder owns alias policy."""
    return {"id": identity, "label": Path(source).name, "type": "file",
            "file_type": "code", "source_file": source,
            "source_location": "L1", "_origin": "ast"}


def _edge(source, target, relation="imports", source_file="main.py"):
    return {"source": source, "target": target, "relation": relation,
            "source_file": source_file, "source_location": "L1",
            "confidence": "EXTRACTED", "_origin": "ast"}


@pytest.mark.parametrize("profile", [False, True], ids=["legacy", "source"])
@pytest.mark.parametrize("directed", [False, True], ids=["undirected", "directed"])
@pytest.mark.parametrize("reverse", [False, True], ids=["forward-input", "reverse-input"])
def test_req_qml010_ac02_aliases_share_the_complete_import_cohort(tmp_path, profile, directed, reverse):
    """Same-language aliases seed a chained import without minting fake file twins."""
    rows = [_edge("main", "Real"), _edge("Real", "external"),
            _edge("main", "external", "calls")]
    raw = storage({"nodes": [_file("main", "main.py"), _file("lib_real", "lib/Real.py")],
                   "edges": list(reversed(rows)) if reverse else rows}, profile)
    graph = build_from_json(raw, directed=directed, root=tmp_path)
    assert set(graph) == {"main", "lib_real", "external"}
    assert arcs(graph, "imports") == {("main", "lib_real"), ("lib_real", "external")}
    assert arcs(graph, "calls") == {("main", "external")}
    assert all("Real" not in (edge["source"], edge["target"]) for edge in raw["edges"])
    path = tmp_path / "imports.json"
    assert to_json(graph, {}, str(path), original_links=raw["edges"])
    restored = load_node_link_graph(path)
    assert arcs(restored, "imports") == arcs(graph, "imports")
    assert arcs(restored, "calls") == arcs(graph, "calls")


@pytest.mark.parametrize("profile", [False, True], ids=["legacy", "source"])
@pytest.mark.parametrize("relation", ["imports", "references"])
def test_req_qml022_ac06_aliases_cannot_claim_a_foreign_language(tmp_path, profile, relation):
    """A Dart fact cannot acquire Java file authority through an old short stem."""
    raw = storage({"nodes": [_file("main", "main.dart"), _file("lib_real", "lib/Real.java")],
                   "edges": [_edge("main", "Real", relation, "main.dart")]}, profile)
    graph = build_from_json(raw, directed=True, root=tmp_path)
    assert not graph.has_edge("main", "lib_real")
    if relation == "imports":
        assert arcs(graph, relation) == {("main", "Real")}
        assert graph.nodes["Real"].get("external") is True
    else:
        assert not list(graph.edges())
    path = tmp_path / "foreign.json"
    assert to_json(graph, {}, str(path), original_links=raw["edges"])
    assert not load_node_link_graph(path).has_edge("main", "lib_real")


def _source_graph(root):
    """The storage-only marker admits parallel arcs without granting new authority."""
    raw = storage({"nodes": [_file("one", "one.py"), _file("two", "two.py")],
                   "edges": [_edge("one", "two", "calls", "one.py"),
                             _edge("two", "one", "references", "two.py")]}, True)
    return build_from_json(raw, directed=False, root=root)


def _accounting(graph):
    graph.graph.update(_dedup_collapsed=2, _pruned_node_count=1,
                       _dedup_remap={"folded_semantic": "one"})


def test_req_qml010_ac03_failed_write_retains_source_dedup_evidence_and_retries(tmp_path, monkeypatch):
    """A real failed atomic replace retains disk and remap state until successful retry."""
    graph = _source_graph(tmp_path)
    path = tmp_path / "graph.json"
    assert to_json(graph, {}, str(path))
    accepted = path.read_bytes()
    _accounting(graph)
    before = state(graph)
    replace = os.replace

    def fail_output(source, target):
        if Path(str(target).removeprefix("\\\\?\\")) == path:
            raise OSError(errno.EIO, "injected source accounting replacement failure")
        return replace(source, target)

    with monkeypatch.context() as fault:
        fault.setattr(os, "replace", fail_output)
        with pytest.raises(OSError, match="source accounting"):
            to_json(graph, {}, str(path))
    assert path.read_bytes() == accepted and state(graph) == before
    assert to_json(graph, {}, str(path))
    restored = load_node_link_graph(path)
    assert arcs(restored, "calls") == {("one", "two")}
    assert arcs(restored, "references") == {("two", "one")}
    assert not {"_dedup_remap", "_dedup_collapsed", "_pruned_node_count"} & graph.graph.keys()
    assert not {"_dedup_remap", "_dedup_collapsed", "_pruned_node_count"} & json.loads(path.read_text())["graph"].keys()
    assert to_json(graph, {}, str(path))


def test_req_qml022_ac06_shrink_rejection_keeps_source_remap_for_retry(tmp_path):
    """A missing independently evidenced semantic record is not a dedup allowance."""
    graph = _source_graph(tmp_path)
    graph.add_node("evidence", label="Independent account", source_file="notes.md",
                   _origin="semantic", rationale="Independent source attribution")
    path = tmp_path / "graph.json"
    assert to_json(graph, {}, str(path))
    accepted = path.read_bytes()
    graph.remove_node("evidence")
    _accounting(graph)
    before = state(graph)
    assert to_json(graph, {}, str(path)) is False
    assert state(graph) == before and path.read_bytes() == accepted


@pytest.mark.parametrize("value", [None, [], {1: "one"}, {"folded": []}])
def test_req_qml022_ac06_invalid_source_remap_rejects_before_write(tmp_path, value):
    """Explicit internal accounting cannot be coerced into an accepted source remap."""
    graph = _source_graph(tmp_path)
    path = tmp_path / "graph.json"
    assert to_json(graph, {}, str(path))
    accepted = path.read_bytes()
    graph.graph["_dedup_remap"] = deepcopy(value)
    before = state(graph)
    assert to_json(graph, {}, str(path), force=True) is False
    assert state(graph) == before and path.read_bytes() == accepted


def test_req_qml010_ac02_external_seed_validates_before_custom_resolution():
    """Malformed endpoints never invoke the builder resolver through a hidden alias."""
    import networkx as nx
    from graphify.accepted_link_frame import AcceptedLinkFrame
    graph = nx.Graph()
    graph.add_node("one")
    visited = []
    rows = [None, {"source": [], "target": "foreign", "relation": "imports"},
            {"source": "one", "target": "foreign", "relation": "calls"}]
    AcceptedLinkFrame.seed_external_targets(
        graph, rows, {"one"}, {}, normalize=str, mint=lambda *_: pytest.fail("unexpected mint"),
        prepare=lambda *_: {}, admit=lambda *_: True, relations={"imports"},
        resolve=lambda *args: visited.append(args))
    assert visited == [] and set(graph) == {"one"}


def _reference_collision(root, *, directed=False, reverse=False):
    """Validate a saved frame before adding the fresh declaration that shadows its ID."""
    from graphify.source_relationships import adopt_transport
    saved = storage({"nodes": [
        {"id": "shared", "label": "Authored mention", "file_type": "document",
         "source_file": "readme.md", "_origin": "semantic",
         "metadata": {"graphify_reference_stub": True}},
        {"id": "review", "label": "Review", "file_type": "document",
         "source_file": "readme.md", "_origin": "semantic"}],
        "edges": [_edge("review", "shared", "describes", "readme.md")],
        "directed": directed, "multigraph": True}, True)
    saved = adopt_transport(saved)
    edge = saved["edges"][0]
    edge["_origin"] = "semantic"
    if reverse:
        edge["source"], edge["target"] = edge["target"], edge["source"]
    edge.update({"from": edge["source"], "to": edge["target"]})
    assert arcs(build_from_json(deepcopy(saved), directed=directed, root=root), "describes") == {
        ("review", "shared")}
    saved["nodes"].append(_file("shared", "config.yaml"))
    return saved


@pytest.mark.parametrize("directed,reverse", [(False, False), (False, True), (True, False)])
@pytest.mark.parametrize("preserve", [False, True], ids=["marked-reference", "saved-semantic"])
def test_req_qml010_ac02_reference_namespace_retains_the_validated_frame(
        tmp_path, directed, reverse, preserve):
    """A semantic rename carries both alias spellings and logical direction through disk."""
    from graphify.dedup import _namespace_reference_stub_collisions
    raw = _reference_collision(tmp_path, directed=directed, reverse=reverse)
    nodes, edges = _namespace_reference_stub_collisions(
        raw["nodes"], raw["edges"], [], tmp_path, preserve_saved_semantics=preserve)
    raw.update(nodes=nodes, edges=edges, links=edges)
    mention = next(node["id"] for node in nodes if node.get("label") == "Authored mention")
    assert mention != "shared" and edges[0]["_tgt"] == mention
    assert (edges[0]["from"], edges[0]["to"]) == (edges[0]["source"], edges[0]["target"])
    graph = build_from_json(raw, directed=directed, root=tmp_path)
    assert arcs(graph, "describes") == {("review", mention)}
    path = tmp_path / "namespaced.json"
    assert to_json(graph, {}, str(path), original_links=raw["edges"])
    assert arcs(load_node_link_graph(path), "describes") == {("review", mention)}


@pytest.mark.parametrize("damage", ["foreign", "partial", "aliases", "directed_reverse"])
def test_req_qml022_ac06_reference_namespace_does_not_repair_invalid_transport(tmp_path, damage):
    """Namespacing does not launder a pre-existing invalid direction or endpoint alias."""
    from graphify.dedup import _namespace_reference_stub_collisions
    raw = _reference_collision(tmp_path, directed=damage == "directed_reverse")
    edge = raw["edges"][0]
    if damage == "foreign":
        edge["_tgt"] = "foreign"
    elif damage == "partial":
        edge.pop("_src")
    elif damage == "aliases":
        edge["from"] = 1
    else:
        edge["source"], edge["target"] = edge["target"], edge["source"]
        edge.update({"from": edge["source"], "to": edge["target"]})
    nodes, edges = _namespace_reference_stub_collisions(
        raw["nodes"], raw["edges"], [], tmp_path, preserve_saved_semantics=True)
    raw.update(nodes=nodes, edges=edges, links=edges)
    with pytest.raises(ValueError, match="SOURCE_RELATIONSHIPS_INVALID"):
        build_from_json(raw, directed=raw["directed"], root=tmp_path)


def test_req_qml010_ac02_reference_namespace_does_not_treat_a_storage_key_as_proof(tmp_path):
    """An arbitrary key stays untrusted through renaming; the builder derives storage anew."""
    from graphify.dedup import _namespace_reference_stub_collisions
    from graphify.source_relationships import storage_key
    raw = _reference_collision(tmp_path)
    raw["edges"][0]["key"] = "unverified"
    nodes, edges = _namespace_reference_stub_collisions(
        raw["nodes"], raw["edges"], [], tmp_path, preserve_saved_semantics=True)
    assert edges[0]["key"] == "unverified"
    raw.update(nodes=nodes, edges=edges, links=edges)
    mention = next(node["id"] for node in nodes if node.get("label") == "Authored mention")
    graph = build_from_json(raw, root=tmp_path)
    assert arcs(graph, "describes") == {("review", mention)}
    assert edges[0]["key"] == storage_key("review", mention, "describes")


@pytest.mark.parametrize("damage", ["markers", "alias"])
def test_req_qml010_ac03_reference_namespace_cannot_heal_a_future_identity(tmp_path, damage):
    """A foreign future ID cannot become accepted because physical endpoints changed first."""
    from graphify.dedup import _namespace_reference_stub_collisions
    from graphify.source_relationships import storage_key
    raw = _reference_collision(tmp_path)
    edge = raw["edges"][0]
    future = "readme__ref__shared"
    edge["key"] = storage_key("review", future, "describes")
    if damage == "markers":
        edge["_tgt"] = future
    else:
        edge.pop("_src")
        edge.pop("_tgt")
        edge["to"] = future
    before = deepcopy(edge)
    nodes, edges = _namespace_reference_stub_collisions(
        raw["nodes"], raw["edges"], [], tmp_path, preserve_saved_semantics=True)
    raw.update(nodes=nodes, edges=edges, links=edges)
    with pytest.raises(ValueError, match="SOURCE_RELATIONSHIPS_INVALID"):
        build_from_json(raw, root=tmp_path)
    assert edges[0] == before
