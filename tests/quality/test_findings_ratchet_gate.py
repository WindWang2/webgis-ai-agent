"""Quality V2 红线闸——findings 棘轮 + waiver + 行为化覆盖。

四条红线：
1. 棘轮：当前 findings 各 code 计数 ≤ findings-baseline.json 上限
   （回升即红；新 code 未登记视为上限 0，防静默新增债务类别）；
2. waiver：过期 waiver 即红；豁免 (code, subject) 精确匹配；
3. 行为化覆盖：真实 dispatch 测试覆盖的工具数 ≥ 基线下限（只升不降）；
4. 工具行 ``behavior`` 词表合法，且 behavior=none ⇔ TOOL_UNTESTED
   （行为证据与 findings 互相一致，防双轨记账漂移）。

注意：本文件位于 tests/quality/，被 discovery 扫描自排除——闸自身不得
污染"测试引用/行为"证据。
"""

from __future__ import annotations

import json

import pytest

from app.lib.quality.behavioral import (
    BEHAVIOR_LEVELS,
    behavior_level,
    discover_behavioral_dispatch,
)
from app.lib.quality.manifest import (
    FINDINGS_BASELINE_PATH,
    MANIFEST_VERSION,
    WAIVERS_PATH,
    compile_quality_manifest,
    evaluate_findings_ratchet,
    load_findings_baseline,
    repo_root,
)


@pytest.fixture(scope="module")
def manifest():
    return compile_quality_manifest()


@pytest.fixture(scope="module")
def baseline():
    data = load_findings_baseline()
    assert data, f"棘轮基线缺失：{FINDINGS_BASELINE_PATH}"
    return data


# ── 1. 棘轮 ──────────────────────────────────────────────────────────────


def test_ratchet_passes_against_committed_baseline(manifest, baseline):
    """提交的 baseline 与当前派生 findings 必须相容（回升即红）。"""
    r = manifest.ratchet_report
    assert r["pass"], f"findings 棘轮违规: {r['violations']}"
    assert r["behavioral_dispatch"] >= r["min_behavioral_dispatch"]


def test_ratchet_blocks_regression():
    """任意 code 回升（基线 -1）必须判 FAIL。"""
    findings = [
        {
            "code": "TOOL_UNTESTED",
            "subject": "x",
            "detail": "",
            "surface": "tools",
            "domain": "tools",
        }
    ]
    baseline = {"findings_max": {"TOOL_UNTESTED": 1}}
    ok = evaluate_findings_ratchet(findings, 0, baseline=baseline, today="2026-09-09")
    assert ok["pass"]
    worse = evaluate_findings_ratchet(
        findings * 2, 0, baseline=baseline, today="2026-09-09"
    )
    assert not worse["pass"]
    assert worse["violations"] == [
        {"code": "TOOL_UNTESTED", "baseline": 1, "current": 2}
    ]


def test_ratchet_new_code_defaults_to_zero_cap():
    """未登记的新 finding code = 静默新增债务类别 → 必须红。"""
    findings = [
        {
            "code": "BRAND_NEW_CODE",
            "subject": "y",
            "detail": "",
            "surface": "tools",
            "domain": "tools",
        }
    ]
    r = evaluate_findings_ratchet(
        findings, 0, baseline={"findings_max": {}}, today="2026-09-09"
    )
    assert not r["pass"]
    assert r["violations"][0]["baseline"] == 0


def test_ratchet_behavioral_floor_cannot_drop():
    """dispatch 覆盖数跌破基线下限 → 红（行为覆盖只升不降）。"""
    baseline = {"findings_max": {}, "min_behavioral_dispatch": 5}
    assert evaluate_findings_ratchet([], 5, baseline=baseline)["pass"]
    assert not evaluate_findings_ratchet([], 4, baseline=baseline)["pass"]


# ── 2. waiver ────────────────────────────────────────────────────────────


def test_waiver_excludes_exact_match_only():
    findings = [
        {
            "code": "TOOL_UNTESTED",
            "subject": "a",
            "detail": "",
            "surface": "tools",
            "domain": "tools",
        },
        {
            "code": "TOOL_UNTESTED",
            "subject": "b",
            "detail": "",
            "surface": "tools",
            "domain": "tools",
        },
    ]
    waivers = [
        {
            "code": "TOOL_UNTESTED",
            "subject": "a",
            "reason": "demo",
            "expires": "2999-01-01",
        }
    ]
    r = evaluate_findings_ratchet(
        findings,
        0,
        baseline={"findings_max": {"TOOL_UNTESTED": 1}},
        waivers=waivers,
        today="2026-01-01",
    )
    assert r["pass"], r
    assert r["waived_current"] == 1
    assert r["waivers_active"] == 1


