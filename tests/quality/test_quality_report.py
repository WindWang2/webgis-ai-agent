"""Quality Report 红线（ADR-0245 Wave 20）。

报告是各派生工件的聚合投影：字节一致 + 聚合值与源工件一致。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from gen_quality_report import DEFAULT_JSON, DEFAULT_MD, build_report, main, render_md  # noqa: E402


def test_report_current():
    report = build_report()
    assert DEFAULT_MD.read_text(encoding="utf-8") == render_md(report)
    assert json.loads(DEFAULT_JSON.read_text(encoding="utf-8")) == report


def test_report_aggregates_match_sources():
    report = build_report()
    manifest = json.loads(
        (REPO / "docs/quality/quality-manifest.json").read_text(encoding="utf-8"))
    agg = report["aggregates"]["quality_manifest"]
    assert agg["fingerprint"] == manifest["fingerprint"]
    assert agg["counts"] == manifest["counts"]
    drift = json.loads(
        (REPO / "docs/quality/contract-drift-report.json").read_text(encoding="utf-8"))
    assert report["aggregates"]["contract_drift"]["counts"] == drift["counts"]


def test_findings_grouping_is_complete():
    report = build_report()
    manifest = json.loads(
        (REPO / "docs/quality/quality-manifest.json").read_text(encoding="utf-8"))
    grouped = report["aggregates"]["quality_manifest"]["findings_by_code"]
    assert sum(grouped.values()) == len(manifest["findings"])


def test_output_paths_are_repo_anchored(tmp_path, monkeypatch, capsys):
    # 回归：输出路径曾为 cwd 相对（scripts/gen_quality_report.py:25）——
    # 子目录下 --check 误报 stale、写入模式把生成物写进错误目录的 docs 树。
    assert DEFAULT_MD == REPO / "docs/quality/QUALITY_REPORT.md"
    assert DEFAULT_JSON == REPO / "docs/quality/quality-report.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["gen_quality_report.py", "--check"])
    if main() == 1:  # 工件过期时，stale 列表须指向 REPO 内真实路径，而非 cwd 相对路径
        assert str(REPO) in capsys.readouterr().out
