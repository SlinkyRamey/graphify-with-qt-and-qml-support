"""Source identity, MATLAB scope, and repository call contract regressions."""

from pathlib import Path

import pytest

from graphify.extractors import matlab_ast
from graphify.extractors.matlab import extract_matlab
from graphify.extractors.matlab_resolution import resolve_matlab_calls


def _extract(root: Path, name: str, body: str, *, require_parser: bool = True) -> dict:
    if require_parser:
        pytest.importorskip("tree_sitter_language_pack")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return extract_matlab(path, root=root)


def _node(result: dict, name: str) -> dict:
    return next(node for node in result["nodes"] if node["label"] == name)


def _relations(nodes: list[dict], edges: list[dict], relation: str) -> set:
    by_id = {node["id"]: node for node in nodes}
    return {
        (
            by_id[edge["source"]]["label"], by_id[edge["target"]]["label"],
            edge["source_location"],
        )
        for edge in edges if edge["relation"] == relation
    }


def _combine(root: Path, *results: dict) -> tuple[list, list, list]:
    nodes = [node for result in results for node in result["nodes"]]
    edges = [edge for result in results for edge in result["edges"]]
    raw_calls = [call for result in results for call in result.get("raw_calls", [])]
    return nodes, edges, resolve_matlab_calls(nodes, edges, raw_calls, root=root)


def test_matlab_class_owns_methods_accessors_and_members(tmp_path):
    """REQ-MATLAB-001-AC02: Member kinds and locations follow the class AST."""
    result = _extract(tmp_path, "Motor.m", """classdef (Abstract) Motor < lib.Base & handle
    properties (SetAccess = private)
        Speed (1,1) double = 0
    end
    events
        Changed
    end
    enumeration
        Active(1)
    end
    methods
        function y = get.Speed(obj)
            y = obj.Speed;
        end
    end
    methods (Abstract)
        y = status(obj)
    end
end
""")
    motor = _node(result, "Motor")
    assert motor["metadata"]["matlab_superclasses"] == [
        {"name": "lib.Base", "source_location": "L1"},
        {"name": "handle", "source_location": "L1"},
    ]
    for name, kind, line in [
        ("Speed", "property", 3), ("Changed", "event", 6), ("Active", "enum_member", 9),
        ("get.Speed", "method", 12), ("status", "method", 17),
    ]:
        node = _node(result, name)
        assert node["metadata"]["kind"] == f"matlab_{kind}"
        assert node["source_location"] == f"L{line}"
        assert node["_origin"] == "ast"
        assert ("Motor", name, f"L{line}") in _relations(result["nodes"], result["edges"], "contains")
    assert _node(result, "status")["metadata"]["matlab_declaration_only"] is True


def test_matlab_nested_and_case_exact_names_keep_owner(tmp_path):
    """REQ-MATLAB-001-AC02: A nearer nested binding and exact spelling win."""
    result = _extract(tmp_path, "entry.m", """function out = entry()
out = work() + helper();
function y = helper()
y = 1;
end
end
function y = work()
y = helper() + Helper();
function z = helper()
z = 2;
end
function z = Helper()
z = 3;
end
end
""")
    nodes = result["nodes"]
    by_id = {node["id"]: node for node in nodes}
    assert len(by_id) == len(nodes)
    calls = {
        (by_id[e["source"]]["label"], by_id[e["target"]]["label"], by_id[e["target"]]["source_location"])
        for e in result["edges"] if e["relation"] == "calls"
    }
    assert calls == {
        ("entry", "work", "L7"), ("entry", "helper", "L3"),
        ("work", "helper", "L9"), ("work", "Helper", "L12"),
    }


