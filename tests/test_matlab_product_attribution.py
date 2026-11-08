"""Exercise documented product evidence through extraction and graph publication."""

from __future__ import annotations

from base64 import b64decode
import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZipFile

import pytest

from graphify.extract import extract
from graphify.paths import load_node_link_graph
from graphify.source_relationships import is_source_graph
from graphify.watch import _rebuild_code
from tests.test_matlab_simulink_pipeline import _accepted_products, _successful


_RELATION = "uses_matlab_product"
_DSP_PATH = "dspsolvers/Cholesky Solver"
_LINEBREAK_PATH = "dspsolvers/Forward\nSubstitution"
_SHARED_SCENE_PATH = "sim3dlib/Simulation 3D Scene Configuration"
_API_PRODUCTS = {
    "matlab.lang.makeValidName": "MATLAB",
    "Simulink.SimulationInput": "Simulink",
    "parpool": "Parallel Computing Toolbox",
    "lqr": "Control System Toolbox",
    "linio": "Simulink Control Design",
    "quadprog": "Optimization Toolbox",
    "sym": "Symbolic Math Toolbox",
    "sfroot": "Stateflow",
    "rigidBodyTree": "Robotics System Toolbox",
    "sim3d.Actor": "Simulink 3D Animation",
    "mpc": "Model Predictive Control Toolbox",
}


def _source(root: Path, name: str, text: str) -> Path:
    pytest.importorskip("tree_sitter_language_pack")
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _rel(target: str, kind: str) -> str:
    return ('<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Target="{target}" Id="root" Type="{kind}"/></Relationships>')


def _model(path: Path, blocks: str, *, library=False, chart_type="") -> Path:
    """Package only authored fixture data, with optional genuine chart ownership."""
    root = "Library" if library else "Model"
    if chart_type:
        blocks += (f'<Block BlockType="SubSystem" Name="Chart" SID="10">'
                   f'<P Name="SFBlockType">{chart_type}</P></Block>\n')
    with ZipFile(path, "w") as package:
        package.writestr("_rels/.rels", _rel(
            "simulink/blockdiagram.xml",
            "http://schemas.mathworks.com/simulink/2010/relationships/blockDiagram",
        ))
        package.writestr("simulink/blockdiagram.xml", f"""<ModelInformation><{root}><System>
{blocks}</System></{root}></ModelInformation>""")
        if chart_type:
            package.writestr("simulink/_rels/blockdiagram.xml.rels", _rel(
                "stateflow.xml", "http://schemas.mathworks.com/simulinkModel/2014/relationships/stateflowMachine",
            ))
            package.writestr("simulink/stateflow.xml", """<Stateflow>
<machine id="7"><Children><chart id="9"><P Name="name">Chart</P></chart></Children></machine>
<instance id="8"><P Name="machine">7</P><P Name="chart">9</P><P Name="name">Chart</P></instance>
</Stateflow>""")
    return path


def _ref(sid: int, target: str) -> str:
    return (f'<Block BlockType="Reference" Name="Ref{sid}" SID="{sid}">'
            f'<P Name="SourceBlock">{escape(target)}</P></Block>\n')


def _extract(root: Path, *paths: Path) -> dict:
    return extract(list(paths), root=root, cache_root=root, parallel=False)


def _uses(data: dict, evidence: str | None = None) -> list[tuple[dict, dict, dict]]:
    """Read exact owners, sites and relation records from either public shape."""
    nodes = {n["id"]: n for n in data["nodes"]}
    sites = {identity: n["metadata"]["matlab_product"] for identity, n in nodes.items()
             if "matlab_product" in n.get("metadata", {})}
    edges = [e for e in data.get("links", data.get("edges", [])) if e["relation"] == _RELATION]
    assert len(edges) == len(sites)
    assert {e["target"] for e in edges} == sites.keys()
    uses = []
    for edge in edges:
        site = sites[edge["target"]]
        owner, target = nodes[edge["source"]], nodes[edge["target"]]
        assert owner["source_file"] == target["source_file"] == edge["source_file"]
        assert target["source_location"] == edge["source_location"]
        assert site["name"] in target["label"]
        assert site["release"] == "R2026b"
        if site["evidence"] != "source_format":
            assert edge["confidence"] == "INFERRED"
        if evidence is None or site["evidence"] == evidence:
            uses.append((owner, target, site))
    return uses


def _symbols(data: dict, evidence: str) -> set[str]:
    return {site["symbol"] for _, _, site in _uses(data, evidence)}


