"""REQ-QML-005-AC05 explicit component capture through production extraction."""

import json

import networkx as nx
import pytest

from graphify.build import build_from_json
from graphify.extract import extract
from graphify.extractors.qml_facts import qml_metadata
from tests.qml_expression_helpers import extraction, single, target_edges


DELEGATE = """import QtQuick

ListView {
    id: outer
    property int payload: 7
    model: 1
    delegate: Item {
        property LocalValue outer: LocalValue {}
        property int observed: outer.payload
    }
}
"""


@pytest.mark.parametrize(
    "directive, expected_file",
    [
        pytest.param("pragma ComponentBehavior: Bound\n", "Main.qml", id="bound"),
        pytest.param("pragma ComponentBehavior: Unbound\n", "LocalValue.qml", id="unbound"),
        pytest.param("", "LocalValue.qml", id="default"),
        pytest.param("// pragma ComponentBehavior: Bound\n", "LocalValue.qml", id="comment"),
        pytest.param("pragma ComponentBehavior: Unknown\n", "LocalValue.qml", id="unknown"),
        pytest.param(
            "pragma ComponentBehavior: Unbound\npragma ComponentBehavior: Bound\n",
            "LocalValue.qml",
            id="conflicting",
        ),
    ],
)
def test_req_qml005_ac05_outer_id_precedes_local_property_only_when_bound(
    tmp_path, directive, expected_file
):
    """The explicit directive changes the binding target and survives graph transport."""
    sources = {
        "Main.qml": directive + DELEGATE,
        "LocalValue.qml": "import QtQuick\nItem { property int payload: 9 }",
    }
    first = extraction(tmp_path, sources)
    site = single(first, "read", "outer.payload")
    target = single(first, "property", "payload", expected_file)
    edge = target_edges(first, site, "qml_binding_read")
    assert [item["target"] for item in edge] == [target["id"]]

    graph = build_from_json(first, root=tmp_path, directed=True)
    portable = nx.node_link_graph(json.loads(json.dumps(nx.node_link_data(graph))))
    assert portable.has_edge(site["id"], target["id"])
    assert qml_metadata(portable.nodes[site["id"]])["span"] == qml_metadata(site)["span"]
    if expected_file == "Main.qml":
        source = single(first, "file", file="Main.qml")
        metadata = qml_metadata(portable.nodes[source["id"]])
        span = metadata["component_behavior_span"]
        assert metadata["component_behavior"] == "Bound"
        original = (tmp_path / "Main.qml").read_bytes()
        assert original[span["start_byte"]:span["end_byte"]] == directive.strip().encode()
        assert qml_metadata(site)["evidence"] == [source["id"]]
        assert qml_metadata(portable.nodes[site["id"]])["evidence"] == [source["id"]]


def test_req_qml005_ac05_bound_capture_excludes_local_ids_js_aliases_and_foreign_ids(tmp_path):
    """Nested source scopes inherit only established outer IDs with their own boundaries intact."""
    sources = {
        "Main.qml": """pragma ComponentBehavior: Bound
import QtQml

QtObject {
    id: outer
    property int outerValue: 7
    property QtObject source: QtObject { id: pulse; signal remotePulse() }
    component Nested: QtObject {
        id: local
        property int localValue: 9
        signal pulse()
        onPulse: localValue = outer.outerValue
        property Connections wire: Connections { target: pulse; function onRemotePulse() {} }
        property int observed: outer.outerValue
        property alias forbidden: outer.outerValue
        property alias allowed: local.localValue
        property int foreign: outside.outsideValue
        function shadow(outer: var): var { return outer.outerValue }
        property Component deeper: Component {
            QtObject {
                id: outer
                property int deepest: 42
                property int innerObserved: outer.deepest + local.localValue
            }
        }
    }
}
""",
        "Foreign.qml": "import QtQml\nQtObject { id: outside; property int outsideValue: 1 }",
    }
    result = extraction(tmp_path, sources)
    reads = [
        item
        for item in result["nodes"]
        if qml_metadata(item).get("kind") == "read"
        and qml_metadata(item).get("raw_name") == "outer.outerValue"
    ]
    assert sorted(qml_metadata(item)["status"] for item in reads) == ["dynamic", "resolved", "resolved"]
    outer_decl = single(result, "property", "outerValue")
    for read in reads:
        if qml_metadata(read)["status"] == "resolved":
            assert [edge["target"] for edge in target_edges(result, read)] == [outer_decl["id"]]
    assert not target_edges(result, single(result, "alias", "forbidden"))
    assert not target_edges(result, single(result, "read", "outside.outsideValue"))

    for kind, site_name, target_name in (
        ("alias", "allowed", "localValue"),
        ("read", "outer.deepest", "deepest"),
        ("read", "local.localValue", "localValue"),
    ):
        site = single(result, kind, site_name)
        target = single(result, "property", target_name)
        assert [edge["target"] for edge in target_edges(result, site)] == [target["id"]]
    for handler, signal in (("onPulse", "pulse"), ("onRemotePulse", "remotePulse")):
        site = single(result, "handler", handler)
        target = single(result, "signal", signal)
        assert [edge["target"] for edge in target_edges(result, site)] == [target["id"]]


