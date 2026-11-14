"""Public incremental rebuilds retain unchanged C++ alias/base/include facts."""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import graphify.extract as extract_mod
from graphify.watch import _rebuild_code

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("tree_sitter_cpp") is None,
    reason="tree_sitter_cpp not installed",
)


def _corpus(tmp_path, caller_name="use.cpp"):
    (tmp_path / "worker.hpp").write_text("struct Worker { void Work(); };", encoding="utf-8")
    (tmp_path / "types.hpp").write_text(
        '#include "worker.hpp"\nstruct Base { using Alias = Worker; Alias actions; }; struct Derived : Base {};',
        encoding="utf-8")
    caller = tmp_path / caller_name
    caller.write_text('#include "types.hpp"\nvoid Run(Derived& x) { x.actions.Work(); }', encoding="utf-8")
    return caller


def _assert_call(tmp_path):
    graph = json.loads((tmp_path / "graphify-out" / "graph.json").read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert any(nodes[e["source"]]["label"] == "Run()"
               and nodes[e["target"]]["label"] == ".Work()"
               and e["confidence"] == "INFERRED"
               for e in graph.get("links", graph.get("edges", [])) if e["relation"] == "calls")


@pytest.mark.parametrize("caller_name, expected_batch", [
    ("use.cuh", ["use.cuh"]),
    ("use.cpp", ["types.hpp", "use.cpp", "worker.hpp"]),
])
def test_watcher_rebuild_retains_unchanged_cpp_type_facts(tmp_path, monkeypatch, caller_name, expected_batch):
    """An unrelated dialect change reuses context; potential Qt providers refresh the cohort."""
    caller = _corpus(tmp_path, caller_name)
    assert _rebuild_code(tmp_path, no_cluster=True, acquire_lock=False)
    _assert_call(tmp_path)
    real = extract_mod.extract
    batches = []

    def record(paths, **kwargs):
        batches.append(sorted(Path(path).name for path in paths))
        return real(paths, **kwargs)

    monkeypatch.setattr(extract_mod, "extract", record)
    caller.write_text(caller.read_text(encoding="utf-8") + "\n// changed caller\n", encoding="utf-8")
    assert _rebuild_code(tmp_path, incremental=True, no_cluster=True, acquire_lock=False)
    assert batches == [expected_batch]
    _assert_call(tmp_path)


def test_cli_extract_retains_unchanged_cpp_type_facts(tmp_path):
    caller = _corpus(tmp_path)
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))

    def run():
        result = subprocess.run([sys.executable, "-m", "graphify", "extract", ".", "--code-only"],
                                cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stdout + result.stderr

    run()
    _assert_call(tmp_path)
    caller.write_text(caller.read_text(encoding="utf-8") + "\n// changed caller\n", encoding="utf-8")
    run()
    _assert_call(tmp_path)
