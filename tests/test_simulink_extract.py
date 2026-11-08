"""Saved Simulink archive structure, direction, and rejection contracts."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from graphify.extractors import simulink_archive
from graphify.extractors.simulink import extract_simulink

_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL_TYPE = "http://schemas.mathworks.com/simulink/2010/relationships/"


def _rels(*relations: tuple[str, str, str]) -> str:
    records = [
        f'<Relationship Id="{identity}" Target="{target}" Type="{_REL_TYPE}{kind}"/>'
        for identity, target, kind in relations
    ]
    return f'<Relationships xmlns="{_REL_NS}">{"".join(records)}</Relationships>'


def _archive(path: Path, entries: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as package:
        for member, data in entries.items():
            package.writestr(member, data)
    return path


def _modern(system: str, **diagram) -> dict[str, str | bytes]:
    return {
        "_rels/.rels": _rels(("blockDiagram", "simulink/blockDiagram.json", "blockDiagram")),
        "simulink/blockDiagram.json": json.dumps({"BlockDiagram": {"System": {"Ref": "system_root"}, **diagram}}),
        "simulink/_rels/blockDiagram.json.rels": _rels(("system_root", "systems/system_root.xml", "system")),
        "simulink/systems/system_root.xml": system,
    }


def _flat(system: str, *, kind="Model", declarations="") -> dict[str, str | bytes]:
    return {
        "_rels/.rels": _rels(("blockDiagram", "/simulink/blockdiagram.xml", "blockDiagram")),
        "simulink/blockdiagram.xml": f"<ModelInformation><{kind}>{declarations}{system}</{kind}></ModelInformation>",
    }


def _meta(record):
    return record["metadata"]["simulink"]


def _nodes(result, kind):
    return [record for record in result["nodes"] if _meta(record)["kind"] == kind]


def _edges(result, relation):
    return [record for record in result["edges"] if record["relation"] == relation]


def test_req_matlab001_ac03_ac04_split_signals_and_subsystem_mapping(tmp_path):
    """Nested branches and an ordinary subsystem expose saved direction and member locations."""
    entries = _modern("""<System>
<Block BlockType="Inport" Name="Source" SID="1">
  <PortProperties><Port Type="out" Index="1"><P Name="Name">demand</P></Port></PortProperties>
</Block>
<Block BlockType="SubSystem" Name="Inner/Control" SID="2"><System Ref="system_2"/></Block>
<Block BlockType="Outport" Name="Sink" SID="3"/>
<Block BlockType="SubSystem" Name="Trigger" SID="4"><PortCounts trigger="1"/></Block>
<Line><P Name="Name">demand &amp; feedback</P><P Name="Src">1#out:1</P>
  <P Name="Dst">2#in:1</P>
  <Branch><P Name="Dst">3#in:1</P>
    <Branch><P Name="Dst">4#trigger</P></Branch>
  </Branch>
</Line>
</System>""")
    entries["simulink/systems/_rels/system_root.xml.rels"] = _rels(("system_2", "system_2.xml", "system"))
    entries["simulink/systems/system_2.xml"] = """<System>
<Block BlockType="Inport" Name="Input" SID="2::1"/>
<Block BlockType="Outport" Name="Output" SID="2::2"><P Name="Port">2</P></Block>
<Line><P Name="Src">2::1#out:1</P><P Name="Dst">2::2#in:1</P></Line>
</System>"""
    result = extract_simulink(_archive(tmp_path / "models" / "Controller.slx", entries), root=tmp_path)
    assert result["diagnostics"] == []
    assert len(_nodes(result, "model")) == 1
    assert len(_nodes(result, "subsystem")) == 2
    ports = {n["id"]: _meta(n) for n in _nodes(result, "port")}
    assert len([n for n in ports.values() if n.get("port_name") == "demand"]) == 1
    assert len({n["id"] for n in result["nodes"]}) == len(result["nodes"])
    signals = _edges(result, "simulink_signal")
    assert [(ports[e["source"]]["sid"], ports[e["target"]]["sid"]) for e in signals] == [
        ("1", "2"), ("1", "3"), ("1", "4"), ("2::1", "2::2"),
    ]
    assert [e["source_location"] for e in signals] == ["L8", "L10", "L11", "L4"]
    assert [_meta(e)["member"] for e in signals] == ["simulink/systems/system_root.xml"] * 3 + [
        "simulink/systems/system_2.xml",
    ]
    assert all(e["source_file"] == "models/Controller.slx" for e in signals)
    assert all((e["_src"], e["_tgt"]) == (e["source"], e["target"]) for e in signals)
    assert _meta(signals[0])["signal_name"] == "demand & feedback"
    mappings = _edges(result, "simulink_port_mapping")
    assert [(ports[e["source"]]["sid"], ports[e["target"]]["sid"]) for e in mappings] == [
        ("2", "2::1"), ("2::2", "2"),
    ]
    assert ports[mappings[0]["source"]]["block_path"] == "Controller/Inner//Control"
    assert ports[mappings[1]["target"]]["port_index"] == 2


def test_req_matlab001_ac03_flat_library_references(tmp_path):
    """An inline XML library retains literal model, library, and S-function declarations."""
    system = """<System>
