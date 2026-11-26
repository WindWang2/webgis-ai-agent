"""WP6 规模测量 —— 20 成员真实生产 + 50 成员 preflight 上限 + 缓存复用。

资源预算（任务书 W6.3）：成员预算 50（freeze plan 契约帽）；20 成员真实
渲染用最小文档 + 低 DPI（96）控制在可测窗口内；产物字节总量有界断言。
性能基准数字以本测试输出为准（本地资源，非配额承诺）。
"""
from __future__ import annotations

import time
import uuid

import pytest

from tests.unit.series_production.test_series_production_executor import (
    _freeze_plan_for,
    _seed_member_session,
)

weasyprint = pytest.importorskip("weasyprint")


class TestScale:
    async def test_fifty_member_preflight_budget(self):
        # preflight：50 成员（契约帽）—— 不渲染，验证计划/缓存键/manifest 容量
        from app.services.series_production.contract import (
            validate_freeze_plan_payload,
        )
        from app.services.series_production.executor import member_cache_key

        members = [
            {"member_id": f"m{i:02d}", "title": f"成员{i:02d}",
             "session_id": f"sess-scale-{i:02d}",
             "mapspec_fingerprint": uuid.uuid4().hex}
            for i in range(50)
        ]
        plan = validate_freeze_plan_payload(_freeze_plan_for(members, formats=("svg",)))
        assert len(plan.members) == 50
        keys = {
            member_cache_key(m.model_dump(), ["svg"], 96)
            for m in plan.members
        }
        assert len(keys) == 50, "成员缓存键必须互不相同（同格式同 dpi 仍按内容区分）"

        from app.services.series_production.manifest import build_series_manifest

        manifest = build_series_manifest(plan, [])
        assert manifest["series"]["member_count"] == 50
        assert manifest["summary"]["all_complete"] is False

        # 超 50 拒绝
        with pytest.raises(ValueError):
            validate_freeze_plan_payload(_freeze_plan_for(
                [dict(m, member_id=f"x{i}") for i, m in enumerate(members)] + [
                    {"member_id": "overflow", "session_id": "s",
                     "mapspec_fingerprint": "f" * 8}],
            ))

    async def test_twenty_members_real_production_and_cache(self, store_root, exports_dir):
        from app.services.series_production.executor import execute_series_production

        started = time.monotonic()
        members = []
        for i in range(20):
            sid, fp, _ = await _seed_member_session(with_print_layout=True)
            members.append({"member_id": f"m{i:02d}", "title": f"区{i:02d}",
                            "session_id": sid, "mapspec_fingerprint": fp})
        seed_seconds = time.monotonic() - started

        plan = _freeze_plan_for(members, formats=("svg",), dpi=96)
        t0 = time.monotonic()
        first = await execute_series_production(plan)
        render_seconds = time.monotonic() - t0

        assert first["summary"]["complete"] == 20, first["summary"]
        assert first["summary"]["failed"] == 0
        assert first["manifest"]["summary"]["all_complete"] is True

        # 产物字节总量有界（20 成员 × SVG；<2MB 总量预期，硬帽 8MB）
        total_bytes = sum(
            a.bytes_size
            for r in first["results"] for a in r.artifacts
        )
        assert total_bytes > 0
        assert total_bytes < 8 * 1024 * 1024, f"产物总量超预算：{total_bytes}"

        # 缓存复用：第二次全命中且显著更快（不把墙钟当正确性 oracle，
        # 只断言复用计数与零新渲染）
        t1 = time.monotonic()
        second = await execute_series_production(plan)
        reuse_seconds = time.monotonic() - t1
        assert second["summary"]["cached_reused"] == 20
        assert second["summary"]["failed"] == 0

        # 资源测量披露（测试输出即记录；软上限防失控）
        print(
            f"[scale] members=20 seed={seed_seconds:.1f}s "
            f"render={render_seconds:.1f}s reuse={reuse_seconds:.1f}s "
            f"total_artifact_bytes={total_bytes}"
        )
        assert render_seconds < 120, f"20 成员渲染超时预算：{render_seconds:.1f}s"
