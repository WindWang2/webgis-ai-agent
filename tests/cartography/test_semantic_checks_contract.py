"""semantic_checks 契约测试矩阵（V11 W0.6，ADR-0160）。

44 个检查码 × 冻结 schema 的契约面（docs/dev/ac-v11-contracts/
semantic-checks.v1.json）：

1. **码清单冻结**：源码扫描 semantic_checks.py 的大写/点分码字面量，
   必须与契约 JSON 逐名一致（新增/删除码必须显式改契约文件 —— 防
   「静默加码」与「静默丢码」）；
2. **blocking 集冻结**：BLOCKING_VALIDATION_CODES 与契约一致（v2 = 5 码，
   ADR-0199 增补 SCENE_TERRAIN_SOURCE_REF/_TYPE；定义在 lifecycle_engine，
   不在 semantic_checks —— S1 复核修正项）；
3. **结果 schema 冻结**：CartographyCheck.to_dict 键集 + status/severity
   枚举；not_evaluated 永不进 pass（报告 rollup 语义）；
4. **not_evaluated 化解表**：每个可出 not_evaluated 的码必须有化解条目
   （resolution=plan + target_wave）—— ADR-0160 §5 的机器可读形态。
"""

import json
import re
from pathlib import Path

import pytest

from app.lib.cartography.semantic_checks import CartographyReport

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT = (
    REPO_ROOT / "docs/dev/ac-v11-contracts/semantic-checks.v1.json"
)
SOURCE = REPO_ROOT / "app/lib/cartography/semantic_checks.py"

#: 源码扫描噪音白名单：形似码但实为常量名/词表（非检查码）。
SCAN_NOISE = re.compile(r"^(CARTO_[A-Z_]+|RGBA)$")


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text(encoding="utf-8"))


def _scan_source_codes() -> tuple[set[str], set[str]]:
    src = SOURCE.read_text(encoding="utf-8")
    upper = {
        m for m in re.findall(r'"([A-Z][A-Z_]{3,})"', src)
        if not SCAN_NOISE.match(m)
    }
    dotted = set(re.findall(r'"(carto\.[a-z_.]+)"', src))
    return upper, dotted


def test_contract_file_exists_and_versioned(contract) -> None:
    assert CONTRACT.exists()
    assert contract["contract"] == "semantic-checks"
    # v2（ADR-0199）：+SCENE_TERRAIN_SOURCE_REF / _TYPE（blocking 集显式扩编）。
    assert contract["version"] == 2


def test_uppercase_codes_frozen(contract) -> None:
    upper, _ = _scan_source_codes()
    assert upper == set(contract["codes"]["uppercase"]), (
        "大写检查码与冻结契约漂移 —— 新增/删除码必须显式更新 "
        "docs/dev/ac-v11-contracts/semantic-checks.v1.json"
    )
    assert len(upper) == 38


def test_dotted_codes_frozen(contract) -> None:
    _, dotted = _scan_source_codes()
    assert dotted == set(contract["codes"]["dotted"])
    assert len(dotted) == 6


def test_total_code_count_is_44(contract) -> None:
    total = (len(contract["codes"]["uppercase"])
             + len(contract["codes"]["dotted"]))
    assert total == 44


def test_blocking_codes_frozen(contract) -> None:
    from app.services.mapspec.lifecycle_engine import BLOCKING_VALIDATION_CODES
    assert set(BLOCKING_VALIDATION_CODES) == set(contract["blocking_codes"]["codes"])
    # v2：3 既有码 + SCENE_TERRAIN_SOURCE_REF/_TYPE（ADR-0199，显式契约变更）。
    assert len(BLOCKING_VALIDATION_CODES) == 5
    # blocking 码不属于 semantic_checks 44 码族（coordinator 直发射）
    upper, dotted = _scan_source_codes()
    assert not (set(BLOCKING_VALIDATION_CODES) & (upper | dotted))


def test_check_result_schema_frozen(contract) -> None:
    schema = contract["check_result_schema"]["CartographyCheck.to_dict"]
    report = CartographyReport()
    report.add_check("carto.load.ratio", "pass", "ok")
    dumped = report.checks[0].to_dict()
    assert set(dumped) == set(schema)
    assert set(contract["check_result_schema"]["status_enum"]) == {
        "pass", "fail", "warning", "not_evaluated",
    }
    assert set(contract["check_result_schema"]["severity_enum"]) == {
        "error", "warning", "info",
    }


def test_not_evaluated_never_reads_as_pass() -> None:
    """缺证据永不成功：ok 恒 False；rollup 语义分三态（S1 复核实证）。"""
    # 1) deterministic 混合（有评估 + 有缺证据）→ warning，但 ok=False
    report = CartographyReport()
    report.add_check("carto.load.ratio", "pass", "ok")
    report.add_check(
        "CRS_EVIDENCE", "not_evaluated", "无 profile 可判",
        evidence_class="deterministic",
    )
    dumped = report.to_dict()
    assert dumped["status"] == "warning"
    assert dumped["ok"] is False and dumped["complete"] is False

    # 2) deterministic 全缺证据 → not_evaluated（不是 pass）
    report2 = CartographyReport()
    report2.add_check(
        "BBOX_VALIDITY", "not_evaluated", "无 bbox 输入",
        evidence_class="deterministic",
    )
    dumped2 = report2.to_dict()
    assert dumped2["status"] == "not_evaluated"
    assert dumped2["ok"] is False

    # 3) 非 deterministic（visual 类）缺证据 → 不参与 deterministic rollup，
    #    但 ok 仍为 False（无任何可计证据）
    report3 = CartographyReport()
    report3.add_check(
        "VISUAL_OVERLAP", "not_evaluated", "视觉证据不可用",
        severity="info", evidence_class="visual",
    )
    dumped3 = report3.to_dict()
    assert dumped3["ok"] is False and dumped3["complete"] is False


def test_not_evaluated_codes_have_resolutions(contract) -> None:
    """每个可出 not_evaluated 的码必须有化解条目（plan / target_wave）。"""
    upper, dotted = _scan_source_codes()
    all_codes = upper | dotted
    table = {r["code"]: r for r in contract["not_evaluated_resolutions"]}
    assert table, "化解表不得为空"
    for code, entry in table.items():
        assert code in all_codes, f"化解表引用了不存在的码: {code}"
        assert entry["resolution"] in ("plan", "not_evaluable")
        if entry["resolution"] == "plan":
            assert re.fullmatch(r"W\d+", entry["target_wave"]), (
                f"{code}: plan 条目必须有 target_wave（W0–W9）"
            )
            assert entry.get("plan"), f"{code}: plan 条目必须有方案说明"
    # S1 复核实证的 9 个 not_evaluated 出口必须全部在表内
    required = {
        "VISUAL_OVERLAP", "STYLE_EXPRESSION_SUPPORT", "RESULT_VISIBILITY",
        "OPACITY_VALIDITY", "CRS_EVIDENCE", "BBOX_VALIDITY",
        "RESULT_DATA_PRESENCE", "GEOMETRY_LAYER_TYPE", "RESULT_MAP_PROVENANCE",
    }
    assert required <= set(table), (
        f"化解表缺码: {sorted(required - set(table))}"
    )