<Block BlockType="Reference" Name="Dependency" SID="1"><P Name="SourceBlock">Source/A&amp;B </P></Block>
<Block BlockType="ModelReference" Name="Controller" SID="2"><P Name="ModelNameDialog">ModelOne</P></Block>
<Block BlockType="M-S-Function" Name="Worker" SID="3"><P Name="FunctionName">controller_step</P></Block>
</System>"""
    result = extract_simulink(_archive(tmp_path / "Library.slx", _flat(system, kind="Library")))
    assert result["diagnostics"] == []
    assert _meta(_nodes(result, "library")[0])["model_name"] == "Library"
    assert [(_meta(n)["reference_kind"], _meta(n)["reference_target"]) for n in _nodes(result, "block")] == [
        ("library", "Source/A&B "), ("model", "ModelOne"), ("s_function", "controller_step"),
    ]


@pytest.mark.parametrize(
    ("source_kind", "filename", "code", "expected_file", "expected_entry"),
    [
        ("MATLAB Code", "stale.m", "% ordinary comment\nvalue = configure_model();\n", None, "configure_model"),
        ("MATLAB Code", None, "%{\nvalue = commented();\n%}\n", None, None),
        ("MATLAB Code", None, 'value = "configure_model()";', None, None),
        ("MATLAB Code", None, "value = pkg.configure_model();", None, None),
        ("MATLAB Code", None, "value = configure_model(arg);", None, None),
        ("MAT-File", "state.mat", "value = stale_code();", "state.mat", None),
        ("MATLAB File", "configure.m", "value = stale_code();", "configure.m", None),
        ("Model File", "stale.m", "value = stale_code();", None, None),
    ],
)
def test_req_matlab002_ac01_workspace_uses_selected_saved_source(
    tmp_path, source_kind, filename, code, expected_file, expected_entry,
):
    """The workspace declaration selects one literal mechanism without preserving executable text."""
    result = extract_simulink(_archive(tmp_path / "Controller.slx", _modern(
        "<System/>", DataDictionary="parameters.sldd",
        ModelWorkspace={"WSDataSource": source_kind, "WSSourceFileName": filename, "WSMATLABCode": code},
    )))
    fields = _meta(_nodes(result, "model")[0])
    assert fields.get("workspace_source_file") == expected_file
    assert fields.get("workspace_entry_call") == expected_entry
    assert fields["workspace_source_kind"] == source_kind
    assert fields["data_dictionary"] == "parameters.sldd"
    assert "WSMATLABCode" not in json.dumps(result)
    assert code not in json.dumps(result)


def test_req_matlab001_ac04_system_local_sid_and_no_unresolved_signal(tmp_path):
    """The same SID in two scopes has distinct identities and cannot fix a missing endpoint."""
    entries = _flat("""<System>
<Block BlockType="Inport" Name="Outer" SID="1"/>
<Block BlockType="SubSystem" Name="Inner" SID="2"><System>
  <Block BlockType="Outport" Name="Local" SID="1"/>
  <Line><P Name="Src">2#out:1</P><P Name="Dst">1#in:1</P></Line>
