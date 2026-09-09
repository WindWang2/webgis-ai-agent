"""V3 worker 化投影端到端（ADR-0119 / Wave 10-11）：真实子进程。

钉死：api>=1.2 的 worker 扩展可声明 algorithm（描述符元数据面）/
data_provider（7 方法 RPC 代理 + mixin 探测）/cartography/recipes，
全部投影进权威 registry + 台账回滚；undeclared 申报 fail closed。
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from app.extensions_platform.diagnostics import has_errors
from app.extensions_platform.host import ExtensionHost, HostPolicy


MAIN_V3 = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec
    from app.extensions_platform.sdk.declarations import CartographyItemSpec, WorkflowPackSpec
    from app.extensions_platform.sdk.provider import StreamingVectorProvider


    class DemoProvider(StreamingVectorProvider):
        \"\"\"worker 内的活适配器实例（永不离开 worker 进程）。\"\"\"

        def __init__(self, ctx=None):
            self._ctx = ctx

        def probe(self):
            return True

        def capabilities(self):
            return ["vector_features", "pushdown_bbox"]

        def list_datasets(self):
            return [{"id": "demo.layer", "kind": "feature"}]

        def describe(self, dataset_id):
            from app.schemas.data_fabric_schema import DatasetDescriptor
            return DatasetDescriptor(
                id=dataset_id,
                source_type="v3demo_prov",
                geometry_type="Point",
            )

        def preview(self, dataset_id, limit=10):
            return {"dataset_id": dataset_id, "features": [], "limit": limit}

        def query(self, dataset_id, query_spec):
            from app.schemas.data_fabric_schema import QueryResult
            return QueryResult(
                dataset_id=dataset_id,
                features=[{"type": "Feature", "properties": {"q": True}, "geometry": None}],
                returned_count=1,
                total_count=1,
            )

        def health(self):
            from app.schemas.data_fabric_schema import DataFabricHealth
            return DataFabricHealth(status="healthy", latency_ms=1.0)

        def stream_features(self, query, page_size=500):
            for i in range(3):
                yield {"type": "Feature", "properties": {"i": i}, "geometry": None}


    def activate(ctx):
        # 1) 执行工具（worker 内）
        def run_algo(features):
            return {"count": len(features or [])}
        ctx.register_tool(ToolExtensionSpec(
            name="algo_runner", description="runs the demo algorithm",
            func=run_algo,
            parameters={"type": "object", "properties": {"features": {"type": "array"}}},
        ))
        # 2) 算法描述符（可序列化；执行体 = 上面的 worker 工具）
        ctx.register_algorithm_v3({
            "id": "demo_algo",
            "name": "demo algorithm",
            "capabilities": ["count_features"],
            "tool_candidates": ["algo_runner"],
        })
        # 3) 数据 provider（工厂 → 实例留 worker）
        ctx.register_data_provider_v3(
            "prov", DemoProvider, description="demo provider",
            mixins=("streaming_vector",),
        )
        # 4) cartography（payload 即 JSON）
        ctx.register_cartography_item(CartographyItemSpec(
            kind="theme", id="demo_theme",
            payload={"name_zh": "演示主题"},
        ))
        # 5) recipe pack（pydantic 模型 → model_dump）
        from app.services.gis_harness.recipes import CartographyRecipe
        recipe = CartographyRecipe.model_validate({
            "id": "v3demo_demo_recipe",
            "name": "demo recipe",
        })
        ctx.register_workflow_pack(WorkflowPackSpec(pack_id="demo_pack", recipes=[recipe]))
    """
)


def _make_v3_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "v3demo.full"
    pack.mkdir(parents=True)
    (pack / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": "v3demo.full",
                "name": "full",
                "namespace": "v3demo",
                "version": "1.0.0",
                "api_version": "1.2.0",
                "entry_point": "main",
                "permissions": ["model_provider"],
                "tools": [
                    {"name": "algo_runner", "description": "runs the demo algorithm"}
                ],
                "algorithms": [
                    {"id": "demo_algo", "description": "demo algorithm"}
                ],
                "data_providers": [{"source_type": "prov", "description": "demo"}],
                "cartography_items": [
                    {"kind": "theme", "id": "demo_theme", "description": "demo"}
                ],
                "workflow_packs": [{"pack_id": "demo_pack", "description": "demo"}],
                "execution": {
                    "mode": "worker",
                    "startup_timeout_s": 20.0,
                    "call_timeout_s": 15.0,
                },
            }
        )
    )
    (pack / "main.py").write_text(MAIN_V3)
    return pack


