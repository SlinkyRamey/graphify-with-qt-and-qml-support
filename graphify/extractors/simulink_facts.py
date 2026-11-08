"""Source-owned facts for saved Simulink systems and block declarations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any
from xml.etree import ElementTree as ET

from graphify.extractors.simulink_archive import (
    MAX_DEPTH,
    SimulinkArchive,
    SimulinkInputError,
    XmlPart,
    bounded_text,
    parameter,
)
from graphify.extractors.simulink_signals import declared_ports, port_mappings, signal_lines

_MAX_FACTS = 200_000
_MAX_DIAGNOSTICS = 50


def _reference(block: ET.Element, block_type: str) -> dict[str, str]:
    """Preserve declarations only. A separate corpus pass owns target binding."""
    if block_type == "ModelReference":
        kind, target = "model", parameter(block, "ModelNameDialog") or parameter(block, "ModelName")
    elif block_type == "Reference":
        kind, target = "library", parameter(block, "SourceBlock")
    elif block_type in {"S-Function", "M-S-Function"}:
        kind, target = "s_function", parameter(block, "FunctionName")
    elif block_type == "SubSystem":
        kind, target = "subsystem", parameter(block, "ReferencedSubsystem")
    else:
        return {}
    return {"reference_kind": kind, "reference_target": bounded_text(target)} if target.strip() else {}


@dataclass(frozen=True)
class Block:
    """A block identity and its owning system member's source location."""

    identity: str
    sid: str
    name: str
    path: str
    block_type: str
    part: XmlPart
    element: ET.Element


