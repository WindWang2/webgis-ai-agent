"""Independently testable webgis_product_edit handler and explicit registration."""
from __future__ import annotations
from app.tools.registry import tool

from app.services.gis_harness.tools import (
    ToolRegistry,
    Any,
    Dict,
    List,
    Optional,
    ProductEditArgs,
    logger,
)


async def webgis_product_edit(
    session_id: Optional[str] = None,
    op: str = "",
    target: str = "",
    payload: Optional[Dict[str, Any]] = None,
    reason: str = "",
) -> dict:
    from app.services.gis_harness.product_spec import (
        VIEW_KIND_COMPONENT_FAMILIES,
        apply_product_edit,
        spec_digest,
        spec_from_storage,
        storage_payload,
    )
    from app.services.mapspec_store import mapspec_store
    from app.services.session_plan import load_session_plan

    if not session_id:
        return {"success": False, "message": "Missing session_id"}
    if not op:
        return {"success": False, "message": "Missing op (edit operation)"}

    envelope = await load_session_plan(session_id)
    chapter = envelope.gis_chapter if envelope is not None else None
    stored = (chapter or {}).get("product_spec")
    if not isinstance(stored, dict):
        return {
            "success": False,
            "message": "会话尚无产品 spec —— 先用 webgis_map_intent + "
                       "webgis_map_product 组装产品，再做产品编辑",
        }
    spec = spec_from_storage(stored)
    if spec is None:
        return {
            "success": False,
            "message": "已持久化的 product_spec 无法解析（版本/结构）——"
                       "重新组装产品后再编辑",
        }

    new_spec, errors, affected = apply_product_edit(
        spec, op, target=target, payload=payload, reason=reason)
    if new_spec is None:
        return {
            "success": False,
            "message": "编辑被拒（spec 未变）",
            "errors": [str(e)[:160] for e in errors[:6]],
        }

    # 乐观并发预检（review P1：读-改-写竞态）——落物理面之前重读 envelope，
    # 若基线 digest 已被并发写入者移动 → 拒绝本次编辑（spec 未变），由
    # 模型基于最新产品重试。merge 侧的 CAS 守卫是第二道闸。
    _base_digest = spec_digest(spec)
    _fresh_envelope = await load_session_plan(session_id)
    _fresh_chapter = _fresh_envelope.gis_chapter if _fresh_envelope else None
    _fresh_stored = (_fresh_chapter or {}).get("product_spec")
    if isinstance(_fresh_stored, dict):
        _fresh_digest = str(_fresh_stored.get("digest") or "")
        if _fresh_digest and _fresh_digest != _base_digest:
            return {
                "success": False,
                "message": "产品已被并发更新（digest 不一致）——请基于最新产品重试编辑",
                "current_digest": _fresh_digest,
                "base_digest": _base_digest,
            }

    from app.services.gis_harness.product_edit_commit import commit_product_edit, apply_product_component

    committed_spec = storage_payload(new_spec)
    if not await commit_product_edit(session_id, _base_digest, committed_spec):
        return {"success": False, "code": "PRODUCT_SPEC_CONFLICT",
                "message": "产品已并发更新，编辑未提交；请基于最新产品重试"}
    committed_digest = spec_digest(new_spec)

    # 物理面：组件族承载视图的移除/关闭 → MapSpec 组件通道；数据级变更
    # → unapplied_effects 诚实欠账（不静默重查/重分析）。
    # 归属规则（review P1：禁止跨视图 type 全表误删）：组件必须能归因到
    # 目标视图 —— options.layerId == 视图 layer_hint，或组件 id == 视图
    # component_hint；不可归因（含无 hint 视图）→ 物理欠账披露，不删。
    removed_component_ids: List[str] = []
    patched_component_ids: List[str] = []
    unapplied: List[Dict[str, str]] = []
    spec_before = spec
    _physical_ops = {"remove_view", "toggle_view", "toggle_component"}
    if op in _physical_ops:
        try:
            current = await mapspec_store.get_mapspec(session_id) or {}
            components = list(((current.get("layout") or {}).get("components")) or [])
            # toggle_component 语义（review P1 修复：enabled 缺省 True，
            # 开/关各走各的通道，关闭绝不滑向删除）
            _toggle_disable = op == "toggle_component" and not bool(
                (payload or {}).get("enabled", True))
            _toggle_enable = op == "toggle_component" and bool(
                (payload or {}).get("enabled", True))
            _families: List[str] = []
            _view = None
            if op == "toggle_component":
                _ctype = str((payload or {}).get("component_type") or target)
                _families = [_ctype]
            else:
                _vid = target
                _view = spec_before.view(_vid)
                _kind = str(_view.kind or "") if _view else ""
                _families = list(
                    VIEW_KIND_COMPONENT_FAMILIES.get(_kind, ()))
            for comp in components:
                if not isinstance(comp, dict):
                    continue
                if str(comp.get("type") or "") not in _families:
                    continue
                cid = str(comp.get("id") or "")
                if op in ("remove_view", "toggle_view") and _view is not None:
                    opts = comp.get("options") or {}
                    attributed = (
                        (_view.binding.layer_hint
                         and str(opts.get("layerId") or "") == _view.binding.layer_hint)
                        or (_view.binding.component_hint
                            and cid == _view.binding.component_hint)
                    )
                    if not attributed:
                        unapplied.append({
                            "effect": "component_attribution",
                            "detail": f"component {cid} not attributable to view "
                                      f"{_view.view_id} (no layer/component hint match)",
                        })
                        continue
                if _toggle_disable:
                    if comp.get("enabled") is not False:
                        res = await apply_product_component(
                            session_id, committed_digest, cid, enabled=False)
                        if res.get("success"):
                            patched_component_ids.append(cid)
                        else:
                            unapplied.append({
                                "effect": "component_disable",
                                "detail": f"{cid}: {str(res.get('message') or 'rejected')[:100]}",
                            })
                elif _toggle_enable:
                    if comp.get("enabled") is False:
                        res = await apply_product_component(
                            session_id, committed_digest, cid, enabled=True)
                        if res.get("success"):
                            patched_component_ids.append(cid)
                        else:
                            unapplied.append({
                                "effect": "component_enable",
                                "detail": f"{cid}: {str(res.get('message') or 'rejected')[:100]}",
                            })
                else:
                    res = await apply_product_component(
                        session_id, committed_digest, cid)
                    if res.get("success"):
                        removed_component_ids.append(cid)
                    else:
                        # review P1：局部失败必须披露 —— spec 已提交而
                        # MapSpec 半同步时，欠账账本是唯一对账真相。
                        unapplied.append({
                            "effect": "component_remove",
                            "detail": f"{cid}: {str(res.get('message') or 'rejected')[:100]}",
                        })
        except Exception as exc:  # noqa: BLE001 — 物理面失败 → 欠账披露
            logger.exception("webgis_product_edit component sync failed")
            unapplied.append({
                "effect": "component_sync",
                "detail": str(exc)[:120],
            })
    if op == "set_view_filter":
        unapplied.append({
            "effect": "data_requery",
            "detail": "filter changed on product semantics — refetch/re-analysis "
                      "owed via data tools before the map reflects it",
        })
    if op == "replace_component" and (payload or {}).get("chart_kind"):
        unapplied.append({
            "effect": "chart_regen",
            "detail": f"chart kind -> {payload.get('chart_kind')} — chart data "
                      "regeneration owed via chart pipeline",
        })

    out: Dict[str, Any] = {
        "success": True,
        "spec_id": new_spec.spec_id,
        "revision": new_spec.revision,
        "digest": spec_digest(new_spec),
        "base_spec_digest": _base_digest,
        "op": op,
        "target": target,
        "affected_views": [str(a)[:64] for a in affected[:8]],
        "removed_components": removed_component_ids[:8],
        "disabled_components": patched_component_ids[:8],
        "unapplied_effects": unapplied[:4],
        "product_spec": storage_payload(new_spec),
        "summary": (
            f"产品编辑完成 op={op} target={target or '-'}；受影响视图 "
            f"{len(affected)}，移除组件 {len(removed_component_ids)}，"
            f"待办效果 {len(unapplied)}"
        ),
    }
    final_envelope = await load_session_plan(session_id)
    final_spec = (final_envelope.gis_chapter or {}).get("product_spec") if final_envelope else None
    if not isinstance(final_spec, dict) or final_spec.get("digest") != committed_digest:
        out.update(success=False, code="PRODUCT_SPEC_SUPERSEDED", message="编辑已被后续产品变更取代")
        out.pop("product_spec", None)
    return out



