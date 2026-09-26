"""最终显示确认 —— deterministic finalization check（F12 / ADR-0214 D6）。

DoD「最终完成状态能证明期望图层/组件确实已显示」的机械对账：

- 期望面 = ``PlanCompilation.display_expectations``（编译期落定的
  layer_id → 期望可见性 + 必需组件 id 列表）；
- 实际面 = 当前 MapSpec（``layout.visibility`` / 组件 ``enabled``）+
  ``display_confirmation`` 的 render ACK seam。

纯函数、零 LLM、零自由文本结论 —— 只有 expected/actual 逐项对账与
结构化 reason codes。``service.MapPlanCompilerService.finalize`` 负责
await ACK 读取（本模块保持同步纯函数，便于单测与 replay）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.plan_ir import spec_doc_of
from app.services.map_plan_compiler.compiler import DisplayExpectation

__all__ = ["ExpectationRow", "FinalizationCheck", "check_final_display"]


class ExpectationRow(BaseModel):
    """单条期望对账（图层或组件；机械可 diff）。"""

    model_config = ConfigDict(frozen=True)

    kind: Literal["layer", "component"]
    target: str = Field(max_length=200)
    expected: str = Field(default="", max_length=48)   # visible/hidden/present/absent
    actual: str = Field(default="", max_length=48)     # 同词表；"missing" = 目标不在场
    ok: bool = True
    reason_code: str = Field(default="", max_length=64)


class FinalizationCheck(BaseModel):
    """终态对账报告：confirmed ⇒ 期望显示面已物理在位（含 ACK 语义）。"""

    model_config = ConfigDict(frozen=True)

    status: Literal["confirmed", "unconfirmed", "blocked"]
    rows: List[ExpectationRow] = Field(default_factory=list, max_length=96)
    ack_ok: bool = True
    ack_mode: str = Field(default="auto", max_length=16)
    reason_codes: List[str] = Field(default_factory=list, max_length=24)

    @property
    def failed_rows(self) -> List[ExpectationRow]:
        return [r for r in self.rows if not r.ok]


def _actual_visibility(layer: Optional[Dict[str, Any]]) -> str:
    if layer is None:
        return "missing"
    if layer.get("visible") is False:  # schema bool 形态（upsert 面）
        return "hidden"
    layout = layer.get("layout") if isinstance(layer.get("layout"), dict) else {}
    return "visible" if layout.get("visibility", "visible") != "none" else "hidden"


def _actual_component(component: Optional[Dict[str, Any]]) -> str:
    if component is None:
        return "missing"
    return "present" if bool(component.get("enabled", True)) else "hidden"


def check_final_display(
    expectations: DisplayExpectation,
    current: Optional[Dict[str, Any]],
    *,
    display_confirmed: bool = True,
    ack_mode: str = "auto",
    baseline_fingerprint: str = "",
) -> FinalizationCheck:
    """期望显示面 vs 当前 MapSpec（+ACK）的确定性对账。

    ``display_confirmed``：调用方从 display_confirmation 取得（auto 模式
    恒 True / required 模式比对 render_seq ack）；本函数保持纯函数。
    ``baseline_fingerprint``：语义保留位（当前实现对账以期望面为准，
    指纹漂移归 receipt_is_stale 管）。
    """
    del baseline_fingerprint  # 见 docstring：漂移对账在 receipt 面
    current = spec_doc_of(current)
    layers_now = {
        str(l.get("id")): l
        for l in (current.get("layers") or [])
        if isinstance(l, dict) and l.get("id")
    }
    layout = current.get("layout") if isinstance(current.get("layout"), dict) else {}
    components_now = {
        str(c.get("id")): c
        for c in (layout.get("components") or [])
        if isinstance(c, dict) and c.get("id")
    }

    rows: List[ExpectationRow] = []
    for layer_id, expected_visible in sorted(expectations.layers.items()):
        actual = _actual_visibility(layers_now.get(layer_id))
        expected = "visible" if expected_visible else "hidden"
        ok = (actual == expected) if actual != "missing" else (not expected_visible)
        rows.append(ExpectationRow(
            kind="layer", target=layer_id, expected=expected, actual=actual,
            ok=ok,
            reason_code="" if ok else (
                "FINAL_LAYER_MISSING" if actual == "missing" else "FINAL_LAYER_MISMATCH"),
        ))
    for component_id in expectations.components:
        actual = _actual_component(components_now.get(component_id))
        ok = actual == "present"
        rows.append(ExpectationRow(
            kind="component", target=component_id, expected="present", actual=actual,
            ok=ok,
            reason_code="" if ok else (
                "FINAL_COMPONENT_MISSING" if actual == "missing" else "FINAL_COMPONENT_DISABLED"),
        ))

    reason_codes: List[str] = []
    if not display_confirmed:
        reason_codes.append("FINAL_ACK_PENDING")
    for r in rows:
        if not r.ok and r.reason_code not in reason_codes:
            reason_codes.append(r.reason_code)

    if not expectations.layers and not expectations.components:
        return FinalizationCheck(
            status="blocked", rows=rows, ack_ok=display_confirmed,
            ack_mode=ack_mode, reason_codes=["FINAL_NO_EXPECTATIONS"],
        )
    if not display_confirmed or any(not r.ok for r in rows):
        status: Literal["confirmed", "unconfirmed", "blocked"] = "unconfirmed"
    else:
        status = "confirmed"
    return FinalizationCheck(
        status=status, rows=rows, ack_ok=display_confirmed,
        ack_mode=ack_mode, reason_codes=reason_codes[:24],
    )
