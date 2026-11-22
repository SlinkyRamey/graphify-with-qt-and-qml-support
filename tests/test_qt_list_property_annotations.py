"""REQ-QML-008-AC07: keep documented C++ list-property policy as source metadata."""

import json

import pytest

from graphify.extract import extract
from graphify.extractors.qml_cmake import extract_cmake
from graphify.extractors.qt_cpp_facts import qt_metadata
from graphify.qt_project_index import QtProjectIndex
from graphify.qt_qml_bridge import build_qt_qml_bridge
from tests.qml_persistence_helpers import protected_snapshot
from tests.qt_cpp_test_helpers import collect, facts, write
from tests.test_qml_types_qt612_updates import run_update

_PREFIX = "QML_LIST_PROPERTY_ASSIGN_BEHAVIOR_"


def _header(annotation, body=""):
    return f"""class Basket : public QObject {{
    Q_OBJECT
    QML_ELEMENT
    {annotation}
    Q_PROPERTY(QQmlListProperty<QObject> items READ items)
public:
    QQmlListProperty<QObject> items();
    {body}
}};
"""


def _registration(result):
    registration, = facts(result, "registration", "Basket")
    return registration["id"], qt_metadata(registration)


def _lookup(tmp_path, result):
    project = write(tmp_path, "CMakeLists.txt", "qt_add_qml_module(app URI Demo VERSION 1.0 SOURCES basket.hpp)\n")
    metadata = extract_cmake(project, root=tmp_path)
    nodes, edges = result["nodes"] + metadata["nodes"], result["edges"] + metadata["edges"]
    index = build_qt_qml_bridge(nodes, edges, root=tmp_path,
                                project_index=QtProjectIndex(nodes, edges, root=tmp_path))
    return index, index.module_type("Demo", 1, 0, "Basket")


@pytest.mark.parametrize("suffix,behavior", [
    ("APPEND", "Append"),
    ("REPLACE", "Replace"),
    ("REPLACE_IF_NOT_DEFAULT", "ReplaceIfNotDefault"),
])
def test_req_qml008_ac07_documented_list_policy_preserves_provider(tmp_path, suffix, behavior):
    """Keep the exact annotation, native provider and property source identity."""
    marker = _PREFIX + suffix
    source = "#ifndef BASKET_HPP\n#define BASKET_HPP\n" + _header(marker) + "#endif\n"
    result = collect(tmp_path, {"basket.hpp": source})
    _, md = _registration(result)
    assert md["status"] == "resolved" and md["unsupported_macros"] == []
    assert md["list_property_assign_behavior"] == behavior
    annotation, = [item for item in md["annotations"] if item["name"] == marker]
    span = annotation["span"]
    assert annotation["args"] == [] and source.encode()[span["start_byte"]:span["end_byte"]] == marker.encode()
    index, provider = _lookup(tmp_path, result)
    assert provider.status == "resolved"
    assert index.member(provider.target_id, "items").status == "resolved"


@pytest.mark.parametrize("annotation,body,status,reason", [
    (_PREFIX + "REPLACE()", "", "unsupported", "qml_list_property_assign_behavior_form_unsupported"),
    (_PREFIX + "REPLACE(x)", "", "unsupported", "qml_list_property_assign_behavior_form_unsupported"),
    (_PREFIX + "REPLACE\n" + _PREFIX + "REPLACE", "", "ambiguous", "qml_list_property_assign_behavior_ambiguous"),
    (_PREFIX + "REPLACE\n" + _PREFIX + "APPEND", "", "ambiguous", "qml_list_property_assign_behavior_ambiguous"),
    (_PREFIX + 'REPLACE\nQ_CLASSINFO("QML.ListPropertyAssignBehavior", "Append")', "",
     "ambiguous", "qml_list_property_assign_behavior_ambiguous"),
    ("\n#ifdef USE_REPLACE\n" + _PREFIX + "REPLACE\n#endif", "", "dynamic", "conditional_qml_list_property_assign_behavior"),
    ("", "void work() {\n" + _PREFIX + "REPLACE\n}", "unsupported", "qml_list_property_assign_behavior_scope_unsupported"),
    (_PREFIX + "REPLACE\nQML_ATTACHED(Attachment)", "", "unsupported", "foreign_extended_or_attached_mapping_unsupported"),
])
def test_req_qml008_ac07_list_policy_has_no_authority_from_invalid_markers(tmp_path, annotation, body, status, reason):
    """Uncertain list annotations cannot make a previously rejected provider usable."""
    result = collect(tmp_path, {"basket.hpp": _header(annotation, body)})
    _, md = _registration(result)
    assert (md["status"], md["reason"]) == (status, reason)
    assert "list_property_assign_behavior" not in md
    _, provider = _lookup(tmp_path, result)
    assert provider.status != "resolved"


def test_req_qml008_ac07_nested_list_policy_does_not_authorize_parent_hint(tmp_path):
    """A nested class or comment does not become list policy of the outer type."""
    annotation = "struct Inner {\n" + _PREFIX + "REPLACE\n};\n// " + _PREFIX + "REPLACE"
    result = collect(tmp_path, {"basket.hpp": _header(annotation)})
    _, md = _registration(result)
    assert md["status"] == "resolved" and "list_property_assign_behavior" not in md


def test_req_qml008_ac07_list_policy_update_removes_stale_annotation(tmp_path, monkeypatch):
    """The same registration ID survives a source-only list policy removal."""
    path = write(tmp_path, "basket.hpp", _header(_PREFIX + "REPLACE"))
    monkeypatch.chdir(tmp_path)
    assert run_update(tmp_path, monkeypatch) == 0
    graph = tmp_path / "graphify-out/graph.json"
    original, md = _registration(json.loads(graph.read_text(encoding="utf-8")))
    assert md["list_property_assign_behavior"] == "Replace"
    write(tmp_path, "basket.hpp", _header(""))
    assert run_update(tmp_path, monkeypatch) == 0
    identity, md = _registration(json.loads(graph.read_text(encoding="utf-8")))
    assert identity == original and md["status"] == "resolved"
    assert "list_property_assign_behavior" not in md
    direct = extract([path], root=tmp_path, cache_root=tmp_path / "fresh-cache", parallel=False)
    assert _registration(direct) == (identity, md)
    settled = protected_snapshot(tmp_path / "graphify-out")
    assert run_update(tmp_path, monkeypatch) == 0
    assert protected_snapshot(tmp_path / "graphify-out") == settled
