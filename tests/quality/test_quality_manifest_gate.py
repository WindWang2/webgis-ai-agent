"""Quality Manifest 红线闸（ADR-0104）。

三条红线：
1. 提交的 docs/quality/QUALITY_MANIFEST.md 与 quality-manifest.json 必须与
   registry 当前派生结果字节一致（过期即红，与 science/workflow/catalog 同款）；
2. 编译确定性：同一提交两次编译指纹一致；
3. findings 词表与形态合法（消费方：Wave 20 质量报告）。

注意：本文件位于 tests/quality/，被 discovery 扫描自排除——闸自身不得
污染"测试引用"证据。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from gen_quality_manifest import DEFAULT_JSON, DEFAULT_MD, generate  # noqa: E402

from app.lib.quality.discovery import EXCLUDED_PREFIXES  # noqa: E402
from app.lib.quality.manifest import (  # noqa: E402
    FINDING_CODES,
    GATE_FIELDS,
    MANIFEST_VERSION,
    _SEVERITIES,
    collect,
    compile_quality_manifest,
    gate,
)


@pytest.fixture(autouse=True)
def _fresh_registries():
    """R1 review MAJOR-4：闸对进程内 registry 污染免疫（单一事实源口径）。"""
    from app.lib.gis.algorithm_registry import reset_algorithm_registry
    from app.lib.gis.artifacts import reset_artifact_type_registry
    from app.lib.gis.capability_registry import reset_capability_registry

    reset_algorithm_registry()
    reset_capability_registry()
    reset_artifact_type_registry()
    yield


@pytest.fixture(scope="module")
def manifest():
    return compile_quality_manifest()


def test_committed_manifest_is_current():
    """提交的 manifest 与当前派生结果字节一致（过期即红）。"""
    md, js = generate()
    assert DEFAULT_MD.read_text(encoding="utf-8") == md, (
        "QUALITY_MANIFEST.md 过期：运行 python scripts/gen_quality_manifest.py"
    )
    assert DEFAULT_JSON.read_text(encoding="utf-8") == js, (
        "quality-manifest.json 过期：运行 python scripts/gen_quality_manifest.py"
    )


def test_compile_is_deterministic(manifest):
    """同一提交两次编译指纹一致（无时间戳、无顺序噪声）。"""
    again = compile_quality_manifest()
    assert again.fingerprint == manifest.fingerprint
    assert again.findings == manifest.findings


def test_manifest_shape(manifest):
    assert manifest.manifest_version == MANIFEST_VERSION
    assert len(manifest.fingerprint) == 64
    assert manifest.counts["tools"] >= 200, "活工具规模跌破下限"
    assert manifest.counts["capabilities"] > 0
    assert manifest.counts["artifact_types"] > 0
    # 每行工具必须带 tags 投影（R1 修复：曾经漏投影导致闸误报 0%）
    with_tags = [t for t in manifest.tools if "tags" in t]
    assert len(with_tags) == len(manifest.tools)


def test_findings_vocabulary(manifest):
    for f in manifest.findings:
        assert f["code"] in FINDING_CODES, f"未知 finding code: {f['code']}"
        assert f["severity"] in _SEVERITIES
        assert f["subject"]
        assert f["detail"]


def test_planned_tools_never_flagged_untested(manifest):
    """PLANNED 工具不可执行，不得进入 TOOL_UNTESTED 线索。"""
    untested = {f["subject"] for f in manifest.findings if f["code"] == "TOOL_UNTESTED"}
    planned = {t["name"] for t in manifest.tools if t["status"] == "planned"}
    assert not (untested & planned)


def test_gate_report_contract(manifest):
    report = manifest.gate_report
    assert report["total"] >= 200
    assert set(report["fields"]) == set(GATE_FIELDS)
    assert gate(report) == 0, "描述符富化回退（ADR-0103）：禁止低于基线棘轮"
    assert collect()["total"] == report["total"]


def test_discovery_excludes_quality_gates():
    """闸文件不得污染测试引用证据（自排除）。"""
    assert "tests/quality/" in EXCLUDED_PREFIXES
    assert "tests/unit/test_descriptor_coverage_gate.py" in EXCLUDED_PREFIXES


def test_legacy_descriptor_gate_script_import_face():
    """ADR-0103 历史 import 面保持：GATE_THRESHOLDS / collect / gate。"""
    from check_tool_descriptor_coverage import GATE_THRESHOLDS as legacy_thresholds
    from check_tool_descriptor_coverage import collect as legacy_collect
    from check_tool_descriptor_coverage import gate as legacy_gate

    assert legacy_thresholds is not None
    report = legacy_collect()
    assert report["total"] >= 200
    assert legacy_gate(report) == 0


def test_gate_report_uses_module_manifest(manifest):
    """R1 review：复用 module 级 manifest，避免第三次全量 discovery 扫描。"""
    assert manifest.gate_report["total"] == manifest.counts["tools"]
