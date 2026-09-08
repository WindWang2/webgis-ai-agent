"""ExtDemo 示例扩展包端到端测试（ADR-0104 Wave 14）。

被测对象是仓库内真实存在的 extensions/examples/extdemo-pack（活文档 +
集成测试夹具二合一）：拷贝到 tmp_path 后用 ExtensionHost 驱动完整生命周期
——发现 → 激活 → 五类 registry 投影核对 → 工具派发 / 算法数值 smoke →
provider 离线语义 → recipe 编译期校验 → 健康门 → 停用零残留。

约定：
- ToolRegistry 用测试自建实例（宿主注入面）；算法/provider/组件/recipe
  走 context 内部的**真实单例** registry（已被其他测试加载）。对顺序鲁
  棒：只做 has/get 存在性断言，不做计数断言；激活前清理可能的残留
  （此前失败运行留下的 extdemo_* 条目），保证可重复。
- 全程无网络。
"""

from __future__ import annotations

import shutil
from math import pi
from pathlib import Path

import pytest

from app.extensions_platform.host import ExtensionHost, ExtensionState, HostPolicy
from app.extensions_platform.sdk import run_authoring_checks
from app.tools.registry import ToolRegistry

REPO_ROOT = Path(__file__).resolve().parents[3]
PACK_SOURCE = REPO_ROOT / "extensions" / "examples" / "extdemo-pack"

EXTENSION_ID = "extdemo.pack"


# ---------------------------------------------------------------- helpers


def _cleanup_leftovers() -> None:
    """顺序鲁棒性：幂等清掉本包此前失败运行可能留下的全部残留。

    单例 registry 是进程级持久的——若某次断言失败发生在 deactivate 之前，
    extdemo_* 条目会存活到下一轮测试并在激活期触发碰撞。清理走各 registry
    的扩展卸载通道（unregister，幂等、目标不存在返回 False）。
    """
    from app.lib.cartography.component_registry import get_component_registry
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.services.data_fabric.registry import get_registry
    from app.services.gis_harness.recipes import get_recipe_registry

    get_algorithm_registry().unregister("extdemo.compactness")
    get_registry().unregister("extdemo_demo_tile_catalog")
    get_component_registry().unregister("extdemo_note_scale_bar")
    get_recipe_registry().unregister("extdemo_demo_overview")


@pytest.fixture()
def pack_root(tmp_path: Path) -> Path:
    """把仓库内示例包拷贝进 tmp（发现根 = tmp_path 本身）。"""
    target = tmp_path / "extdemo-pack"
    shutil.copytree(PACK_SOURCE, target)
    return tmp_path


@pytest.fixture()
def activated(pack_root: Path):
    """发现并激活示例包；无论断言成败都回滚，不给单例 registry 留僵尸。"""
    _cleanup_leftovers()
    tool_registry = ToolRegistry()
    host = ExtensionHost(
        tool_registry=tool_registry,
        policy=HostPolicy(
            roots=(pack_root,),
            builtin_ids=frozenset({EXTENSION_ID}),
            grants={EXTENSION_ID: frozenset({"network"})},
        ),
    )
    host.discover()
    diagnostics = host.activate(EXTENSION_ID)
    record = host.get_record(EXTENSION_ID)
    assert record is not None, "extdemo.pack must be discovered"
    try:
        yield host, tool_registry, record, diagnostics
    finally:
        if record.state in (ExtensionState.ACTIVE, ExtensionState.DEGRADED):
            host.deactivate(EXTENSION_ID)
        _cleanup_leftovers()


# ---------------------------------------------------------------- tests