def _activate(tmp_path: Path):
    from app.tools.registry import ToolRegistry

    pack = _make_v3_pack(tmp_path)
    policy = HostPolicy(
        roots=(tmp_path,),
        allow=frozenset({"v3demo.full"}),
        allow_local_untrusted_activation=True,
    )
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=policy)
    host.discover()
    diags = host.activate("v3demo.full")
    return host, pack, diags


def test_worker_v3_projection_all_sections(tmp_path):
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.cartography.themes import get_cartographic_theme_registry
    from app.services.data_fabric.registry import get_registry
    from app.services.gis_harness.recipes import get_recipe_registry

    host, pack, diags = _activate(tmp_path)
    try:
        assert not has_errors(diags), [d.message for d in diags if d.severity.value == "error"]
        record = host.get_record("v3demo.full")
        assert record is not None and record.state.value == "active"

        # 算法投影（描述符元数据 + tool_candidates 指向 worker 工具投影名）。
        algo = get_algorithm_registry().get("v3demo.demo_algo")
        assert algo is not None
        assert algo.tool_candidates == ["v3demo_algo_runner"]

        # 数据 provider 投影 + mixin 探测（动态继承可被 issubclass 探测）。
        registry = get_registry()
        assert "v3demo_prov" in registry.supported_source_types()
        spec = registry.resolve("v3demo_prov")
        from app.extensions_platform.sdk.provider import extended_provider_capabilities

        caps = extended_provider_capabilities(spec.adapter_cls)
        assert caps == ["streaming_vector"]
        # 七方法 RPC 往返（真实 worker 子进程）。
        from app.schemas.data_fabric_schema import ConnectionProfile

        adapter = spec.adapter_cls(
            ConnectionProfile(source_type="v3demo_prov", name="t")
        )
        assert adapter.probe() is True
        assert adapter.list_datasets()[0]["id"] == "demo.layer"
        descriptor = adapter.describe("demo.layer")
        assert descriptor.id == "demo.layer"
        result = adapter.query("demo.layer", None)
        assert result.returned_count == 1
        features = list(adapter.stream_features({"bbox": None}))
        assert [f["properties"]["i"] for f in features] == [0, 1, 2]

        # cartography / recipe 投影。
        themes = get_cartographic_theme_registry()
        assert themes.get_theme("v3demo_demo_theme") is not None
        recipe = get_recipe_registry().get("v3demo_demo_recipe")
        assert recipe is not None
    finally:
        host.reset()


def test_worker_v3_deactivate_rolls_back_all_sections(tmp_path):
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.cartography.themes import get_cartographic_theme_registry
    from app.services.data_fabric.registry import get_registry
    from app.services.gis_harness.recipes import get_recipe_registry

    host, pack, diags = _activate(tmp_path)
    try:
        assert not has_errors(diags)
        deact = host.deactivate("v3demo.full")
        assert not has_errors(deact), [d.message for d in deact]
        # 台账回滚：全部投影零残留。
        assert get_algorithm_registry().has("v3demo.demo_algo") is False
        assert "v3demo_prov" not in get_registry().supported_source_types()
        assert get_cartographic_theme_registry().get_theme("v3demo_demo_theme") is None
        assert get_recipe_registry().get("v3demo_demo_recipe") is None
    finally:
        host.reset()


def test_worker_v3_undeclared_handshake_offer_fails_closed(tmp_path):
    """握手申报未声明节 → typed 激活失败 + worker 回收。"""
    pack = _make_v3_pack(tmp_path)
    manifest = json.loads((pack / "manifest.json").read_text())
    manifest["algorithms"] = []  # 声明删除但 worker 仍申报
    (pack / "manifest.json").write_text(json.dumps(manifest))
    # worker 内 register_algorithm_v3 会被 _require_declared 拒绝 → 握手失败
    # （fail closed 在 worker 侧就发生）。
    from app.tools.registry import ToolRegistry

    policy = HostPolicy(
        roots=(tmp_path,),
        allow=frozenset({"v3demo.full"}),
        allow_local_untrusted_activation=True,
    )
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=policy)
    host.discover()
    diags = host.activate("v3demo.full")
    assert has_errors(diags)
    assert any("algorithms" in d.message or "declared" in d.message for d in diags)
    host.reset()


def test_worker_v3_api_1_1_still_rejects_class_sections(tmp_path):
    """版本门控：api 1.1 + worker + 类实例节 → manifest 层拒绝（V2 语义不变）。"""
    from app.extensions_platform.manifest import GisExtensionManifest

    with pytest.raises(Exception, match="api_version"):
        GisExtensionManifest.model_validate(
            {
                "schema_version": 1,
                "id": "old.pack",
                "name": "pack",
                "namespace": "old",
                "version": "1.0.0",
                "api_version": "1.1.0",
                "entry_point": "main",
                "execution": {"mode": "worker"},
                "algorithms": [{"id": "algo_one", "description": ""}],
            }
        )