def test_matlab_script_local_functions_and_optional_function_end(tmp_path):
    """REQ-MATLAB-001-AC02: Script statements and implicit function ends differ."""
    script = _extract(tmp_path, "report.m", """function y = twice(x)
y = 2 * x;
end
value = twice(2);
""")
    assert _node(script, "report.m")["metadata"]["matlab_file_kind"] == "script"
    assert _node(script, "twice")["metadata"]["matlab_primary"] is False
    assert ("report.m", "twice", "L4") in _relations(script["nodes"], script["edges"], "calls")
    function_file = _extract(tmp_path, "entry.m", """function y = entry(x)
y = plusone(x);
function y = plusone(x)
y = x + 1;
""")
    assert _node(function_file, "entry")["metadata"]["matlab_primary"] is True
    assert _node(function_file, "plusone")["metadata"]["matlab_primary"] is False
    assert ("entry", "plusone", "L2") in _relations(function_file["nodes"], function_file["edges"], "calls")


def test_matlab_comments_literals_and_indexing_do_not_bind(tmp_path):
    """REQ-MATLAB-001-AC02: Only unambiguous callable sites reach resolution."""
    result = _extract(tmp_path, "entry.m", """function y = entry(taken, obj)
% function fake()
text = "fake()";
y = actual(1) + obj.member(actual(2));
y = y + taken(1) + actual(:) + actual(end - 1) + actual{1};
y = y + obj.actual(1);
actual = [1, 2];
end
function y = actual(x)
y = x;
end
function y = taken(x)
y = x;
end
""")
    assert "fake" not in {node["label"] for node in result["nodes"]}
    assert not _relations(result["nodes"], result["edges"], "calls")
    assert result["raw_calls"] == []

    updated = _extract(tmp_path, "entry.m", """function y = entry(obj)
y = obj.member(actual(1)) + actual(:) + actual(end);
end
function y = actual(x)
y = x;
end
""")
    assert _relations(updated["nodes"], updated["edges"], "calls") == {("entry", "actual", "L2")}


def test_matlab_corpus_calls_respect_private_package_and_ambiguity(tmp_path):
    """REQ-MATLAB-002-AC01: Resolve eligible primaries without global label fallback."""
    entry = _extract(tmp_path, "caller/entry.m", """function y = entry(x)
y = secret(x) + pkg.convert(x) + external(x) + Convert(x);
end
""")
    private = _extract(tmp_path, "caller/private/secret.m", "function y = secret(x)\ny=x;\nend\n")
    public = _extract(tmp_path, "lib/secret.m", "function y = secret(x)\ny=x;\nend\n")
    package = _extract(tmp_path, "lib/+pkg/convert.m", "function y = convert(x)\ny=x;\nend\n")
    external = _extract(tmp_path, "lib/external.m", "function y = external(x)\ny=x;\nend\n")
    conflict = _extract(tmp_path, "other/external.m", "function y = external(x)\ny=x;\nend\n")
    nodes, edges, _ = _combine(tmp_path, entry, private, public, package, external, conflict)
    assert _relations(nodes, edges, "calls") == {("entry", "secret", "L2"), ("entry", "convert", "L2")}
    chosen = next(edge for edge in edges if edge["relation"] == "calls" and edge["target"] == _node(private, "secret")["id"])
    assert chosen["confidence"] == "INFERRED"
    assert chosen["target_file"] == _node(private, "secret")["source_file"]


def test_matlab_import_precedes_local_function_and_inheritance_is_source_bound(tmp_path):
    """REQ-MATLAB-002-AC01: Explicit imports bind and undeclared bases stay evidence."""
    caller = _extract(tmp_path, "entry.m", """function y = entry(x)
y = convert(x);
import lib.convert
end
function y = convert(x)
y = 2*x;
end
""")
    imported = _extract(tmp_path, "+lib/convert.m", "function y = convert(x)\ny=x;\nend\n")
    base = _extract(tmp_path, "+lib/Base.m", "classdef Base\nend\n")
    child = _extract(tmp_path, "Child.m", "classdef Child < lib.Base & absent.Other\nend\n")
    nodes, edges, _ = _combine(tmp_path, caller, imported, base, child)
    target = _node(imported, "convert")["id"]
    assert {(e["relation"], e["target"]) for e in edges if e["relation"] in {"imports", "calls"}} == {
        ("imports", target), ("calls", target),
    }
    assert _relations(nodes, edges, "inherits") == {("Child", "Base", "L1")}
    assert _node(child, "Child")["metadata"]["matlab_superclasses"][-1]["name"] == "absent.Other"