def register_product_edit(registry: ToolRegistry) -> None:
    tool(
        registry,
        tier=2, domains=["report", "statistics"], name="webgis_product_edit",
        capabilities=['thematic_cartography'],
        contract_version=1,
        description=(
            "地图产品语义编辑：改的是产品构成（MapProductSpec 视图图），不是"
            "单个渲染组件——『把统计图去掉』(op=toggle_component,"
            "component_type=chart_panel,enabled=false)、『改成柱状图』"
            "(op=replace_component,target=v-chart,payload.chart_kind=bar)、"
            "『加一个主城区插图』(op=add_view,payload.view={kind:inset,...})、"
            "『只保留耕地』(op=set_view_filter,payload.filter={...})、"
            "『改成汇报 16:9』(op=set_delivery,payload.delivery={targets:"
            "[png,pdf],aspect:16:9})、『删掉某视图』(op=remove_view)、"
            "『改视图标题』(op=set_caption)。"
            "\n语义：编辑先验证后提交，失败 spec 原样；受影响视图重编译披露在"
            " affected_views；数据级 filter 变更不改已落地图层（数据重查是"
            " unapplied_effects 披露的欠账，由数据工具完成）。组件级微调"
            "（换指北针样式/移位置）仍用 webgis_component_update。"
            "\n前置：会话已有产品（先 webgis_map_intent + webgis_map_product）。"
        ),
        args_model=ProductEditArgs,
        side_effect="state_mutation",
        deterministic=False,
        latency_class="fast",
        memory_class="light",
        scale_class="small",
        tags=("产品编辑", "去掉统计图", "改成柱状图", "插图", "16:9", "product edit"),
        output_semantic_type="map_product",
        result_size_policy="inline_small",
        required_context=("map_state", "cartography_state"),
        map_mutations=("component",),
        data_mutations=("session_state",),
        failure_modes=("invalid_args", "missing_data"),
        summary=(
            "地图产品语义编辑：在产品构成（视图图）上做增删改（去视图/换图表"
            "类型/加插图/改过滤/改交付），先验证后提交，受影响视图重编译；数据"
            "级变更只披露欠账不静默重查。组件级样式微调用 webgis_component_update。"
        ),
        examples=(
            "把右边的统计图去掉",
            "改成柱状图，再加一个主城区插图",
        ),
        anti_examples=(
            "把图例移到左下角——那是组件位置微调，用 webgis_component_update",
            "重新查询 2024 年的学校——那是数据获取，用对应数据工具",
        ),
    )(webgis_product_edit)
