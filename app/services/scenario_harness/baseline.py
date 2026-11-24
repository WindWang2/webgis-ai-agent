"""共享基准绑定（WP2）：候选的公共出发点（一次捕获，多候选引用）。

红线（与包 docstring 同款）：
- 绑定的是**事实**（spec 修订/指纹、数据版本、目标摘要），不是聊天文本；
- 指纹/revision 全部来自父会话权威 MapSpec（``mapspec_store_instance``），
  绝不复制分支或前端运行时状态；
- 数据集锚只收 ref 化数据源（inline 载荷不进锚清单）—— ref 读不到就是
  诚实空串，不造默认 revision（GIS-03 同款纪律）；
- ``goal_digest`` 只在有真实读取入口时取值（mission binding →
  WorkingContextStore）；任何一步不可用 → 诚实空串，不造读取路径。
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from app.services.mapspec.store import mapspec_store_instance
from app.services.scenario_harness.contracts import (
    REGISTRY_BASELINES,
    BaselineDatasetAnchor,
    BaselineMove,
    ScenarioBaseline,
)
from app.services.scenario_harness.store import (
    RegistryError,
    get_registry_entry,
    load_registry,
    mint_id,
    upsert_registry_entry,
    utc_now_iso,
)
from app.services.simulation.whatif.branch_manager import fingerprint_mapspec

#: 数据集锚上限（与 ScenarioBaseline.datasets max_length 同界）。
MAX_DATASET_ANCHORS = 12


async def _map_state(session_id: str, store: Any = None) -> Dict[str, Any]:
    """map_state 快照读（store 注入面：测试/调用方可换后端）。"""
    if store is None:
        from app.services.session_data import session_data_manager as store  # noqa: E702

    state = await store.get_map_state(session_id)
    return state if isinstance(state, dict) else {}


async def _current_revision(session_id: str, store: Any = None) -> int:
    """父会话当前 mutation revision（map_state 权威令牌；坏值落 0）。"""
    state = await _map_state(session_id, store)
    try:
        return int(state.get("_cartographic_mutation_revision", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _dataset_ref_ids(spec: Dict[str, Any]) -> List[str]:
    """spec["sources"] → ref 化数据源清单（source_id 序稳定；≤12）。

    inline/url/dataPath 载荷不是版本化数据集，不进锚清单 —— 锚的本职是
    "数据版本推进可见"，无版本的载荷无漂移可言。
    """
    sources = spec.get("sources")
    if not isinstance(sources, dict):
        return []
    out: List[str] = []
    for source_id in sorted(sources.keys()):
        entry = sources.get(source_id)
        if not isinstance(entry, dict):
            continue
        # ref 键有两个真实落点：直挂 entry.ref(_id)，或 ingestion 双层包装
        # entry.inlineData.ref(_id)（与 extract_layer_payloads 的解包对称）。
        inline = entry.get("inlineData")
        nested = inline if isinstance(inline, dict) else {}
        ref_id = str(
            entry.get("ref_id")
            or entry.get("ref")
            or nested.get("ref_id")
            or nested.get("ref")
            or ""
        )
        if ref_id and ref_id not in out:
            out.append(ref_id)
        if len(out) >= MAX_DATASET_ANCHORS:
            break
    return out[:MAX_DATASET_ANCHORS]


async def _dataset_anchor(
    session_id: str, ref_id: str, store: Any = None
) -> BaselineDatasetAnchor:
    """单数据集锚（content_revision 经公开描述符面读；读不到 = 诚实空）。"""
    revision = ""
    try:
        reader = store
        if reader is None:
            from app.services.session_data import (
                session_data_manager as reader,  # type: ignore[assignment]
            )

        get_desc = getattr(reader, "get_ref_descriptor", None)
        desc = await get_desc(session_id, ref_id) if get_desc else None
        if isinstance(desc, dict):
            revision = str(desc.get("content_revision", "") or "")
    except Exception:  # noqa: BLE001 — 锚读取是观察面，绝不阻断捕获
        revision = ""
    return BaselineDatasetAnchor(ref_id=ref_id, content_revision=revision)


async def _read_goal_digest(session_id: str, store: Any = None) -> str:
    """working basis 的确定性摘要（目标条件：分母/区域/时间窗）。

    读取链（全部真实入口，无一条造出来的路径）：
    session map_state ``_mission_binding`` → ``WorkingContextStore.load`` →
    ``wc.basis`` canonical 指纹。任一步不可用（无绑定/无 DB/加载冲突）
    → 诚实空串：goal_changed 判定对 "" == "" 退化为未变化，不误报。
    """
    state = await _map_state(session_id, store)
    binding = state.get("_mission_binding")
    if not isinstance(binding, dict):
        return ""
    mission_id = str(binding.get("mission_id") or "")
    if not mission_id:
        return ""
    org_id = str(binding.get("org_id") or "")

    def _load() -> Any:
        from app.services.gis_context.store import WorkingContextStore

        return WorkingContextStore().load(mission_id, org_id=org_id)

    try:
        wc = await asyncio.to_thread(_load)
    except Exception:  # noqa: BLE001 — DB 不可用/绑定失效 → 诚实空
        return ""
    if wc is None:
        return ""
    return fingerprint_mapspec(wc.basis.model_dump(mode="json"))


async def capture_baseline(
    session_id: str, *, turn_id: str = "", note: str = "", store: Any = None
) -> ScenarioBaseline:
    """捕获父会话当前权威 spec 为共享基准（一次捕获，多候选引用）。"""
    spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        raise RegistryError(
            "baseline_missing",
            f"父会话 {session_id} 无权威 MapSpec，无基准可捕获（先完成制图初始化）",
        )
    datasets = [
        await _dataset_anchor(session_id, ref_id, store=store)
        for ref_id in _dataset_ref_ids(spec)
    ]
    baseline = ScenarioBaseline(
        baseline_id=mint_id("sb", session_id),
        session_id=session_id,
        mapspec_revision=await _current_revision(session_id, store),
        mapspec_fingerprint=fingerprint_mapspec(spec),
        datasets=datasets,
        goal_digest=await _read_goal_digest(session_id, store),
        selection_digest="",  # 选择集读取入口未接 → 诚实空（不造读取路径）
        captured_at=utc_now_iso(),
        turn_id=str(turn_id or "")[:64],
        note=str(note or "")[:160],
    )
    # 容量 12 由 upsert 权威执行（超限 RegistryError，调用方决定复用/清理）
    await upsert_registry_entry(session_id, REGISTRY_BASELINES, baseline, store=store)
    return baseline


async def latest_baseline(
    session_id: str, *, store: Any = None
) -> Optional[ScenarioBaseline]:
    """最新基准（(captured_at, baseline_id) 最大；无 → None）。"""
    entries = await load_registry(session_id, REGISTRY_BASELINES, store=store)
    if not entries:
        return None
    return max(entries.values(), key=lambda b: (b.captured_at, b.baseline_id))


async def observe_baseline_move(
    session_id: str, baseline_id: str, *, store: Any = None
) -> BaselineMove:
    """基准自捕获以来的漂移观察（确定性比较，逐锚披露）。"""
    baseline = await get_registry_entry(
        session_id, REGISTRY_BASELINES, baseline_id, store=store
    )
    if baseline is None:
        raise RegistryError(
            "baseline_not_found",
            f"基准 {baseline_id!r} 不存在（会话 {session_id}）",
        )
    spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        # 权威 spec 消失是最剧烈的漂移（不是"无法判定就报未动"）
        fingerprint_changed = True
    else:
        fingerprint_changed = (
            fingerprint_mapspec(spec) != baseline.mapspec_fingerprint
        )
    dataset_changes: List[Dict[str, str]] = []
    for anchor in baseline.datasets:
        current = (await _dataset_anchor(session_id, anchor.ref_id, store=store)).content_revision
        if current != anchor.content_revision:
            dataset_changes.append(
                {
                    "ref_id": anchor.ref_id,
                    "from": anchor.content_revision,
                    "to": current,
                }
            )
    goal_changed = (
        await _read_goal_digest(session_id, store) != baseline.goal_digest
    )
    moved = fingerprint_changed or bool(dataset_changes) or goal_changed
    return BaselineMove(
        baseline_id=baseline_id,
        moved=moved,
        revision_from=baseline.mapspec_revision,
        revision_to=await _current_revision(session_id, store),
        fingerprint_changed=fingerprint_changed,
        dataset_changes=dataset_changes,
        goal_changed=goal_changed,
        observed_at=utc_now_iso(),
    )


async def get_or_create_baseline(
    session_id: str, *, turn_id: str = "", store: Any = None
) -> ScenarioBaseline:
    """复用策略：最新基准 revision+指纹与当前一致 → 直接复用（不新增）。"""
    spec = await mapspec_store_instance.get_mapspec(session_id)
    if not isinstance(spec, dict) or not spec:
        raise RegistryError(
            "baseline_missing",
            f"父会话 {session_id} 无权威 MapSpec，无基准可捕获",
        )
    fingerprint = fingerprint_mapspec(spec)
    latest = await latest_baseline(session_id, store=store)
    if (
        latest is not None
        and latest.mapspec_fingerprint == fingerprint
        and latest.mapspec_revision == await _current_revision(session_id, store)
    ):
        return latest
    return await capture_baseline(session_id, turn_id=turn_id, store=store)


__all__ = [
    "capture_baseline",
    "get_or_create_baseline",
    "latest_baseline",
    "observe_baseline_move",
]
