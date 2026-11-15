"""REQ-QML-008-AC05: generated property accessors retain their bindable declaration."""

import json

import pytest

from graphify.extract import extract
from graphify.extractors.qt_cpp_facts import qt_metadata
from tests.qml_persistence_helpers import protected_snapshot
from tests.qt_cpp_test_helpers import collect, facts, write
from tests.test_qml_types_qt612_updates import run_update


def _header(property_attributes, declarations):
    return f"""class Meter : public QObject {{
    Q_OBJECT
    Q_PROPERTY(int count {property_attributes})
public:
    {declarations}
}};
"""


def _accessors(result):
    return {qt_metadata(node)["accessor_role"]: (node["id"], qt_metadata(node))
            for node in facts(result, "property_accessor")}


def test_req_qml008_ac05_bindable_defaults_link_original_declaration(tmp_path):
    """The two generated roles reference one real API and never become C++ methods."""
    attributes = "READ default WRITE default BINDABLE bindableCount NOTIFY countChanged"
    source = _header(attributes, """::QBindable< int > bindableCount();
signals:
    void countChanged();""")
    result = collect(tmp_path, {"meter.h": source})
    accessors = _accessors(result)
    bindable = qt_metadata(facts(result, "member", "bindableCount")[0])["generic_target_id"]
    property_node = facts(result, "property", "count")[0]
    prop = qt_metadata(property_node)
    assert prop["read"] == prop["write"] == "default"
    assert prop["bindable"] == "bindableCount"
    assert bindable
    for role in ("read", "write"):
        identity, md = accessors[role]
        assert md["status"] == "resolved" and md["reason"] == ""
        assert md["raw_name"] == "default" and md["accessor_source"] == "qt_bindable_default"
        assert md["bindable"] == "bindableCount" and md["bindable_target_id"] == bindable
        assert md["generic_target_id"] == ""
        assert md["owner_id"] == md["property_id"] == property_node["id"]
        span = md["span"]
        assert source.encode()[span["start_byte"]:span["end_byte"]] == f"Q_PROPERTY(int count {attributes})".encode()
        assert [(edge["target"], edge["relation"], edge["context"]) for edge in result["edges"]
                if edge["source"] == identity] == [(bindable, "references", "qt_property_bindable_accessor")]
    assert accessors["notify"][1]["status"] == "resolved"
    assert "accessor_source" not in accessors["notify"][1]
    assert not facts(result, "member", "default")


@pytest.mark.parametrize("attributes,declarations,status,reason", [
    ("READ default", "QBindable<int> bindableCount();", "unsupported", "generated_accessor_requires_literal_bindable"),
    ("READ default BINDABLE", "QBindable<int> bindableCount();", "unsupported", "generated_accessor_requires_literal_bindable"),
    ("READ default BINDABLE bindableCount()", "QBindable<int> bindableCount();", "unsupported", "generated_accessor_requires_literal_bindable"),
    ("READ default BINDABLE bindableCount BINDABLE bindableCount", "QBindable<int> bindableCount();", "unsupported", "duplicate_property_attribute"),
    ("READ default BINDABLE bindableCount", "QBindable<int> other();", "unavailable", "bindable_declaration_unavailable"),
    ("READ default BINDABLE bindableCount", "QBindable<long> bindableCount();", "unsupported", "bindable_declaration_shape_unsupported"),
    ("READ default BINDABLE bindableCount", "QBindable<int> *bindableCount();", "unsupported", "bindable_declaration_shape_unsupported"),
    ("READ default BINDABLE bindableCount", "QBindable<int> bindableCount(int i = 0);", "unsupported", "bindable_declaration_shape_unsupported"),
    ("READ default BINDABLE bindableCount", "QBindable<int> bindableCount(); QBindable<int> bindableCount(int i);", "ambiguous", "bindable_declaration_ambiguous"),
    ("READ default BINDABLE bindableCount", "\n#if ENABLED\nQBindable<int> bindableCount();\n#endif", "dynamic", "conditional_bindable_declaration"),
])
def test_req_qml008_ac05_bindable_defaults_fail_closed(tmp_path, attributes, declarations, status, reason):
    """A malformed, conditional, or non-unique declaration cannot supply a reference."""
    source = _header(attributes, declarations)
    result = collect(tmp_path, {"meter.h": source})
    identity, md = _accessors(result)["read"]
    assert (md["status"], md["reason"]) == (status, reason)
    assert md["accessor_source"] == "qt_bindable_default"
    assert not md["generic_target_id"] and not md["bindable_target_id"]
    assert not [edge for edge in result["edges"] if edge["source"] == identity]


