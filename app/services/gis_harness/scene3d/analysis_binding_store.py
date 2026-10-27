"""3D 分析绑定的会话级存取与失效裁决接线（G3D-V2-C / ADR-0276）。

#1829 时代 ``evaluate_staleness`` 是写好但**零生产调用**的纯函数——
``run_3d_analysis`` 只把 ``staleness_binding`` schema 塞进返回值，没有
裁决执行方。本模块把断链接上（不改纯函数语义）：

- 绑定持久化：会话 map_state 的 ``_scene3d_analysis_bindings`` 键
  （有界 FIFO ≤ :data:`MAX_SCENE3D_BINDINGS`，与 Interaction IR ledger
  同一状态面——零第二存储/第二真相）；
- 裁决入口 :func:`evaluate_session_staleness`：读绑定 + 当前
  Interaction IR 投影（``interaction_snapshot_via_store`` 定向读）+ 当前
  MapSpec 的 raster-dem 源集 → 逐条 ``evaluate_staleness``；
- 消费方：``run_3d_analysis``（执行前对账旧绑定 + 成功后登记）、
  ``apply_scene_representation``（场景突变后对账——观察点语义漂移/
  DEM 源消失的 Agent 可观察 ACK）。相机通道（fly_camera/SetViewIntent）
  **不**消费本模块——视域与镜头无关，把相机当分析输入是范畴错误
  （staleness.py 显式拒绝）。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.services.gis_harness.scene3d.staleness import (
    AnalysisBinding,
    evaluate_staleness,
)

logger = logging.getLogger(__name__)

#: map_state 字段名（与 _interaction_ir_ledger 同族：下划线前缀 = 后端
#: 私有键，恢复面不下发前端，agent 消费走本模块投影）。
BINDINGS_KEY = "_scene3d_analysis_bindings"

#: 绑定上限（FIFO——最旧的分析先让位；裁决输出同此界）。
MAX_SCENE3D_BINDINGS = 8


async def load_analysis_bindings(session_id: str) -> List[AnalysisBinding]:
    """读会话绑定（损坏条目剔除并记日志——存储损坏不冒充空分析）。"""
    from app.services.session_data import session_data_manager

    raw = await session_data_manager.get_state_field(session_id, BINDINGS_KEY)
    items = raw if isinstance(raw, list) else []
    out: List[AnalysisBinding] = []
    dropped = 0
    for item in items:
        try:
            out.append(AnalysisBinding.model_validate(item))
        except Exception:  # noqa: BLE001 — 单条损坏不拖垮其余绑定
            dropped += 1
    if dropped:
        logger.warning(
            "[scene3d-bindings] %d corrupt binding(s) dropped for %s",
            dropped, session_id,
        )
    return out


async def register_analysis_binding(
    session_id: str, binding: AnalysisBinding
) -> Dict[str, Any]:
    """登记/刷新一条分析绑定（同 goal+layer+observer 幂等覆盖，FIFO 有界）。

    选择上下文戳记：调用方未显式给 observer_feature_id 时，用登记时刻的
    Interaction IR selection 补全——分析是**响应当前选择**做出的，之后
    同一选择不得把它判旧（换选别的要素才失效）。上下文读取失败按无选择
    处理（fail-open，不阻塞登记）。
    """
    from app.services.gis_harness.interaction_ir.projection import (
        interaction_snapshot_via_store,
    )
    from app.services.session_data import session_data_manager

    if not binding.observer_feature_id:
        try:
            snap = await interaction_snapshot_via_store(
                session_data_manager, session_id
            )
            sel = snap.get("selection") if isinstance(snap.get("selection"), dict) else {}
            sel_feature = str((sel or {}).get("feature_id") or "")
            if sel_feature:
                binding = binding.model_copy(
                    update={"observer_feature_id": sel_feature[:128]}
                )
        except Exception:  # noqa: BLE001 — 上下文缺席按无选择处理
            pass
    bindings = await load_analysis_bindings(session_id)
    kept = [
        b
        for b in bindings
        if not (
            b.analysis_goal == binding.analysis_goal
            and b.result_layer_id == binding.result_layer_id
            and b.observer_fingerprint() == binding.observer_fingerprint()
        )
    ]
    kept.append(binding)
    evicted = max(0, len(kept) - MAX_SCENE3D_BINDINGS)
    if evicted:
        kept = kept[evicted:]
    ok = await session_data_manager.set_map_state_fields(
        session_id, {BINDINGS_KEY: [b.model_dump() for b in kept]}
    )
    return {
        "success": bool(ok),
        "binding_count": len(kept),
        "evicted_oldest": evicted,
    }


def _dem_source_ids_of(mapspec: Optional[Dict[str, Any]]) -> List[str]:
    """当前 spec 的已核实 raster-dem 源 id 集（复用 evidence 单一真相，
    不做第二套探测——避免判定口径漂移）。"""
    from app.services.gis_harness.scene3d.evidence import verify_vertical_evidence

    return verify_vertical_evidence(mapspec).dem_source_ids()


async def evaluate_session_staleness(
    session_id: str,
    *,
    mapspec: Optional[Dict[str, Any]] = None,
    dem_source_ids: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """对账会话内全部 3D 分析绑定的失效状态（只读，无副作用）。

    返回有界投影 ``[{binding, verdict}]``（≤ MAX_SCENE3D_BINDINGS 条，
    verdict 为 StaleVerdict.model_dump）——agent/验收直接可断言；
    interaction 投影读失败按空投影处理（只读面 fail-open，与
    interaction_snapshot_via_store 同纪律）。
    """
    from app.services.gis_harness.interaction_ir.projection import (
        interaction_snapshot_via_store,
    )
    from app.services.session_data import session_data_manager

    bindings = await load_analysis_bindings(session_id)
    if not bindings:
        return []
    snapshot = await interaction_snapshot_via_store(
        session_data_manager, session_id
    )
    # dem 检查只在有权威 spec（或显式源集）时执行——无 spec 上下文的
    # 只读诊断不得把「未知」当「无源」（staleness 纯函数契约：
    # current_dem_source_ids=None = 无法核实 = 跳过该项）。
    if dem_source_ids is None and mapspec is not None:
        dem_source_ids = _dem_source_ids_of(mapspec)
    out: List[Dict[str, Any]] = []
    for b in bindings:
        verdict = evaluate_staleness(
            b,
            interaction_snapshot=snapshot,
            current_dem_source_ids=dem_source_ids,
        )
        out.append(
            {
                "binding": b.model_dump(),
                "verdict": verdict.model_dump(),
            }
        )
    return out


def stale_verdicts_only(
    evaluations: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """只保留 state != fresh 的条目（display_stale/stale——ACK 摘要）。"""
    return [
        e
        for e in evaluations
        if (e.get("verdict") or {}).get("state") != "fresh"
    ]