def _provenance(data: dict) -> dict:
    # Cluster labels and presentation ordering are outside the evidence contract.
    return {witness["id"]: (owner["id"], witness["source_file"], witness["source_location"], site)
            for owner, witness, site in _uses(data)}


def test_req_matlab003_ac01_product_matrix_includes_all_selected_families(tmp_path):
    """A documented API or saved library path supplies a witness for every family."""
    program = _source(tmp_path, "entry.m", "function entry()\n" + "".join(
        f"    {api}();\n" for api in _API_PRODUCTS
    ) + "end\n")
    diagram = _model(tmp_path / "Demo.slx", _ref(1, _DSP_PATH))
    data = _extract(tmp_path, program, diagram)
    assert not data.get("diagnostics")
    observed = {site["symbol"]: site["name"] for _, _, site in _uses(data, "api")}
    assert observed == _API_PRODUCTS
    for owner, witness, site in _uses(data, "api"):
        assert owner["label"] == "entry"
        assert witness["source_location"] == f"L{list(_API_PRODUCTS).index(site['symbol']) + 2}"
        assert site["column"] >= 5
        assert (site.get("ownership") == "shared") is (site["symbol"] == "sim3d.Actor")
    (owner, witness, site), = _uses(data, "library_block")
    assert (owner["label"], witness["source_location"], site["symbol"], site["name"]) == (
        "Ref1", "L2", _DSP_PATH, "DSP System Toolbox",
    )
    assert {(site["name"], site["symbol"]) for _, _, site in _uses(data, "source_format")} == {
        ("MATLAB", ".m"), ("Simulink", ".slx"),
    }


@pytest.mark.parametrize("chart_type", ["Chart", "MATLAB Function"])
def test_req_matlab003_ac02_stateflow_product_requires_a_saved_stateflow_chart(tmp_path, chart_type):
    """An ordinary MATLAB Function may have a chart record without the product."""
    path = _model(tmp_path / "Demo.slx", "", chart_type=chart_type)
    data = _extract(tmp_path, path)
    assert not data.get("diagnostics")
    assert any(n.get("metadata", {}).get("simulink", {}).get("kind") == "stateflow_chart"
               for n in data["nodes"])
    assert [(site["name"], site["symbol"]) for _, _, site in _uses(data, "stateflow_chart")] == (
        [("Stateflow", "Chart")] if chart_type == "Chart" else []
    )


def test_req_matlab003_ac02_simulink_reference_matching_is_exact_and_offline(tmp_path, monkeypatch):
    """Saved paths remain literal even when their prefixes resemble vendor roots."""
    import sys

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", None)
    path = _model(tmp_path / "Demo.slx", "".join((
        _ref(1, _DSP_PATH), _ref(2, f"{_DSP_PATH} custom"),
        _ref(3, "$bdroot/Cholesky Solver"), _ref(4, "other/Cholesky Solver"),
        _ref(5, _SHARED_SCENE_PATH),
    )))
    by_symbol = {site["symbol"]: site for _, _, site in _uses(_extract(tmp_path, path), "library_block")}
    assert by_symbol.keys() == {_DSP_PATH, _SHARED_SCENE_PATH}
    assert by_symbol[_SHARED_SCENE_PATH]["name"] == "Simulink 3D Animation"
    assert by_symbol[_SHARED_SCENE_PATH]["ownership"] == "shared"
    assert by_symbol[_DSP_PATH].get("ownership") != "shared"


def test_req_matlab003_ac02_lexical_guards_and_corpus_names_win(tmp_path):
    """Inert text, object dispatch, local shadows, and vendor spelling lookalikes stay unassigned."""
    caller = _source(tmp_path, "entry.m", """function entry(sim3d, rigidBodyTree)
    % lqr();
    displayText = "quadprog()";
    rigidBodyTree(1);
    sim3d.Actor();
    parpool();
    lqr();
    Quadprog();
    missing.vendorApi();
    unknown(1);
end
function parpool()
end
""")
    local = _source(tmp_path, "lqr.m", "function lqr()\nend\n")
    wildcard = _source(tmp_path, "ambiguous.m", """function ambiguous()
import custom.*
sym('x');
sim3d.Actor();
end
""")
    data = _extract(tmp_path, caller, local, wildcard)
    assert not data.get("diagnostics")
    assert _uses(data, "api") == []
    assert _uses(data, "import") == []
    by_id = {n["id"]: n for n in data["nodes"]}
    assert any(e["relation"] == "calls" and by_id[e["target"]]["source_file"] == "lqr.m"
               for e in data["edges"])


