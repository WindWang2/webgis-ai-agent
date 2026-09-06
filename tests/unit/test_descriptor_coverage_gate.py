"""描述符覆盖率闸（ADR-0103）——把 scripts/check_tool_descriptor_coverage.py
的 gate 变成测试套件内的常驻红线：富化只许前进，不许回退。"""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from check_tool_descriptor_coverage import GATE_THRESHOLDS, collect, gate  # noqa: E402


def test_descriptor_coverage_gate_passes():
    report = collect()
    assert report["total"] >= 200, f"live tools dropped to {report['total']}"
    assert gate(report) == 0


def test_gate_thresholds_present():
    for field in ("side_effect", "tags", "latency_class", "memory_class", "capabilities"):
        assert field in GATE_THRESHOLDS
