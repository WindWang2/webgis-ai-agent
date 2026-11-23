"""回归：symbology_audit 的级数 k 扫描轴必须生效（RE_K_ASSIGN 曾被定义未使用）。"""
from __future__ import annotations

import sys

import scripts.symbology_audit as audit


def test_hardcoded_k_is_reported(tmp_path, monkeypatch, capsys):
    target = tmp_path / "app" / "example.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "def build_spec(data):\n    return make_spec(data, k=5)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["symbology_audit.py"])

    assert audit.main() == 1

    out = capsys.readouterr().out
    assert "k 硬编码 '5'" in out
    assert "app/example.py:2" in out
