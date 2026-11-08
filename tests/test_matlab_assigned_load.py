"""Exercise the native-audit regression at the public MATLAB corpus boundary."""

import pytest

from graphify.extract import extract


@pytest.mark.parametrize(
    ("statement", "local_load", "expected"),
    [
        ('data = load("input.mat");', False, True),
        ('load("input.mat");', False, False),
        ("load input.mat", False, False),
        ('getCurrentTask = load("input.mat");', False, False),
        ('data = load("input.mat");', True, False),
        ('data = load("input.mat");\nclear data', False, False),
    ],
    ids=["assigned", "no-output", "command", "bound-name", "project-shadow", "other-mutation"],
)
def test_req_matlab003_ac02_assigned_load_preserves_only_proved_product_sites(
    tmp_path, statement, local_load, expected,
):
    """An assigned built-in load may preserve a product match without inventing call edges."""
    pytest.importorskip("tree_sitter_language_pack")
    caller = tmp_path / "run_task.m"
    caller.write_text(
        f"function task = run_task()\n{statement}\ntask = getCurrentTask();\nhelper();\nend\n",
        encoding="utf-8",
    )
    helper = tmp_path / "helper.m"
    helper.write_text("function out = helper()\nout = 1;\nend\n", encoding="utf-8")
    sources = [caller, helper]
    if local_load:
        shadow = tmp_path / "load.m"
        shadow.write_text("function out = load(path)\nout = path;\nend\n", encoding="utf-8")
        sources.append(shadow)
    result = extract(sources, root=tmp_path, cache_root=tmp_path, parallel=False)
    assert not result.get("failed_sources")
    witnesses = [
        node for node in result["nodes"]
        if node.get("metadata", {}).get("matlab_product", {}).get("symbol") == "getCurrentTask"
    ]
    assert len(witnesses) == int(expected)
    if expected:
        assert witnesses[0]["source_file"] == "run_task.m"
        assert witnesses[0]["source_location"] == "L3"
        assert witnesses[0]["metadata"]["matlab_product"]["name"] == "Parallel Computing Toolbox"
    assert not any(edge["relation"] == "calls" for edge in result["edges"])
