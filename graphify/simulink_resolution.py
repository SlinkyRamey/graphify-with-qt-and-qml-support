"""Join literal saved references against one admitted source corpus."""

from __future__ import annotations

from collections import defaultdict
from pathlib import PurePosixPath


def _unique(index: dict, key):
    candidates = index.get(key, ())
    return candidates[0] if len(candidates) == 1 else None


def _name(value: str, extension: str) -> str | None:
    # Only a bare identifier or the same identifier with an explicit extension
    # is a lookup key. Paths, protected models and computed values stay literal.
    if value.lower().endswith(extension):
        value = value[:-len(extension)]
    return value if value.isidentifier() else None


def _relation(source: dict, target: dict, relation: str, value: str) -> dict:
    evidence = {"reference_target": value}
    member = source.get("metadata", {}).get("simulink", {}).get("member")
    if isinstance(member, str):
        evidence["member"] = member
    return {
        "source": source["id"],
        "target": target["id"],
        "source_file": source["source_file"],
        "source_location": source.get("source_location"),
        "relation": relation,
        "confidence": "INFERRED",
        "confidence_score": 0.85,
        "metadata": {"simulink": evidence},
    }


def resolve_simulink_references(nodes: list[dict], edges: list[dict], raw_calls: list[dict]) -> None:
    """Add unambiguous source matches without consulting the MATLAB search path."""
    roots: dict[str, list[dict]] = defaultdict(list)
    block_paths: dict[tuple[str, str], list[dict]] = defaultdict(list)
    matlab_functions: dict[str, list[dict]] = defaultdict(list)
    matlab_files: dict[str, list[dict]] = defaultdict(list)
    sim_nodes = []
    by_id = {node["id"]: node for node in nodes}
    for node in nodes:
        metadata = node.get("metadata", {})
        source_file = node["source_file"]
        sim = metadata.get("simulink")
        if sim is not None:
            sim_nodes.append((node, sim))
            if sim["kind"] in {"model", "library"}:
                roots[sim["model_name"]].append(node)
            elif sim["kind"] in {"block", "subsystem"}:
                block_paths[(source_file, sim["block_path"])].append(node)
        elif metadata.get("language") == "matlab":
            source = PurePosixPath(source_file)
            # S-functions and workspace sources reference ordinary public names.
            # Package, private and class-folder entries have narrower visibility.
            if any(p == "private" or p.startswith(("+", "@")) for p in source.parts[:-1]):
                continue
            if metadata.get("kind") == "matlab_file":
                matlab_files[source.stem].append(node)
            elif metadata.get("matlab_primary") and metadata.get("kind") == "matlab_function":
                name = metadata.get("matlab_name")
                if name == source.stem:
                    matlab_functions[name].append(node)

    for node, sim in sim_nodes:
        kind, name = sim.get("reference_kind"), sim.get("reference_target")
        target, relation = None, ""
        if kind == "model" and isinstance(name, str):
            target = _unique(roots, _name(name, ".slx"))
            if target is not None and target["metadata"]["simulink"]["kind"] != "model":
                target = None
            relation = "simulink_model_reference"
        elif kind == "library" and isinstance(name, str):
            owner, sep, child = name.partition("/")
            if owner == "$bdroot" and sep:
                # Internal library links use the physical owning archive, even
                # when another archive elsewhere shares its basename.
                root_name = PurePosixPath(node["source_file"]).stem
                target = _unique(block_paths, (node["source_file"], f"{root_name}/{child}"))
            elif sep:
                library = _unique(roots, owner)
                if library and library["metadata"]["simulink"]["kind"] == "library":
                    target = _unique(block_paths, (library["source_file"], name))
            relation = "simulink_library_link"
        elif kind == "s_function" and sim.get("block_type") == "M-S-Function" and isinstance(name, str):
            target = _unique(matlab_functions, _name(name, ".m"))
            relation = "simulink_s_function"
        if target is not None:
            edges.append(_relation(node, target, relation, name))

        workspace = sim.get("workspace_source_file")
        if (sim.get("workspace_source_kind") == "MATLAB File"
                and isinstance(workspace, str) and workspace.lower().endswith(".m")):
            target = _unique(matlab_files, _name(workspace, ".m"))
            if target is not None:
                edges.append(_relation(node, target, "simulink_workspace_source", workspace))

        entry = sim.get("workspace_entry_call")
        if isinstance(entry, str):
            target = _unique(matlab_functions, _name(entry, ".m"))
            if target is not None:
                edges.append(_relation(node, target, "simulink_workspace_source", entry))

    for call in raw_calls:
        if not call.get("_matlab_model_reference"):
            continue
        caller = by_id.get(call["caller_nid"])
        name = call["callee"]
        model = _unique(roots, _name(name, ".slx"))
        if caller is not None and model is not None and model["metadata"]["simulink"]["kind"] == "model":
            edge = _relation(caller, model, "matlab_model_reference", name)
            edge["source_location"] = call["source_location"]
            edge["metadata"]["simulink"]["matlab_api"] = call["matlab_api"]
            edges.append(edge)
