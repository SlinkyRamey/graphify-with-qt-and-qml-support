"""Resolve MATLAB witnesses against admitted source declarations only."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from graphify.extractors.matlab_ast import IDENTIFIER, QUALIFIED
from graphify.matlab_product_catalog import API_PRODUCTS
from graphify.matlab_products import append_product


def _source_path(value: object, root: Path | None) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = (root if root is not None else Path.cwd()) / path
    if ".." in path.parts:
        return None
    if root is not None and not path.is_relative_to(root):
        return None
    return path


def _namespace(directory: Path) -> tuple[str, ...]:
    parts = []
    for part in reversed(directory.parts):
        if not part.startswith("+") or not IDENTIFIER.fullmatch(part[1:]):
            break
        parts.append(part[1:])
    return tuple(reversed(parts))


def _lookup_key(path: Path, kind: str, name: str, root: Path | None) -> tuple[Path | None, str] | None:
    directory = path.parent.relative_to(root) if root is not None else path.parent
    if directory.name.startswith("@"):
        # Class-folder constructors have their own primary class declaration.
        # Separate class-folder method files are not ordinary public functions.
        if kind != "matlab_class" or directory.name != f"@{name}":
            return None
        directory = directory.parent
    if directory.name == "private":
        if "private" in directory.parent.parts:
            return None
        parent = root / directory.parent if root is not None else directory.parent
        return parent, name
    if any(part == "private" or part.startswith("@") for part in directory.parts):
        return None
    namespace = _namespace(directory)
    if any(part.startswith("+") for part in directory.parts[: len(directory.parts) - len(namespace)]):
        # Only consecutive, properly named namespace directories define a
        # package. An ordinary folder inside a package does not inherit it.
        return None
    return None, ".".join((*namespace, name))


class _MatlabCorpus:
    """An invocation-local index of declarations with exact saved names."""

    def __init__(self, nodes: list[dict], root: Path | None):
        self.root = root.absolute() if root is not None else None
        self.nodes: dict[str, dict] = {}
        self.targets: dict[tuple[Path | None, str], list[dict]] = defaultdict(list)
        self.files: set[tuple[Path | None, str]] = set()
        for node in nodes:
            meta = node.get("metadata")
            if node.get("_origin") != "ast" or not isinstance(meta, dict) or meta.get("language") != "matlab":
                continue
            nid = node.get("id")
            path = _source_path(node.get("source_file"), self.root)
            if not isinstance(nid, str) or path is None or path.suffix.lower() != ".m":
                continue
            self.nodes[nid] = node
            kind = meta.get("kind")
            if kind == "matlab_file" and IDENTIFIER.fullmatch(path.stem):
                # A script or a file with a different primary declaration
                # can still occupy the name used by a vendor function.
                file_key = _lookup_key(path, "matlab_class", path.stem, self.root)
                if file_key is not None:
                    self.files.add((file_key[0], file_key[1].casefold()))
            if kind not in {"matlab_class", "matlab_function"} or meta.get("matlab_primary") is not True:
                continue
            name = meta.get("matlab_name")
            if not isinstance(name, str) or name != path.stem or not IDENTIFIER.fullmatch(name):
                continue
            key = _lookup_key(path, kind, name, self.root)
            if key is not None:
                self.targets[key].append(node)

    def candidates(self, caller: dict, name: str) -> list[dict]:
        path = _source_path(caller.get("source_file"), self.root)
        if path is None:
            return []
        if "." not in name:
            private = self.targets.get((path.parent, name))
            if private:
                return private
        return self.targets.get((None, name), [])

    def shadows_product(self, caller: dict, name: str) -> bool:
        path = _source_path(caller.get("source_file"), self.root)
        if path is None:
            return True
        # Match exact qualified candidates and their class/namespace prefixes.
        # A case-only file collision is ambiguous on case-insensitive hosts.
        parts = name.casefold().split(".")
        return (path.parent, parts[0]) in self.files or any(
            (None, ".".join(parts[:length])) in self.files for length in range(1, len(parts) + 1)
        )


def _edge(caller: dict, target: dict, relation: str, location: str, context: str) -> dict:
    return {
        "source": caller["id"], "target": target["id"], "relation": relation,
        "confidence": "INFERRED", "weight": 1.0, "source_file": caller["source_file"],
        "target_file": target["source_file"], "source_location": location,
        "context": context,
    }


def resolve_matlab_calls(
    nodes: list[dict], edges: list[dict], raw_calls: list[dict], *,
    root: Path | None = None, product_nodes: list[dict] | None = None,
) -> list[dict]:
    """Add eligible MATLAB edges and return unshadowed Simulink API witnesses."""
    corpus = _MatlabCorpus(nodes, root)
    model_references = []
    seen = {
        (edge.get("source"), edge.get("target"), edge.get("relation"), edge.get("source_location"))
        for edge in edges
    }

    def append(edge: dict) -> None:
        key = (edge["source"], edge["target"], edge["relation"], edge["source_location"])
        if key not in seen:
            seen.add(key)
            edges.append(edge)

    for raw in raw_calls:
        if raw.get("_matlab_call") is not True:
            continue
        caller_id = raw.get("caller_nid")
        caller = corpus.nodes.get(caller_id) if isinstance(caller_id, str) else None
        name = raw.get("callee")
        location = raw.get("source_location")
        if caller is None or not isinstance(name, str) or not isinstance(location, str):
            continue
        if _source_path(raw.get("source_file"), corpus.root) != _source_path(caller["source_file"], corpus.root):
            continue
        if product_nodes is not None:
            _record_api_product(corpus, caller, raw, product_nodes, edges)
        if raw.get("matlab_assigned_load"):
            continue
        if raw.get("_matlab_model_reference") is True:
            api = raw.get("matlab_api")
            if isinstance(api, str) and not corpus.candidates(caller, api):
                model_references.append(raw)
            continue
        if not QUALIFIED.fullmatch(name):
            continue
        matches = corpus.candidates(caller, name)
        if len(matches) != 1 or matches[0]["id"] == caller["id"]:
            continue
        relation = "imports" if raw.get("matlab_import") is True else "calls"
        append(_edge(caller, matches[0], relation, location, "matlab_corpus"))

    for caller in corpus.nodes.values():
        meta = caller["metadata"]
        for base in meta.get("matlab_superclasses", []):
            name = base.get("name")
            if not isinstance(name, str) or not QUALIFIED.fullmatch(name):
                continue
            matches = corpus.candidates(caller, name)
            if (len(matches) == 1 and matches[0]["id"] != caller["id"]
                    and matches[0]["metadata"]["kind"] == "matlab_class"):
                append(_edge(caller, matches[0], "inherits", base["source_location"], "matlab_corpus"))
    return model_references


def _record_api_product(corpus: _MatlabCorpus, caller: dict, raw: dict,
                        nodes: list[dict], edges: list[dict]) -> None:
    """Use catalog spelling only after the parsed and corpus shadows are known."""
    symbol = raw.get("matlab_api") if raw.get("_matlab_model_reference") is True else raw.get("callee")
    column = raw.get("matlab_column")
    if not isinstance(symbol, str) or type(column) is not int or column < 1:
        return
    product = API_PRODUCTS.get(symbol)
    if product is None or raw.get("matlab_product_blocked") or corpus.shadows_product(caller, symbol):
        return
    if raw.get("matlab_assigned_load") and corpus.shadows_product(caller, "load"):
        return
    evidence = "import" if raw.get("matlab_import") is True else "api"
    append_product(nodes, edges, caller, product, evidence, symbol, raw["source_location"], column=column)
