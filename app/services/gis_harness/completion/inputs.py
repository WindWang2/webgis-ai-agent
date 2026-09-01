"""输入聚合（一次读齐，validators 全部纯函数）— ADR-0081 / ADR-0091。"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional


async def gather_completion_inputs(
    session_id: str,
    chapter: Dict[str, Any],
    *,
    mapspec: Optional[Dict[str, Any]] = None,
    map_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """读取完成度校验所需的全部既有真相（不新建状态）。

    - MapSpec（layers / sources / layout.components / visibility）；
    - bound refs 的 O(1) descriptor（存在性 + feature_count + bbox）；
    - 组合模板的 required/optional 组件契约（复用 composition cardinality，
      不发明第二套 required/optional schema）；
    - render observation + 当前 mutation revision（P9：渲染级校验输入，
      与 revision 读取共用一次 map_state 读，不重复拉全量状态）。
    """
    from app.services.session_data import session_data_manager

    if map_state is None:
        try:
            map_state = await session_data_manager.get_map_state(session_id)
        except Exception:  # noqa: BLE001 — 状态读失败按缺席处理
            map_state = None

    render_observation = None
    mapspec_revision = 0
    if isinstance(map_state, dict):
        from app.services.gis_harness.render_observation import (
            load_render_observation,
        )

        render_observation = await load_render_observation(session_id, map_state)
        try:
            mapspec_revision = int(
                map_state.get("_cartographic_mutation_revision") or 0
            )
        except (TypeError, ValueError):
            mapspec_revision = 0

    if mapspec is None:
        from app.services.mapspec_store import mapspec_store

        mapspec = await mapspec_store.get_mapspec(session_id) or {}

    refs: Dict[str, Optional[dict]] = {}
    pending_refs: List[str] = []

    def _collect(ref: Any) -> None:
        if (
            isinstance(ref, str)
            and ref.startswith("ref:")
            # 磁盘态栅格（ref:raster/*）不在 session store —— 由
            # artifact_lifecycle 的 mtime 巡检负责，这里不强判过期。
            and not ref.startswith("ref:raster/")
            and ref not in refs
        ):
            refs[ref] = None
            pending_refs.append(ref)

    for row in list(chapter.get("data_requirements") or []) + list(
        chapter.get("analysis_steps") or []
    ):
        if not isinstance(row, dict):
            continue
        _collect(row.get("bound_ref"))
    # MapSpec source refs（P1/ADR-0082）：source 指针是第二个绑定面 ——
    # 行 ref 存活不代表 spec source 的 ref 存活（TTL/LRU 按 ref 独立驱逐）。
    raw_sources = mapspec.get("sources")
    if isinstance(raw_sources, dict):
        source_defs = [v for v in raw_sources.values() if isinstance(v, dict)]
    else:
        source_defs = [s for s in (raw_sources or []) if isinstance(s, dict)]
    for src in source_defs:
        for key in ("ref", "ref_id", "image_ref", "imageRef", "result_ref"):
            _collect(src.get(key))

    if pending_refs:
        # 并发取 descriptor（review F-2）：逐个 await 在 Redis 后端是每 ref
        # 一个串行往返 —— 1k 节点 ≈ 1k 次 RTT 且挂在工具回调关键路径上。
        # 三态区分（review 终审 F4）：ok（拿到 descriptor）/ missing（两次
        # 探测都返回 None —— 确认驱逐，→ 过期 finding）/ unknown（持续
        # 异常 —— 存储抖动，**从 refs 移除**：validators 对未知跳过，绝不
        # 把瞬态错误判成过期并持久化假 failed）。
        async def _fetch(ref: str) -> tuple[str, str, Optional[dict]]:
            try:
                desc = await session_data_manager.get_ref_descriptor(session_id, ref)
            except Exception:  # noqa: BLE001
                desc = None
                try:
                    desc = await session_data_manager.get_ref_descriptor(
                        session_id, ref
                    )
                    if desc is not None:
                        return ref, "ok", desc
                    return ref, "missing", None
                except Exception:  # noqa: BLE001
                    return ref, "unknown", None
            if desc is not None:
                return ref, "ok", desc
            # None 可能是驱逐也可能是抖动 —— 复核一次
            try:
                recheck = await session_data_manager.get_ref_descriptor(
                    session_id, ref
                )
                if recheck is not None:
                    return ref, "ok", recheck
                return ref, "missing", None
            except Exception:  # noqa: BLE001
                return ref, "unknown", None

        fetched = await asyncio.gather(*(_fetch(r) for r in pending_refs))
        for ref, state, desc in fetched:
            if state == "ok":
                refs[ref] = desc
            elif state == "missing":
                refs[ref] = None
            else:  # unknown：从 refs 移除（validators 按未知跳过）
                refs.pop(ref, None)

    # required 组件以 composition slot 族语义表达（slot id ≠ 组件类型名：
    # "legend" 槽可由 legend/categorical_legend/continuous_colorbar 任一满足
    # —— 校验/修复按 allowed_component_types 族判定，不发明第二套 schema）。
    required_slots: List[List[str]] = []
    compo_id = str(
        (chapter.get("template_selection") or {}).get("composition_template_id")
        or ""
    )
    if compo_id:
        try:
            from app.lib.cartography.composition_templates import (
                get_composition_template_registry,
            )

            tpl = get_composition_template_registry().get(compo_id)
            if tpl is not None:
                for slot in tpl.component_slots:
                    if slot.cardinality != "required":
                        continue
                    allowed = [str(t) for t in (slot.allowed_component_types or [])]
                    required_slots.append(allowed or [str(slot.id)])
        except Exception:  # noqa: BLE001 — 模板缺失退化为无 required 断言
            required_slots = []
    if not required_slots:
        # 兜底：组合证据缺失时按最小契约断言（title + scale_bar）—— 与
        # composition seeds 一致，避免旧章节误报。
        required_slots = [["title"], ["scale_bar"]]

    # facet contract（语义级 QA 输入）：派生只读，失败退化为 None。
    facet_contract = None
    try:
        from app.services.gis_harness.product_facets import derive_facet_contract

        facet_contract = derive_facet_contract(chapter)
    except Exception:  # noqa: BLE001 — 契约缺席只丢语义级披露
        facet_contract = None

    # artifact records 快照（CRS 契约输入；best-effort，失败 → {}）
    artifact_records: Dict[str, Any] = {}
    try:
        from app.services.artifact_registry import list_artifacts

        records = await list_artifacts(session_id)
        artifact_records = {r.artifact_id: r for r in records}
    except Exception:  # noqa: BLE001 — registry 是增值记录层，不阻断终验
        artifact_records = {}

    return {
        "mapspec": mapspec,
        "descriptors": refs,
        "required_slots": required_slots,
        "render_observation": render_observation,
        "mapspec_revision": mapspec_revision,
        "facet_contract": facet_contract,
        "artifact_records": artifact_records,
    }
