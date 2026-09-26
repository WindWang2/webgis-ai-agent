"""视觉 finding → 自愈缺陷翻译桥 + 修复提案存储（F15/ADR-0214 决策四）。

**单一真相纪律**：taxonomy → HEALABLE 类别的翻译只构造 healer 可分类的
鸭子 critique（legacy 维度 + 证据标记词），分类/定位一律交回
``visual_healer.normalize_visual_report``（含「未定位诚实跳过」纪律）——
本模块不建第二分类器、不扩 HEALABLE 词表。

提案（plan 零突变）：``map_state["_visual_repair_proposals"]`` ≤4 FIFO；
apply 走 ``MapSpecLifecycleEngine.apply_visual_heal_patch``（锁/CAS/
revision 单调/幂等回放全部复用），收敛耗尽以 typed 回执诚实硬停。
"""
from __future__ import annotations

import hashlib
import logging
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence


logger = logging.getLogger(__name__)

PROPOSALS_KEY = "_visual_repair_proposals"
_MAX_PROPOSALS = 4

#: taxonomy → healer 可分类的鸭子形状（legacy 维度 + 证据标记词）。
#: 分类关键词与 ``visual_healer._DIMENSION_KEYWORD_RULES`` 同源（漂移由
#: test_visual_repair_endpoint 互锁）；映射外的 taxonomy 类 = 不可自愈，
#: plan 面诚实 skip（``unmapped_category``）。
_TAXO_TO_HEAL_SHAPE: Dict[str, Dict[str, str]] = {
    "label_collision": {
        "dimension": "information_density", "marker": "label overlap 重叠",
    },
    "contrast": {
        "dimension": "color_discriminability", "marker": "low contrast 对比不足",
    },
    "overlap": {
        "dimension": "readability", "marker": "occluded 遮挡 layer order",
    },
}

_SKIPPED_UNMAPPED = "unmapped_category"


def visual_findings_to_defects(
    visual_findings: Sequence[Any],
    *,
    known_layer_ids: Sequence[str] = (),
    finding_ids: Optional[Sequence[str]] = (),
) -> Dict[str, Any]:
    """存储态 visual findings → healer 缺陷列表 + 诚实 skip 面。

    ``finding_ids`` 非空时只翻译命中 id 的条目（空串 id 的条目在过滤打开
    时一并跳过——id 缺席的条目不可被定向修复）。
    返回 ``{"defects": [...], "skipped": [...], "finding_ids": [...]}``。
    """
    from app.services.mapspec.visual_healer import normalize_visual_report

    selected = list(visual_findings or [])[:12]
    if finding_ids:
        wanted = {str(f)[:96] for f in finding_ids}
        selected = [
            f for f in selected
            if str(_finding_id_of(f)) in wanted
        ]
    critiques: List[Any] = []
    skipped: List[Dict[str, str]] = []
    used_ids: List[str] = []
    for finding in selected:
        fid = str(_finding_id_of(finding))
        taxonomy = _taxonomy_of_finding(finding)
        shape = _TAXO_TO_HEAL_SHAPE.get(taxonomy)
        if shape is None:
            skipped.append({"finding_id": fid, "reason": _SKIPPED_UNMAPPED,
                            "category": taxonomy or "unknown"})
            continue
        evidence = str(_field_of(finding, "evidence") or "")[:200]
        suggestion = f"{shape['marker']}: {evidence}"
        critiques.append(SimpleNamespace(
            dimension=shape["dimension"],
            severity=str(_field_of(finding, "severity") or "warning"),
            suggestion=suggestion,
            evidence=evidence,
            layer_ids=None,           # 结构化缺席 → healer 文本定位纪律
        ))
        used_ids.append(fid)
    defects = normalize_visual_report(
        SimpleNamespace(critiques=critiques),
        known_layer_ids=known_layer_ids,
    ) if critiques else []
    return {"defects": defects, "skipped": skipped, "finding_ids": used_ids}


def _finding_id_of(finding: Any) -> str:
    return str(_field_of(finding, "finding_id") or "")


def _taxonomy_of_finding(finding: Any) -> str:
    """存储态 finding（dict 或对象）→ taxonomy 类。"""
    code = str(_field_of(finding, "code") or "")
    if code.startswith("visual_"):
        from app.services.gis_harness.visual_observation.taxonomy import (
            VISUAL_TAXONOMY,
        )

        short = code[len("visual_"):]
        if short in VISUAL_TAXONOMY:
            return short
    evidence = str(_field_of(finding, "evidence") or "")
    from app.services.gis_harness.visual_observation.taxonomy import (
        normalize_to_taxonomy,
    )

    return normalize_to_taxonomy("", evidence=evidence)


def _field_of(finding: Any, name: str) -> Any:
    if isinstance(finding, dict):
        return finding.get(name)
    return getattr(finding, name, None)


# ── 提案存储（≤4 FIFO；apply 依 proposal_id 幂等重放）──────────────────────

def build_proposal_id(defects: Sequence[Any], base_revision: int) -> str:
    from app.services.mapspec.visual_healer import defect_fingerprint

    digest = defect_fingerprint(tuple(defects))
    return f"vrepair-{hashlib.sha256(digest.encode()).hexdigest()[:12]}" \
           f"-{int(base_revision)}"


def defect_payload(defects: Sequence[Any]) -> List[Dict[str, Any]]:
    from app.services.mapspec.visual_healer import dataclass_dict

    return [dataclass_dict(d) for d in defects]


async def save_proposal(session_id: str, proposal: Dict[str, Any]) -> bool:
    from app.services.session_data import session_data_manager

    try:
        raw = await session_data_manager.get_map_state(session_id)
        proposals = (raw or {}).get(PROPOSALS_KEY)
        entries = [p for p in proposals if isinstance(p, dict)] \
            if isinstance(proposals, list) else []
        entries = [
            p for p in entries
            if str(p.get("proposal_id") or "") != str(proposal.get("proposal_id") or "")
        ]
        entries.append(proposal)
        while len(entries) > _MAX_PROPOSALS:
            entries.pop(0)
        return bool(await session_data_manager.set_map_state(
            session_id, PROPOSALS_KEY, entries))
    except Exception:  # noqa: BLE001 — 提案存储失败 = plan 失败（typed 上抛面）
        logger.warning("[VisualRepair] save proposal failed", exc_info=True)
        return False


async def load_proposal(
    session_id: str, proposal_id: str
) -> Optional[Dict[str, Any]]:
    from app.services.session_data import session_data_manager

    try:
        raw = await session_data_manager.get_map_state(session_id)
        proposals = (raw or {}).get(PROPOSALS_KEY)
        if not isinstance(proposals, list):
            return None
        for p in proposals:
            if isinstance(p, dict) and str(
                    p.get("proposal_id") or "") == str(proposal_id or ""):
                return p
        return None
    except Exception:  # noqa: BLE001 — 诚实缺席
        return None


def defects_from_proposal(proposal: Dict[str, Any]) -> List[Any]:
    """提案 dict → VisualCritiqueItem 序列（形状漂移诚实跳过）。"""
    from app.services.mapspec.visual_healer import VisualCritiqueItem

    out: List[Any] = []
    for raw in (proposal.get("defects") or []):
        if isinstance(raw, dict):
            try:
                out.append(VisualCritiqueItem(**raw))
            except TypeError:
                continue
    return out


__all__ = [
    "PROPOSALS_KEY",
    "visual_findings_to_defects",
    "build_proposal_id",
    "defect_payload",
    "save_proposal",
    "load_proposal",
    "defects_from_proposal",
]