</System></Block>
<Line><P Name="Src">1#out:1</P><P Name="Dst">999#in:1</P></Line>
</System>""")
    result = extract_simulink(_archive(tmp_path / "Model.slx", entries))
    assert len({n["id"] for n in _nodes(result, "block")}) == 2
    assert _edges(result, "simulink_signal") == []
    assert {d["code"] for d in result["diagnostics"]} == {"SIMULINK_SIGNAL_SOURCE", "SIMULINK_SIGNAL_TARGET"}


def test_req_matlab002_ac02_ids_survive_move_rename_and_saved_line_edits(tmp_path):
    """Node IDs follow the relative source and saved SID rather than model location or display text."""
    source = tmp_path / "source"
    entries = _flat('<System><Block BlockType="Inport" Name="Input" SID="17"/></System>')
    first = _archive(source / "models" / "Model.slx", entries)
    copied_root = tmp_path / "elsewhere"
    shutil.copytree(source, copied_root)
    moved = extract_simulink(copied_root / "models" / "Model.slx", root=copied_root)
    original = extract_simulink(first, root=source)
    assert original == moved
    renamed = _flat('<System>\n<Block BlockType="Inport" Name="New Input" SID="17"/></System>')
    edited = extract_simulink(_archive(first, renamed), root=source)
    assert [n["id"] for n in original["nodes"]] == [n["id"] for n in edited["nodes"]]
    assert _nodes(edited, "block")[0]["source_location"] == "L2"
    assert _nodes(edited, "block")[0]["label"] == "New Input"
    rejected = extract_simulink(first, root=copied_root)
    assert rejected["error"] == "SIMULINK_ROOT"
    assert rejected["nodes"] == []


@pytest.mark.parametrize(
    ("member", "payload", "code"),
    [
        ("simulink/systems/system_root.xml", "<System><Block></System>", "SIMULINK_XML_INVALID"),
        ("simulink/systems/system_root.xml",
         '<!DOCTYPE System [<!ENTITY ext SYSTEM "file:///private.xml">]><System>&ext;</System>',
         "SIMULINK_XML_UNSAFE"),
        ("simulink/systems/system_root.xml",
         '<!DOCTYPE System [<!ENTITY ext "secret">]><System>&ext;</System>'.encode("utf-16"),
         "SIMULINK_XML_UNSAFE"),
        ("simulink/blockDiagram.json", '{"BlockDiagram": {}, "BlockDiagram": {}}', "SIMULINK_JSON_INVALID"),
        ("simulink/_rels/blockDiagram.json.rels",
         _rels(("system_root", "../outside.xml", "system")), "SIMULINK_PACKAGE_PATH"),
        ("simulink/_rels/blockDiagram.json.rels",
         _rels(("system_root", "systems/missing.xml", "system")), "SIMULINK_PACKAGE_MEMBER"),
        ("simulink/_rels/blockDiagram.json.rels",
         _rels(("system_root", "systems/system_root.xml", "system")).replace(
             'Target="systems/system_root.xml"', 'Target="https://example.test/" TargetMode="External"'),
         "SIMULINK_PACKAGE_PATH"),
        ("SIMULINK/BLOCKDIAGRAM.JSON", "{}", "SIMULINK_PACKAGE_PATH"),
    ],
)
def test_req_matlab002_ac03_archive_rejection_is_atomic(tmp_path, member, payload, code):
    """Corrupt, aliased, and external package records produce bounded failures and no facts."""
    entries = _modern('<System><Block BlockType="Inport" Name="Input" SID="1"/></System>')
    entries[member] = payload
    result = extract_simulink(_archive(tmp_path / "Model.slx", entries))
    assert result["error"] == code
    assert result["nodes"] == result["edges"] == []
    assert result["diagnostics"][0]["code"] == code
    assert result["diagnostics"][0]["severity"] == "error"


def test_req_matlab002_ac03_size_limit_and_cyclic_system_rejected(tmp_path, monkeypatch):
    """Archive budget and relationship cycles fail before publishing any saved fact."""
    entries = _modern('<System><Block BlockType="SubSystem" Name="Inner" SID="1"><System Ref="again"/></Block></System>')
    entries["simulink/systems/_rels/system_root.xml.rels"] = _rels(("again", "system_root.xml", "system"))
    path = _archive(tmp_path / "Model.slx", entries)
    cycle = extract_simulink(path)
    assert cycle["error"] == "SIMULINK_SYSTEM_CYCLE"
    assert cycle["nodes"] == cycle["edges"] == []
    monkeypatch.setattr(simulink_archive, "MAX_PART_BYTES", 32)
    too_big = extract_simulink(path)
    assert too_big["error"] == "SIMULINK_LIMIT"
    assert too_big["nodes"] == too_big["edges"] == []
