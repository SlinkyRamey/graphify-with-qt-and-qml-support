"""Saved port declarations and directed signal endpoints within one model system."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree as ET

from graphify.extractors.simulink_archive import SimulinkInputError, XmlPart, parameter

if TYPE_CHECKING:
    from graphify.extractors.simulink_facts import Block, SimulinkFacts

_INPUT = frozenset({"in", "enable", "trigger", "reset", "ifaction"})
_OUTPUT = frozenset({"out", "state"})
_NUMBERED = frozenset({"in", "out", "lconn", "rconn"})
_PORT_KINDS = _INPUT | _OUTPUT | _NUMBERED
_MAX_PORTS = 2048
_ENDPOINT = re.compile(r"([^#]+)#([a-z]+)(?::([0-9]{1,4}))?\Z")


def _number(value: str) -> int | None:
    if re.fullmatch(r"[0-9]{1,4}", value):
        number = int(value)
        if 0 <= number <= _MAX_PORTS:
            return number
    return None


def _index(kind: str, number: int) -> int | None:
    # Simulink saves trigger/enable/state endpoint forms without a suffix.
    return number if kind in _NUMBERED or number != 1 else None


def declared_ports(facts: SimulinkFacts, block: Block) -> None:
    """Prefer explicit PortProperties, then admit counted and standard interface ports."""
    declared: set[tuple[str, int | None]] = set()
    for element in block.element.findall("./PortProperties/Port"):
        kind = element.get("Type", "")
        if kind not in _PORT_KINDS:
            continue
        number = _number(element.get("Index", "1"))
        if not number:
            raise SimulinkInputError("SIMULINK_STRUCTURE", "A model port has an invalid declared index.")
        index = _index(kind, number)
        key = (kind, index)
        if key in declared:
            raise SimulinkInputError("SIMULINK_STRUCTURE", "A model block declares a duplicate port.")
        declared.add(key)
        facts.port(block, kind, index, block.part, element, name=parameter(element, "Name"))
    counts = block.element.findall("PortCounts")
    if len(counts) > 1:
        raise SimulinkInputError("SIMULINK_STRUCTURE", "A model block declares conflicting port counts.")
    if counts:
        for kind, raw in counts[0].attrib.items():
            if kind not in _PORT_KINDS:
                continue
            count = _number(raw)
            if count is None:
                raise SimulinkInputError("SIMULINK_LIMIT", "A model block has an unsupported port count.")
            for number in range(1, count + 1):
                facts.port(block, kind, _index(kind, number), block.part, counts[0])
    if block.block_type in {"Inport", "Outport"}:
        kind = "out" if block.block_type == "Inport" else "in"
        facts.port(block, kind, 1, block.part, block.element)


def _endpoint(text: str, blocks: dict[str, Block],
              direction: frozenset[str]) -> tuple[Block, str, int | None] | None:
    match = _ENDPOINT.fullmatch(text.strip())
    if not match:
        return None
    sid, kind, value = match.groups()
    number = _number(value) if value is not None else None
    if kind not in direction or sid not in blocks:
        return None
    if value is not None and not number or value is None and kind in _NUMBERED:
        return None
    return blocks[sid], kind, _index(kind, number) if number else None


def _destinations(facts: SimulinkFacts, part: XmlPart, segment: ET.Element,
                  signal_name: str) -> Iterator[tuple[ET.Element, str, str]]:
    name = parameter(segment, "Name") or signal_name
    destination = parameter(segment, "Dst")
    if destination:
        yield segment, destination, name
    for branch in segment.findall("Branch"):
        if parameter(branch, "Src"):
            facts.warning("SIMULINK_SIGNAL_BRANCH", "A branch with an independent source was skipped.", part, branch)
            continue
        yield from _destinations(facts, part, branch, name)


def signal_lines(facts: SimulinkFacts, part: XmlPart, system: ET.Element, blocks: dict[str, Block]) -> None:
    """Join saved directional endpoints in the local SID scope, including fan-out."""
    for line in system.findall("Line"):
        source = _endpoint(parameter(line, "Src"), blocks, _OUTPUT)
        if source is None:
            facts.warning("SIMULINK_SIGNAL_SOURCE", "Saved signal source is absent or unsupported.", part, line)
            continue
        has_destination = False
        for segment, raw, name in _destinations(facts, part, line, ""):
            has_destination = True
            destination = _endpoint(raw, blocks, _INPUT)
            if destination is None:
                facts.warning("SIMULINK_SIGNAL_TARGET", "Saved signal target is absent or unsupported.", part, segment)
                continue
            source_port = facts.port(*source, part, line)
            target_port = facts.port(*destination, part, segment)
            facts.signal(source_port, target_port, part, segment, name)
        if not has_destination:
            facts.warning("SIMULINK_SIGNAL_TARGET", "Saved signal has no supported destination.", part, line)


def port_mappings(facts: SimulinkFacts, parent: Block, blocks: Iterable[Block]) -> None:
    """Connect a subsystem boundary only to unique ordinary saved interface blocks."""
    if parent.block_type != "SubSystem":
        return
    mappings: dict[tuple[str, int], list[Block]] = defaultdict(list)
    for block in blocks:
        if block.block_type not in {"Inport", "Outport"}:
            continue
        kind = "in" if block.block_type == "Inport" else "out"
        # Inport/Outport's documented default Port value is 1. There is no
        # usable ordering proof for two saved blocks claiming the same port.
        index = _number(parameter(block.element, "Port") or "1")
        if not index:
            facts.warning("SIMULINK_PORT_MAPPING", "Subsystem port number is unsupported.", block.part, block.element)
            continue
        mappings[kind, index].append(block)
    for (kind, index), candidates in mappings.items():
        block = candidates[0]
        bus = parameter(block.element, "IsBusElementPort").lower() == "on"
        if len(candidates) != 1 or bus:
            facts.warning("SIMULINK_PORT_MAPPING", "Ambiguous or bus-element subsystem mapping was skipped.",
                          block.part, block.element)
            continue
        outer = facts.port(parent, kind, index, parent.part, parent.element)
        inner_kind = "out" if kind == "in" else "in"
        inner = facts.port(block, inner_kind, 1, block.part, block.element)
        source, target = (outer, inner) if kind == "in" else (inner, outer)
        proof = block.element.find("./P[@Name='Port']")
        facts.edge(source, target, "simulink_port_mapping", block.part,
                   proof if proof is not None else block.element, "subsystem_port_mapping")
