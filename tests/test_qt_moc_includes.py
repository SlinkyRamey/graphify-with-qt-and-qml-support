"""REQ-QML-008-AC06 source-only meta-object compiler include evidence."""
from __future__ import annotations

import pytest

from graphify.extract import extract
from graphify.extractors.qt_cpp_facts import qt_metadata
from graphify.extractors.qt_cpp_syntax import QtCppError, read_cpp
from graphify.watch import _rebuild_code
from tests.test_qt_final_incremental_parity import clean, normalized, run


SOURCE = '''// Multibyte source before the annotation: café
Q_MOC_INCLUDE("model/Forward.h")
class Backend {
    Q_OBJECT
    Q_MOC_INCLUDE("other/Details.hpp")
    Q_PROPERTY(int count READ count)
public:
    int count() const;
};
#if USE_EXTRA
Q_MOC_INCLUDE("optional.h")
#endif
void ordinary() { const char *ignored = R"(Q_MOC_INCLUDE("ignored.h"))"; }
'''


def _sites(nodes):
    return {md["raw_name"]: (identity, md) for identity, data in nodes
            if (md := qt_metadata(data)).get("kind") == "moc_include"}


def test_req_qml008_ac06_moc_includes_are_source_sites_with_original_bytes(tmp_path):
    """Class/file/conditional markers survive alongside the real property."""
    path = tmp_path / "backend.h"
    source = SOURCE.encode()
    path.write_bytes(source)
    unit = read_cpp(path, tmp_path)
    assert [item["args"][0] for item in unit.macros if item["name"] == "Q_MOC_INCLUDE"] == [
        '"model/Forward.h"', '"other/Details.hpp"', '"optional.h"']
    result = extract([path], root=tmp_path, cache_root=tmp_path, parallel=False)
    assert not result["qml_failures"] and not result["failed_sources"]
    sites = _sites((node["id"], node) for node in result["nodes"])
    assert set(sites) == {"model/Forward.h", "other/Details.hpp", "optional.h"}
    assert sites["model/Forward.h"][1]["status"] == "source_only"
    assert sites["optional.h"][1]["conditional"] is True
    assert sites["optional.h"][1]["status"] == "dynamic"
    assert sites["other/Details.hpp"][1]["class_name"] == "Backend"
    assert all(node["source_file"] == "backend.h" for node in result["nodes"])
    assert any(qt_metadata(node).get("kind") == "property" for node in result["nodes"])
    for literal, (identity, md) in sites.items():
        span = md["span"]
        assert source[span["start_byte"]:span["end_byte"]] == f'Q_MOC_INCLUDE("{literal}")'.encode()
        edges = [edge for edge in result["edges"] if identity in (edge["source"], edge["target"])]
        assert len(edges) == 1 and edges[0]["relation"] == "contains"
        assert edges[0]["target"] == identity


def test_req_qml008_ac06_present_header_never_supplies_ordinary_include_authority(tmp_path):
    """An accepted matching path cannot convert a generated hint into an import."""
    path = tmp_path / "backend.h"
    header = tmp_path / "payload.h"
    path.write_text('Q_MOC_INCLUDE("payload.h")\nstruct Backend {};\n', encoding="utf-8")
    header.write_text('struct Payload {};\n', encoding="utf-8")
    result = extract([path, header], root=tmp_path, cache_root=tmp_path, parallel=False)
    assert not result["qml_failures"] and not result["failed_sources"]
    facts = _sites((node["id"], node) for node in result["nodes"])
    assert set(facts) == {"payload.h"}
    assert not [edge for edge in result["edges"] if edge["relation"] == "imports"]


@pytest.mark.parametrize("source", [
    'Q_MOC_INCLUDE(HEADER)\n',
    'Q_MOC_INCLUDE("a.h", "b.h")\n',
    'Q_MOC_INCLUDE("")\n',
    'Q_MOC_INCLUDE("a.h" "b.h")\n',
    '#define Q_MOC_INCLUDE(x)\nQ_MOC_INCLUDE("a.h")\n',
])
def test_req_qml008_ac06_unsupported_moc_include_has_a_source_diagnostic(tmp_path, source):
    """Unsupported forms cannot acquire a target or accept changed input."""
    path = tmp_path / "backend.h"
    path.write_text(source, encoding="utf-8")
    with pytest.raises(QtCppError) as caught:
        read_cpp(path, tmp_path)
    assert caught.value.code == "QT_CPP_SYNTAX"
    assert caught.value.span is not None
    assert caught.value.span["start_row"] == source.count("\n") - 1


def test_req_qml008_ac06_warm_reload_matches_fresh_and_removes_stale_markers(tmp_path, monkeypatch):
    """Change the only Qt marker, then check real watcher persistence/reload."""
    path = tmp_path / "only.h"
    path.write_text('Q_MOC_INCLUDE("original.h")\nstruct Backend {};\n', encoding="utf-8")
    first = run(tmp_path, monkeypatch, "watcher")
    assert set(_sites(first.nodes(data=True))) == {"original.h"}
    graph = tmp_path / "graphify-out/graph.json"
    baseline = graph.read_bytes()
    path.write_text('Q_MOC_INCLUDE(HEADER)\nstruct Backend {};\n', encoding="utf-8")
    assert not _rebuild_code(tmp_path, changed_paths=[path], no_cluster=True)
    assert graph.read_bytes() == baseline
    path.write_text('Q_MOC_INCLUDE("new.h")\nstruct Backend {};\n', encoding="utf-8")
    updated = run(tmp_path, monkeypatch, "watcher", changes=[path])
    assert set(_sites(updated.nodes(data=True))) == {"new.h"}
    assert normalized(updated) == normalized(clean(tmp_path, tmp_path / ".fresh-cache"))
