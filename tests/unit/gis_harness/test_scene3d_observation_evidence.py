"""G3DV2-B M1：纯 3D 会话观察证据链单测。

覆盖：
1. DTO ``scene3d_assets`` 归一（词表/边界/形状 —— 非法 → None 证据缺席）；
2. envelope ``scene_work`` 投影（纯 3D spec：零 layer_work + scene 工作项
   + model3d data dependency —— 与 corpus s13 场景同语义的直测）。
"""

import pytest

from app.lib.cartography.render_work_envelope import project_render_work_envelope
from app.schemas.chat_schema import CartographicRuntimeObservationRequest


pytestmark = [pytest.mark.unit]


def _request(**overrides) -> CartographicRuntimeObservationRequest:
    payload = {
        "client_generation": 1,
        "mapspec_fingerprint": "carto-sha256:" + "a" * 64,
        "style_loaded": True,
    }
    payload.update(overrides)
    return CartographicRuntimeObservationRequest(**payload)


class TestScene3dAssetsDto:
    def test_valid_entries_pass_through_bounded_projection(self):
        req = _request(scene3d_assets=[
            {"asset_id": "campus", "code": "mounted", "reason": "2tri"},
            {"asset_id": "tower", "code": "context_lost", "reason": "webgl_context_lost"},
            {"asset_id": "ghost", "code": "cancelled"},
        ])
        assert req.scene3d_assets == [
            {"asset_id": "campus", "code": "mounted", "reason": "2tri"},
            {"asset_id": "tower", "code": "context_lost", "reason": "webgl_context_lost"},
            {"asset_id": "ghost", "code": "cancelled"},
        ]

    def test_extra_keys_dropped_and_reason_bounded(self):
        req = _request(scene3d_assets=[
            {"asset_id": "a", "code": "degraded", "reason": "x" * 500, "at": 123},
        ])
        assert req.scene3d_assets == [
            {"asset_id": "a", "code": "degraded", "reason": "x" * 120},
        ]

    def test_unknown_code_entry_dropped_not_422(self):
        req = _request(scene3d_assets=[
            {"asset_id": "a", "code": "exploded"},  # 词表外
            {"asset_id": "b", "code": "mounted"},
        ])
        assert req.scene3d_assets == [{"asset_id": "b", "code": "mounted"}]

    def test_all_invalid_or_oversize_becomes_absent(self):
        # 全非法 → None（证据缺席 ≠ 空集）
        req = _request(scene3d_assets=[{"asset_id": "a", "code": "nope"}])
        assert req.scene3d_assets is None
        # 超 16 条截断（前端环投影同值）
        req16 = _request(scene3d_assets=[
            {"asset_id": f"a{i}", "code": "mounted"} for i in range(20)
        ])
        assert req16.scene3d_assets is not None
        assert len(req16.scene3d_assets) == 16

    def test_non_list_payload_absent(self):
        req = _request(scene3d_assets={"asset_id": "a"})
        assert req.scene3d_assets is None


class TestSceneWorkProjection:
    """纯 3D spec → scene_work（corpus s13 的直测镜像）。"""

    SPEC = {
        "version": "1.7",
        "sources": {
            "model-a": {
                "type": "model3d", "format": "glb",
                "url": "https://assets.test/campus.glb", "content_revision": 2,
            },
        },
        "layers": [],
        "scene": {
            "mode": "3d",
            "assets": [
                {"id": "campus", "source": "model-a", "visible": True},
                {"id": "tower", "source": "model-a", "visible": False},
                {"id": "dangling", "source": "missing-src", "visible": True},
            ],
        },
        "layout": {"components": []},
    }

    def test_pure_3d_scene_work_and_model3d_dependency(self):
        env = project_render_work_envelope(
            self.SPEC, session_id="sess-X", revision=5,
            fingerprint="carto-sha256:" + "b" * 64,
        )
        assert env.layer_work == ()
        assert [d.source_id for d in env.data_dependencies] == ["model-a"]
        assert env.data_dependencies[0].kind == "model3d"
        assert env.data_dependencies[0].revision == "2"
        by_asset = {s.asset_id: s for s in env.scene_work}
        assert set(by_asset) == {"campus", "tower", "dangling"}
        assert by_asset["campus"].format == "glb"
        assert by_asset["campus"].visible is True
        assert by_asset["tower"].visible is False
        # 悬空源 → format=unknown 披露性保留（不虚构）
        assert by_asset["dangling"].format == "unknown"
        assert by_asset["dangling"].revision is None
        # scene_work 进入 work 身份指纹
        assert env.work_fingerprint

    def test_scene_work_changes_work_fingerprint(self):
        base = project_render_work_envelope(
            self.SPEC, session_id="sess-X", revision=5,
            fingerprint="carto-sha256:" + "b" * 64,
        )
        spec_no_scene = dict(self.SPEC)
        spec_no_scene = {**self.SPEC, "scene": {"mode": "3d", "assets": []}}
        other = project_render_work_envelope(
            spec_no_scene, session_id="sess-X", revision=5,
            fingerprint="carto-sha256:" + "b" * 64,
        )
        assert base.work_fingerprint != other.work_fingerprint

    def test_scene_free_spec_yields_empty_scene_work(self):
        env = project_render_work_envelope(
            {"version": "1.5", "sources": {}, "layers": []},
            session_id="sess-X", revision=1, fingerprint="f" * 16,
        )
        assert env.scene_work == ()
        assert "scene_work" in env.to_dict()
