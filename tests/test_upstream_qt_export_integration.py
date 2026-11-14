"""INC-QML-49: preserved upstream links share Qt's durable export validation."""
from __future__ import annotations

import copy
import errno
import json
import os
from pathlib import Path

import networkx as nx
import pytest

import graphify.__main__ as mainmod
from graphify.build import build_from_json, build_merge
from graphify.export import to_json
from graphify.extractors.qml_facts import qml_metadata
from graphify.paths import load_node_link_graph
from graphify.source_relationships import is_source_graph
from tests.source_observation_helpers import observations, produce, reference, state
from tests.test_qt_json_direction import actual_graph, damage


def mixed_graph(root):
    """Real QML facts and ordinary parallel transport exercise one output boundary."""
    graph, source, target = actual_graph(root)
    # Reverse undirected insertion order to make raw storage differ from the
    # accepted QML read direction; generic links have no Qt marker transport.
    reversed_graph = nx.Graph()
    reversed_graph.add_node(target, **graph.nodes[target])
    reversed_graph.add_nodes_from((key, data) for key, data in graph.nodes(data=True) if key != target)
    reversed_graph.add_edges_from((v, u, copy.deepcopy(data)) for u, v, data in graph.edges(data=True))
    reversed_graph.add_node("ordinary_caller", label="caller", source_file="ordinary.py", file_type="code")
    reversed_graph.add_node("ordinary_target", label="target", source_file="ordinary.py", file_type="code")
    reversed_graph.add_edge("ordinary_caller", "ordinary_target", relation="calls", confidence="EXTRACTED")
    links = copy.deepcopy(nx.node_link_data(reversed_graph, edges="links")["links"])
    links.append({"source": "ordinary_caller", "target": "ordinary_target",
                  "relation": "imports", "confidence": "EXTRACTED"})
    return reversed_graph, links, source, target


def assert_preserved(path, source, target):
    """The durable file keeps both generic mechanisms and the actual QML proof."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    ordinary = [link for link in payload["links"] if link["source"] == "ordinary_caller"]
    assert {link["relation"] for link in ordinary} == {"calls", "imports"} and len(ordinary) == 2
    read = next(link for link in payload["links"] if link.get("context") == "qml_binding_read")
    assert (read["source"], read["target"]) == (source, target)
    assert read["source_location"] and qml_metadata(read)
    assert "_src" not in read and "_tgt" not in read
    loaded = load_node_link_graph(path)
    assert loaded.has_edge(source, target)
    assert loaded.nodes[source]["source_file"] == "Main.qml"
    return payload


def test_req_qml_010_ac02_original_parallel_links_retain_qml_direction(tmp_path):
    """Upstream's original-link path preserves distinct relations and Qt source provenance."""
    graph, links, source, target = mixed_graph(tmp_path)
    before = copy.deepcopy(nx.node_link_data(graph, edges="links")), copy.deepcopy(links)
    path = tmp_path / "graph.json"
    assert to_json(graph, {}, str(path), original_links=links, built_at_commit="fixed")
    assert_preserved(path, source, target)
    assert (nx.node_link_data(graph, edges="links"), links) == before
    accepted = path.read_bytes()
    assert to_json(graph, {}, str(path), original_links=links, built_at_commit="fixed")
    assert path.read_bytes() == accepted


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("corruption", ["partial_source", "foreign_target", "list", "collapsed"])
def test_req_qml_018_ac06_preserved_invalid_link_refuses_replacement(tmp_path, capsys, force, corruption):
    """A corrupt original link cannot hide behind the simple graph's accepted edge."""
    graph, links, source, target = mixed_graph(tmp_path)
    path = tmp_path / "previous.json"
    assert to_json(graph, {}, str(path), original_links=links, built_at_commit="fixed")
    accepted, stamp = path.read_bytes(), path.stat().st_mtime_ns
    malformed = copy.deepcopy(links)
    read = next(link for link in malformed if link.get("context") == "qml_binding_read")
    damage(read, corruption, source, target)
    before = copy.deepcopy(malformed), copy.deepcopy(nx.node_link_data(graph, edges="links"))
    assert to_json(graph, {}, str(path), original_links=malformed, force=force, built_at_commit="fixed") is False
    assert path.read_bytes() == accepted and path.stat().st_mtime_ns == stamp
    diagnostic = capsys.readouterr().err
    assert "QT_EXPORT_DIRECTION" in diagnostic and "private-needle" not in diagnostic
    assert (malformed, nx.node_link_data(graph, edges="links")) == before
    assert to_json(graph, {}, str(path), original_links=links, built_at_commit="fixed")
    assert path.read_bytes() == accepted
    assert_preserved(path, source, target)


