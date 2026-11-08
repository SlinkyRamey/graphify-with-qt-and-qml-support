"""Validate saved Stateflow ownership without parsing or evaluating chart scripts."""

from __future__ import annotations

from pathlib import Path
from zipfile import ZipFile

import pytest

from graphify.extractors.simulink import extract_simulink

_OPC = "http://schemas.openxmlformats.org/package/2006/relationships"
_DIAGRAM_REL = "http://schemas.mathworks.com/simulink/2010/relationships/blockDiagram"
_MACHINE_REL = "http://schemas.mathworks.com/simulinkModel/2014/relationships/stateflowMachine"
_CHART_REL = "http://schemas.mathworks.com/simulinkModel/2018/relationships/stateflowMachine"


def _rel(kind, target, identity):
    return (f'<Relationships xmlns="{_OPC}">'
            f'<Relationship Type="{kind}" Target="{target}" Id="{identity}"/></Relationships>')


def _save_chart_model(path: Path, *, instance_path="Chart", relation=True, chart_body=True):
    """Use real OPC machine/chart relationships with a synthetic algorithm body."""
    entries = {
        "_rels/.rels": _rel(_DIAGRAM_REL, "simulink/blockdiagram.xml", "blockDiagram"),
        "simulink/blockdiagram.xml": """<ModelInformation><Model><System>
<Block BlockType="SubSystem" Name="Chart" SID="12"><P Name="SFBlockType">Chart</P></Block>
</System></Model></ModelInformation>""",
        "simulink/stateflow/machine.xml": f"""<Stateflow>
<machine id="7"><Children><chart Ref="chart_9"/></Children></machine>
<instance id="8"><P Name="machine">7</P><P Name="chart">9</P><P Name="name">{instance_path}</P></instance>
</Stateflow>""",
        "simulink/stateflow/_rels/machine.xml.rels": _rel(_CHART_REL, "chart_9.xml", "chart_9"),
    }
    if relation:
        entries["simulink/_rels/blockdiagram.xml.rels"] = _rel(
            _MACHINE_REL, "stateflow/machine.xml", "stateflow_machine",
        )
    if chart_body:
        entries["simulink/stateflow/chart_9.xml"] = """<chart id="9">
<P Name="name">Chart</P>
<Children>
<state SSID="24"><P Name="type">FUNC_STATE</P><P Name="labelString">calculate</P>
<eml><P Name="isEML">1</P><P Name="script">function y=calculate()
y=private_chart_source();
end</P></eml></state>
<state SSID="25"><P Name="type">OR_STATE</P><P Name="labelString">irrelevant</P></state>
</Children></chart>"""
    with ZipFile(path, "w") as package:
        for member, data in entries.items():
            package.writestr(member, data)
    return path


def _meta(record):
    return record["metadata"]["simulink"]


def test_req_matlab001_ac03_saved_chart_and_embedded_function_owners(tmp_path):
    """The block owns its chart and the chart owns its source-located declaration."""
    result = extract_simulink(_save_chart_model(tmp_path / "Model.slx"))
    assert not result["diagnostics"]
    by_kind = {_meta(n)["kind"]: n for n in result["nodes"]}
    block = by_kind["subsystem"]
    chart = by_kind["stateflow_chart"]
    function = by_kind["stateflow_emfunction"]
    assert chart["source_location"] == "L1"
    assert _meta(chart)["block_path"] == _meta(block)["block_path"] == "Model/Chart"
    assert _meta(chart)["member"] == "simulink/stateflow/chart_9.xml"
    assert (function["label"], function["source_location"], _meta(function)["state_id"]) == ("calculate", "L4", "24")
    ownership = {_meta(e)["kind"]: e for e in result["edges"]}
    owner = ownership["stateflow_chart_owner"]
    assert (owner["source"], owner["target"], owner["source_location"]) == (block["id"], chart["id"], "L3")
    assert _meta(owner)["member"] == "simulink/stateflow/machine.xml"
    declaration = ownership["stateflow_function_owner"]
    assert (declaration["source"], declaration["target"]) == (chart["id"], function["id"])
    assert "private_chart_source" not in repr(result)


@pytest.mark.parametrize(
    ("kwargs", "error", "diagnostic"),
    [
        ({"instance_path": "WrongBlock"}, None, "SIMULINK_STATEFLOW_OWNER"),
        ({"relation": False}, None, "SIMULINK_STATEFLOW_OWNER"),
        ({"chart_body": False}, "SIMULINK_PACKAGE_MEMBER", "SIMULINK_PACKAGE_MEMBER"),
    ],
)
def test_req_matlab002_ac03_stateflow_requires_present_unambiguous_ownership(tmp_path, kwargs, error, diagnostic):
    """Missing links or conflicting owners produce no chart or function nodes."""
    result = extract_simulink(_save_chart_model(tmp_path / "Model.slx", **kwargs))
    assert result.get("error") == error
    assert result["diagnostics"][0]["code"] == diagnostic
    assert not any(_meta(n)["kind"].startswith("stateflow_") for n in result["nodes"])
    if error:
        assert result["nodes"] == result["edges"] == []
