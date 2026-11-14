"""INC133: promised source cohorts inspect endpoint aliases before adapters."""
from __future__ import annotations

from copy import deepcopy

import networkx as nx
import pytest

from graphify.build import build, build_from_json
from graphify.export import to_json
from graphify.paths import load_node_link_graph
from graphify.source_relationships import FIELD
from tests.source_relationship_projection_helpers import arcs, state


def raw(*, promised=True, numeric=False):
    """Public raw storage input; no parser or native-source authority is fabricated."""
    one, two = (1, 2) if numeric else ("one", "two")
    result = {"nodes": [{"id": identity, "label": str(identity), "file_type": "code",
                         "source_file": f"{index}.py", "source_location": "L1", "_origin": "ast"}
                        for identity, index in ((one, 1), (two, 2))],
              "edges": [{"source": one, "target": two, "from": one, "to": two,
                         "relation": "calls", "confidence": "EXTRACTED", "source_file": "1.py",
                         "source_location": "L2", "_src": one, "_tgt": two}],
              "directed": False, "multigraph": promised}
    if promised:
        result["graph"] = {FIELD: {"contract_version": 1}}
    return result


def consume(value, boundary, root):
    if boundary == "combined":
        return build([value], root=root, dedup=False)
    if boundary == "reload":
        return load_node_link_graph(value)
    return build_from_json(value, root=root)


@pytest.mark.parametrize("boundary", ["direct", "combined", "reload"])
@pytest.mark.parametrize("numeric", [False, True], ids=["text", "numeric"])
def test_req_qml010_ac02_agreeing_source_aliases_keep_the_existing_adapters(tmp_path, boundary, numeric):
    """Exact aliases remain valid for supported string and numeric identity frames."""
    data = raw(numeric=numeric)
    graph = consume(data, boundary, tmp_path)
    assert graph.is_multigraph() and graph.number_of_edges() == 1
    assert FIELD in graph.graph


@pytest.mark.parametrize("boundary", ["direct", "combined", "reload"])
@pytest.mark.parametrize("alias", ["from", "to"])
@pytest.mark.parametrize("value", ["future_id", None, True, 1, [], {}])
def test_req_qml010_ac03_raw_alias_conflict_rejects_before_input_conversion(
        tmp_path, boundary, alias, value):
    """A supplied unused endpoint alias cannot vanish behind primary endpoint selection."""
    data = raw()
    data["edges"][0][alias] = deepcopy(value)
    before = deepcopy(data)
    with pytest.raises(ValueError, match="SOURCE_RELATIONSHIPS_INVALID"):
        consume(data, boundary, tmp_path)
    assert data == before


@pytest.mark.parametrize("boundary", ["direct", "combined", "reload"])
def test_req_qml022_ac06_bool_alias_is_not_an_integer_identity(tmp_path, boundary):
    """Python equality cannot admit True as an alias for the integer node 1."""
    data = raw(numeric=True)
    data["edges"][0]["from"] = True
    before = deepcopy(data)
    with pytest.raises(ValueError, match="SOURCE_RELATIONSHIPS_INVALID"):
        consume(data, boundary, tmp_path)
    assert data == before


@pytest.mark.parametrize("boundary", ["direct", "combined", "reload"])
@pytest.mark.parametrize("bad_mode", [None, "directed", "multigraph"])
def test_req_qml022_ac06_unused_collection_alias_and_modes_are_not_hidden(tmp_path, boundary, bad_mode):
    """Every supplied cohort is inspected, including one an adapter would not select."""
    data = raw()
    data["links"] = deepcopy(data["edges"])
    data["links"][0]["to"] = None
    if bad_mode:
        data[bad_mode] = "false"
    before = deepcopy(data)
    with pytest.raises(ValueError, match="SOURCE_RELATIONSHIPS_INVALID"):
        consume(data, boundary, tmp_path)
    assert data == before


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("alias", ["from", "to"])
@pytest.mark.parametrize("value", ["future_id", None, True, []])
def test_req_qml010_ac03_export_alias_rejection_retains_disk_accounting_and_recovers(
        tmp_path, force, alias, value):
    """The actual writer rejects a bad alternate view before consuming retry evidence."""
    data = raw()
    graph = build_from_json(data, root=tmp_path)
    path = tmp_path / "graph.json"
    assert to_json(graph, {}, str(path))
    accepted = path.read_bytes()
    links = deepcopy(data["edges"])
    links[0][alias] = deepcopy(value)
    graph.graph.update(_dedup_collapsed=3, _pruned_node_count=2,
                       _dedup_remap={"old": "one"})
    before, alternate = state(graph), deepcopy(links)
    assert to_json(graph, {}, str(path), force=force, original_links=links) is False
    assert path.read_bytes() == accepted and state(graph) == before and links == alternate
    links[0][alias] = links[0]["source" if alias == "from" else "target"]
    assert to_json(graph, {}, str(path), original_links=links)
    assert arcs(load_node_link_graph(path), "calls") == {("one", "two")}
    assert not {"_dedup_remap", "_dedup_collapsed", "_pruned_node_count"} & graph.graph.keys()