def test_req_qml_018_ac04_real_recluster_keeps_parallel_and_qml_links(tmp_path, monkeypatch, capsys):
    """The production offline CLI reloads and writes every original relationship twice."""
    graph, links, source, target = mixed_graph(tmp_path)
    out = tmp_path / "graphify-out"
    out.mkdir()
    path = out / "graph.json"
    assert to_json(graph, {}, str(path), original_links=links, built_at_commit="fixed")
    previous = assert_preserved(path, source, target)
    monkeypatch.setattr(mainmod, "_check_skill_version", lambda _: None)
    monkeypatch.setattr(mainmod.sys, "argv", ["graphify", "cluster-only", str(tmp_path), "--no-viz", "--no-label"])
    for _ in range(2):
        mainmod.main()
        current = assert_preserved(path, source, target)
        assert current["links"] == previous["links"]
        assert current["built_at_commit"] == "fixed"
        assert "Done -" in capsys.readouterr().out


def test_req_qml014_ac04_typed_semantic_replacement_retains_retry_authority(tmp_path, monkeypatch, capsys):
    """An accepted source graph can replace one document without excusing another."""
    raw = produce(tmp_path)
    raw["nodes"].extend([
        {"id": "release_old", "label": "Old release", "file_type": "document", "source_file": "Release.md"},
        {"id": "guide", "label": "Migration guide", "file_type": "document", "source_file": "Guide.md"},
    ])
    output = tmp_path / "graph.json"
    baseline = build_from_json(raw, root=tmp_path)
    assert is_source_graph(baseline)
    assert to_json(baseline, {}, str(output), built_at_commit="fixed")
    accepted, stamp = output.read_bytes(), output.stat().st_mtime_ns

    chunk = {"nodes": [
        {"id": "release_new", "label": "New release", "file_type": "document", "source_file": "Release.md"},
    ], "edges": []}
    graph = build_merge([chunk], output, dedup=False, root=tmp_path)
    assert is_source_graph(graph)
    assert graph.graph["_replaced_sources"] == ["Release.md"]
    guide = dict(graph.nodes["guide"])
    graph.remove_node("guide")
    assert not to_json(graph, {}, str(output), built_at_commit="fixed")
    diagnostic = capsys.readouterr().err
    assert "saved semantic node(s) are missing from the new graph: guide" in diagnostic
    assert "release_old" not in diagnostic
    assert graph.graph["_replaced_sources"] == ["Release.md"]
    assert output.read_bytes() == accepted and output.stat().st_mtime_ns == stamp
    graph.add_node("guide", **guide)

    # Fail the actual atomic replace, retaining both the old product and all
    # validated accounting needed for an identical retry.
    before = state(graph)
    replace = os.replace

    def fail_graph_replace(source, target):
        if Path(str(target).removeprefix("\\\\?\\")).resolve() == output.resolve():
            raise OSError(errno.EIO, "injected typed semantic graph replace failure")
        return replace(source, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "replace", fail_graph_replace)
        with pytest.raises(OSError, match="injected typed semantic graph replace failure"):
            to_json(graph, {}, str(output), built_at_commit="fixed")
    assert state(graph) == before
    assert output.read_bytes() == accepted and output.stat().st_mtime_ns == stamp

    assert to_json(graph, {}, str(output), built_at_commit="fixed")
    payload = json.loads(output.read_bytes())
    assert "_replaced_sources" not in graph.graph and "_replaced_sources" not in payload["graph"]
    loaded = load_node_link_graph(output)
    assert is_source_graph(loaded)
    assert {"release_new", "guide"} <= set(loaded) and "release_old" not in loaded
    assert {item["context"] for item in observations(reference(loaded))} == {"field", "generic_arg"}
