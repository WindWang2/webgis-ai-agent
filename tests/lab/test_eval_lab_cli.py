"""E15 CLI 契约测试：eval_lab.py 子进程端到端（机器可读报告 + 基线纪律）。

资源纪律：单进程顺序（CLI 默认）；用 --spec 限定单规格控制运行时长。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCENARIOS = REPO / "tests" / "fixtures" / "lab" / "scenarios"


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {**__import__("os").environ, "USE_REDIS": "false"}
    return subprocess.run(
        [sys.executable, str(REPO / "scripts" / "eval_lab.py"), *args],
        capture_output=True, text=True, env=env, timeout=240, cwd=REPO,
    )


class TestEvalLabCli:
    def test_single_spec_green_json(self, tmp_path):
        out = tmp_path / "report.json"
        proc = _cli("--spec", str(SCENARIOS / "lab-j3-density-hotspot.json"),
                    "-f", "json", "-o", str(out))
        assert proc.returncode == 0, proc.stderr[-400:]
        report = json.loads(out.read_text())
        assert report["kind"] == "eval_lab_report"
        assert report["green"] == 1 and report["red"] == 0

    def test_baseline_compare_detects_drift(self, tmp_path):
        baseline = tmp_path / "baseline.json"
        spec = str(SCENARIOS / "lab-j3-density-hotspot.json")
        write = _cli("--spec", spec, "--write-baseline", str(baseline))
        assert write.returncode == 0, write.stderr[-400:]
        # 篡改基线（digest 漂移）→ 比对必须报 drift 且退出码 1。
        payload = json.loads(baseline.read_text())
        payload["entries"][0]["replay_digest"] = "tampered"
        baseline.write_text(json.dumps(payload, ensure_ascii=False))
        compare = _cli("--spec", spec, "--baseline", str(baseline),
                       "-f", "json", "-o", str(tmp_path / "r.json"))
        assert compare.returncode == 1
        report = json.loads((tmp_path / "r.json").read_text())
        drifts = report["baseline_comparison"]["drifts"]
        assert any(d["kind"] == "digest_drift" for d in drifts)

    def test_missing_spec_load_fails_with_usage_code(self, tmp_path):
        proc = _cli("--spec", str(tmp_path / "missing.json"))
        assert proc.returncode == 2

    def test_markdown_output_contains_table(self, tmp_path):
        out = tmp_path / "report.md"
        proc = _cli("--spec", str(SCENARIOS / "lab-j5-user-midflight-recolor.json"),
                    "-f", "md", "-o", str(out))
        assert proc.returncode == 0, proc.stderr[-400:]
        text = out.read_text()
        assert "Offline Harness Evaluation Lab Report" in text
        assert "user_wins_compliance" in text
