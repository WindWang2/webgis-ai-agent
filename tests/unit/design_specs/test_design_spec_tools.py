"""WP1 工具面测试 —— registry 注册 + 真实 dispatch（不经 mock 的生产入口）。

身份面：``use_origin(JobOrigin(...))`` 等价生产 agent 流（owner 闸与
creator_id 来源与 atlas 工具测试同款）。
"""
from __future__ import annotations

import pytest

from app.core.database import Base, Engine, SessionLocal

pytestmark = pytest.mark.unit


def _payload(name="工具规范", **kw) -> dict:
    base = {
        "spec_version": "design_spec.v1",
        "name": name,
        "color": {"theme_id": "cartographic.light_interactive"},
        "classification": {"default_method": "quantiles", "shared_required": True},
    }
    base.update(kw)
    return base


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    r = ToolRegistry()
    init_tools(r)
    return r


@pytest.fixture(autouse=True)
def _setup():
    Base.metadata.create_all(bind=Engine)
    yield
    with SessionLocal() as db:
        from app.models.design_spec import CartographyDesignSpec, CartographyDesignSpecVersion

        db.query(CartographyDesignSpecVersion).delete()
        db.query(CartographyDesignSpec).delete()
        db.commit()


def _owner_ctx():
    from app.services.jobs.context import JobOrigin, use_origin

    return use_origin(JobOrigin(
        session_id="sess-dspec-tools", owner_id="dspec-tool-user"))


class TestRegistered:
    def test_five_tools_registered(self, registry):
        schemas = {s.get("function", {}).get("name") for s in registry.get_schemas()}
        assert {
            "webgis_design_spec_list",
            "webgis_design_spec_inspect",
            "webgis_design_spec_preview",
            "webgis_design_spec_create",
            "webgis_design_spec_version",
        } <= schemas


class TestDispatch:
    async def test_create_then_list_then_inspect(self, registry):
        with _owner_ctx():
            created = await registry.dispatch(
                "webgis_design_spec_create", {"payload": _payload(name="调度规范")}
            )
            assert created["success"], created
            listing = await registry.dispatch("webgis_design_spec_list", {"limit": 5})
            assert listing["success"]
            assert any(i["name"] == "调度规范" for i in listing["items"])

            spec_id = created["spec"]["id"]
            inspected = await registry.dispatch(
                "webgis_design_spec_inspect", {"spec_id": spec_id}
            )
            assert inspected["success"]
            assert inspected["payload"]["name"] == "调度规范"
            assert inspected["reference_validation"]["ok"]

            preview = await registry.dispatch(
                "webgis_design_spec_preview", {"spec_id": spec_id}
            )
            assert preview["success"]
            assert preview["spec"]["id"] == spec_id
            assert "effective_payload" not in preview  # 有界：payload 由 inspect 携带

    async def test_anonymous_create_rejected(self, registry):
        result = await registry.dispatch(
            "webgis_design_spec_create", {"payload": _payload(name="匿名规范")}
        )
        assert not result["success"]
        assert result["code"] == "DESIGN_SPEC_FORBIDDEN"

    async def test_invalid_payload_returns_violations(self, registry):
        with _owner_ctx():
            bad = _payload(name="违规规范")
            bad["no_such_key"] = {"stops": [1, 2, 3]}
            result = await registry.dispatch(
                "webgis_design_spec_create", {"payload": bad}
            )
            assert not result["success"]
            assert result["code"] == "DESIGN_SPEC_INVALID"
            assert result["violations"]

    async def test_version_action_lifecycle(self, registry):
        with _owner_ctx():
            created = await registry.dispatch(
                "webgis_design_spec_create", {"payload": _payload(name="生命周期规范")}
            )
            spec_id = created["spec"]["id"]
            v2 = await registry.dispatch(
                "webgis_design_spec_version",
                {
                    "spec_id": spec_id,
                    "action": "create_version",
                    "payload": _payload(name="生命周期规范", description="v2"),
                },
            )
            assert v2["success"] and v2["version"] == 2
            pub = await registry.dispatch(
                "webgis_design_spec_version", {"spec_id": spec_id, "action": "publish"}
            )
            assert pub["success"] and pub["status"] == "published"
            dep = await registry.dispatch(
                "webgis_design_spec_version",
                {"spec_id": spec_id, "action": "deprecate_version", "note": "old"},
            )
            assert dep["success"] and dep["deprecated"] is True
            ret = await registry.dispatch(
                "webgis_design_spec_version",
                {"spec_id": spec_id, "action": "retire", "note": "done"},
            )
            assert ret["success"] and ret["status"] == "retired"

    async def test_unknown_action_rejected_with_hint(self, registry):
        with _owner_ctx():
            created = await registry.dispatch(
                "webgis_design_spec_create", {"payload": _payload(name="未知动作规范")}
            )
            result = await registry.dispatch(
                "webgis_design_spec_version",
                {"spec_id": created["spec"]["id"], "action": "explode"},
            )
            assert not result["success"]
            assert result["code"] == "invalid_args"

    async def test_not_found_has_hint(self, registry):
        with _owner_ctx():
            result = await registry.dispatch(
                "webgis_design_spec_inspect", {"spec_id": "missing-spec"}
            )
            assert not result["success"]
            assert result["code"] == "DESIGN_SPEC_NOT_FOUND"
            assert result["correction_hint"]


class TestMapDesignToolsRegistered:
    def test_wp2_tools_registered(self, registry):
        schemas = {s.get("function", {}).get("name") for s in registry.get_schemas()}
        assert {
            "webgis_map_design_extract",
            "webgis_map_design_recommend",
            "webgis_map_design_check_bindings",
            "webgis_map_design_apply",
        } <= schemas

    async def test_recommend_dispatch_with_denominator_gate(self, registry):
        with _owner_ctx():
            result = await registry.dispatch(
                "webgis_map_design_recommend",
                {"facts": {"purposes": ["distribution"], "geometry_kind": "point",
                           "statistic": "density", "denominator": {"available": False}}},
            )
            assert result["recommender_version"]
            assert any(c["code"] == "DENOMINATOR_MISSING" for c in result["clarifications"])

    async def test_apply_requires_owned_session(self, registry):
        # #2156 fail-closed 语义：无 runtime 绑定时，LLM 提供的 session_id
        # 一律拒绝（不能点名他人会话）
        with _owner_ctx():
            result = await registry.dispatch(
                "webgis_map_design_apply",
                {"session_id": "not-my-session", "spec_id": "any"},
            )
            assert not result["success"]
            assert result["code"] == "session_forbidden"

    async def test_apply_with_bound_session_and_missing_spec(self, registry):
        from app.lib.runtime.context import bind_runtime_context

        with _owner_ctx(), bind_runtime_context(session_id="sess-dspec-tools"):
            result = await registry.dispatch(
                "webgis_map_design_apply",
                {"session_id": "sess-dspec-tools", "spec_id": "missing-spec"},
                "sess-dspec-tools",
            )
            assert not result["success"]
            assert result["code"] == "DESIGN_SPEC_NOT_FOUND"