def test_matlab_model_api_witnesses_require_literal_and_unshadowed_name(tmp_path):
    """REQ-MATLAB-002-AC01: Never substitute dynamic handles or a local lookalike."""
    saved = _extract(tmp_path, "entry.m", """function entry(model)
load_system('Saved');
open_system model_2
sim(model);
open_system('Saved/SubSystem');
end
""")
    _, _, models = _combine(tmp_path, saved)
    assert [(m["matlab_api"], m["callee"], m["source_location"]) for m in models] == [
        ("load_system", "Saved", "L2"), ("open_system", "model_2", "L3"),
    ]
    override = _extract(tmp_path, "load_system.m", "function load_system(x)\nend\n")
    _, _, models = _combine(tmp_path, saved, override)
    assert [m["matlab_api"] for m in models] == ["open_system"]

    lexical = _extract(tmp_path, "entry.m", """function entry(load_system)
load_system('Saved');
open_system('Saved');
end
function open_system(x)
end
""")
    _, _, models = _combine(tmp_path, lexical)
    assert models == []


def test_matlab_source_identity_is_independent_of_root_and_lines(tmp_path):
    """REQ-MATLAB-002-AC02: Names survive relocation and unrelated line changes."""
    body = "function y = entry(x)\ny = x;\nend\n"
    a = _extract(tmp_path / "one", "+lib/entry.m", body)
    b = _extract(tmp_path / "two", "+lib/entry.m", "% line insertion\n" + body)
    assert _node(a, "entry")["id"] == _node(b, "entry")["id"]
    assert _node(b, "entry")["source_location"] == "L2"


@pytest.mark.parametrize(
    ("body", "code", "skipped"),
    [
        ("This is plain prose without MATLAB syntax.\n", "MATLAB_UNSUPPORTED", True),
        ("function [output = broken(\n", "MATLAB_SYNTAX", False),
    ],
)
def test_matlab_parse_failures_are_bounded(tmp_path, body, code, skipped):
    """REQ-MATLAB-001-AC01: Report malformed MATLAB and skip unsupported prose."""
    result = _extract(tmp_path, "input.m", body)
    assert result["nodes"] == result["edges"] == []
    assert result["diagnostics"][0]["code"] == code
    assert result.get("skipped", False) == skipped
    assert bool(result.get("matlab_failures")) is not skipped
    assert body not in str(result["diagnostics"])


def test_matlab_parser_missing_does_not_invent_declarations(tmp_path, monkeypatch):
    """REQ-MATLAB-001-AC01: Missing optional grammar reports a recoverable action."""
    import sys

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)
    result = _extract(tmp_path, "entry.m", "function entry()\nend\n", require_parser=False)
    assert result["nodes"] == result["edges"] == []
    assert result["diagnostics"][0]["code"] == "MATLAB_PARSER_MISSING"
    assert "not installed" in result["error"]
    assert "graphifyy[matlab]" in result["error"]


def test_matlab_excessive_source_size_has_no_partial_facts(tmp_path, monkeypatch):
    """REQ-MATLAB-001-AC01: Check the source bound before a grammar invocation."""
    monkeypatch.setattr(matlab_ast, "MAX_SOURCE_BYTES", 32)
    result = _extract(
        tmp_path, "input.m", "function input()\n" + "x = 1;\n" * 10 + "end\n",
        require_parser=False,
    )
    assert result["nodes"] == result["edges"] == []
    assert result["diagnostics"][0]["code"] == "MATLAB_LIMIT"


def test_matlab_file_anchors_keep_exact_path_identity(tmp_path):
    """REQ-MATLAB-002-AC02: Source identity survives slug and case collisions."""
    body = "function y = entry(x)\ny = x;\nend\n"
    first = _extract(tmp_path / "one", "dir-a/entry.m", body)
    second = _extract(tmp_path / "two", "dir_a/entry.m", body)
    case_only = _extract(tmp_path / "three", "Dir-A/entry.m", body)
    ids = [node["id"] for result in (first, second, case_only) for node in result["nodes"]]
    assert len(ids) == len(set(ids)) == 6
    assert all(nid.startswith("matlab_") for nid in ids)
