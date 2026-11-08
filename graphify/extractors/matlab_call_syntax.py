"""Extract call and workspace evidence from a single MATLAB AST scope."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import re
from typing import TYPE_CHECKING

from graphify.extractors.matlab_ast import (
    DECLARATIONS,
    IDENTIFIER,
    QUALIFIED,
    SIMULINK_APIS,
    command_name,
    syntax_text,
)

if TYPE_CHECKING:
    from tree_sitter import Node


_WORKSPACE_CHANGES = {"assignin", "clear", "eval", "evalc", "evalin", "load", "run"}
_MODEL_NAME = re.compile(r"[^\W\d_]\w*(?:\.slx)?")
_IMPORT_NAME = re.compile(r"[^\W\d_]\w*(?:\.[^\W\d_]\w*)+(?:\.\*)?|[^\W\d_]\w*\.\*")
_ARGUMENT_TRIVIA = {"comment", "line_continuation"}
_MATRIX_EXPRESSIONS = {
    "number", "identifier", "matrix", "field_expression", "function_call",
    "unary_operator", "binary_operator", "parenthesis",
}


@dataclass
class CallSite:
    name: str
    syntax: Node
    model_name: str = ""
    ss_feedthrough: str = ""


@dataclass
class Workspace:
    """Lexical evidence is collected before any site is matched to a function."""

    bindings: set[str] = field(default_factory=set)
    imports: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    import_sites: list[tuple[str, Node]] = field(default_factory=list)
    wildcard_import: bool = False
    uncertain: bool = False
    assigned_load: bool = False
    sites: list[CallSite] = field(default_factory=list)


def _binding_roots(node: Node | None) -> set[str]:
    if node is None:
        return set()
    if node.type == "identifier":
        return {syntax_text(node).partition(".")[0]}
    if node.type == "multioutput_variable":
        return set().union(*(_binding_roots(child) for child in node.named_children))
    if node.type == "function_call":
        return _binding_roots(node.child_by_field_name("name"))
    if node.type == "field_expression":
        return _binding_roots(node.child_by_field_name("object"))
    return set()


def _qualified_callee(node: Node, name: str) -> str:
    parent = node.parent
    if parent is None or parent.type != "field_expression":
        return name
    # Dot syntax can be an object member or namespace member. Carry its exact
    # spelling for later package matching and never fall back to the bare name.
    parts = []
    for child in parent.named_children:
        if child == node:
            return ".".join([*parts, name]) if parts else ""
        if child.type != "identifier" or not IDENTIFIER.fullmatch(syntax_text(child)):
            return ""
        parts.append(syntax_text(child))
    return ""


def _index_only(args: Node | None) -> bool:
    """The grammar represents MATLAB indexing and calls with one node type."""
    stack = list(args.named_children) if args is not None else []
    while stack:
        child = stack.pop()
        if child.type in {"end_keyword", "spread_operator"}:
            return True
        if child.type not in {"function_call", "lambda", "string"}:
            stack.extend(child.named_children)
    return False


def _literal_model(arg: Node | None) -> str:
    if arg is None or arg.type not in {"string", "command_argument"}:
        return ""
    value = syntax_text(arg)
    if len(value) > 258:
        return ""
    if len(value) >= 2 and value[0] in "'\"" and value[0] == value[-1]:
        value = value[1:-1]
    return value if _MODEL_NAME.fullmatch(value) else ""


def _positional_arguments(args: Node | None) -> list[Node]:
    """Named options are not positional fields in the pinned grammar."""
    if args is None or args.has_error:
        return []
    positional = args.children_by_field_name("argument")
    visible = [node for node in args.named_children if node.type not in _ARGUMENT_TRIVIA]
    return positional if visible == positional else []


def _expression_uses_only(node: Node, allowed: set[str]) -> bool:
    pending = [node]
    while pending:
        term = pending.pop()
        if term.type in _ARGUMENT_TRIVIA:
            continue
        if term.type not in allowed:
            return False
        pending.extend(term.named_children)
    return True


def _matrix_expression(node: Node) -> bool:
    # Direct brace indexing can expand into multiple arguments. Matrix
    # slices using parentheses remain an ordinary argument expression.
    while node.type == "parenthesis":
        enclosed = [child for child in node.named_children if child.type not in _ARGUMENT_TRIVIA]
        if len(enclosed) != 1:
            return False
        node = enclosed[0]
    if node.type not in _MATRIX_EXPRESSIONS:
        return False
    return not any(
        term.type == "function_call" and any(child.type == "{" for child in term.children)
        for term in (node, *node.named_children) if node.type in {"function_call", "field_expression"}
    )


def _ss_feedthrough(args: Node | None) -> str:
    """Distinguish the documented four-matrix form from text-option overloads."""
    positional = _positional_arguments(args)
    if len(positional) != 4 or not all(_matrix_expression(node) for node in positional):
        return ""
    feedthrough = positional[3]
    if _expression_uses_only(feedthrough, {"number", "matrix", "row", "unary_operator"}):
        return "numeric_literal"
    if feedthrough.type != "function_call" or syntax_text(feedthrough.child_by_field_name("name")) != "zeros":
        return ""
    sizes = _positional_arguments(next((n for n in feedthrough.named_children if n.type == "arguments"), None))
    allowed = {"number", "identifier", "unary_operator", "binary_operator", "parenthesis"}
    if 1 <= len(sizes) <= 2 and all(_expression_uses_only(size, allowed) for size in sizes):
        return "zeros"
    return ""


def _call_site(node: Node) -> CallSite | None:
    name = syntax_text(node.child_by_field_name("name"))
    if not IDENTIFIER.fullmatch(name) or not any(child.type == "(" for child in node.children):
        return None
    if any(child.type == "superclass" for child in node.children):
        return None
    parent = node.parent
    if parent is not None and parent.type == "function_call" and parent.child_by_field_name("name") == node:
        return None
    name = _qualified_callee(node, name)
    args = next((child for child in node.named_children if child.type == "arguments"), None)
    if not name or _index_only(args):
        return None
    arg = args.child_by_field_name("argument") if args is not None else None
    return CallSite(
        name, node, _literal_model(arg) if name in SIMULINK_APIS else "",
        _ss_feedthrough(args) if name == "ss" else "",
    )


def _record_imports(result: Workspace, args: list[Node]) -> None:
    for arg in args:
        value = syntax_text(arg)
        if len(value) > 256 or not _IMPORT_NAME.fullmatch(value):
            result.uncertain = True
            continue
        result.import_sites.append((value, arg))
        if value.endswith(".*"):
            result.wildcard_import = True
        else:
            result.imports[value.rsplit(".", 1)[-1]].add(value)


def _visit_command(node: Node, result: Workspace) -> None:
    name = command_name(node)
    args = [child for child in node.named_children if child.type == "command_argument"]
    if name == "import":
        _record_imports(result, args)
    elif name in _WORKSPACE_CHANGES:
        result.uncertain = True
    elif QUALIFIED.fullmatch(name):
        model = _literal_model(args[0]) if args and name in SIMULINK_APIS else ""
        result.sites.append(CallSite(name, node, model))


def _assigned_load(node: Node) -> bool:
    """MATLAB's single assigned load output binds only the assignment target."""
    assignment = node.parent
    if assignment is None or assignment.type != "assignment":
        return False
    left = assignment.child_by_field_name("left")
    return (left is not None and left.type == "identifier"
            and assignment.child_by_field_name("right") == node)


