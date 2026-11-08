"""MATLAB source declarations, lexical scopes, and offline extraction facts."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from graphify.extractors.matlab_ast import (
    DECLARATIONS,
    QUALIFIED,
    MatlabSourceError,
    matlab_file_kind,
    parse_matlab,
    read_matlab_source,
    syntax_text,
)
from graphify.ids import decl_salt, make_id

if TYPE_CHECKING:
    from tree_sitter import Node


@dataclass
class MatlabScope:
    """The parsed file, class, or callable owns its immediate declarations."""

    syntax: Node
    record: dict
    scope_key: str
    parent: MatlabScope | None = None
    functions: dict[str, list[MatlabScope]] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        return self.record["metadata"]["kind"]

    @property
    def name(self) -> str:
        return self.record["metadata"]["matlab_name"]


def _source_owner(path: Path, root: Path | None) -> Path:
    if root is None:
        return path
    try:
        path.resolve().relative_to(root.resolve())
        owner = path.absolute().relative_to(root.absolute())
        if ".." in owner.parts:
            raise ValueError("non-canonical source owner")
        return owner
    except (OSError, RuntimeError, ValueError) as exc:
        raise MatlabSourceError("MATLAB_ROOT", "MATLAB source is outside the admitted scan root") from exc


class MatlabFacts:
    """One input supplies all raw declarations and syntax-owned evidence."""

    def __init__(self, path: Path, owner: Path, syntax: Node, file_kind: str):
        self.path = path
        self.owner = owner
        self.file_kind = file_kind
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.raw_calls: list[dict] = []
        self._occurrences: Counter[tuple[str, str, str]] = Counter()
        self._owner_key = sha256(owner.as_posix().encode("utf-8")).hexdigest()[:16]
        self.file = MatlabScope(
            syntax,
            self._node(
                make_id("matlab", self._owner_key, "file", path.stem[:64]), path.name,
                "file", syntax, matlab_file_kind=file_kind,
            ),
            "file",
        )
        self.scopes = [self.file]
        self.class_member_names: set[str] = set()

    def _node(self, nid: str, name: str, kind: str, syntax: Node, **metadata) -> dict:
        if len(name) > 256:
            raise MatlabSourceError("MATLAB_LIMIT", "MATLAB declaration name exceeds the graph limit")
        node = {
            "id": nid, "label": name, "file_type": "code", "source_file": str(self.path),
            "_origin": "ast",
            "source_location": "L1" if kind == "file" else f"L{syntax.start_point.row + 1}",
            "metadata": {
                "language": "matlab", "kind": f"matlab_{kind}", "matlab_name": name,
                "matlab_column": syntax.start_point.column + 1, **metadata,
            },
        }
        self.nodes.append(node)
        return node

    def add_symbol(self, owner: MatlabScope, name: str, kind: str, syntax: Node, **metadata) -> tuple[dict, str]:
        key = (owner.scope_key, kind, name)
        occurrence = self._occurrences[key]
        self._occurrences[key] += 1
        # A fixed-width exact-spelling salt preserves distinct declarations after
        # common case folding and avoids growing the ID at every nesting level.
        scope_key = decl_salt(repr((*key, occurrence)))
        nid = make_id("matlab", self._owner_key, kind, name[:64], scope_key)
        record = self._node(nid, name, kind, syntax, **metadata)
        self.add_edge(owner.record["id"], nid, "contains", syntax)
        return record, scope_key

    def add_edge(self, source: str, target: str, relation: str, syntax: Node) -> None:
        self.edges.append({
            "source": source, "target": target, "relation": relation, "confidence": "EXTRACTED",
            "source_file": str(self.path), "source_location": f"L{syntax.start_point.row + 1}",
            "weight": 1.0,
        })

    def add_call(self, owner: MatlabScope, callee: str, syntax: Node, **metadata) -> None:
        self.raw_calls.append({
            "caller_nid": owner.record["id"], "callee": callee, "source_file": str(self.path),
            "source_location": f"L{syntax.start_point.row + 1}",
            "matlab_column": syntax.start_point.column + 1, "_matlab_call": True, **metadata,
        })

    def _class_members(self, syntax: Node, owner: MatlabScope) -> None:
        for section in syntax.named_children:
            if section.type == "methods":
                self.collect(section, owner)
                continue
            rule = {
                "properties": ("property", "property"),
                "events": ("identifier", "event"),
                "enumeration": ("enum", "enum_member"),
            }.get(section.type)
            if rule is None:
                continue
            node_type, kind = rule
            for child in section.named_children:
                if child.type != node_type:
                    continue
                name_node = child
                if kind == "property":
                    name_node = child.child_by_field_name("name")
                elif kind == "enum_member":
                    name_node = next((n for n in child.named_children if n.type == "identifier"), None)
                name = syntax_text(name_node)
                if QUALIFIED.fullmatch(name):
                    self.add_symbol(owner, name, kind, child)

    def collect(self, syntax: Node, owner: MatlabScope) -> None:
        for child in syntax.named_children:
            if child.type == "block" and syntax.type in {"source_file", "function_definition"}:
                self.collect(child, owner)
            if child.type not in DECLARATIONS:
                continue
            name = syntax_text(child.child_by_field_name("name"))
            if not QUALIFIED.fullmatch(name):
                continue
            kind = "class" if child.type == "class_definition" else (
                "method" if owner.kind == "matlab_class" else "function"
            )
            # The grammar stores accessor prefixes as anonymous tokens beside
            # the name field. Keep get.Speed and set.Speed distinct from Speed.
            accessor = next((n.type[:-1] for n in child.children if n.type in {"get.", "set."}), "")
            if accessor and kind == "method":
                name = f"{accessor}.{name}"
            primary = (
                owner is self.file and self.file_kind == kind
                and "matlab_primary_name" not in self.file.record["metadata"]
            )
            metadata: dict = {"matlab_primary": primary}
            if primary:
                self.file.record["metadata"]["matlab_primary_name"] = name
            if child.type == "function_signature":
                metadata["matlab_declaration_only"] = True
            if kind == "class":
                metadata["matlab_superclasses"] = _superclasses(child)
            record, key = self.add_symbol(owner, name, kind, child, **metadata)
            scope = MatlabScope(child, record, key, owner)
            self.scopes.append(scope)
            if kind == "function":
                owner.functions.setdefault(name, []).append(scope)
            elif kind == "method":
                self.class_member_names.add(name)
            if kind == "class":
                self._class_members(child, scope)
            else:
                self.collect(child, scope)


def _superclasses(syntax: Node) -> list[dict[str, str]]:
    names = []
    for group in syntax.named_children:
        if group.type != "superclasses":
            continue
        for child in group.named_children:
            name = syntax_text(child).strip()
            if child.type == "property_name" and QUALIFIED.fullmatch(name):
                if len(name) > 256 or len(names) == 50:
                    raise MatlabSourceError("MATLAB_LIMIT", "MATLAB inheritance exceeds the graph limit")
                names.append({"name": name, "source_location": f"L{child.start_point.row + 1}"})
    return names


def _failure(path: Path, owner: Path, exc: MatlabSourceError) -> dict:
    unsupported = exc.code == "MATLAB_UNSUPPORTED"
    diagnostic = {
        "code": exc.code, "severity": "info" if unsupported else "error",
        "source_file": owner.name if owner.is_absolute() else owner.as_posix(),
        "owner": "matlab", "message": str(exc),
        "recovery": "Use supported MATLAB source and install graphifyy[matlab] when needed.",
    }
    if exc.line is not None:
        diagnostic["source_location"] = f"L{exc.line}"
    result: dict = {"nodes": [], "edges": [], "diagnostics": [diagnostic]}
    if unsupported:
        result["skipped"] = True
    else:
        result["error"] = str(exc)
        result["matlab_failures"] = [{"code": exc.code, "source_file": str(path)}]
    return result


def extract_matlab(path: Path, *, root: Path | None = None) -> dict:
    """Extract saved MATLAB structure with optional grammar and explicit root."""
    from graphify.extractors.matlab_calls import collect_matlab_calls

    owner = Path(path.name)
    try:
        owner = _source_owner(path, root)
        syntax = parse_matlab(read_matlab_source(path))
        facts = MatlabFacts(path, owner, syntax, matlab_file_kind(syntax))
        facts.collect(syntax, facts.file)
        collect_matlab_calls(facts)
        return {"nodes": facts.nodes, "edges": facts.edges, "raw_calls": facts.raw_calls}
    except MatlabSourceError as exc:
        return _failure(path, owner, exc)
