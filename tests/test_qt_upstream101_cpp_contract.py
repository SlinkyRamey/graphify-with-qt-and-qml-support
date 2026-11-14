"""INC-QML-133: retain Qt source authority across upstream C++ call changes."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest

from graphify.build import build_from_json
from graphify.cache import load_cached
from graphify.export import to_json
from graphify.extract import extract, extract_cpp
from graphify.extractors.cpp_member_identity import member_fact
from graphify.paths import load_node_link_graph
from tests.cpp_same_unit_helpers import facade, native_calls, project, source


@pytest.mark.parametrize("mode", ["direct", "facade"])
@pytest.mark.parametrize("profile", ["lf", "bom_crlf"])
def test_req_qml003_ac02_salted_alias_keeps_source_containment(tmp_path, mode, profile):
    """The file-name collision and literal-name collision keep their actual alias nodes."""
    raw = ("using cpp = int;\nusing Cpp = double;\n"
           "struct Lower { cpp value; };\nstruct Upper { Cpp value; };\n").encode()
    if profile == "bom_crlf":
        raw = b"\xef\xbb\xbf// original \xcf\x80\r\n" + raw.replace(b"\n", b"\r\n")
    path = tmp_path / "types.cpp"
    path.write_bytes(raw)
    result = extract_cpp(path) if mode == "direct" else extract(
        [path], root=tmp_path, cache_root=tmp_path, parallel=False)
    aliases = {node["label"]: node for node in result["nodes"]
               if node["label"] in {"cpp", "Cpp"} and node.get("source_file")}
    assert set(aliases) == {"cpp", "Cpp"}
    assert len({node["id"] for node in aliases.values()}) == 2
    file_node, = [node for node in result["nodes"] if node["label"] == "types.cpp"]
    assert all(any(edge["source"] == file_node["id"] and edge["target"] == node["id"]
                   and edge["relation"] == "contains" for edge in result["edges"])
               for node in aliases.values())
    by_id = {node["id"]: node for node in result["nodes"]}
    reference_targets = {by_id[edge["source"]]["label"]: edge["target"]
                         for edge in result["edges"] if edge["relation"] == "references"
                         and by_id.get(edge["source"], {}).get("label") in {"Lower", "Upper"}}
    assert reference_targets == {"Lower": aliases["cpp"]["id"], "Upper": aliases["Cpp"]["id"]}
    assert path.read_bytes() == raw


@pytest.mark.parametrize("profile", ["lf", "bom_crlf"])
def test_req_qml022_ac06_qt_member_typed_receiver_and_local_value_stay_distinct(tmp_path, profile):
    """A QML-invoked canonical member retains typed calls without a shadowed free call."""
    text = source(owner="Cpp", prefix="class Peer { public: void execute(); };\n",
                  definition="void Cpp::run() { auto helper=[]{}; helper(); Peer p; p.execute(); }")
    raw = text.encode()
    if profile == "bom_crlf":
        raw = b"\xef\xbb\xbf// original \xcf\x80\r\n" + raw.replace(b"\n", b"\r\n")
    project(tmp_path, owner="Cpp", raw=raw)
    result = facade(tmp_path)  # Exercises both cold and warm production entry points.
    member, = [node for node in result["nodes"] if (fact := member_fact(node))
               and fact["role"] == "declaration" and node["label"] == ".run()"]
    native, = native_calls(result)
    assert native["target"] == member["id"]
    by_id = {node["id"]: node for node in result["nodes"]}
    targets = [by_id[edge["target"]] for edge in result["edges"]
               if edge["source"] == member["id"] and edge["relation"] == "calls"]
    assert [node["label"] for node in targets] == [".execute()"]
    assert member["definition_file"] == "panel.cpp"
    assert not any(node["label"] == "helper" for node in result["nodes"])
    output = tmp_path / "accepted.json"
    assert to_json(build_from_json(result, root=tmp_path), {}, str(output), force=True)
    restored = load_node_link_graph(json.loads(output.read_text(encoding="utf-8")))
    assert restored.nodes[member["id"]]["metadata"] == member["metadata"]
    assert restored.has_edge(member["id"], targets[0]["id"])
    assert restored.has_edge(native["source"], native["target"])


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("change", ["remove_include", "conflict_include"])
def test_req_qml022_ac06_included_owner_rebuild_drops_stale_dispatch(tmp_path, reverse, change):
    """Source-only include changes invalidate member routing, then permit exact recovery."""
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    header, impl, other = (tmp_path / "include/api.h", tmp_path / "src/api.cpp",
                           tmp_path / "src/other.cpp")
    header.write_text("class Api {public:void run();void send();};", encoding="utf-8")
    original = '#include "api.h"\nvoid Api::run(){send(); utility();}'
    impl.write_text(original, encoding="utf-8")
    other.write_text("void send(){}\nvoid utility(){}", encoding="utf-8")
    paths = [header, impl, other]
    if reverse:
        paths.reverse()
    cache_root = tmp_path / "cache"
    cold = extract(paths, root=tmp_path, cache_root=cache_root, parallel=False)
    assert cold == extract(paths, root=tmp_path, cache_root=cache_root, parallel=False)
    by_id = {node["id"]: node for node in cold["nodes"]}
    caller, = [node["id"] for node in cold["nodes"] if node["label"] == "Api::run()"]
    accepted = {edge["target"] for edge in cold["edges"]
                if edge["source"] == caller and edge["relation"] == "calls"}
    assert {by_id[target]["label"] for target in accepted} == {".send()", "utility()"}
    saved = tmp_path / "baseline.json"
    assert to_json(build_from_json(cold, root=tmp_path), {}, str(saved), force=True)
    loaded = load_node_link_graph(json.loads(saved.read_text(encoding="utf-8")))
    context_nodes = [{"id": identity, **data} for identity, data in loaded.nodes(data=True)
                     if data.get("source_file") != "src/api.cpp"]
    # Undirected storage iteration can reverse a file's contains edge. Reuse its
    # validated logical direction, as the production persisted-context path does.
    context_edges = [{"source": data.get("_src", left), "target": data.get("_tgt", right), **data}
                     for left, right, data in loaded.edges(data=True)
                     if data.get("source_file") != "src/api.cpp"]
    context_before = deepcopy((context_nodes, context_edges))
    changed = [impl]
    if change == "remove_include":
        impl.write_text(original.split("\n", 1)[1], encoding="utf-8")
    else:
        conflict = tmp_path / "include/conflict.h"
        conflict.write_text(header.read_text(encoding="utf-8"), encoding="utf-8")
        impl.write_text('#include "conflict.h"\n' + original, encoding="utf-8")
        changed.append(conflict)
    partial = extract(changed, root=tmp_path, cache_root=cache_root, parallel=False,
                      resolution_context_nodes=context_nodes, resolution_context_edges=context_edges)
    assert not any(edge["target"] == caller and edge["relation"] == "method" for edge in partial["edges"])
    assert not any(edge["source"] == caller and edge["relation"] == "calls" for edge in partial["edges"]), [
        (edge, by_id.get(edge["target"], {})) for edge in partial["edges"]
        if edge["source"] == caller and edge["relation"] == "calls"]
    assert (context_nodes, context_edges) == context_before
    impl.write_text(original, encoding="utf-8")
    recovered = extract([impl], root=tmp_path, cache_root=cache_root, parallel=False,
                        resolution_context_nodes=context_nodes, resolution_context_edges=context_edges)
    assert {edge["target"] for edge in recovered["edges"]
            if edge["source"] == caller and edge["relation"] == "calls"} == accepted
    assert not any(node["label"] == "Api" for node in recovered["nodes"])
    assert not any("_cpp_included_member" in node for node in recovered["nodes"])
    cached = load_cached(impl, root=tmp_path, cache_root=cache_root)
    assert cached is not None and cached["raw_calls"]
    assert not any("_cpp_included_member" in call for call in cached["raw_calls"])
