"""RenderWorkEnvelope 会话供给测试（Goal 03，ADR-0224）。

锁定：fail-open（坏 state → None 绝不抛）、缓存身份键（同 (fp, rev, seq)
命中 / 变化失效）、last-emitted 记忆驱动 invalidation hints、
`project_envelope_from_state` 的 revision/seq 提取与防御。
"""
from __future__ import annotations

import pytest

from app.services import render_work_supply as supply


@pytest.fixture(autouse=True)
def _clean_cache():
    supply.reset_render_work_cache_for_tests()
    yield
    supply.reset_render_work_cache_for_tests()


def _state(mapspec, revision=5, seq=11):
    return {
        "mapspec": mapspec,
        "_cartographic_mutation_revision": revision,
        "_cartographic_observation": {"sequence": seq},
    }


_SPEC_V1 = {
    "version": "1.5",
    "sources": {"s1": {"type": "geojson", "content_revision": 2}},
    "layers": [{"id": "l1", "type": "circle", "source": "s1"}],
}


class TestProjectFromState:
    def test_projects_with_previous_memory(self):
        env1 = supply.project_envelope_from_state(
            _state(_SPEC_V1), "sess-X", fingerprint="fp1"
        )
        assert env1 is not None and env1.invalidation.style_only is None
        # 数据 revision 前进的下一代 → data_changed 命中（previous 来自
        # last-emitted 记忆）。
        spec2 = dict(_SPEC_V1)
        spec2["sources"] = {"s1": {"type": "geojson", "content_revision": 3}}
        env2 = supply.project_envelope_from_state(
            _state(spec2, revision=6, seq=12), "sess-X", fingerprint="fp2"
        )
        assert env2.invalidation.data_changed == ("s1",)
        assert env2.invalidation.style_only is False

    def test_session_isolation_of_memory(self):
        supply.project_envelope_from_state(
            _state(_SPEC_V1), "sess-A", fingerprint="fp1"
        )
        spec2 = dict(_SPEC_V1)
        spec2["sources"] = {"s1": {"type": "geojson", "content_revision": 3}}
        # 不同会话：无 previous 记忆 → hints unknown（不串会话状态）。
        env = supply.project_envelope_from_state(
            _state(spec2), "sess-B", fingerprint="fp2"
        )
        assert env.invalidation.style_only is None

    def test_failopen_on_garbage(self):
        assert supply.project_envelope_from_state({"mapspec": 42}, "s", fingerprint="f") is None
        assert supply.project_envelope_from_state({}, "s", fingerprint="f") is None
        assert supply.project_envelope_from_state({"mapspec": None}, "s", fingerprint="f") is None

    def test_bad_revision_defensive(self):
        env = supply.project_envelope_from_state(
            {
                "mapspec": _SPEC_V1,
                "_cartographic_mutation_revision": "not-a-number",
                "_cartographic_observation": "garbage",
            },
            "sess-Y",
            fingerprint="fp",
        )
        assert env is not None
        assert env.mapspec_revision == 0
        assert env.observation_target_seq == 0

    def test_no_mutation_no_memory_rewrite(self):
        """同身份键重投影：envelope 等价且不推进 previous 记忆。"""
        st = _state(_SPEC_V1)
        env1 = supply.project_envelope_from_state(st, "sess-Z", fingerprint="fp1")
        env2 = supply.project_envelope_from_state(st, "sess-Z", fingerprint="fp1")
        assert env1 == env2
        # 再投影下一代（style-only），记忆仍是第一代 → style_only=True。
        spec2 = dict(_SPEC_V1)
        spec2["layers"] = [
            {"id": "l1", "type": "circle", "source": "s1",
             "paint": {"circle-color": "#123456"}}
        ]
        env3 = supply.project_envelope_from_state(
            _state(spec2, revision=6), "sess-Z", fingerprint="fp2"
        )
        assert env3.invalidation.style_only is True


class TestChronologyGuard:
    """Review P2-1：last-emitted 记忆单调 —— 迟到旧代不倒拨 previous 基线、
    不回退缓存。"""

    def test_late_old_projection_degrades_to_unknown(self):
        supply.project_envelope_from_state(
            _state(_SPEC_V1, revision=6, seq=20), "sess-L", fingerprint="fp-new"
        )
        # 迟到的旧代（revision 5）—— hints 必须无前代可比（不反向），且
        # 缓存不被回退。
        late = supply.project_envelope_from_state(
            _state(_SPEC_V1, revision=5, seq=10), "sess-L", fingerprint="fp-old"
        )
        assert late.invalidation.style_only is None
        fresh = supply.get_latest_for_tests("sess-L")
        assert fresh[1] == 6  # (fp, revision, seq) —— 仍是新代

    def test_newer_projection_advances_memory(self):
        supply.project_envelope_from_state(
            _state(_SPEC_V1, revision=5), "sess-L", fingerprint="fp1"
        )
        spec2 = dict(_SPEC_V1)
        spec2["sources"] = {"s1": {"type": "geojson", "content_revision": 3}}
        env = supply.project_envelope_from_state(
            _state(spec2, revision=6), "sess-L", fingerprint="fp2"
        )
        assert env.invalidation.data_changed == ("s1",)


class TestExpectedWorkFingerprint:
    """服务端 work echo 对账的期望指纹重算（纯投影，无缓存副作用）。"""

    def test_matches_client_projection(self):
        from app.lib.cartography.render_work_envelope import (
            project_render_work_envelope,
        )

        env = project_render_work_envelope(
            _SPEC_V1, session_id="sess-M", revision=4, fingerprint="fp-z",
            observation_seq=31,
        )
        assert supply.expected_work_fingerprint(
            _SPEC_V1, session_id="sess-M", revision=4, fingerprint="fp-z",
        ) == env.work_fingerprint

    def test_garbage_mapspec_none(self):
        assert supply.expected_work_fingerprint(
            None, session_id="s", revision=1, fingerprint="f",
        ) is None


class TestAsyncSupply:
    @pytest.mark.asyncio
    async def test_failopen_when_state_missing(self, monkeypatch):
        class _BrokenManager:
            async def get_map_state(self, sid):
                raise RuntimeError("store down")

        monkeypatch.setattr(
            "app.services.session_data.session_data_manager", _BrokenManager()
        )
        assert await supply.get_render_work_envelope("sess-E") is None

    @pytest.mark.asyncio
    async def test_deleted_session_none(self, monkeypatch):
        class _Manager:
            async def get_map_state(self, sid):
                return {"_cartographic_deleted": True}

        monkeypatch.setattr(
            "app.services.session_data.session_data_manager", _Manager()
        )
        assert await supply.get_render_work_envelope("sess-E") is None

    @pytest.mark.asyncio
    async def test_empty_session_none(self):
        assert await supply.get_render_work_envelope("") is None
        assert await supply.get_render_work_envelope(None) is None


if __name__ == "__main__":
    pytest.main([__file__])
