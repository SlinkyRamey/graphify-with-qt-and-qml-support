"""Attach source-owned MathWorks product witnesses to admitted graph facts."""

from __future__ import annotations

from base64 import b64encode
from hashlib import sha256
import json

from graphify.matlab_product_catalog import LIBRARY_PRODUCTS, SHARED_PRODUCT_SYMBOLS


def append_product(
    nodes: list[dict], edges: list[dict], owner: dict, product: str, evidence: str,
    symbol: str, location: str, *, column: int | None = None, member: str | None = None,
    signature: str | None = None,
) -> None:
    """Keep one node per site so either graph profile can preserve every use."""
    site = json.dumps([owner["id"], product, evidence, symbol, location, column], ensure_ascii=True)
    witness = "matlab_product_" + sha256(site.encode("ascii")).hexdigest()
    details: dict[str, str | int] = {
        "name": product, "evidence": evidence, "symbol": symbol, "release": "R2026b",
    }
    if column is not None:
        details["column"] = column
    if signature is not None:
        details["signature"] = signature
    if member is not None:
        details["member"] = member
    if evidence == "library_block":
        # The catalog literal can contain saved line breaks. Keep it
        # recoverable after ordinary graph text sanitation.
        details["symbol_base64"] = b64encode(symbol.encode("utf-8")).decode("ascii")
    if symbol in SHARED_PRODUCT_SYMBOLS:
        details["ownership"] = "shared"
    provenance = {"source_file": owner["source_file"], "source_location": location}
    nodes.append({
        "id": witness, "label": f"{product} · {symbol}", "file_type": "code", "type": "concept",
        "_origin": "ast", "metadata": {"matlab_product": details}, **provenance,
    })
    # Even a documented match is a source-level inference about the product,
    # independent of whether MATLAB could resolve it on a particular host.
    edges.append({
        "source": owner["id"], "target": witness, "_src": owner["id"], "_tgt": witness,
        "relation": "uses_matlab_product", "confidence": "INFERRED", **provenance,
    })


def _saved_product(node: dict, local_roots: set[str], chart_owners: set[str]) -> tuple[str, str, str] | None:
    metadata = node["metadata"]
    if metadata.get("kind") == "matlab_file":
        return "MATLAB", "source_format", ".m"
    sim = metadata.get("simulink", {})
    if sim.get("kind") in {"model", "library"}:
        return "Simulink", "source_format", ".slx"
    if node["id"] in chart_owners and sim.get("stateflow_block_type") == "Chart":
        return "Stateflow", "stateflow_chart", "Chart"
    if sim.get("reference_kind") == "library":
        path = sim["reference_target"]
        product = LIBRARY_PRODUCTS.get(path)
        if product is not None and path.partition("/")[0].casefold() not in local_roots:
            return product, "library_block", path
    return None


def record_saved_products(nodes: list[dict], edges: list[dict], product_nodes: list[dict]) -> None:
    """Use saved structure and literal library paths, never display names."""
    if not nodes:
        return
    local_roots = {
        sim["model_name"].casefold() for node in nodes
        if (sim := node["metadata"].get("simulink", {})).get("kind") in {"model", "library"}
    }
    chart_owners = {
        edge["source"] for edge in edges
        if edge.get("metadata", {}).get("simulink", {}).get("kind") == "stateflow_chart_owner"
    }
    for node in nodes:
        match = _saved_product(node, local_roots, chart_owners)
        if match is not None:
            member = node["metadata"].get("simulink", {}).get("member")
            append_product(product_nodes, edges, node, *match, node["source_location"], member=member)
