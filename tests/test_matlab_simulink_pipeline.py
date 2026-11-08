"""Run the normal CLI with saved MATLAB and Simulink source, without a runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

from graphify.paths import load_node_link_graph
from graphify.source_relationships import is_source_graph

FIXTURE = Path(__file__).parent / "fixtures" / "matlab_simulink_pipeline"
MODEL_REFS = "simulink_model_reference"
LIBRARY_LINKS = "simulink_library_link"
MATLAB_S_FUNCTIONS = "simulink_s_function"
MATLAB_WORKSPACE_SOURCES = "simulink_workspace_source"
MATLAB_MODEL_REFS = "matlab_model_reference"
SIGNALS = "simulink_signal"


def _relationships(*records):
    return (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="{identity}" Target="{target}" '
            f'Type="http://schemas.mathworks.com/simulink/2010/relationships/{kind}"/>'
            for identity, target, kind in records
        )
        + "</Relationships>"
    )


def _archive(path, fixture, *, library=False, workspace=False, child=False):
    """Package readable synthetic XML as a modern split SLX archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    header: dict[str, object] = {"System": {"Ref": "system_root"}}
    if library:
        header["LibraryType"] = "BlockLibrary"
    if workspace is True:
        header["ModelWorkspace"] = {
            "WSDataSource": "MATLAB Code",
            "WSMATLABCode": "value=configure_model();",
        }
    elif workspace:
        header["ModelWorkspace"] = {"WSDataSource": workspace, "WSSourceFileName": "configure_model.m"}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="json" ContentType="application/json"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Override PartName="/simulink/blockDiagram.json" '
            'ContentType="application/vnd.mathworks.simulink.mdl+json"/>'
            "</Types>",
        )
        archive.writestr("_rels/.rels", _relationships(
            ("blockDiagram", "simulink/blockDiagram.json", "blockDiagram"),
        ))
        archive.writestr("simulink/blockDiagram.json", json.dumps({"BlockDiagram": header}))
        archive.writestr("simulink/_rels/blockDiagram.json.rels", _relationships(
            ("system_root", "systems/system_root.xml", "system"),
        ))
        archive.write(FIXTURE / fixture, "simulink/systems/system_root.xml")
        if child:
            archive.writestr("simulink/systems/_rels/system_root.xml.rels", _relationships(
                ("system_50", "system_50.xml", "system"),
            ))
            archive.write(FIXTURE / "payload.xml", "simulink/systems/system_50.xml")


def _project(root, *, with_providers=True):
    _archive(root / "Driver.slx", "driver.xml", workspace=True)
    _archive(root / "Components.slx", "components.xml", library=True, child=True)
    for name in ("compute_step.m", "configure_model.m"):
        shutil.copyfile(FIXTURE / name, root / name)
    (root / "keep.py").write_text("def retained():\n    return 1\n", encoding="utf-8")
    if with_providers:
        _archive(root / "providers" / "Payload.slx", "payload.xml")


def _run(root, command, *options):
    # Suppress installed-skill refreshes, keeping the real console entry point,
    # scan, cache, assembly, export and update paths. Keep every output in tmp_path.
    env = dict(os.environ, GRAPHIFY_OUT="graphify-out", GRAPHIFY_NO_AUTO_REFRESH="1",
               GRAPHIFY_NO_TIPS="1", PYTHONHASHSEED="0")
    env.pop("GRAPHIFY_FORCE", None)
    flags = ("--code-only",) if command == "extract" else ()
    return subprocess.run(
        [sys.executable, "-m", "graphify", command, ".", *flags, *options],
        cwd=root, env=env, capture_output=True, encoding="utf-8", errors="replace", timeout=90,
    )


def _successful(root, command, *options):
    completed = _run(root, command, *options)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads((root / "graphify-out" / "graph.json").read_bytes())


def _links(data):
    return data["links"] if "links" in data else data["edges"]


def _simulink_node(data, source, **facts):
    matches = [
        node for node in data["nodes"]
        if node["source_file"] == source and all(
            node.get("metadata", {}).get("simulink", {}).get(key) == value
            for key, value in facts.items()
        )
    ]
    assert len(matches) == 1, (source, facts, matches)
    return matches[0]["id"]


