"""图层校验 validator（layers facet）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts import (
    F_ARTIFACT_EXPIRED,
    F_LAYER_HIDDEN,
    F_LAYER_MISSING,
    F_NO_RESULT_LAYER,
    F_SOURCE_MISSING,
    RESULT_LAYER_ROLES,
    R_SHOW_LAYER,
    MapCompletionFinding,
    _spec_layers,
)


def _layer_declared_visible(layer: Dict[str, Any]) -> bool:
    layout = layer.get("layout") or {}
    return layer.get("visible") is not False and layout.get("visibility") != "none"


def validate_layers(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
    descriptors: Optional[Dict[str, Optional[dict]]] = None,
) -> List[MapCompletionFinding]:
    """图层校验：结果层存在、source 在册、可见、source ref 存活。

    ``descriptors``（P1/ADR-0082）：MapSpec source 的 ref 指针也过存活
    校验 —— 行 ref 存活而 source ref 被 TTL/LRU 驱逐时，此前会假
    complete（review C-2）。缺省 None 时退化为旧行为（兼容直调测试）。
    """
    findings: List[MapCompletionFinding] = []
    layers = _spec_layers(mapspec)
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        source_by_id = {
            str(k): v for k, v in raw_sources.items() if isinstance(v, dict)
        }
    else:
        source_by_id = {
            str(s.get("id") or ""): s
            for s in (raw_sources or [])
            if isinstance(s, dict)
        }
    sources = set(source_by_id.keys())
    sources.discard("")
    by_id = {str(ly.get("id") or ""): ly for ly in layers}

    result_ids = [
        str(ly.get("layer_id") or "")
        for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict)
        and ly.get("layer_id")
        and str(ly.get("role") or "") in RESULT_LAYER_ROLES
        and ly.get("enabled") is not False
    ]
    if result_ids:
        for lid in result_ids:
            layer = by_id.get(lid)
            if layer is None:
                findings.append(
                    MapCompletionFinding(
                        code=F_LAYER_MISSING,
                        severity="error",
                        target=lid,
                        detail="planned result layer not present in MapSpec",
                    )
                )
                continue
            src = str(layer.get("source") or "")
            if src and src not in sources:
                findings.append(
                    MapCompletionFinding(
                        code=F_SOURCE_MISSING,
                        severity="error",
                        target=lid,
                        detail=f"layer source '{src[:48]}' not registered in MapSpec sources",
                    )
                )
            elif src and descriptors is not None:
                src_def = source_by_id.get(src) or {}
                # V4：磁盘栅格（ref:raster/*）与 store ref 同面探测（inputs
                # 经 probe_ref 合成 descriptor）—— 过期栅格不再静默跳过。
                src_ref = next(
                    (
                        src_def.get(k)
                        for k in ("ref", "ref_id", "image_ref", "imageRef", "result_ref")
                        if isinstance(src_def.get(k), str)
                        and src_def.get(k).startswith("ref:")
                    ),
                    None,
                )
                if (
                    src_ref
                    and src_ref in descriptors
                    and descriptors[src_ref] is None
                ):
                    findings.append(
                        MapCompletionFinding(
                            code=F_ARTIFACT_EXPIRED,
                            severity="error",
                            target=lid,
                            detail=(
                                f"layer source ref '{src_ref[:48]}' expired from "
                                "session store (TTL/LRU eviction)"
                            ),
                        )
                    )
            if not _layer_declared_visible(layer):
                intent = layer.get("cartographic_intent")
                user_owned = (
                    isinstance(intent, dict)
                    and intent.get("presentation_owner") == "user"
                )
                if user_owned:
                    # user-wins（review B-6）：显隐权威在用户 —— 用户的隐藏
                    # 就是期望状态。降级为 warning、不修复不对抗，否则每个
                    # 触发点都重放一个注定被 owner 守卫拒绝的突变、永续
                    # needs_repair + 重复 toast。
                    findings.append(
                        MapCompletionFinding(
                            code=F_LAYER_HIDDEN,
                            severity="warning",
                            target=lid,
                            detail="result layer hidden by user (user-wins, disclosed only)",
                        )
                    )
                else:
                    findings.append(
                        MapCompletionFinding(
                            code=F_LAYER_HIDDEN,
                            severity="error",
                            target=lid,
                            detail="result layer desired-visibility is none",
                            repair=R_SHOW_LAYER,
                        )
                    )
    elif layers:
        # 无 planned result layer 绑定（旧章节 / 纯展示路径）：退化为
        # “至少一个可见数据层”断言（basemap/label 子层不算 —— 有 source
        # 引用且非 none-position 的数据层）。
        if not any(_layer_declared_visible(ly) for ly in layers):
            findings.append(
                MapCompletionFinding(
                    code=F_NO_RESULT_LAYER,
                    severity="error",
                    target="layers",
                    detail="no visible data layer in final MapSpec",
                )
            )
    else:
        findings.append(
            MapCompletionFinding(
                code=F_NO_RESULT_LAYER,
                severity="error",
                target="layers",
                detail="MapSpec has no layers",
            )
        )
    return findings