class SimulinkFacts:
    """One model owns its typed nodes and edges until all systems are read."""

    def __init__(self, archive: SimulinkArchive, relative: str, member: str, line: int,
                 kind: str, details: dict[str, Any]):
        self.archive = archive
        self.relative = relative
        self.nodes: list[dict[str, Any]] = []
        self.edges: list[dict[str, Any]] = []
        self.diagnostics: list[dict[str, Any]] = []
        self._ports: dict[tuple[str, str, int | None], str] = {}
        self._signals: set[tuple[str, str]] = set()
        self.stateflow_blocks: dict[str, list[Block]] = {}
        self.model_name = bounded_text(PurePosixPath(relative).stem)
        self.model = self.source_node(kind, (), self.model_name, member, line, node_type="file",
                                model_name=self.model_name, block_path=self.model_name, **details)

    def _identity(self, kind: str, key: tuple) -> str:
        record = json.dumps([self.relative, kind, key], ensure_ascii=True, separators=(",", ":"))
        return "simulink_" + hashlib.sha256(record.encode("ascii")).hexdigest()

    def _provenance(self, member: str, line: int, kind: str, fields: dict) -> dict:
        return {
            "source_file": self.relative, "source_location": f"L{line}",
            "metadata": {"simulink": {"kind": kind, "member": member, **fields}},
        }

    def _limit(self) -> None:
        if len(self.nodes) + len(self.edges) >= _MAX_FACTS:
            raise SimulinkInputError("SIMULINK_LIMIT", "Model exceeds the saved structure fact limit.")

    def source_node(self, kind: str, key: tuple, label: str, member: str, line: int,
              *, node_type: str = "concept", **fields) -> str:
        self._limit()
        identity = self._identity(kind, key)
        self.nodes.append({
            "id": identity, "label": label, "file_type": "code", "type": node_type, "_origin": "ast",
            **self._provenance(member, line, kind, fields),
        })
        return identity

    def edge(self, source: str, target: str, relation: str, part: XmlPart, element: ET.Element,
             kind: str, **fields) -> None:
        self._limit()
        self.edges.append({
            "source": source, "target": target, "_src": source, "_tgt": target,
            "relation": relation, "confidence": "EXTRACTED",
            **self._provenance(part.member, part.line(element), kind, fields),
        })

    def warning(self, code: str, message: str, part: XmlPart, element: ET.Element) -> None:
        if len(self.diagnostics) < _MAX_DIAGNOSTICS:
            self.diagnostics.append({
                "code": code, "message": message, "severity": "warning", "owner": "simulink",
                "source_file": self.relative, "source_location": f"L{part.line(element)}",
                "member": part.member, "recovery": "Check the saved block and port identities and save the model again.",
            })

    def port(self, block: Block, kind: str, index: int | None, part: XmlPart, element: ET.Element,
             *, name: str = "") -> str:
        # Declarations run before signal endpoints. An observed port fills an
        # absent declaration without changing the identity of a declared port.
        key = (block.identity, kind, index)
        if key not in self._ports:
            suffix = f"{kind}:{index}" if index is not None else kind
            fields: dict[str, Any] = {"port_index": index} if index is not None else {}
            if name:
                fields["port_name"] = bounded_text(name)
            identity = self.source_node(
                "port", key, f"{block.name} {suffix}", part.member, part.line(element),
                sid=block.sid, block_path=block.path, port_kind=kind, **fields,
            )
            self._ports[key] = identity
            self.edge(block.identity, identity, "contains", part, element, "port_owner")
        return self._ports[key]

    def signal(self, source: str, target: str, part: XmlPart, element: ET.Element, name: str) -> None:
        pair = (source, target)
        if pair in self._signals:
            # The legacy graph storage cannot represent duplicate logical
            # signal endpoints. Preserve the first physical line and diagnose.
            self.warning("SIMULINK_SIGNAL_DUPLICATE", "Duplicate saved signal endpoints were skipped.", part, element)
            return
        self._signals.add(pair)
        fields = {"signal_name": bounded_text(name)} if name else {}
        self.edge(source, target, "simulink_signal", part, element, "signal", **fields)

    def _block(self, parent: str, parent_path: str, part: XmlPart, element: ET.Element,
               seen: dict[str, Block]) -> Block:
        sid = element.get("SID", "")
        name = element.get("Name", "")
        block_type = element.get("BlockType", "")
        if not sid or not name or not block_type or "#" in sid or sid in seen:
            raise SimulinkInputError("SIMULINK_STRUCTURE", "A system has missing or conflicting saved block identities.")
        bounded_text(sid)
        bounded_text(block_type)
        path = bounded_text(f"{parent_path}/{name.replace('/', '//')}")
        kind = "subsystem" if block_type == "SubSystem" else "block"
        details = _reference(element, block_type)
        chart_type = parameter(element, "SFBlockType")
        if chart_type:
            details["stateflow_block_type"] = bounded_text(chart_type)
        identity = self.source_node(kind, (parent, sid), name, part.member, part.line(element),
                              sid=sid, block_path=path, block_type=block_type, **details)
        self.edge(parent, identity, "contains", part, element, "block_owner")
        block = Block(identity, sid, name, path, block_type, part, element)
        if block_type == "SubSystem" and chart_type in {"Chart", "MATLAB Function", "MATLABFunction"}:
            self.stateflow_blocks.setdefault(path, []).append(block)
        return block

    def _child_system(self, block: Block, element: ET.Element) -> tuple[XmlPart, ET.Element]:
        ref = element.get("Ref")
        if ref is None:
            return block.part, element
        if not ref or len(element):
            raise SimulinkInputError("SIMULINK_STRUCTURE", "A saved subsystem has an invalid system reference.")
        part = self.archive.system(block.part.member, ref)
        return part, part.root

    def _system(self, part: XmlPart, element: ET.Element, parent: str, path: str,
                members: frozenset[str], depth: int) -> dict[str, Block]:
        if depth > MAX_DEPTH:
            raise SimulinkInputError("SIMULINK_LIMIT", "Model system hierarchy exceeds the nesting limit.")
        blocks: dict[str, Block] = {}
        for child in element.findall("Block"):
            block = self._block(parent, path, part, child, blocks)
            blocks[block.sid] = block
            declared_ports(self, block)
        signal_lines(self, part, element, blocks)
        for block in blocks.values():
            systems = block.element.findall("System")
            if len(systems) > 1:
                raise SimulinkInputError("SIMULINK_STRUCTURE", "A block declares multiple saved child systems.")
            if systems:
                child_part, child_system = self._child_system(block, systems[0])
                external = child_part is not part or "Ref" in systems[0].attrib
                if external and child_part.member in members:
                    raise SimulinkInputError("SIMULINK_SYSTEM_CYCLE", "Saved system relationships contain a cycle.")
                children = self._system(child_part, child_system, block.identity, block.path,
                                        members | {child_part.member}, depth + 1)
                port_mappings(self, block, children.values())
        return blocks

    def add_system(self, part: XmlPart, system: ET.Element) -> None:
        self._system(part, system, self.model, self.model_name, frozenset({part.member}), 0)

    def result(self) -> dict:
        return {"nodes": self.nodes, "edges": self.edges, "diagnostics": self.diagnostics}