def _matlab_function(data, name):
    matches = [
        node for node in data["nodes"]
        if node["source_file"] == f"{name}.m"
        and node.get("metadata", {}).get("matlab_name") == name
        and node.get("metadata", {}).get("matlab_primary") is True
    ]
    (node,) = matches
    assert node["source_location"] == "L1"
    return node["id"]


def _pairs(data, relation):
    # Serialized endpoints represent the producer's logical direction even in
    # the ordinary undirected clustered graph.
    return [(edge["source"], edge["target"]) for edge in _links(data) if edge["relation"] == relation]


def _assert_refs(data, *, model_path):
    driver = "Driver.slx"
    model_source = _simulink_node(data, driver, kind="block", sid="2")
    model_edges = _pairs(data, MODEL_REFS)
    assert model_edges == (
        [(model_source, _simulink_node(data, model_path, kind="model"))] if model_path else []
    )
    assert _pairs(data, MATLAB_MODEL_REFS) == (
        [(_matlab_function(data, "configure_model"), _simulink_node(data, model_path, kind="model"))]
        if model_path else []
    )
    assert _pairs(data, LIBRARY_LINKS) == [
        (_simulink_node(data, driver, kind="block", sid="3"),
         _simulink_node(data, "Components.slx", kind="subsystem", sid="50")),
    ]
    assert _pairs(data, MATLAB_S_FUNCTIONS) == [
        (_simulink_node(data, driver, kind="block", sid="4"), _matlab_function(data, "compute_step")),
    ]


def _assert_workspace_source(data):
    (workspace_source, workspace_target), = _pairs(data, MATLAB_WORKSPACE_SOURCES)
    assert workspace_source == _simulink_node(data, "Driver.slx", kind="model")
    assert workspace_target == _matlab_function(data, "configure_model")


def _assert_signals(data):
    source = _simulink_node(data, "Driver.slx", kind="port", sid="90", port_kind="out", port_index=1)
    targets = {
        _simulink_node(data, "Driver.slx", kind="port", sid=sid, port_kind="in", port_index=1)
        for sid in ("2", "3", "4")
    }
    expected = {(source, target) for target in targets}
    driver_signals = [edge for edge in _links(data)
                      if edge["source_file"] == "Driver.slx" and edge["relation"] == SIGNALS]
    assert len(driver_signals) == 3
    assert {(edge["source"], edge["target"]) for edge in driver_signals} == expected
    for edge in driver_signals:
        metadata = edge["metadata"]["simulink"]
        assert metadata["signal_name"] == "command"
        assert metadata["member"] == "simulink/systems/system_root.xml"
        line = int(edge["source_location"].removeprefix("L"))
        assert '<P Name="Dst">' in (FIXTURE / "driver.xml").read_text(encoding="utf-8").splitlines()[line - 1]
    loaded = load_node_link_graph(data)
    persisted = {
        (edge.get("_src", first), edge.get("_tgt", second))
        for first, second, edge in loaded.edges(data=True)
        if edge.get("source_file") == "Driver.slx" and edge.get("relation") == SIGNALS
    }
    assert persisted == expected


def _accepted_products(root):
    out = root / "graphify-out"
    return {
        name: (out / name).read_bytes()
        for name in ("graph.json", "manifest.json", "GRAPH_REPORT.md", "graph.html", ".graphify_root")
        if (out / name).exists()
    }


@pytest.mark.parametrize("source_profile", [False, True], ids=["legacy", "source-relationships"])
def test_req_matlab_002_ac01_ac02_cli_persists_exact_saved_direction_and_dependencies(tmp_path, source_profile):
    """Cold extraction and subsequent update publish the same saved references."""
    _project(tmp_path)
    if source_profile:
        # A real C# local-function fact selects the existing source graph profile.
        (tmp_path / "Keep.cs").write_text("class Holder{void Use(){void Local(){} Local();}}", encoding="utf-8")
    data = _successful(tmp_path, "extract")
    assert is_source_graph(load_node_link_graph(data)) is source_profile
    assert _simulink_node(data, "Components.slx", kind="library")
    _assert_refs(data, model_path="providers/Payload.slx")
    _assert_signals(data)
    updated = _successful(tmp_path, "update")
    assert is_source_graph(load_node_link_graph(updated)) is source_profile
    _assert_refs(updated, model_path="providers/Payload.slx")
    _assert_signals(updated)
    for relation in (MODEL_REFS, LIBRARY_LINKS, MATLAB_S_FUNCTIONS, MATLAB_MODEL_REFS, MATLAB_WORKSPACE_SOURCES, SIGNALS):
        assert sorted(_pairs(updated, relation)) == sorted(_pairs(data, relation))
    _assert_workspace_source(data)
    _assert_workspace_source(updated)