@pytest.mark.parametrize("source_profile", [False, True], ids=["legacy", "source-relationships"])
def test_req_matlab003_ac03_cli_cold_warm_and_reload_keep_distinct_source_sites(tmp_path, source_profile):
    """Two same-line calls and an explicit import survive both graph formats."""
    _source(tmp_path, "entry.m", """function entry()
    import sim3d.Actor
    quadprog(1); quadprog(2);
end
""")
    _model(tmp_path / "Demo.slx", _ref(1, _LINEBREAK_PATH))
    if source_profile:
        # A real C# local function selects the existing typed source graph profile.
        (tmp_path / "Keep.cs").write_text("class Holder{void Use(){void Local(){} Local();}}", encoding="utf-8")
    cold = _successful(tmp_path, "extract")
    warm = _successful(tmp_path, "update")
    assert is_source_graph(load_node_link_graph(cold)) is source_profile
    loaded = load_node_link_graph(warm)
    assert is_source_graph(loaded) is source_profile
    for data in (cold, warm):
        (_, _, imported), = _uses(data, "import")
        assert (imported["symbol"], imported["ownership"]) == ("sim3d.Actor", "shared")
        (_, _, block), = _uses(data, "library_block")
        assert block["member"] == "simulink/blockdiagram.xml"
        assert b64decode(block["symbol_base64"], validate=True).decode("utf-8") == _LINEBREAK_PATH
        calls = [(witness["id"], site["column"]) for _, witness, site in _uses(data, "api")]
        assert len(calls) == 2 and len({identity for identity, _ in calls}) == 2
        assert {column for _, column in calls} == {5, 18}
    assert _provenance(cold) == _provenance(warm)
    for _, witness, site in _uses(warm):
        assert loaded.nodes[witness["id"]]["metadata"]["matlab_product"] == site


def test_req_matlab003_ac03_watcher_retracts_and_restores_evidence_for_project_shadows(tmp_path):
    """New library and source providers invalidate unchanged consumers and cache hits."""
    _source(tmp_path, "entry.m", "function entry()\nquadprog(1);\nend\n")
    _model(tmp_path / "Demo.slx", _ref(1, _DSP_PATH))

    def rebuild(changed=None):
        assert _rebuild_code(tmp_path, changed_paths=changed, no_cluster=True, acquire_lock=False)
        return json.loads((tmp_path / "graphify-out" / "graph.json").read_bytes())

    before = rebuild()
    assert _symbols(before, "api") == {"quadprog"}
    assert _symbols(before, "library_block") == {_DSP_PATH}
    local_source = _source(tmp_path, "quadprog.m", "value = 1;\n")
    local_library = _model(tmp_path / "dspsolvers.slx", '<Block BlockType="Gain" Name="Cholesky Solver" SID="1"/>\n',
                           library=True)
    shadowed = rebuild([local_source, local_library])
    assert _uses(shadowed, "api") == _uses(shadowed, "library_block") == []
    assert any(e["relation"] == "simulink_library_link" for e in shadowed["links"])
    local_source.unlink()
    local_library.unlink()
    recovered = rebuild([local_source, local_library])
    assert _provenance(recovered) == _provenance(before)


def test_req_matlab003_ac03_corrupt_model_retains_attribution_and_accepts_repair(tmp_path, capsys):
    """A real malformed replacement keeps accepted products byte for byte."""
    diagram = _model(tmp_path / "Demo.slx", _ref(1, _DSP_PATH))
    assert _rebuild_code(tmp_path, no_cluster=True, acquire_lock=False)
    previous = _accepted_products(tmp_path)
    assert {"graph.json", "manifest.json"} <= previous.keys()
    assert _symbols(json.loads(previous["graph.json"]), "library_block") == {_DSP_PATH}
    diagram.write_bytes(b"PK\x03\x04corrupt")
    assert not _rebuild_code(tmp_path, changed_paths=[diagram], no_cluster=True, acquire_lock=False, force=True)
    assert "SIMULINK_INCOMPLETE" in capsys.readouterr().out
    assert _accepted_products(tmp_path) == previous
    _model(diagram, _ref(1, "unknown/Cholesky Solver"))
    assert _rebuild_code(tmp_path, changed_paths=[diagram], no_cluster=True, acquire_lock=False)
    assert not _uses(json.loads(_accepted_products(tmp_path)["graph.json"]), "library_block")