def test_req_qml005_ac05_editing_directive_replaces_the_incremental_target(tmp_path):
    """A QML-only edit reuses unchanged resolver context and drops the stale target."""
    # Equal directive-line widths isolate the semantics from source-site offsets.
    sources = {
        "Main.qml": "pragma ComponentBehavior: Bound  \n" + DELEGATE,
        "LocalValue.qml": "import QtQuick\nItem { property int payload: 9 }",
    }
    before = extraction(tmp_path, sources)
    old_site = single(before, "read", "outer.payload")
    old_target = single(before, "property", "payload", "Main.qml")
    new_target = single(before, "property", "payload", "LocalValue.qml")
    assert [edge["target"] for edge in target_edges(before, old_site)] == [old_target["id"]]
    main = tmp_path / "Main.qml"
    main.write_text("pragma ComponentBehavior: Unbound\n" + DELEGATE, encoding="utf-8")
    borrowed_nodes = [node for node in before["nodes"] if node["source_file"] == "LocalValue.qml"]
    borrowed_edges = [edge for edge in before["edges"] if edge.get("source_file") == "LocalValue.qml"]
    context_before = json.dumps((borrowed_nodes, borrowed_edges), sort_keys=True)
    incremental = extract(
        [main], root=tmp_path, cache_root=tmp_path, parallel=False,
        resolution_context_nodes=borrowed_nodes, resolution_context_edges=borrowed_edges,
    )
    new_site = single(incremental, "read", "outer.payload")
    assert new_site["id"] == old_site["id"]
    assert [edge["target"] for edge in target_edges(incremental, new_site)] == [new_target["id"]]
    assert json.dumps((borrowed_nodes, borrowed_edges), sort_keys=True) == context_before
    full = extract([main, tmp_path / "LocalValue.qml"], root=tmp_path, cache_root=tmp_path, parallel=False)
    assert single(full, "read", "outer.payload") == new_site
    assert target_edges(full, new_site) == target_edges(incremental, new_site)


def test_req_qml005_ac05_ambiguous_outer_id_does_not_fall_back(tmp_path):
    """Competing outer IDs do not authorize either target or a colliding property."""
    result = extraction(tmp_path, {
        "Main.qml": """pragma ComponentBehavior: Bound
import QtQuick
ListView {
    id: outer
    property int payload: 7
    Item { id: outer; property int payload: 8 }
    delegate: Item {
        property LocalValue outer: LocalValue {}
        property int observed: outer.payload
    }
}
""",
        "LocalValue.qml": "import QtQuick\nItem { property int payload: 9 }",
    })
    site = single(result, "read", "outer.payload")
    metadata = qml_metadata(site)
    assert metadata["status"] == "ambiguous"
    assert len(metadata["candidates"]) == 2
    assert not target_edges(result, site)
