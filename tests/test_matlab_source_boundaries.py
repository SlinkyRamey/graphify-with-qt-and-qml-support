"""Verify source trust and optional MATLAB parser boundaries."""

from pathlib import Path

import pytest

from graphify.extractors.matlab import extract_matlab
from graphify.extractors.matlab_resolution import resolve_matlab_calls


def _source(root: Path, name: str, body: str, *, encoding: str = "utf-8") -> dict:
    pytest.importorskip("tree_sitter_language_pack")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding=encoding)
    return extract_matlab(path, root=root)


def _resolve(root: Path, caller: dict, *targets: dict) -> list[dict]:
    nodes = [*caller["nodes"], *(node for result in targets for node in result["nodes"])]
    edges: list[dict] = []
    resolve_matlab_calls(nodes, edges, caller["raw_calls"], root=root)
    return edges


def test_matlab_imported_receiver_does_not_become_namespace(tmp_path):
    """An explicit import claims the first name in a qualified expression."""
    caller = _source(tmp_path, "entry.m", """function y = entry(x)
import tools.pkg
y = pkg.convert(x);
end
""")
    imported = _source(tmp_path, "+tools/pkg.m", "classdef pkg\nend\n")
    package = _source(tmp_path, "+pkg/convert.m", "function y = convert(x)\ny=x;\nend\n")
    edges = _resolve(tmp_path, caller, imported, package)
    assert len(edges) == 1
    assert edges[0]["relation"] == "imports"
    assert edges[0]["target_file"] == str(tmp_path / "+tools/pkg.m")


def test_matlab_source_lookup_ignores_layout_outside_scan_root(tmp_path):
    """An outer private folder does not restrict members of the admitted root."""
    root = tmp_path / "private" / "repo"
    caller = _source(root, "entry.m", "function y = entry(x)\ny=convert(x);\nend\n")
    target = _source(root, "lib/convert.m", "function y = convert(x)\ny=x;\nend\n")
    edges = _resolve(root, caller, target)
    assert len(edges) == 1
    assert edges[0]["target_file"] == str(root / "lib/convert.m")
    target["nodes"][-1]["_origin"] = "semantic"
    assert _resolve(root, caller, target) == []


def test_matlab_corpus_call_must_share_source_owner(tmp_path):
    """A stale or corrupted call witness cannot borrow another source's ID."""
    caller = _source(tmp_path, "entry.m", "function y = entry(x)\ny=convert(x);\nend\n")
    target = _source(tmp_path, "lib/convert.m", "function y = convert(x)\ny=x;\nend\n")
    caller["raw_calls"][0]["source_file"] = str(tmp_path / "impostor.m")
    assert _resolve(tmp_path, caller, target) == []


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "cp1252"])
def test_matlab_bounded_text_decoding_preserves_lines(tmp_path, encoding):
    """Unicode and legacy Windows text keep their original source coordinates."""
    result = _source(tmp_path, "entry.m", "% résumé\nfunction entry()\nend\n", encoding=encoding)
    assert [(node["label"], node["source_location"]) for node in result["nodes"]] == [
        ("entry.m", "L1"), ("entry", "L2"),
    ]