def inspect_workspace(syntax: Node) -> Workspace:
    """Respect parsed scopes and collect bindings, imports, and literal sites."""
    result = Workspace()
    stack = list(reversed(syntax.named_children))
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in DECLARATIONS or kind in {"lambda", "handle_operator"}:
            continue
        if kind in {"function_output", "function_arguments", "persistent_operator", "global_operator"}:
            for child in node.named_children:
                result.bindings.update(_binding_roots(child))
            continue
        if kind == "arguments_statement":
            for child in node.named_children:
                if child.type == "property":
                    result.bindings.update(_binding_roots(child.child_by_field_name("name")))
            continue
        if kind == "assignment":
            left = node.child_by_field_name("left")
            result.bindings.update(_binding_roots(left))
            stack.extend(child for child in reversed(node.named_children) if child != left)
            continue
        if kind in {"iterator", "catch_clause"}:
            first = next(iter(node.named_children), None)
            if first is not None and first.type == "identifier":
                result.bindings.add(syntax_text(first))
        if kind == "command":
            _visit_command(node, result)
            continue
        if kind == "function_call":
            site = _call_site(node)
            if site is not None:
                if site.name == "load" and _assigned_load(node):
                    result.assigned_load = True
                elif site.name in _WORKSPACE_CHANGES or site.name == "import":
                    result.uncertain = True
                elif site.name != "feval":
                    result.sites.append(site)
        stack.extend(reversed(node.named_children))
    return result
