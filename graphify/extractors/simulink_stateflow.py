"""Saved Stateflow chart ownership and explicit embedded function declarations."""

from __future__ import annotations

from collections import Counter, defaultdict
import re
from xml.etree import ElementTree as ET

from graphify.extractors.simulink_archive import SimulinkInputError, XmlPart, bounded_text, parameter
from graphify.extractors.simulink_facts import Block, SimulinkFacts

_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,127}\Z")


def _charts(facts: SimulinkFacts, part: XmlPart, machine: ET.Element) -> dict[str, tuple[XmlPart, ET.Element]]:
    charts: dict[str, tuple[XmlPart, ET.Element]] = {}
    for saved in machine.findall("./Children/chart"):
        if "Ref" in saved.attrib:
            if len(saved):
                raise SimulinkInputError("SIMULINK_STATEFLOW_STRUCTURE", "A Stateflow chart has conflicting saved bodies.")
            member = facts.archive.target(part.member, "stateflowChart", saved.attrib["Ref"])
            chart_part = facts.archive.xml(member)
            chart = chart_part.root
        else:
            chart_part, chart = part, saved
        identity = chart.get("id", "")
        if chart.tag != "chart" or "Ref" in chart.attrib or not identity or identity in charts:
            raise SimulinkInputError("SIMULINK_STATEFLOW_STRUCTURE", "Stateflow chart identities are missing or conflicting.")
        charts[bounded_text(identity)] = (chart_part, chart)
    return charts


def _functions(facts: SimulinkFacts, part: XmlPart, chart: ET.Element, chart_node: str,
               chart_id: str, block: Block) -> None:
    # A FUNC_STATE marked as embedded MATLAB is a Stateflow declaration.
    # Its script remains in the archive. This node is not a MATLAB file export.
    declarations = [state for state in chart.iter("state") if parameter(state, "type") == "FUNC_STATE"]
    counts = Counter(state.get("SSID") for state in declarations)
    for state in declarations:
        eml = state.findall("eml")
        if len(eml) != 1 or parameter(eml[0], "isEML") != "1":
            continue
        identity = state.get("SSID", "")
        name = parameter(state, "labelString")
        has_script = bool(parameter(eml[0], "script").strip())
        if not identity or counts[identity] != 1 or not _IDENTIFIER.fullmatch(name) or not has_script:
            facts.warning("SIMULINK_STATEFLOW_DECLARATION", "A saved Stateflow function has an ambiguous declaration.",
                          part, state)
            continue
        node = facts.source_node(
            "stateflow_emfunction", (chart_node, bounded_text(identity)), name, part.member, part.line(state),
            node_type="function", chart_id=chart_id, state_id=identity, block_path=block.path,
        )
        facts.edge(chart_node, node, "contains", part, state, "stateflow_function_owner")


def _owner(facts: SimulinkFacts, name: str) -> Block | None:
    if not name:
        return None
    candidates = [*facts.stateflow_blocks.get(name, ()),
                  *facts.stateflow_blocks.get(f"{facts.model_name}/{name}", ())]
    return candidates[0] if len(candidates) == 1 else None


def add_saved_stateflow(facts: SimulinkFacts, diagram: str) -> None:
    """Attach a chart only when its saved machine, instance and block agree."""
    member = facts.archive.optional_target(diagram, "stateflowMachine")
    if member is None:
        for candidates in facts.stateflow_blocks.values():
            block = candidates[0]
            facts.warning("SIMULINK_STATEFLOW_OWNER", "A saved Stateflow block has no admitted machine relationship.",
                          block.part, block.element)
        return
    part = facts.archive.xml(member)
    machines = part.root.findall("machine") if part.root.tag == "Stateflow" else []
    if len(machines) != 1 or not machines[0].get("id"):
        raise SimulinkInputError("SIMULINK_STATEFLOW_STRUCTURE", "The archive has no unique saved Stateflow machine.")
    machine = machines[0]
    machine_id = bounded_text(machine.attrib["id"])
    charts = _charts(facts, part, machine)
    owners: dict[str, list[tuple[Block, str, ET.Element]]] = defaultdict(list)
    for instance in part.root.findall("instance"):
        chart_id = parameter(instance, "chart")
        name = parameter(instance, "name")
        owner = _owner(facts, name)
        if (parameter(instance, "machine") != machine_id or chart_id not in charts or owner is None
                or parameter(charts[chart_id][1], "name") != name):
            facts.warning("SIMULINK_STATEFLOW_OWNER", "A saved Stateflow instance has no unique matching block owner.",
                          part, instance)
            continue
        owners[owner.identity].append((owner, chart_id, instance))
    linked: set[str] = set()
    for candidates in owners.values():
        owner, chart_id, instance = candidates[0]
        if len(candidates) != 1:
            facts.warning("SIMULINK_STATEFLOW_OWNER", "A saved block has multiple Stateflow instance claims.",
                          part, instance)
            continue
        chart_part, chart = charts[chart_id]
        node = facts.source_node(
            "stateflow_chart", (owner.identity, machine_id, chart_id), owner.name,
            chart_part.member, chart_part.line(chart),
            chart_id=chart_id, machine_id=machine_id, block_path=owner.path,
        )
        facts.edge(owner.identity, node, "contains", part, instance, "stateflow_chart_owner")
        _functions(facts, chart_part, chart, node, chart_id, owner)
        linked.add(chart_id)
    for chart_id, (chart_part, chart) in charts.items():
        if chart_id not in linked:
            facts.warning("SIMULINK_STATEFLOW_OWNER", "A saved Stateflow chart has no admitted owner.",
                          chart_part, chart)
