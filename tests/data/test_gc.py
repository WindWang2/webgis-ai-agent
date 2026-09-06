"""GC Planner 与磁盘孤儿清扫测试（§十四）。"""
import os
import time


from app.lib.artifact_cache import (
    publish_artifact,
    sweep_orphan_disk_artifacts,
)
from app.services.artifact_registry import (
    get_artifact,
    register_artifact,
)
from app.services.data_lifecycle.gc import execute_session_gc, plan_session_gc
from app.services.session_data import session_data_manager


def _mk(path, content=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


class TestDiskSweep:
    def test_sweep_temp_orphans_and_aged(self, tmp_path, monkeypatch):
        # 定向到临时目录（ARTIFACT_DIR 是模块常量 —— monkeypatch 之）
        monkeypatch.setattr("app.lib.artifact_cache.ARTIFACT_DIR", str(tmp_path))
        # 合法条目（fresh）
        _mk(tmp_path / "aaaaaaaaaaaaaaaa.tif", b"tif-a")
        _mk(tmp_path / "aaaaaaaaaaaaaaaa.meta", b"{}")
        # 崩溃临时件（mkstemp 命名不合 <16hex> 规则）
        _mk(tmp_path / "tmpabc123.tif")
        # 半边孤儿
        _mk(tmp_path / "bbbbbbbbbbbbbbbb.tif", b"lonely-tif")
        _mk(tmp_path / "cccccccccccccccc.meta", b"lonely-meta")
        # 超龄条目
        old = time.time() - 40 * 86400
        _mk(tmp_path / "dddddddddddddddd.tif", b"old")
        _mk(tmp_path / "dddddddddddddddd.meta", b"{}")
        os.utime(tmp_path / "dddddddddddddddd.meta", (old, old))

        result = sweep_orphan_disk_artifacts()
        assert result["temp_leftovers"] == 1
        assert result["orphan_tif"] == 1
        assert result["orphan_meta"] == 1
        assert result["aged"] == 1
        # 合法条目幸存
        assert (tmp_path / "aaaaaaaaaaaaaaaa.tif").exists()
        assert (tmp_path / "aaaaaaaaaaaaaaaa.meta").exists()

    def test_publish_then_sweep_keeps_fresh_entry(self, tmp_path, monkeypatch):
        # 工作文件放独立子目录；ARTIFACT_DIR 指向空的缓存目录
        monkeypatch.setattr("app.lib.artifact_cache.ARTIFACT_DIR", str(tmp_path / "cache"))
        work = tmp_path / "work"
        src = _mk(work / "src.tif", b"source")
        key = "eeeeeeeeeeeeeeee"

        def _compute():
            return str(_mk(work / "out.tif", b"out"))

        out = publish_artifact(key, str(src), _compute)
        assert os.path.exists(out)
        result = sweep_orphan_disk_artifacts()
        assert result == {"temp_leftovers": 0, "orphan_tif": 0, "orphan_meta": 0, "aged": 0}
        assert (tmp_path / "cache" / f"{key}.tif").exists()  # 仍存活


class TestSessionGCPlan:
    async def _ledger(self, sid: str):
        # src(valid, live) ← derived(stale 候选) ← grand(expired 候选但被血缘根保留)
        fc = {"type": "FeatureCollection", "features": []}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-derived", producer_tool="t",
            inputs=[ref], metadata={"size_bytes": 1234},
        )
        await register_artifact(
            sid, artifact_id="ref:geojson-grand", producer_tool="t",
            inputs=["ref:geojson-derived"],
        )
        # 手工构造状态：derived → stale（无活引用）；grand → expired
        from app.services.artifact_registry import update_record_metadata

        await update_record_metadata(sid, "ref:geojson-derived", status="stale")
        await update_record_metadata(sid, "ref:geojson-grand", status="expired")
        # superseded 且持久层 persistent → 受保护
        await register_artifact(
            sid, artifact_id="ref:geojson-persisted", producer_tool="t",
            metadata={"persistence_tier": "persistent"},
        )
        await update_record_metadata(sid, "ref:geojson-persisted", status="superseded")
        return ref

    async def test_plan_respects_protections(self):
        sid = "gc-plan"
        src_ref = await self._ledger(sid)
        plan = await plan_session_gc(sid)
        candidates = {c.artifact_id for c in plan.candidates}
        protected = {p.artifact_id: p.reason for p in plan.protected}
        # live（账本 valid 且被 store 探测，但无行/spec 引用 → sweep 语义下是 stale 候选；
        # 但 plan 只把 GC 态记录作候选 —— valid 不入候选）
        assert src_ref not in candidates
        assert "ref:geojson-derived" in candidates
        # grand 是 expired 候选？—— 不是：它是 derived 的血缘根？derived 已非 valid
        # （血缘根保护只对 valid 下游生效）→ grand 应入候选
        assert "ref:geojson-grand" in candidates
        # persistent 层即使 superseded 也受保护
        assert "ref:geojson-persisted" in protected
        assert "persistence_tier=persistent" in protected["ref:geojson-persisted"]

    async def test_lineage_root_retained_for_valid_downstream(self):
        sid = "gc-root"
        fc = {"type": "FeatureCollection", "features": []}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        await register_artifact(
            sid, artifact_id="ref:geojson-child", producer_tool="t", inputs=[ref]
        )
        # 把根打成 stale（例如 sweep 判定无活引用），但下游仍 valid → 保留
        from app.services.artifact_registry import update_record_metadata

        await update_record_metadata(sid, ref, status="stale")
        plan = await plan_session_gc(sid)
        assert ref not in plan.candidate_ids
        protected = {p.artifact_id: p.reason for p in plan.protected}
        assert "retained lineage root" in protected[ref]

    async def test_execute_delegates_to_orphan_collector(self):
        sid = "gc-exec"
        fc = {"type": "FeatureCollection", "features": []}
        ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(sid, artifact_id=ref, producer_tool="t")
        # 孤儿候选必须有真实 store 载荷（registry 删除走 delete_ref）
        orphan_ref = await session_data_manager.store(sid, fc, prefix="geojson")
        await register_artifact(
            sid, artifact_id=orphan_ref, producer_tool="t",
            metadata={"persistence_tier": "session"},
        )
        from app.services.artifact_registry import update_record_metadata

        await update_record_metadata(sid, orphan_ref, status="stale")
        deleted = await execute_session_gc(sid)
        assert orphan_ref in deleted
        rec = await get_artifact(sid, orphan_ref)
        assert rec.status == "expired"  # 删除后置 expired（registry 纪律）

    async def test_plan_empty_ledger_safe(self):
        plan = await plan_session_gc("gc-empty")
        assert plan.candidates == []
        assert plan.to_dict()["candidate_count"] == 0
