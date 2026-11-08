"""Resolve lexical MATLAB calls and record supported corpus lookup witnesses.

The variable-shadowing and lexical-tier design builds on Julio León's Graphify
PR #4836 (a24491c211fbb854a4d1e1241656af01d43fe849). The source here uses the
pinned language-pack grammar and adds separate namespace/import evidence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from graphify.extractors.matlab_ast import SIMULINK_APIS
from graphify.extractors.matlab_call_syntax import Workspace, inspect_workspace

if TYPE_CHECKING:
    from graphify.extractors.matlab import MatlabFacts, MatlabScope
    from graphify.extractors.matlab_call_syntax import CallSite


def _lexical_scopes(scope: MatlabScope):
    current: MatlabScope | None = scope
    while current is not None and current.kind in {"matlab_function", "matlab_method", "matlab_file"}:
        yield current
        current = current.parent
        if current is not None and current.kind == "matlab_file":
            return


def _same_file_candidates(scope: MatlabScope, facts: MatlabFacts, name: str) -> list[MatlabScope]:
    for ancestor in _lexical_scopes(scope):
        local = ancestor.functions.get(name)
        if local is not None:
            return local
    return facts.file.functions.get(name, [])


def _record_calls(
    facts: MatlabFacts, scope: MatlabScope, workspaces: dict[str, Workspace], site: CallSite
) -> None:
    workspace = workspaces[scope.scope_key]
    name = site.name
    head = name.partition(".")[0]
    ancestors = [workspaces[a.scope_key] for a in _lexical_scopes(scope)]
    if len(name) > 256 or any(a.uncertain or head in a.bindings for a in ancestors):
        return
    # An assigned built-in load returns data without injecting its fields.
    # Product matching must still rule out a shadowed implementation. Keep
    # the previous conservative local call edges for load-bearing scopes.
    assigned_load = any(a.assigned_load for a in ancestors)
    if assigned_load and (
        "load" in facts.class_member_names or _same_file_candidates(scope, facts, "load")
        or any("load" in a.bindings or "load" in a.imports or a.wildcard_import for a in ancestors)
    ):
        return
    load_evidence = {"matlab_assigned_load": True} if assigned_load else {}
    # A declaration's full syntax is scanned before any call binds. MATLAB's
    # explicit imports apply throughout the function, including earlier lines.
    if name == head:
        imported = workspace.imports.get(name)
        if imported is not None:
            if len(imported) == 1:
                facts.add_call(scope, next(iter(imported)), site.syntax, matlab_explicit_import=True, **load_evidence)
            return
        if any(name in a.imports for a in ancestors[1:]):
            return
        local = _same_file_candidates(scope, facts, name)
        if local:
            if len(local) == 1 and not assigned_load:
                target = local[0]
                primary = target.record["metadata"]["matlab_primary"]
                if not primary or target.name == facts.path.stem:
                    facts.add_edge(scope.record["id"], target.record["id"], "calls", site.syntax)
                    if scope.kind == "matlab_file":
                        facts.edges[-1]["confidence"] = "INFERRED"
            return
        if name in facts.class_member_names or any(a.wildcard_import for a in ancestors):
            return
        if name in SIMULINK_APIS and site.model_name:
            facts.add_call(
                scope, site.model_name, site.syntax, _matlab_model_reference=True,
                matlab_api=name, **load_evidence,
            )
            return
    if name != head and any(head in ancestor.imports for ancestor in ancestors):
        return
    # Keep the existing qualified lookup, but avoid vendor claims when dot
    # syntax could refer to a same-file function or a class member instead.
    product_blocked = name != head and (
        bool(_same_file_candidates(scope, facts, head)) or head in facts.class_member_names
        or any(a.wildcard_import for a in ancestors)
    )
    facts.add_call(scope, name, site.syntax, matlab_product_blocked=product_blocked, **load_evidence)


def collect_matlab_calls(facts: MatlabFacts) -> None:
    callables = [
        scope for scope in facts.scopes
        if scope.kind in {"matlab_function", "matlab_method"}
        or (scope is facts.file and facts.file_kind == "script")
    ]
    workspaces = {scope.scope_key: inspect_workspace(scope.syntax) for scope in callables}
    for scope in callables:
        workspace = workspaces[scope.scope_key]
        for name, syntax in workspace.import_sites:
            # Explicit import spelling is source evidence even when its target
            # is outside the admitted corpus or has conflicting declarations.
            facts.add_symbol(scope, name, "import", syntax)
            if not name.endswith(".*"):
                facts.add_call(
                    scope, name, syntax, matlab_explicit_import=True, matlab_import=True,
                )
        for site in workspace.sites:
            _record_calls(facts, scope, workspaces, site)