@pytest.mark.parametrize("value", [None, True, [], "historical"])
def test_req_qml022_ac06_unpromised_legacy_endpoint_alias_keeps_its_tolerance(tmp_path, value):
    """The optional source contract does not impose new rejection on ordinary legacy graphs."""
    data = raw(promised=False)
    data["edges"][0]["from"] = deepcopy(value)
    graph = build_from_json(data, root=tmp_path)
    assert type(graph) is nx.Graph and FIELD not in graph.graph
    assert graph.number_of_edges() == 1


@pytest.mark.parametrize("directed", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("aliases", ["both", "from", "to", "none"])
def test_req_qml010_ac02_graph_view_uses_validated_direction_without_mutating_the_graph(
        tmp_path, directed, reverse, aliases):
    """Real undirected iteration may flip physical order; publication preserves the accepted pair."""
    data = raw()
    data["nodes"].reverse()
    data["directed"] = directed
    edge = data["edges"][0]
    if reverse:
        for left, right in (("source", "target"), ("from", "to"), ("_src", "_tgt")):
            edge[left], edge[right] = edge[right], edge[left]
    for name in ("from", "to"):
        if aliases not in {"both", name}:
            edge.pop(name)
    expected = (edge["source"], edge["target"])
    graph = build_from_json(data, directed=directed, root=tmp_path)
    before = state(graph)
    path = tmp_path / "accepted-view.json"
    assert to_json(graph, {}, str(path))
    assert state(graph) == before
    assert arcs(load_node_link_graph(path), "calls") == {expected}
    persisted = load_node_link_graph(path)
    attrs = next(iter(persisted.edges(data=True)))[2]
    for name in ("from", "to"):
        assert (name in attrs) == (aliases in {"both", name})


@pytest.mark.parametrize("damage", ["foreign", "null", "bool", "duplicate", "partial_direction", "directed_flip"])
def test_req_qml010_ac03_graph_view_rejects_bad_aliases_without_borrowing_an_override(tmp_path, damage):
    """An alternate raw view cannot cure a corrupt accepted graph parent frame."""
    data = raw()
    directed = damage == "directed_flip"
    data["directed"] = directed
    graph = build_from_json(data, directed=directed, root=tmp_path)
    path = tmp_path / "graph.json"
    assert to_json(graph, {}, str(path))
    accepted = path.read_bytes()
    attrs = next(iter(graph.edges(data=True)))[2]
    if damage == "foreign":
        attrs["from"] = "foreign"
    elif damage == "null":
        attrs["to"] = None
    elif damage == "bool":
        attrs["from"] = True
    elif damage == "duplicate":
        attrs["to"] = attrs["from"]
    elif damage == "partial_direction":
        attrs.pop("_src")
    else:
        attrs["from"], attrs["to"] = attrs["to"], attrs["from"]
    graph.graph.update(_dedup_collapsed=2, _pruned_node_count=1, _dedup_remap={"old": "one"})
    before, alternate = state(graph), deepcopy(data["edges"])
    assert to_json(graph, {}, str(path), force=True, original_links=alternate) is False
    assert state(graph) == before and path.read_bytes() == accepted and alternate == data["edges"]


def test_req_qml010_ac02_combined_preflight_keeps_cross_chunk_forward_references(tmp_path):
    """Raw cohort validation inspects shape/modes while the combined owner resolves endpoints."""
    first, second = raw(), raw()
    first["nodes"] = first["nodes"][:1]
    second["nodes"], second["edges"] = second["nodes"][1:], []
    graph = build([first, second], root=tmp_path, dedup=False)
    assert set(graph) == {"one", "two"} and arcs(graph, "calls") == {("one", "two")}
