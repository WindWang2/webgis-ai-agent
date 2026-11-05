"""Semantic Checks — 版式/标签确定性发现词表（ADR-0283 D4）.

把 layout solver（V5 约束解）、标注导出抑制统计与像素几何（safe area）
提升为 :class:`~app.lib.cartography.semantic_checks.CartographyFinding` 词表，
接入既有质量回路消费面：

- **词表 closed**（:data:`LAYOUT_FINDING_CODES`）：
  ``LAYOUT_COLLISION_UNRESOLVED`` / ``KEY_COMPONENT_SUPPRESSED`` /
  ``OVERFLOW_SUPPRESSED`` / ``SAFE_AREA_VIOLATION`` / ``LABEL_SUPPRESSED_RATIO``；
- **fix_hint 只路由既有执行器词表**（R9 P2-2：版式类 → component_composer
  ``REPAIR_ACTIONS``（change_anchor/collapse_to_overflow/…，AC-07 修复链
  词汇）；标注类 → selfheal_actions 已注册动作 adjust_labels。不新造
  动作 id、不建第二执行器；presentation commit 构造的执行接线归
  quality_loop 消费面（并行 PR 占用文件））；
- **fail-closed**：标注统计缺席 → ``LABEL_SUPPRESSED_RATIO`` 记
  ``not_evaluated``；几何域进入（rects 在场）而版心缺席 →
  ``SAFE_AREA_VIOLATION`` 记 ``not_evaluated``；两输入都不在场 = 该域
  未参与（无条目，不冒充评估）。上限/幂等/user-wins 由既有闸
  （MAX_REPAIR_ITERATIONS=2、LOCK_REASON_USER_WINS）承担，本模块不重设。

不修改 quality_loop.py（并行 PR 占用）：消费方按既有 seam 聚合本模块
返回的 findings（其 ``check`` 字段与 CartographyFinding 契约同形）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.lib.cartography.layout_solver import LayoutSolutionV5
from app.lib.cartography.semantic_checks import CartographyFinding

#: 版式/标签发现词表（closed）。
LAYOUT_FINDING_CODES: frozenset = frozenset({
    "LAYOUT_COLLISION_UNRESOLVED",
    "KEY_COMPONENT_SUPPRESSED",
    "OVERFLOW_SUPPRESSED",
    "SAFE_AREA_VIOLATION",
    "LABEL_SUPPRESSED_RATIO",
})

#: 标注抑制比阈值梯（suppressed/total；确定性，无墙钟/locale）。
LABEL_SUPPRESSED_WARN_RATIO = 0.10
LABEL_SUPPRESSED_FAIL_RATIO = 0.25

#: 修复动作路由（closed）。R9 P2-2 清偿：版式类动作词即
#: component_composer ``REPAIR_ACTIONS`` 既有词表（change_anchor/shrink/
#: collapse_to_overflow/hide_lowest_priority/keep_required —— AC-07 版面
#: 修复链的执行器词汇），**不新造动作 id**。执行接线（presentation commit
#: 构造）归 quality_loop 消费面（并行 PR 占用文件；本模块只产出建议，
#: 不建第二执行器）。LABEL_SUPPRESSED_RATIO 路由到 selfheal_actions
#: 既有注册动作 adjust_labels（呈现级标注策略调整）。
_FIX_ROUTE = {
    "LAYOUT_COLLISION_UNRESOLVED": "change_anchor",
    "OVERFLOW_SUPPRESSED": "collapse_to_overflow",
    "KEY_COMPONENT_SUPPRESSED": "collapse_to_overflow",
    "LABEL_SUPPRESSED_RATIO": "adjust_labels",
}


def _suggested_fix(code: str) -> Optional[Dict[str, Any]]:
    action = _FIX_ROUTE.get(code)
    if action is None:
        return None
    return {"route": "quality_loop", "action": action}


def _finding(
    check: str,
    severity: str,
    message: str,
    *,
    evidence: Optional[Dict[str, Any]] = None,
    evaluated: bool = True,
    repairability: str = "presentation_only",
) -> CartographyFinding:
    return CartographyFinding(
        check=check, severity=severity, message=message,
        evidence_class="deterministic",
        evidence=evidence or {},
        evaluated=evaluated,
        repairability=repairability if evaluated else "not_repairable",
        suggested_fix=_suggested_fix(check) if evaluated else None,
    )


def evaluate_layout_findings(
    solution: LayoutSolutionV5,
    *,
    label_stats: Optional[Dict[str, int]] = None,
    component_rects_px: Optional[Dict[str, Dict[str, float]]] = None,
    canvas_px: Optional[Dict[str, float]] = None,
    safe_area_px: Optional[Dict[str, float]] = None,
) -> List[CartographyFinding]:
    """从确定性求解产物推导版式/标签发现（纯函数）。

    - ``solution``：solve_layout_v5 结果（conflicts / constraint_findings /
      suppressed 全量消费）；
    - ``label_stats``：导出标签孪生统计（total/placed/suppressed）——
      缺席时 ``LABEL_SUPPRESSED_RATIO`` 记 not_evaluated；
    - ``component_rects_px`` + ``safe_area_px`` + ``canvas_px``：像素几何
      版心对拍域 —— rects 在场而 safe_area 缺席记 not_evaluated；全部
      在场时逐组件检查四边并给出越界边列表。
    """
    findings: List[CartographyFinding] = []

    # ── 1. solver 冲突（未解决）──
    if solution.conflicts:
        findings.append(_finding(
            "LAYOUT_COLLISION_UNRESOLVED", "error",
            f"布局求解存在 {len(solution.conflicts)} 个未解决冲突"
            f"（{', '.join(sorted({c.conflict_type for c in solution.conflicts}))}）",
            evidence={"conflicts": [
                {"component_id": c.component_id,
                 "conflict_type": c.conflict_type}
                for c in solution.conflicts[:16]
            ], "count": len(solution.conflicts)},
        ))

    # ── 2. V5 约束发现透传（closed 映射）──
    for cf in solution.constraint_findings:
        if cf.code == "key_component_suppressed":
            findings.append(_finding(
                "KEY_COMPONENT_SUPPRESSED", "error",
                f"关键组件 {cf.component_id} 被抑制（must_fit 契约违例）",
                evidence={"component_id": cf.component_id,
                          "detail": cf.detail},
            ))
        elif cf.code == "overflow_suppressed":
            findings.append(_finding(
                "OVERFLOW_SUPPRESSED", "warning",
                f"组件 {cf.component_id} 按 suppress 策略移除（已披露，非静默）",
                evidence={"component_id": cf.component_id,
                          "detail": cf.detail},
            ))
        # max_width_clamped / constraint_unmatched：info 级求解轨迹，
        # 不是质量发现 —— 不入词表（避免把正常裁剪当缺陷）。

    # ── 3. 标注抑制比（fail-closed）──
    if label_stats is None:
        findings.append(_finding(
            "LABEL_SUPPRESSED_RATIO", "warning",
            "标注导出统计缺席：抑制比未评估（not_evaluated）",
            evaluated=False,
        ))
    else:
        total = int(label_stats.get("total") or 0)
        suppressed = int(label_stats.get("suppressed") or 0)
        if total <= 0:
            findings.append(_finding(
                "LABEL_SUPPRESSED_RATIO", "warning",
                "标注总量为 0：抑制比未评估（not_evaluated）",
                evaluated=False,
            ))
        else:
            ratio = suppressed / total
            if ratio >= LABEL_SUPPRESSED_FAIL_RATIO:
                findings.append(_finding(
                    "LABEL_SUPPRESSED_RATIO", "error",
                    f"标注抑制比 {ratio:.3f} ≥ {LABEL_SUPPRESSED_FAIL_RATIO}"
                    f"（{suppressed}/{total}）",
                    evidence={"ratio": ratio, "suppressed": suppressed,
                              "total": total},
                ))
            elif ratio >= LABEL_SUPPRESSED_WARN_RATIO:
                findings.append(_finding(
                    "LABEL_SUPPRESSED_RATIO", "warning",
                    f"标注抑制比 {ratio:.3f} ≥ {LABEL_SUPPRESSED_WARN_RATIO}"
                    f"（{suppressed}/{total}）",
                    evidence={"ratio": ratio, "suppressed": suppressed,
                              "total": total},
                ))

    # ── 4. safe area 像素几何（可选域）──
    if component_rects_px is not None:
        canvas_ok = (
            canvas_px is not None
            and float(canvas_px.get("width") or 0) > 0
            and float(canvas_px.get("height") or 0) > 0
        )
        if safe_area_px is None or not canvas_ok:
            # R9 P3-9：canvas_px 空/零维与缺席同判（否则 right/bottom 以
            # 缺省 0 计算必误报）—— 证据不可得 = not_evaluated。
            findings.append(_finding(
                "SAFE_AREA_VIOLATION", "warning",
                "组件像素矩形在场但版心/画布声明缺席：safe area 未评估"
                "（not_evaluated）",
                evaluated=False,
            ))
        else:
            for cid, rect in sorted(component_rects_px.items()):
                violations = _safe_area_violations(rect, safe_area_px,
                                                   canvas_px)
                if violations:
                    findings.append(_finding(
                        "SAFE_AREA_VIOLATION", "warning",
                        f"组件 {cid} 越出版心安全边距（{', '.join(violations)}）",
                        evidence={"component_id": cid,
                                  "violations": violations,
                                  "rect": rect},
                    ))

    return findings


def _safe_area_violations(
    rect: Dict[str, float],
    safe: Dict[str, float],
    canvas: Dict[str, float],
) -> List[str]:
    """四边逐项检查（确定性；返回越界边 closed 列表）。"""
    out: List[str] = []
    if rect.get("y", 0) < safe.get("top", 0):
        out.append("top")
    if rect.get("x", 0) < safe.get("left", 0):
        out.append("left")
    right = rect.get("x", 0) + rect.get("width", 0)
    if right > canvas.get("width", 0) - safe.get("right", 0):
        out.append("right")
    bottom = rect.get("y", 0) + rect.get("height", 0)
    if bottom > canvas.get("height", 0) - safe.get("bottom", 0):
        out.append("bottom")
    return out


__all__ = [
    "LAYOUT_FINDING_CODES",
    "LABEL_SUPPRESSED_WARN_RATIO",
    "LABEL_SUPPRESSED_FAIL_RATIO",
    "evaluate_layout_findings",
]