def test_expired_waiver_fails_gate():
    waivers = [
        {
            "code": "TOOL_UNTESTED",
            "subject": "a",
            "reason": "demo",
            "expires": "2026-01-01",
        }
    ]
    r = evaluate_findings_ratchet(
        [], 0, baseline={"findings_max": {}}, waivers=waivers, today="2026-09-09"
    )
    assert not r["pass"]
    assert r["expired_waivers"] == [
        {"code": "TOOL_UNTESTED", "subject": "a", "expires": "2026-01-01"}
    ]


def test_waivers_file_shape():
    """提交的 waivers.json 形态合法（ Precision：错别字字段=静默失效）。"""
    payload = json.loads((repo_root() / WAIVERS_PATH).read_text(encoding="utf-8"))
    assert isinstance(payload.get("waivers"), list)
    for w in payload["waivers"]:
        assert {"code", "subject", "reason", "expires"} <= set(w)
        assert w["expires"].count("-") == 2, "expires 必须 ISO 日期"


# ── 3. 行为化发现器 ──────────────────────────────────────────────────────


def test_behavioral_discovery_real_dispatch(tmp_path):
    """AST 只认真实 dispatch 调用；纯字符串提及不算。"""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text(
        "import pytest\n"
        "async def test_x():\n"
        "    r = await registry.dispatch('alpha_tool', {})\n"
        "    assert r\n"
        "def test_mention_only():\n"
        "    name = 'beta_tool'\n"
        "    assert name\n",
        encoding="utf-8",
    )
    hits = discover_behavioral_dispatch(root=tmp_path)
    assert hits.get("alpha_tool") == ["tests/test_a.py"]
    assert "beta_tool" not in hits, "提及 ≠ dispatch"


def test_behavior_level_vocabulary():
    b = {"alpha": ["t.py"]}
    assert behavior_level("alpha", b, ["t.py"]) == "dispatch"
    assert behavior_level("beta", b, ["t.py"]) == "mention"
    assert behavior_level("gamma", b, []) == "none"
    assert set(BEHAVIOR_LEVELS) == {"dispatch", "mention", "none"}


def test_tool_rows_behavior_consistent_with_findings(manifest):
    """behavior=none ⇔ TOOL_UNTESTED（两套证据不许漂移）。"""
    none_tools = {t["name"] for t in manifest.tools if t["behavior"] == "none"}
    untested = {f["subject"] for f in manifest.findings if f["code"] == "TOOL_UNTESTED"}
    assert none_tools == untested
    for t in manifest.tools:
        assert t["behavior"] in BEHAVIOR_LEVELS


def test_manifest_v2_fields_present(manifest):
    """V2 字段：findings 可定位（surface/domain）、behavioral 统计、棘轮。"""
    assert manifest.manifest_version == MANIFEST_VERSION == 2
    for f in manifest.findings:
        assert f["surface"] in ("tools", "algorithms", "capabilities", "artifact_types")
        assert f["domain"]
    assert set(manifest.behavioral) <= set(BEHAVIOR_LEVELS)
    assert manifest.counts["findings"] == len(manifest.findings)


def test_baseline_file_is_ordered_subset_of_vocabulary(manifest, baseline):
    """baseline 只能登记已知 code（新 code 必须先改词表再改基线）。"""
    from app.lib.quality.manifest import FINDING_CODES

    assert set(baseline["findings_max"]) <= set(FINDING_CODES)
    codes_now = {f["code"] for f in manifest.findings}
    unknown = codes_now - set(baseline["findings_max"])
    assert not unknown, f"发现未登记基线的 code: {unknown}"


def test_v1_finding_dict_shape_extended_not_replaced():
    """向后兼容：原四字段仍在，新增 surface/domain 为纯扩展。"""
    from app.lib.quality.manifest import QualityFinding

    d = QualityFinding("C", "low", "s", "d", "tools", "tools").to_dict()
    assert set(d) == {"code", "severity", "subject", "detail", "surface", "domain"}
