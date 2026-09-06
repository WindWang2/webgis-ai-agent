"""Session Lineage Query —— 统一血缘查询测试（§十）。"""

from app.services.artifact_registry import register_artifact
from app.services.data_catalog.lineage_query import (
    SessionLineageQuery,
    session_lineage,
)


async def _build(session_id: str):
    """目标拓扑（§十 ASCII 图同构）：

    src ──► reproj ──┬──► agg ──► stats
                     └──► kde
    另有孤立链 iso（不与主线连通），及替换链 v1 → v2。
    """
    await register_artifact(session_id, artifact_id="ref:src", producer_tool="upload")
    await register_artifact(
        session_id, artifact_id="ref:reproj", producer_tool="reproject",
        inputs=["ref:src"],
    )
    await register_artifact(
        session_id, artifact_id="ref:agg", producer_tool="aggregate",
        inputs=["ref:reproj"],
    )
    await register_artifact(
        session_id, artifact_id="ref:stats", producer_tool="zonal_stats",
        inputs=["ref:agg"],
    )
    await register_artifact(
        session_id, artifact_id="ref:kde", producer_tool="kde",
        inputs=["ref:reproj"],
    )
    await register_artifact(session_id, artifact_id="ref:iso", producer_tool="tool_x")
    await register_artifact(
        session_id, artifact_id="ref:iso2", producer_tool="tool_y",
        inputs=["ref:iso"],
    )
    # 替换链：同 capability 两次生产 → v1 superseded，v2.replaces = v1
    await register_artifact(
        session_id, artifact_id="ref:kde-v1", producer_capability="kde", producer_tool="kde"
    )
    await register_artifact(
        session_id, artifact_id="ref:kde-v2", producer_capability="kde", producer_tool="kde"
    )


class TestSessionLineage:
    async def test_parents_children(self):
        sid = "lin-basic"
        await _build(sid)
        records = await _records(sid)
        q = SessionLineageQuery(records)
        assert q.parents("ref:agg") == ["ref:reproj"]
        assert set(q.children("ref:reproj")) == {"ref:agg", "ref:kde"}

    async def test_roots(self):
        sid = "lin-roots"
        await _build(sid)
        q = SessionLineageQuery(await _records(sid))
        roots = q.roots()
        assert "ref:src" in roots and "ref:iso" in roots and "ref:kde-v1" in roots
        assert "ref:agg" not in roots

    async def test_upstream_closure(self):
        sid = "lin-up"
        await _build(sid)
        q = SessionLineageQuery(await _records(sid))
        assert set(q.upstream("ref:stats")) == {"ref:agg", "ref:reproj", "ref:src"}
        assert q.upstream("ref:src") == []

    async def test_downstream_closure(self):
        sid = "lin-down"
        await _build(sid)
        q = SessionLineageQuery(await _records(sid))
        assert set(q.downstream("ref:src")) == {"ref:reproj", "ref:agg", "ref:kde", "ref:stats"}
        assert q.downstream("ref:stats") == []

    async def test_siblings(self):
        sid = "lin-sib"
        await _build(sid)
        q = SessionLineageQuery(await _records(sid))
        assert set(q.siblings("ref:agg")) == {"ref:kde"}

    async def test_view_shape_and_bounds(self):
        sid = "lin-view"
        await _build(sid)
        view = await session_lineage(sid, "ref:stats")
        assert view is not None
        assert view.scope == "session"
        node_ids = {n.id if hasattr(n, "id") else n.artifact_id for n in view.nodes}
        assert {"ref:stats", "ref:agg", "ref:reproj", "ref:src"} <= node_ids
        # 边方向：from=parent → to=child
        edge_pairs = {(e.from_id, e.to_id) for e in view.edges}
        assert ("ref:agg", "ref:stats") in edge_pairs
        # 深度闸：depth=1 只含直接父母
        shallow = SessionLineageQuery(await _records(sid)).view("ref:stats", max_depth=1)
        shallow_ids = {n.artifact_id for n in shallow.nodes}
        assert "ref:src" not in shallow_ids
        assert shallow.truncated  # 上游被深度闸截断 → 显式声明

    async def test_view_node_projection_from_contract(self):
        sid = "lin-proj"
        await register_artifact(
            sid, artifact_id="ref:tbl", producer_tool="aggregate",
            artifact_type="stats_table",
        )
        view = await session_lineage(sid, "ref:tbl")
        node = view.nodes[0]
        assert node.artifact_type == "table"        # 粗类（契约投影）
        assert node.artifact_subtype == "stats_table"
        assert node.lifecycle == "ready"

    async def test_replacement_edge_in_view(self):
        sid = "lin-repl"
        await _build(sid)
        view = await session_lineage(sid, "ref:kde-v2")
        repl = [e for e in view.edges if e.kind == "replacement"]
        assert repl and repl[0].from_id == "ref:kde-v1"

    async def test_missing_artifact_returns_none(self):
        assert await session_lineage("lin-miss", "ref:ghost") is None

    async def test_cycle_safe(self):
        sid = "lin-cycle"
        # 手工构造环：a→b→a（registry 不阻止自由 inputs 注入的历史数据）
        await register_artifact(
            sid, artifact_id="ref:a", producer_tool="t", inputs=["ref:b"]
        )
        await register_artifact(
            sid, artifact_id="ref:b", producer_tool="t", inputs=["ref:a"]
        )
        q = SessionLineageQuery(await _records(sid))
        assert q.upstream("ref:a") != []
        view = q.view("ref:a", max_depth=8)  # 不应死循环/爆栈
        assert len(view.nodes) <= 3

    async def test_summary_bounded(self):
        sid = "lin-sum"
        await _build(sid)
        view = await session_lineage(sid, "ref:src")
        s = view.to_summary()
        assert s["node_count"] == len(view.nodes)
        assert len(s["nodes"]) <= 32


async def _records(session_id: str) -> dict:
    from app.services.artifact_registry import list_artifacts

    return {r.artifact_id: r for r in await list_artifacts(session_id)}