class TestExamplePackLifecycle:
    def test_activation_state_and_projections(self, activated):
        """激活成功，五类条目全部以命名空间化 id 落进真实 registry。"""
        from app.lib.cartography.component_registry import get_component_registry
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.services.data_fabric.registry import get_registry
        from app.services.gis_harness.recipes import get_recipe_registry

        host, tool_registry, record, diagnostics = activated
        # 无警告 → ACTIVE（而非 DEGRADED）；manifest 声明与注册完全对账。
        assert record.state is ExtensionState.ACTIVE
        assert diagnostics == []
        assert tool_registry.has("extdemo_bbox_area")
        assert tool_registry.has("extdemo_polygon_compactness")
        assert get_algorithm_registry().has("extdemo.compactness")
        assert get_registry().is_supported("extdemo_demo_tile_catalog")
        component = get_component_registry().get("extdemo_note_scale_bar")
        assert component is not None
        assert component.runtime_status == "planned"
        assert get_recipe_registry().get("extdemo_demo_overview") is not None

    async def test_tool_dispatch_math(self, activated):
        """经 ToolRegistry 派发：面积数学正确；退化输入诚实报错不伪造。"""
        host, tool_registry, record, _ = activated
        result = await tool_registry.dispatch(
            "extdemo_bbox_area", {"xmin": 0, "ymin": 0, "xmax": 3, "ymax": 2}
        )
        assert result["area"] == pytest.approx(6.0)
        bad = await tool_registry.dispatch(
            "extdemo_bbox_area", {"xmin": 2, "ymin": 0, "xmax": 1, "ymax": 2}
        )
        # 工具内 ValueError 经 dispatch 管线归类为 VALIDATION_ERROR / ValueError。
        assert bad.get("code") == "VALIDATION_ERROR"
        assert bad.get("error_type") == "ValueError"

    def test_algorithm_authoring_smoke(self, activated):
        """authoring harness：数值 smoke 全过；正方形 → π/4 ≈ 0.7854。"""
        from app.lib.gis.algorithm_registry import get_algorithm_registry

        host, tool_registry, record, _ = activated
        module = record.module
        assert module is not None
        diagnostics = run_authoring_checks(
            module.COMPACTNESS_ALGORITHM, module._polygon_compactness_run
        )
        assert diagnostics == []
        square = module._polygon_compactness_run(module._SQUARE_GEOJSON)
        assert square["compactness"] == pytest.approx(pi / 4, abs=1e-3)
        # 投影进算法 registry 的 descriptor 保留词表字段与候选工具。
        descriptor = get_algorithm_registry().get("extdemo.compactness")
        assert descriptor is not None
        assert descriptor.scientific_status == "EXPERIMENTAL"
        assert descriptor.tool_candidates == ["extdemo_polygon_compactness"]

    def test_provider_resolves_offline(self, activated):
        """provider 经 data fabric registry 解析；describe/query/health 全离线。"""
        from app.schemas.data_fabric_schema import ConnectionProfile, QuerySpec
        from app.services.data_fabric.registry import get_registry

        host, tool_registry, record, _ = activated
        spec = get_registry().resolve("extdemo_demo_tile_catalog")
        assert spec.is_raster_tile is True
        adapter = get_registry().build_adapter(
            ConnectionProfile(source_type="extdemo_demo_tile_catalog")
        )
        assert adapter.probe() is True
        assert adapter.health().status == "healthy"
        descriptor = adapter.describe("extdemo_blank_tiles")
        assert descriptor.geometry_type == "Raster"
        # 栅格源没有要素计数：诚实未知（None），不伪造 0。
        assert descriptor.feature_count is None
        # 栅格/瓦片源不回答矢量要素查询：空结果 + 原因（镜像核心 wms 语义）。
        query_result = adapter.query("extdemo_blank_tiles", QuerySpec())
        assert query_result.features == []
        assert query_result.metadata["reason"] == "raster_tile_source_has_no_vector_features"
        with pytest.raises(ValueError):
            adapter.describe("no_such_dataset")

    def test_recipe_passes_compile_time_validation(self, activated):
        """recipe 通过 harness 编译期校验（registry validate 的 recipe 块）。

        说明：不能在激活期跑完整 ``validate_gis_library()``——扩展算法刻意
        不挂 capability（冻结 seam）且扩展组件不入渲染器真值矩阵，两者都会
        产生**设计内**的 issue；这里精确镜像 validate_gis_library 对 recipe
        的检查（capability 引用 + cartography 解析 + TaskType 词表）。
        """
        from typing import get_args

        from app.lib.cartography.model_library import get_map_model_registry
        from app.lib.gis.capability_registry import get_capability_registry
        from app.services.gis_harness.intent import TaskType
        from app.services.gis_harness.recipes import get_recipe_registry

        host, tool_registry, record, _ = activated
        recipe = get_recipe_registry().get("extdemo_demo_overview")
        assert recipe is not None
        assert recipe.intent_tasks
        for task in recipe.intent_tasks:
            assert task in get_args(TaskType)
        capabilities = get_capability_registry()
        referenced = list(recipe.preferred_analysis) + list(recipe.optional_analysis)
        assert referenced, "示例 recipe 应引用真实 capability id"
        for cap in referenced:
            assert capabilities.has(cap), f"unknown capability {cap!r}"
        models = get_map_model_registry()
        for carto in [recipe.primary_cartography, *recipe.secondary_cartography]:
            if carto:
                assert models.resolve(carto) is not None

    def test_health_gate(self, activated):
        host, tool_registry, record, _ = activated
        report = host.health(EXTENSION_ID)
        assert report["status"] == "healthy"
        assert report["messages"] == []
        assert report["state"] == "active"

    def test_deactivate_leaves_no_zombies(self, activated):
        """停用 → 五类 registry 零 extdemo_* 残留（卸载无僵尸）。"""
        from app.lib.cartography.component_registry import get_component_registry
        from app.lib.gis.algorithm_registry import get_algorithm_registry
        from app.services.data_fabric.errors import UnsupportedSourceError
        from app.services.data_fabric.registry import get_registry
        from app.services.gis_harness.recipes import get_recipe_registry

        host, tool_registry, record, _ = activated
        host.deactivate(EXTENSION_ID)
        assert record.state is ExtensionState.COMPATIBLE
        assert not tool_registry.has("extdemo_bbox_area")
        assert not tool_registry.has("extdemo_polygon_compactness")
        assert not get_algorithm_registry().has("extdemo.compactness")
        assert not get_registry().is_supported("extdemo_demo_tile_catalog")
        with pytest.raises(UnsupportedSourceError):
            get_registry().resolve("extdemo_demo_tile_catalog")
        assert get_component_registry().get("extdemo_note_scale_bar") is None
        assert get_recipe_registry().get("extdemo_demo_overview") is None
