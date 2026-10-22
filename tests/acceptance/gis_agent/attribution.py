"""G05 task 级 failure taxonomy 与归因。

任务书词表（WP-C.9）：
``intent_wrong / wrong_dataset / invalid_method / unmet_product /
stale_render / lost_interaction / export_parity / unsupported /
governance_violation``

归因原则：
- 只归因**期望失配**的检查（实际判定 ≠ 场景期望——含负例场景里该红没红）；
- 每条归因链路必须携带：taxonomy 码、face、first stage、权威模块
  （contracts.FACE_AUTHORITY）、以及定位证据（检查 id + detail）；
- 底层工具执行面分类复用 ``HarnessFailureClass``（只读引用，不重复造词）。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .contracts import FACE_AUTHORITY, ScenarioRunResult

TAXONOMY_CODES = (
    "intent_wrong",        # 意图/范围/主体/任务类型解析错或漂移
    "wrong_dataset",       # 数据选择错（ref/来源/字段不符用户目标）
    "invalid_method",      # 方法科学资格违例（样本不足/CRS/单位/降级未披露）
    "unmet_product",       # 产品未满足目标（图层缺失/角色错/completeness 缺口）
    "stale_render",        # 渲染/证据代次落后（revision 不单调/信封旧代）
    "lost_interaction",    # 用户交互决策丢失（user-wins 被覆盖/锁定被重置）
    "export_parity",       # 导出语义不一致（manifest verify fail）
    "unsupported",         # 能力不支持但被当成支持处理（或反之）
    "governance_violation",# 权限/资源/租户边界被跨越
)

#: face × 检查 kind 前缀 → taxonomy 码（人工语义映射，评测侧唯一事实源）。
_FACE_DEFAULT_TAXONOMY = {
    "intent": "intent_wrong",
    "requirement": "unmet_product",
    "method": "invalid_method",
    "data": "wrong_dataset",
    "mapspec": "unmet_product",
    "render_export": "export_parity",
    "final_answer": "unmet_product",
}

_KIND_OVERRIDES: List[tuple] = [
    ("revision_monotonic", "stale_render"),
    ("render_seq", "stale_render"),
    ("export_status", "export_parity"),
    ("export_aspect", "export_parity"),
    ("goal_verdict", "unmet_product"),
    ("user_wins", "lost_interaction"),
    ("lock_preserved", "lost_interaction"),
    ("visibility_preserved", "lost_interaction"),
    ("unsupported_", "unsupported"),
    ("permission", "governance_violation"),
    ("authz", "governance_violation"),
    ("tool_receipt", "wrong_dataset"),
    ("dataset_ref", "wrong_dataset"),
    ("sample_size", "invalid_method"),
    ("crs", "invalid_method"),
    ("unit", "invalid_method"),
]


def taxonomy_for(face: str, kind: str) -> str:
    for prefix, code in _KIND_OVERRIDES:
        if kind.startswith(prefix):
            return code
    return _FACE_DEFAULT_TAXONOMY.get(face, "unmet_product")


def attribute_run(run: ScenarioRunResult) -> Dict[str, List[Dict[str, str]]]:
    """单场景归因：{taxonomy_code: [定位记录]}。"""
    out: Dict[str, List[Dict[str, str]]] = {}
    for c in run.checks:
        if c.verdict.value == c.expected_verdict:
            continue
        code = taxonomy_for(c.face, c.kind)
        out.setdefault(code, []).append({
            "check": c.expectation_id,
            "face": c.face,
            "kind": c.kind,
            "got": c.verdict.value,
            "want": c.expected_verdict,
            "detail": c.detail[:200],
            "authority": FACE_AUTHORITY.get(c.face, ""),
        })
    if run.error:
        # runner 级故障（超时/基础设施 bug）不是被审系统的治理违例——
        # 独立 infra_error 桶，不污染治理热区。
        out.setdefault("infra_error", []).append({
            "check": "<runner>",
            "face": "-",
            "kind": "runner_error",
            "got": "error",
            "want": "-",
            "detail": run.error[:200],
            "authority": "runner infrastructure (not the system under test)",
        })
    return out


def attribute_runs(runs: List[ScenarioRunResult]) -> Dict[str, Dict[str, int]]:
    """批量归因计数：{taxonomy_code: {scenario_id: 失配检查数}}。"""
    agg: Dict[str, Dict[str, int]] = {}
    for run in runs:
        for code, findings in attribute_run(run).items():
            bucket = agg.setdefault(code, {})
            bucket[run.scenario_id] = bucket.get(run.scenario_id, 0) + len(findings)
    return agg


def first_failing_attribution(run: ScenarioRunResult) -> Optional[Dict[str, str]]:
    """first-failing 检查的归因记录（报告『第一坏环节』用）。"""
    for c in run.checks:
        if c.verdict.value != c.expected_verdict:
            return {
                "taxonomy": taxonomy_for(c.face, c.kind),
                "face": c.face,
                "check": c.expectation_id,
                "authority": FACE_AUTHORITY.get(c.face, ""),
            }
    return None


def harness_failure_class_of(error_text: str) -> Optional[str]:
    """底层工具执行面分类（复用 HarnessFailureClass，只读）。"""
    if not error_text:
        return None
    try:
        from app.services.gis_harness.failure_taxonomy import (
            HarnessFailureClass,
        )
        return HarnessFailureClass.classify_harness_failure(error_text)
    except Exception:  # noqa: BLE001 — 底层面缺席不阻断 task 级归因
        return None
