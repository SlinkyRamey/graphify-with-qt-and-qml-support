"""Verify Control System Toolbox evidence without guessing from an overloaded name."""

from __future__ import annotations

import json

import pytest

from graphify.watch import _rebuild_code
from tests.test_matlab_product_attribution import _extract, _source, _uses


def _control_sites(data):
    return [
        (owner, witness, site) for owner, witness, site in _uses(data, "api")
        if site["name"] == "Control System Toolbox"
    ]


@pytest.mark.parametrize("newline", ["\n", "\r\n"], ids=["lf", "crlf"])
def test_req_matlab003_ac05_records_exact_matrix_constructor_sites(tmp_path, newline):
    """The numeric, matrix and continued zero-matrix forms locate each call."""
    pytest.importorskip("tree_sitter_language_pack")
    entry = tmp_path / "entry.m"
    # The continuation yields the same call locations for both on-disk endings.
    source = """function result = entry(plant, A, B, C, m)
    P = ss(A,B,C,0); Q = ss(A,B,C,[-1,0;2,4]);
    result = ss(plant.A,plant.B, ...
        plant.C(1:3,:),zeros(3,m));
    S = ss(A,B,C,zeros(m));
end
"""
    entry.write_bytes(source.replace("\n", newline).encode("utf-8"))
    data = _extract(tmp_path, entry)
    assert not data.get("diagnostics")
    sites = _control_sites(data)
    assert len(sites) == 4
    assert len({witness["id"] for _, witness, _ in sites}) == 4
    assert {(witness["source_location"], site["column"]) for _, witness, site in sites} == {
        ("L2", 9), ("L2", 26), ("L3", 14), ("L5", 9),
    }
    for owner, witness, site in sites:
        assert owner["label"] == "entry"
        assert witness["source_file"] == "entry.m"
        assert (site["symbol"], site["signature"]) == ("ss", "ss(A,B,C,D)")


def test_req_matlab003_ac05_leaves_ambiguous_call_shapes_unassigned(tmp_path):
    """Shared names, extra arguments, dynamic feeds and list expansion abstain."""
    entry = _source(tmp_path, "entry.m", """function entry(A, B, C, D, values, model)
    ss(model);
    ss(model,'rvot','on','off');
    ss(A,B,C,D);
    ss(A,B,C,0,1);
    ss(A,B,C,0,InputName="input");
    ss(values{:},B,C,0);
    ss(A,B,C,zeros(3,2,"like",D));
    model.ss(A,B,C,0);
    unknown.ss(A,B,C,0);
    bode(model,1); pole(model); isstable(model);
end
""")
    data = _extract(tmp_path, entry)
    assert not data.get("diagnostics")
    assert _control_sites(data) == []


@pytest.mark.parametrize("shadow", ["argument", "function", "import", "project", "private", "class-folder", "classdef"])
def test_req_matlab003_ac05_ss_shadow_prevents_vendor_attribution(tmp_path, shadow):
    """Local and admitted project providers win over the documented spelling."""
    argument = ", ss" if shadow == "argument" else ""
    import_text = "    import local.*\n" if shadow == "import" else ""
    nested = "\nfunction out = ss(a,b,c,d)\nout = d;\nend\n" if shadow == "function" else ""
    entry = _source(tmp_path, "entry.m", f"""function result = entry(A, B, C{argument})
{import_text}    result = ss(A,B,C,0);
end
{nested}""")
    paths = [entry]
    if shadow in {"project", "private", "class-folder"}:
        path = {"project": "ss.m", "private": "private/ss.m", "class-folder": "@Custom/ss.m"}[shadow]
        paths.append(_source(tmp_path, path, "function out = ss(a,b,c,d)\nout = d;\nend\n"))
    if shadow == "classdef":
        paths.append(_source(tmp_path, "Custom.m", """classdef Custom
methods
function out = ss(self,b,c,d)
out = d;
end
end
end
"""))
    data = _extract(tmp_path, *paths)
    assert not data.get("diagnostics")
    assert _control_sites(data) == []


@pytest.mark.parametrize("shadow", ["argument", "function", "project", "class-folder"])
def test_req_matlab003_ac05_zeros_shadow_preserves_independent_literal_sites(tmp_path, shadow):
    """A shadowed zeros call loses its shape evidence without affecting numeric literals."""
    argument = ", zeros" if shadow == "argument" else ""
    nested = "\nfunction value = zeros(n,m)\nvalue = 1;\nend\n" if shadow == "function" else ""
    entry = _source(tmp_path, "entry.m", f"""function result = entry(A, B, C, n, m{argument})
    P = ss(A,B,C,zeros(n,m));
    result = ss(A,B,C,0);
end
{nested}""")
    paths = [entry]
    if shadow in {"project", "class-folder"}:
        path = "zeros.m" if shadow == "project" else "@Custom/zeros.m"
        paths.append(_source(tmp_path, path, "function value = zeros(n,m)\nvalue = 1;\nend\n"))
    data = _extract(tmp_path, *paths)
    assert not data.get("diagnostics")
    (_, witness, site), = _control_sites(data)
    assert (witness["source_location"], site["signature"]) == ("L3", "ss(A,B,C,D)")


def test_req_matlab003_ac05_watcher_retracts_and_restores_cached_signature(tmp_path):
    """Adding and removing a project shadow rebinds the same cached source site."""
    _source(tmp_path, "entry.m", "function result = entry(A,B,C,m)\nresult = ss(A,B,C,zeros(3,m));\nend\n")

    def rebuild(changed=None):
        assert _rebuild_code(tmp_path, changed_paths=changed, no_cluster=True, acquire_lock=False)
        return _control_sites(json.loads((tmp_path / "graphify-out/graph.json").read_bytes()))

    ((_, original, metadata),) = rebuild()
    assert original["source_location"] == "L2"
    shadow = _source(tmp_path, "zeros.m", "function value = zeros(n,m)\nvalue = 1;\nend\n")
    assert rebuild([shadow]) == []
    shadow.unlink()
    ((_, recovered, recovered_metadata),) = rebuild([shadow])
    assert recovered["id"] == original["id"]
    assert recovered_metadata == metadata
