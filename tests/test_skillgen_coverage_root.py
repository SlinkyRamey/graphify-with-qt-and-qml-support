"""Exercise the rendered no-coverage repair with a separate trusted scan root."""

import json

import pytest

from tests.test_skillgen import _platform_artifacts, _step_body, _write_chunk
from tools.skillgen import gen


@pytest.mark.parametrize("host", ["claude", "windows"])
def test_b35_coverage_uses_trusted_scan_root(tmp_path, monkeypatch, capsys, host):
    """A BOM-tolerant external scan root owns relative reads and unread cleanup."""
    root = tmp_path / "accepted 'source $ with ; symbols"
    root.mkdir()
    (root / "partial.md").write_text("source\n" * 6, encoding="utf-8")
    (root / "unread.md").write_text("unread\n", encoding="utf-8")
    (tmp_path / "partial.md").write_text("unrelated\n" * 2, encoding="utf-8")
    out = tmp_path / "graphify-out"
    _write_chunk(out, {
        "coverage": {"partial.md": 2},
        "nodes": [{"source_file": path} for path in ("partial.md", "unread.md")],
    })
    (out / ".graphify_root").write_text(str(root), encoding="utf-8-sig")
    (out / ".graphify_uncached.txt").write_text("partial.md\nunread.md", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    core, _ = _platform_artifacts(host)
    marker = "covered[src] = max("
    if host == "windows":
        body, = [block.split(gen._PY_INVOKE_PS_CLOSE, 1)[0] for block in
                 core.split(gen._PY_INVOKE_PS_OPEN + "\n") if marker in block]
    else:
        body = _step_body(core, marker)
    exec(compile(body, "step-b35", "exec"), {"__name__": "__main__"})
    assert "read 2 of 6 lines" in capsys.readouterr().out
    assert (out / ".graphify_partial.txt").read_text(encoding="utf-8").splitlines() == [
        "partial.md", "unread.md",
    ]
    chunk = json.loads((out / ".graphify_chunk_01.json").read_text(encoding="utf-8"))
    assert chunk["nodes"] == [{"source_file": "partial.md"}]
