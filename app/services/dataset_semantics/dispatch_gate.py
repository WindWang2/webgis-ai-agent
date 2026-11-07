"""工具分发输入侧数据资格闸（G03 GD-08 / ADR-0286 D4）。

背景（BASELINE 断链 2）：五态资格裁决只在 recipe 编译面与
``compile_workflow_semantics`` 生效——Agent 走裸分析工具（buffer/
heatmap/…）时，工具参数里的 ``ref:`` 数据没有任何资格/证据校验。

本闸是**薄对接**（不是第二资格权威）：只做证据完整性裁决——
- 语义记录损坏（corrupt/unsupported）→ BLOCK（分析不可建立在不可验证
  的语义上）；
- 记录面期望指纹与 descriptor 指纹不一致（stale）→ BLOCK；
- 记录新鲜一致 → PASS，并附有界 ``qualification_context``（数据事实对
  模型可见——GD-08「readiness 结果进入 ToolDispatch」）；
- 无语义记录 → 默认 PASS + ``descriptor_missing`` 披露（证据缺席 ≠ 数据
  不合格；``DATA_QUALIFICATION_GATE_STRICT=1`` 升级为 BLOCK）。

语义内容的资格词表（五态）仍由 ``gis_harness.data_qualification`` 权威
持有；本闸绝不复算资格，只保证「读得到、对得上、看得见」。

开关：``DATA_QUALIFICATION_GATE=0`` 一键回滚（全放行）。闸门自身异常
fail-open（放行 + 日志）——闸门绝不改变既有失败语义。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

#: 闸门裁决状态（二态：过/阻断——资格五态在消费面另行呈现）。
GATE_PASS = "pass"
GATE_BLOCKED = "blocked"

#: 类型化阻断码（机读；Agent 可据此给用户解释）。
CODE_STALE = "DATA_QUALIFICATION_STALE"
CODE_CORRUPT = "DATA_QUALIFICATION_DESCRIPTOR_CORRUPT"
CODE_MISSING = "DATA_QUALIFICATION_DESCRIPTOR_MISSING"

#: 豁免命名空间（能力投影/哨兵，非数据集——不对它们做资格判定）。
EXEMPT_REF_PREFIXES = ("ref:raster/", "ref:profile-", "ref:redis-unavailable-")

#: 单次闸门扫描的 ref 上限（有界；与 role_binding.MAX_ROLE_BINDING_REFS 同界）。
MAX_GATE_REFS = 16
#: 参数树扫描深度上限（防深结构 DoS——与 GD-09 守卫同纪律）。
MAX_ARG_DEPTH = 6


@dataclass
class DataGateVerdict:
    """闸门裁决（有界、可序列化）。"""

    status: str = GATE_PASS
    code: str = ""
    reason: str = ""
    tool_name: str = ""
    #: ref → 披露（如 descriptor_missing）；放行面的事实披露。
    disclosure: Dict[str, str] = field(default_factory=dict)
    #: ref → 有界数据事实（crs/feature_count/bbox/kind…；模型可见）。
    qualification_context: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.status == GATE_BLOCKED

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "code": self.code[:96],
            "reason": self.reason[:512],
            "disclosure": {k[:64]: v[:64] for k, v in
                           list(self.disclosure.items())[:MAX_GATE_REFS]},
        }


def _iter_ref_strings(node: Any, depth: int = 0) -> Iterator[str]:
    """有界遍历参数树，产出 ``ref:`` 值（去重保序）。"""
    if depth > MAX_ARG_DEPTH:
        return
    if isinstance(node, str):
        if node.startswith("ref:"):
            yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _iter_ref_strings(v, depth + 1)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _iter_ref_strings(v, depth + 1)


def collect_gate_refs(args: Any) -> List[str]:
    """收集本次调用涉及的候选数据 ref（豁免命名空间剔除，≤16）。"""
    seen: List[str] = []
    for ref in _iter_ref_strings(args):
        if ref.startswith(EXEMPT_REF_PREFIXES):
            continue
        if ref not in seen:
            seen.append(ref)
        if len(seen) >= MAX_GATE_REFS:
            break
    return seen


def _refs_capped(args: Any) -> bool:
    """参数树中的候选 ref 是否超过扫描上限（评审 F9：截断必须可观测）。"""
    total = 0
    for ref in _iter_ref_strings(args):
        if ref.startswith(EXEMPT_REF_PREFIXES):
            continue
        total += 1
        if total > MAX_GATE_REFS:
            return True
    return False


def _context_from_descriptor(descriptor: Any) -> Dict[str, Any]:
    """descriptor → 有界数据事实（放行时的模型可见上下文）。"""
    ctx: Dict[str, Any] = {
        "kind": str(getattr(descriptor, "kind", "") or "")[:16],
        "crs": str(getattr(descriptor, "crs", "") or "")[:64],
        "geometry_types": [str(g)[:32] for g in
                           (getattr(descriptor, "geometry_types", None) or [])[:8]],
        "feature_count": getattr(descriptor, "feature_count", None),
        "bbox": getattr(descriptor, "bbox", None),
        "license": str(getattr(descriptor, "license", "") or "")[:64] or None,
        "dimension_provenance": dict(
            getattr(descriptor, "dimension_provenance", None) or {}),
    }
    if isinstance(ctx["bbox"], list):
        try:
            ctx["bbox"] = [round(float(b), 6) for b in ctx["bbox"][:4]]
        except (TypeError, ValueError):
            ctx["bbox"] = None
    return ctx


def gate_enabled() -> bool:
    """kill switch：DATA_QUALIFICATION_GATE=0 → 关闸（一键回滚）。"""
    return os.getenv("DATA_QUALIFICATION_GATE", "1") != "0"


def strict_mode() -> bool:
    """严格模式：无语义记录也阻断（默认关——证据缺席 ≠ 不合格）。"""
    return os.getenv("DATA_QUALIFICATION_GATE_STRICT", "0") == "1"


async def evaluate_dispatch_data_gate(
    session_id: str,
    tool_name: str,
    args: Any,
    *,
    expected_fingerprints: Optional[Dict[str, str]] = None,
) -> DataGateVerdict:
    """裁决本次工具调用的数据证据完整性（fail-open，绝不放大失败面）。

    ``expected_fingerprints``：记录面（durable facts / MapSpec source）
    期望的 descriptor 指纹——与语义记录不一致 = 数据已变、资格过期。
    """
    verdict = DataGateVerdict(tool_name=str(tool_name)[:64])
    if not gate_enabled() or not session_id:
        return verdict
    refs = collect_gate_refs(args)
    if not refs:
        return verdict

    expected = dict(expected_fingerprints or {})
    from app.services.dataset_semantics.store import get_dataset_semantic_store

    store = get_dataset_semantic_store()
    for ref in refs:
        try:
            record = await store.get(session_id, ref)
        except Exception as e:  # noqa: BLE001 — 读面异常按损坏处理
            logger.warning(
                "[data-gate] semantic read raised for %s: %s", ref[:64], e)
            return DataGateVerdict(
                status=GATE_BLOCKED, code=CODE_CORRUPT,
                reason=(f"数据 {ref} 的语义记录读取失败（{e}）——"
                        "分析不可建立在不可验证的数据语义上，请重新上传或"
                        "重新探索该数据后重试。"),
                tool_name=verdict.tool_name,
            )
        if record.ok and record.descriptor is not None:
            want = str(expected.get(ref) or "").strip()
            if want and record.fingerprint and want != record.fingerprint:
                return DataGateVerdict(
                    status=GATE_BLOCKED, code=CODE_STALE,
                    reason=(f"数据 {ref} 的语义指纹与记录面期望不一致"
                            f"（期望 {want[:12]}…，实际 {record.fingerprint[:12]}…）"
                            "——数据内容已变化，既有资格判定已过期，"
                            "请重新确认数据或重新执行资格判定后重试。"),
                    tool_name=verdict.tool_name,
                )
            verdict.qualification_context[ref] = _context_from_descriptor(
                record.descriptor)
        elif record.status in ("corrupt", "unsupported"):
            return DataGateVerdict(
                status=GATE_BLOCKED, code=CODE_CORRUPT,
                reason=(f"数据 {ref} 的语义记录不可读（{record.status}: "
                        f"{record.reason_code}）——请重新上传/物化该数据以"
                        "重建语义身份后重试。"),
                tool_name=verdict.tool_name,
            )
        else:
            # missing：证据缺席 ≠ 数据不合格。默认放行 + 披露；
            # strict 模式升级为阻断（未验证的来源不得进入分析）。
            if strict_mode():
                return DataGateVerdict(
                    status=GATE_BLOCKED, code=CODE_MISSING,
                    reason=(f"数据 {ref} 没有语义身份证（strict 模式："
                            "未验证的数据不得进入分析）——请先通过上传或"
                            "数据探索登记该数据。"),
                    tool_name=verdict.tool_name,
                )
            verdict.disclosure[ref] = "descriptor_missing"
    if _refs_capped(args):
        # 评审 F9：超过扫描上限的 ref 不检查——截断必须披露，不得静默。
        verdict.disclosure["__refs_capped__"] = (
            f">{MAX_GATE_REFS} data refs; beyond cap not checked")
    return verdict


__all__ = [
    "DataGateVerdict",
    "GATE_PASS",
    "GATE_BLOCKED",
    "CODE_STALE",
    "CODE_CORRUPT",
    "CODE_MISSING",
    "collect_gate_refs",
    "evaluate_dispatch_data_gate",
    "gate_enabled",
    "strict_mode",
]