@pytest.mark.parametrize("prefix,declarations,reason", [
    ("template<typename T> class QBindable {};\n", "QBindable<int> bindableCount();", "bindable_type_identity_unavailable"),
    ("", "struct Inner { QBindable<int> bindableCount(); };", "bindable_declaration_unavailable"),
])
def test_req_qml008_ac05_bindable_reference_requires_exact_scope(tmp_path, prefix, declarations, reason):
    """A nested member or source-owned QBindable homonym cannot lend Qt binding evidence."""
    source = prefix + _header("READ default BINDABLE bindableCount", declarations)
    result = collect(tmp_path, {"meter.h": source})
    identity, md = _accessors(result)["read"]
    assert (md["status"], md["reason"], md["bindable_target_id"]) == ("unavailable", reason, "")
    assert not [edge for edge in result["edges"] if edge["source"] == identity]


@pytest.mark.parametrize("marker,status,reason", [
    ("", "unavailable", "bindable_metaobject_declaration_unavailable"),
    ("Q_OBJECT\n    Q_OBJECT", "ambiguous", "bindable_metaobject_declaration_ambiguous"),
    ("\n#if ENABLED\nQ_OBJECT\n#endif", "dynamic", "conditional_bindable_metaobject_declaration"),
    ("Q_GADGET", "resolved", ""),
    ("Q_GADGET_EXPORT(EXPORT_API)", "resolved", ""),
])
def test_req_qml008_ac05_bindable_target_requires_moc_declaration(tmp_path, marker, status, reason):
    """A header guard keeps real moc markers unconditional but confers no authority by itself."""
    source = _header("READ default BINDABLE bindableCount", "QBindable<int> bindableCount();")
    source = source.replace("Q_OBJECT", marker).replace(" : public QObject", "")
    source = "#ifndef METER_H\n#define METER_H\n" + source + "#endif\n"
    result = collect(tmp_path, {"meter.h": source})
    identity, md = _accessors(result)["read"]
    assert (md["status"], md["reason"]) == (status, reason)
    assert bool(md["bindable_target_id"]) == (status == "resolved")
    assert bool([edge for edge in result["edges"] if edge["source"] == identity]) == (status == "resolved")


def test_req_qml008_ac05_bindable_update_and_fresh_facade_agree(tmp_path, monkeypatch):
    """A C++-only edit drops stale binding proof and keeps an explicit READ callable."""
    attributes = "READ count WRITE default BINDABLE bindableCount"
    path = write(tmp_path, "meter.h", _header(attributes, "int count() const; QBindable<int> bindableCount();"))
    monkeypatch.chdir(tmp_path)
    assert run_update(tmp_path, monkeypatch) == 0
    graph = tmp_path / "graphify-out/graph.json"
    initial = json.loads(graph.read_text(encoding="utf-8"))
    initial_accessors = _accessors(initial)
    assert initial_accessors["read"][1]["generic_target_id"]
    assert initial_accessors["write"][1]["bindable_target_id"]
    assert len([edge for edge in initial["links"] if edge.get("context") == "qt_property_bindable_accessor"]) == 1
    write(tmp_path, "meter.h", _header(attributes, "int count() const; QBindable<long> bindableCount();"))
    assert run_update(tmp_path, monkeypatch) == 0
    changed = json.loads(graph.read_text(encoding="utf-8"))
    latest = _accessors(changed)
    assert latest["read"] == initial_accessors["read"]
    assert latest["write"][0] == initial_accessors["write"][0]
    assert latest["write"][1]["status"] == "unsupported"
    assert latest["write"][1]["bindable_target_id"] == ""
    assert not [edge for edge in changed["links"] if edge.get("context") == "qt_property_bindable_accessor"]
    direct = extract([path], root=tmp_path, cache_root=tmp_path / "fresh-cache", parallel=False)
    assert _accessors(direct) == latest
    settled = protected_snapshot(tmp_path / "graphify-out")
    assert run_update(tmp_path, monkeypatch) == 0
    assert protected_snapshot(tmp_path / "graphify-out") == settled
