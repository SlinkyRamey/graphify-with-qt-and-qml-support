"""Admission, cache and refresh policy for saved MATLAB and Simulink facts."""

from __future__ import annotations

import json
from pathlib import Path

from graphify.rcfile import cache_salt, effective_suffix

_MODEL_SUFFIXES = {".m", ".slx"}


def model_cache_salt(path: Path, root: Path) -> str | None:
    """File-owned model and MATLAB IDs need a path as well as a content key."""
    language = cache_salt(path)
    suffix = effective_suffix(path).lower()
    if suffix not in _MODEL_SUFFIXES:
        return language
    absolute = path.absolute()
    owner = absolute.relative_to(root) if absolute.is_relative_to(root) else absolute
    # Epoch 1 retired ambiguous .m entries. Epoch 2 added API witness columns
    # and shadow evidence. Epoch 3 records state-space constructor arguments.
    # Simulink and other languages keep their existing entries.
    epoch = "matlab-simulink-3" if suffix == ".m" else "matlab-simulink-1"
    return json.dumps([epoch, suffix, owner.as_posix(), language])


def refresh_model_paths(changed, admitted, *, removed=(), profile_changed=False):
    """Refresh the live language slice when one of its binding inputs changes."""
    selected = [Path(path) for path in changed]
    if not profile_changed and not any(
        Path(path).suffix.lower() in _MODEL_SUFFIXES or effective_suffix(path).lower() in _MODEL_SUFFIXES
        for path in [*selected, *removed]
    ):
        return selected
    return list(dict.fromkeys([
        *selected, *(Path(path) for path in admitted if effective_suffix(path).lower() in _MODEL_SUFFIXES),
    ]))


def require_complete_models(result: dict, *, scan_root=None, publication_root=None) -> None:
    """Stop publication before an incomplete model scope can replace prior facts."""
    diagnostics = result.get("diagnostics", ())
    failure_codes = {
        str(row["code"]) for row in diagnostics
        if isinstance(row, dict) and isinstance(row.get("code"), str)
        and row["code"].startswith(("SIMULINK_", "MATLAB_"))
        and row.get("severity") in {"error", "fatal"}
    }
    if any(effective_suffix(path).lower() == ".slx" for path in result.get("failed_sources", ())):
        failure_codes.add("SIMULINK_READ_FAILED")
    if failure_codes:
        codes = ", ".join(sorted(failure_codes))
        recovery = "Install graphifyy[matlab] and retry." if "MATLAB_PARSER_MISSING" in failure_codes else (
            "Repair or exclude the unsupported source and retry."
        )
        raise ValueError(f"SIMULINK_INCOMPLETE: {codes}; prior graph and manifest retained. {recovery}")
    if scan_root != publication_root and is_model_result(result):
        raise ValueError("MATLAB_ROOT_MISMATCH: update the model project from its root "
                         "or pass its absolute path; prior products retained")


def is_model_node(node: dict) -> bool:
    """Recognize producer-stamped facts before native symbol tables see them."""
    metadata = node.get("metadata") if isinstance(node, dict) else None
    return isinstance(metadata, dict) and (
        metadata.get("language") == "matlab" or "simulink" in metadata or "matlab_product" in metadata
    )


def is_model_result(result: dict) -> bool:
    return any(is_model_node(node) for node in result.get("nodes", ()))


def partition_model_results(results: list[dict | None]) -> tuple[list[dict], list[dict | None]]:
    """Keep aggregate diagnostics while isolating model facts from native resolution."""
    native: list[dict | None] = []
    models: list[dict] = []
    for result in results:
        if result is None:
            native.append({})
        elif is_model_result(result):
            models.append(result)
            native.append({key: value for key, value in result.items()
                           if key not in {"nodes", "edges", "raw_calls"}})
        else:
            native.append(result)
    return models, native


def exclude_model_sources(live: set, nodes, identity) -> None:
    """Remove model owners from borrowed native context before projection."""
    live.difference_update(
        identity(node["source_file"]) for node in nodes if is_model_node(node) and node.get("source_file")
    )


def without_model_context(nodes, edges):
    """Keep direct API callers' borrowed context in the same language domain."""
    model_ids = {node.get("id") for node in nodes or () if is_model_node(node)}
    if not model_ids:
        return nodes, edges
    return (
        [node for node in nodes or () if node.get("id") not in model_ids],
        [edge for edge in edges or ()
         if edge.get("source") not in model_ids and edge.get("target") not in model_ids],
    )


def extract_model_facts(results: list[dict], root: Path) -> tuple[list[dict], list[dict]]:
    """Resolve exclusively among admitted producer facts before display sanitization."""
    from graphify.extractors.matlab_resolution import resolve_matlab_calls
    from graphify.matlab_products import record_saved_products
    from graphify.simulink_resolution import resolve_simulink_references

    nodes = [node for result in results for node in result.get("nodes", ())]
    edges = [edge for result in results for edge in result.get("edges", ())]
    raw_calls = [call for result in results for call in result.get("raw_calls", ())]
    for item in [*nodes, *edges, *raw_calls]:
        source = item.get("source_file")
        if not source:
            continue
        path = Path(source)
        if path.is_absolute() and path.is_relative_to(root):
            path = path.relative_to(root)
        item["source_file"] = path.as_posix()
    product_nodes: list[dict] = []
    model_references = resolve_matlab_calls(nodes, edges, raw_calls, root=root, product_nodes=product_nodes)
    resolve_simulink_references(nodes, edges, model_references)
    record_saved_products(nodes, edges, product_nodes)
    return [*nodes, *product_nodes], edges