def test_req_matlab_002_ac02_incremental_target_add_ambiguity_and_remove(tmp_path):
    """Changing only candidates must refresh bindings from the unchanged driver."""
    _project(tmp_path, with_providers=False)
    driver = tmp_path / "Driver.slx"
    original = driver.read_bytes()
    initial = _successful(tmp_path, "extract", "--no-cluster")
    _assert_refs(initial, model_path=None)
    _assert_signals(initial)
    candidate = tmp_path / "providers" / "Payload.slx"
    _archive(candidate, "payload.xml")
    _assert_refs(_successful(tmp_path, "extract", "--no-cluster"), model_path="providers/Payload.slx")
    duplicate = tmp_path / "alternatives" / "Payload.slx"
    _archive(duplicate, "payload.xml")
    ambiguous = _successful(tmp_path, "update", "--no-cluster")
    _assert_refs(ambiguous, model_path=None)
    assert _simulink_node(ambiguous, "providers/Payload.slx", kind="model") != _simulink_node(
        ambiguous, "alternatives/Payload.slx", kind="model",
    )
    candidate.unlink()
    recovered = _successful(tmp_path, "extract", "--no-cluster")
    _assert_refs(recovered, model_path="alternatives/Payload.slx")
    assert not any(node["source_file"] == "providers/Payload.slx" for node in recovered["nodes"])
    duplicate.unlink()
    final = _successful(tmp_path, "update", "--no-cluster")
    _assert_refs(final, model_path=None)
    _assert_signals(final)
    assert driver.read_bytes() == original
    assert {n["id"] for n in initial["nodes"] if n["source_file"] == "keep.py"} == {
        n["id"] for n in final["nodes"] if n["source_file"] == "keep.py"
    }


@pytest.mark.parametrize("command", ["extract", "update"])
def test_req_matlab_002_ac03_corrupt_archive_retains_accepted_products_and_retries(tmp_path, command):
    """Replace a previously admitted source with real corrupt bytes, then repair it."""
    _project(tmp_path)
    _successful(tmp_path, "extract")
    original = _accepted_products(tmp_path)
    assert {"graph.json", "manifest.json"} <= original.keys()
    source = tmp_path / "Driver.slx"
    source.write_bytes(b"PK\x03\x04incomplete archive")
    options = ("--force", "--allow-partial") if command == "extract" else ("--force",)
    rejected = _run(tmp_path, command, *options)
    assert rejected.returncode == 1, rejected.stdout + rejected.stderr
    diagnostics = rejected.stdout + rejected.stderr
    assert "SIMULINK_INCOMPLETE" in diagnostics and "SIMULINK_PACKAGE_INVALID" in diagnostics
    assert _accepted_products(tmp_path) == original

    _archive(source, "driver.xml", workspace=True)
    repaired = _successful(tmp_path, command)
    _assert_refs(repaired, model_path="providers/Payload.slx")
    _assert_signals(repaired)


@pytest.mark.parametrize("source_kind", ["MATLAB File", "MAT-File"])
def test_workspace_file_link_requires_matlab_source_type(tmp_path, source_kind):
    """The saved source type decides whether a literal filename names MATLAB code."""
    _project(tmp_path)
    _archive(tmp_path / "Driver.slx", "driver.xml", workspace=source_kind)
    graph = _successful(tmp_path, "extract")
    links = set(_pairs(graph, MATLAB_WORKSPACE_SOURCES))
    if source_kind == "MATLAB File":
        assert links == {
            (_simulink_node(graph, "Driver.slx", kind="model"),
             next(node["id"] for node in graph["nodes"] if node["source_file"] == "configure_model.m"
                  and node.get("metadata", {}).get("kind") == "matlab_file"))
        }
    else:
        assert not links
