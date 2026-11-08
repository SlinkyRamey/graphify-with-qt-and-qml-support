"""Read bounded MATLAB syntax with the optional, offline Tree-sitter grammar."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from graphify.extractors.base import _SOURCE_FALLBACK_ENCODINGS, _UTF16_BOMS, _UTF32_BOMS

if TYPE_CHECKING:
    from tree_sitter import Node


IDENTIFIER = re.compile(r"[^\W\d_]\w*")
QUALIFIED = re.compile(r"[^\W\d_]\w*(?:\.[^\W\d_]\w*)*")
DECLARATIONS = {"function_definition", "function_signature", "class_definition"}
SIMULINK_APIS = {"load_system", "open_system", "sim"}
MAX_SOURCE_BYTES = 5_000_000

# The grammar also accepts unconstrained command words and standalone identifiers.
# Require a source construct with recognizable MATLAB syntax before publishing facts.
_SCRIPT_SYNTAX = {
    "assignment", "function_call", "field_expression", "if_statement", "for_statement",
    "while_statement", "switch_statement", "try_statement", "spmd_statement",
    "global_operator", "persistent_operator", "binary_operator", "unary_operator",
    "matrix", "cell",
}
_COMMAND_EVIDENCE = SIMULINK_APIS | {"clear", "clc", "close", "import"}


class MatlabSourceError(ValueError):
    """A bounded, source-owned diagnostic for an unsupported or failed read."""

    def __init__(self, code: str, message: str, line: int | None = None):
        super().__init__(message)
        self.code = code
        self.line = line


def syntax_text(node: Node | None) -> str:
    data = node.text if node is not None else None
    return data.decode("utf-8") if data else ""


def read_matlab_source(path: Path) -> bytes:
    try:
        with path.open("rb") as stream:
            source = stream.read(MAX_SOURCE_BYTES + 1)
    except OSError as exc:
        raise MatlabSourceError("MATLAB_READ", "Cannot read MATLAB source") from exc
    if len(source) > MAX_SOURCE_BYTES:
        raise MatlabSourceError("MATLAB_LIMIT", "MATLAB source exceeds 5 MB")
    # Keep the common source-encoding order but bound the actual read as well
    # as the grammar input, even when the file grows during analysis.
    encodings = ["utf-8-sig"]
    if source.startswith(_UTF32_BOMS):
        encodings.append("utf-32")
    if source.startswith(_UTF16_BOMS):
        encodings.append("utf-16")
    encodings.extend(_SOURCE_FALLBACK_ENCODINGS)
    for encoding in encodings:
        try:
            text = source.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\0" in text:
            break
        return text.encode("utf-8")
    raise MatlabSourceError("MATLAB_UNSUPPORTED", "Input is not recognized as text MATLAB source")


def parse_matlab(source: bytes) -> Node:
    """Parse source bytes without executing a script, callback, or model."""
    if len(source) > MAX_SOURCE_BYTES:
        raise MatlabSourceError("MATLAB_LIMIT", "MATLAB source exceeds 5 MB")
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError as exc:
        raise MatlabSourceError(
            "MATLAB_PARSER_MISSING", "MATLAB parser not installed; install graphifyy[matlab]"
        ) from exc
    try:
        # The grammar recognizes continuation before LF only. Normalizing
        # CRLF keeps line and in-line byte columns aligned with the source.
        root = get_parser("matlab").parse(source.replace(b"\r\n", b"\n")).root_node
    except (ImportError, LookupError, OSError, RuntimeError, ValueError) as exc:
        raise MatlabSourceError(
            "MATLAB_PARSER_LOAD", "MATLAB parser failed to load; reinstall graphifyy[matlab]"
        ) from exc
    # Check before later traversals and never expose snippets of the rejected source.
    stack = [(root, 0)]
    count = 0
    while stack:
        node, depth = stack.pop()
        count += 1
        if depth > 256 or count > 500_000:
            raise MatlabSourceError("MATLAB_LIMIT", "MATLAB syntax exceeds the size or depth limit")
        stack.extend((child, depth + 1) for child in node.children)
    return root


def command_name(node: Node) -> str:
    if node.type != "command" or not node.named_children:
        return ""
    name = node.named_children[0]
    return syntax_text(name) if name.type == "command_name" else ""


def matlab_file_kind(root: Node) -> str:
    """Classify clean syntax, or report the first bounded parse error."""
    statements = list(root.named_children)
    while any(node.type == "block" for node in statements):
        statements = [
            child for node in statements
            for child in (node.named_children if node.type == "block" else [node])
        ]
    meaningful = [
        node for node in statements if node.type not in {"comment", "line_continuation"}
    ]
    positive = any(
        node.type in DECLARATIONS | _SCRIPT_SYNTAX
        or command_name(node) in _COMMAND_EVIDENCE
        or (node.type == "ERROR" and re.match(rb"\s*(?:function|classdef)\b", node.text or b""))
        for node in meaningful
    )
    if not positive:
        raise MatlabSourceError("MATLAB_UNSUPPORTED", "Input has no recognized MATLAB declarations or script syntax")
    if root.has_error:
        stack = [root]
        while stack:
            node = stack.pop()
            if node.type == "ERROR" or node.is_missing:
                raise MatlabSourceError("MATLAB_SYNTAX", "MATLAB syntax is incomplete or unsupported", node.start_point.row + 1)
            stack.extend(child for child in reversed(node.children) if child.has_error or child.is_missing)
        raise MatlabSourceError("MATLAB_SYNTAX", "MATLAB syntax is incomplete or unsupported")

    first = meaningful[0]
    if first.type == "class_definition":
        return "class"
    if all(node.type == "function_definition" for node in meaningful):
        return "function"
    return "script"
