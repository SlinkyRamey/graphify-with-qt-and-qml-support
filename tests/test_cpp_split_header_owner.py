"""Bind qualified C++ definitions through explicit included declarations."""
import pytest
from graphify.extract import extract
from graphify.extractors.cpp_class_proof import class_fact
from graphify.extractors.cpp_functions import decoded


@pytest.mark.parametrize("reverse", [False, True])
def test_split_header_member_call_never_binds_unrelated_free_function(tmp_path, reverse):
    (tmp_path / "include").mkdir()
    (tmp_path / "src").mkdir()
    h = tmp_path / "include/foo.h"
    h.write_text("class Foo {public: void run(); void say();};", encoding="utf-8")
    impl = tmp_path / "src/foo.cpp"
    impl.write_text('#include "foo.h"\nvoid Foo::say() {}\nvoid Foo::run() {say(); utility();}', encoding="utf-8")
    other = tmp_path / "src/bar.cpp"
    other.write_text('namespace { void say() {} }\nvoid utility() {}', encoding="utf-8")
    paths = [h, impl, other]
    r = extract(list(reversed(paths)) if reverse else paths, root=tmp_path, parallel=False, cache_root=tmp_path)
    nodes = {n["id"]: n for n in r["nodes"]}
    caller = next(n["id"] for n in r["nodes"] if n["label"] == "Foo::run()")
    calls = [e for e in r["edges"] if e["source"] == caller and e["relation"] == "calls"]
    assert any(nodes[e["target"]]["label"] in (".say()", "Foo::say()") for e in calls)
    assert not any(nodes[e["target"]]["label"] == "say()" for e in calls)
    assert any(nodes[e["target"]]["label"] == "utility()" for e in calls)
    owner = next(n["id"] for n in r["nodes"] if n["label"] == "Foo")
    assert any(e["source"] == owner and e["target"] == caller and e["relation"] == "method" for e in r["edges"])
    # An incremental caller must see the same included owner without copying
    # the unchanged header's nodes into the fresh extraction.
    impl.write_text(impl.read_text(encoding="utf-8") + "\n// edit\n", encoding="utf-8")
    fresh = extract([impl], root=tmp_path, parallel=False, cache_root=tmp_path,
        resolution_context_nodes=[n for n in r["nodes"] if n.get("source_file") != "src/foo.cpp"],
        resolution_context_edges=[e for e in r["edges"] if e.get("source_file") != "src/foo.cpp"])
    assert any(e["source"] == caller and e["target"] in nodes and nodes[e["target"]]["label"] == ".say()"
               and e["relation"] == "calls" for e in fresh["edges"])
    assert not any(n["label"] == "Foo" for n in fresh["nodes"])


def test_namespace_qualified_owner_is_not_a_same_named_included_type(tmp_path):
    a = tmp_path / "left.h"
    a.write_text("namespace Left {class Foo {public: void run(); void say();};}", encoding="utf-8")
    b = tmp_path / "right.h"
    b.write_text("namespace Right {class Foo {public: void run(); void say();};}", encoding="utf-8")
    cpp = tmp_path / "main.cpp"
    cpp.write_text('#include "left.h"\n#include "right.h"\nvoid Left::Foo::run(){say();}\nvoid Left::Foo::say(){}', encoding="utf-8")
    r = extract([a, b, cpp], root=tmp_path, parallel=False, cache_root=tmp_path)
    nodes = {n["id"]: n for n in r["nodes"]}
    caller = next(n["id"] for n in r["nodes"] if n.get("_cpp_qualified") == "Left::Foo::run")
    calls = [e for e in r["edges"] if e["source"] == caller and e["relation"] == "calls"]
    assert calls
    assert all(nodes[e["target"]]["source_file"] == "left.h" for e in calls)


def test_ambiguous_included_owner_fails_closed(tmp_path):
    paths = []
    for name in ["a.h", "b.h"]:
        p = tmp_path / name
        p.write_text("class Foo {public: void run(); void say();};", encoding="utf-8")
        paths.append(p)
    cpp = tmp_path / "main.cpp"
    cpp.write_text('#include "a.h"\n#include "b.h"\nvoid Foo::run(){say();}', encoding="utf-8")
    other = tmp_path / "other.cpp"
    other.write_text("void say(){}", encoding="utf-8")
    r = extract([*paths, cpp, other], root=tmp_path, parallel=False, cache_root=tmp_path)
    caller = next(n["id"] for n in r["nodes"] if n["label"] == "Foo::run()")
    # Distinct files genuinely provide the same qualified class, so neither can
    # authorize the implementation even though each declaration has its own ID.
    owners = [n for n in r["nodes"] if (fact := class_fact(n))
              and decoded(fact["qualified_name_b64"]) == "Foo"]
    assert {n["source_file"] for n in owners} == {"a.h", "b.h"}
    assert len({n["id"] for n in owners}) == 2
    assert not any(e["relation"] == "method" and e["target"] == caller for e in r["edges"])
    assert not any(e["source"] == caller and e["relation"] == "calls" for e in r["edges"])


def test_distinct_namespace_types_keep_their_exact_included_owner(tmp_path):
    header = tmp_path / "foo.h"
    header.write_text("namespace Left {class Foo {public: void run(); void say();};}\n"
                      "namespace Right {class Foo {public: void run(); void say();};}", encoding="utf-8")
    cpp = tmp_path / "main.cpp"
    cpp.write_text('#include "foo.h"\nvoid Right::Foo::run(){say();}', encoding="utf-8")
    r = extract([header, cpp], root=tmp_path, parallel=False, cache_root=tmp_path)
    caller_node, = [n for n in r["nodes"] if n.get("_cpp_qualified") == "Right::Foo::run"]
    caller = caller_node["id"]
    assert caller_node["source_file"] == "foo.h" and caller_node["definition_file"] == "main.cpp"
    body = caller_node["metadata"]["cpp_member"]["definition"]
    assert body["role"] == "definition" and decoded(body["owner_b64"]) == "Right::Foo"
    span = body["span"]
    assert cpp.read_bytes()[span["start_byte"]:span["end_byte"]] == b"void Right::Foo::run(){say();}"
    # The fork already retains literal scoped class identities. Unlike upstream's
    # legacy collapsed-ID fixture, these are two independently proven classes;
    # require the exact Right owner/callee and reject every Left relationship.
    owners = {decoded(fact["qualified_name_b64"]): n["id"] for n in r["nodes"]
              if (fact := class_fact(n))}
    assert set(owners) == {"Left::Foo", "Right::Foo"}
    assert owners["Left::Foo"] != owners["Right::Foo"]
    assert {e["source"] for e in r["edges"] if e["relation"] == "method"
            and e["target"] == caller} == {owners["Right::Foo"]}
    by_id = {n["id"]: n for n in r["nodes"]}
    right_say, = [e["target"] for e in r["edges"] if e["source"] == owners["Right::Foo"]
                  and e["relation"] == "method" and by_id[e["target"]]["label"] == ".say()"]
    assert {e["target"] for e in r["edges"] if e["source"] == caller
            and e["relation"] == "calls"} == {right_say}
