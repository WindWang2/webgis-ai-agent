"""ADR-0101 Wave 3: 统一工具面投影 + 确定性检索 + schema 压缩测试。

锁定语义：
- ToolSurfaceProjector 是投影不是注册中心（同输入必同输出、指纹确定性）；
- augment 对 ToolCatalog 选择结果 additive（tier-1/前门永不因检索被挤掉）；
- hidden/planned 工具从任何来源都不进模型可见面；
- 检索补强有界（≤ retrieval_k）、确定性、可解释（reasons.matched）；
- 压缩保留 required/enum 语义，度量字节；
- 检索质量基准（离线 golden 用例，无 LLM/无网络）。
"""
import json

import pytest

from app.services.chat.schema_compression import (
    compress_schema,
    compression_report,
    schema_bytes,
)
from app.services.chat.tool_retrieval import rank_tools, tokenize
from app.services.tool_surface_v2 import SurfaceRequest, ToolSurfaceProjector
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# 测试注册表（有域/无域工具混合）
# ---------------------------------------------------------------------------

@pytest.fixture()
def reg():
    registry = ToolRegistry()

    def _mk(name, desc, tier=1, domains=None, tags=None, params=None,
            side_effect=None, status=None, capability=None):
        async def fn(**kwargs):
            return {"success": True}
        fn.__name__ = name
        kw = {}
        if tier != 1:
            kw["tier"] = tier
        if domains:
            kw["domains"] = domains
        if tags:
            kw["tags"] = tags
        if side_effect:
            kw["side_effect"] = side_effect
        if status:
            kw["status"] = status
        if capability:
            kw["capabilities"] = [capability]
        registry.register(
            name=name, description=desc, func=fn,
            param_descriptions=param_desc, **kw,
        )
        return name

    param_desc = {}
    _mk("list_available_tools", "列出可用工具")
    _mk("query_local_poi", "查询中国本地 POI 兴趣点", tier=1)
    _mk("voronoi_polygons", "泰森多边形 Voronoi 分割", tier=2,
        domains=["statistics"], tags=["voronoi", "thiessen", "泰森"])
    _mk("network_shortest_path", "路网最短路径分析", tier=2,
        domains=["network"], tags=["route", "路径"])
    _mk("ndvi_calculate", "遥感植被指数 NDVI 计算", tier=2,
        domains=["raster"], tags=["ndvi", "植被"])
    _mk("internal_sweeper", "内部清扫工具（隐藏）", tier=1, status="hidden")
    _mk("future_tool", "尚未实现的占位", tier=2, status="planned")
    _mk("spawn_subagent", "委派子代理", tier=3, side_effect="destructive")
    return registry


class FakeCatalog:
    """模拟 ToolCatalog 的最小选择语义（tier1 + 关键词域 tier2）。"""

    def __init__(self, registry):
        self.registry = registry

    def select_schemas(self, user_message, session_id=None, declared_domains=None,
                       turn_id=None, surface=None):
        active = set()
        text = user_message or ""
        for kw, dom in [("泰森", "statistics"), ("路径", "network"),
                        ("NDVI", "raster"), ("植被", "raster")]:
            if kw in text:
                active.add(dom)
        names = set()
        for name in self.registry.list_tools():
            meta = self.registry.metadata(name)
            if int(meta.get("tier", 1)) == 1:
                names.add(name)
            elif int(meta.get("tier", 1)) == 2 and set(meta.get("domains", [])) & active:
                names.add(name)
        return self.registry.get_schemas_subset(names)

    @staticmethod
    def detect_domains(text):
        active = set()
        for kw, dom in [("泰森", "statistics"), ("路径", "network"),
                        ("NDVI", "raster"), ("植被", "raster")]:
            if kw in (text or ""):
                active.add(dom)
        return active


@pytest.fixture()
def catalog(reg):
    return FakeCatalog(reg)


# ---------------------------------------------------------------------------
# 投影基础
# ---------------------------------------------------------------------------

def test_projection_deterministic(reg, catalog):
    proj = ToolSurfaceProjector(reg, catalog)
    r1 = proj.project(SurfaceRequest(user_message="泰森多边形分析", retrieval=False))
    r2 = proj.project(SurfaceRequest(user_message="泰森多边形分析", retrieval=False))
    assert r1.fingerprint == r2.fingerprint
    assert [s["function"]["name"] for s in r1.schemas] == \
        [s["function"]["name"] for s in r2.schemas]


def test_projection_excludes_hidden_and_planned(reg, catalog):
    proj = ToolSurfaceProjector(reg, catalog)
    r = proj.project(SurfaceRequest(user_message="泰森 植被 路径", retrieval=False))
    names = {s["function"]["name"] for s in r.schemas}
    assert "internal_sweeper" not in names
    assert "future_tool" not in names
    assert "internal_sweeper" in r.dropped
    assert r.dropped["internal_sweeper"].startswith("lifecycle:")
    # dropped 的不出现在 reasons
    assert "internal_sweeper" not in r.reasons


def test_projection_reasons_annotate_base_selection(reg, catalog):
    proj = ToolSurfaceProjector(reg, catalog)
    r = proj.project(SurfaceRequest(user_message="查一下泰森多边形", retrieval=False))
    assert "tier1" in r.reasons["query_local_poi"]
    assert any(x.startswith("domain:") for x in r.reasons["voronoi_polygons"])


def test_projection_without_catalog_filters_tier3(reg):
    """review R1 MAJOR 回归锁：no-catalog 退化路径同样过 tier 闸 ——
    此前 get_schemas() 全量基线把 tier-3 schema 直接漏进投影面。"""
    proj = ToolSurfaceProjector(reg, catalog=None)
    r = proj.project(SurfaceRequest(user_message="", retrieval=False))
    names = {s["function"]["name"] for s in r.schemas}
    assert "query_local_poi" in names
    assert "spawn_subagent" not in names
    assert r.dropped.get("spawn_subagent") == "tier3"


# ---------------------------------------------------------------------------
# 检索补强
# ---------------------------------------------------------------------------

def test_retrieval_fills_keyword_miss(reg, catalog):
    """关键词表未覆盖的意图（「泰森」不在 FakeCatalog 词表）→ 检索补上。"""
    proj = ToolSurfaceProjector(reg, catalog)
    r = proj.project(SurfaceRequest(user_message="帮我对辖区做 thiessen 分割"))
    names = {s["function"]["name"] for s in r.schemas}
    assert "voronoi_polygons" in names
    assert "voronoi_polygons" in r.retrieval_added
    reason = r.why("voronoi_polygons")
    assert reason.startswith("retrieval(")


def test_retrieval_never_drops_base_tools(reg, catalog):
    proj = ToolSurfaceProjector(reg, catalog)
    base = proj.project(SurfaceRequest(user_message="路径分析", retrieval=False))
    aug = proj.project(SurfaceRequest(user_message="路径分析", retrieval=True))
    base_names = {s["function"]["name"] for s in base.schemas}
    aug_names = {s["function"]["name"] for s in aug.schemas}
    assert base_names <= aug_names


def test_retrieval_bounded_by_k(reg, catalog):
    proj = ToolSurfaceProjector(reg, catalog)
    r = proj.project(SurfaceRequest(user_message="泰森 路径 植被 ndvi 查询 点", retrieval_k=2))
    assert len(r.retrieval_added) <= 2


def test_retrieval_skips_hidden_candidates(reg, catalog):
    proj = ToolSurfaceProjector(reg, catalog)
    r = proj.project(SurfaceRequest(user_message="内部清扫 清扫工具", retrieval=True))
    names = {s["function"]["name"] for s in r.schemas}
    assert "internal_sweeper" not in names


def test_rank_tools_deterministic_and_explained(reg):
    h1 = rank_tools(reg, "泰森多边形")
    h2 = rank_tools(reg, "泰森多边形")
    assert [h.name for h in h1] == [h.name for h in h2]
    assert h1[0].name == "voronoi_polygons"
    assert h1[0].matched


def test_tokenize_cjk_and_ascii():
    toks = tokenize("成都市的NDVI分布")
    assert "ndvi" in toks
    assert any(len(t) == 2 for t in toks)  # CJK bigram
    assert tokenize("") == ()


# ---------------------------------------------------------------------------
# schema 压缩
# ---------------------------------------------------------------------------

def _sample_schema():
    return {
        "type": "function",
        "function": {
            "name": "create_thematic_map",
            "description": "超长描述。" * 60,
            "parameters": {
                "type": "object",
                "properties": {
                    "geojson": {"type": "object", "description": "GeoJSON 数据 " * 20},
                    "method": {"type": "string", "enum": ["q" + str(i) for i in range(30)],
                               "description": "分类方法"},
                    "n_classes": {"type": "integer", "default": 5},
                },
                "required": ["geojson"],
            },
        },
    }


def test_compress_reduces_bytes_keeps_contract():
    s = _sample_schema()
    before = schema_bytes(s)
    c = compress_schema(s, level="compact")
    after = schema_bytes(c)
    assert after < before
    # 契约保留：required 与 enum 全量（短 enum）
    assert c["function"]["parameters"]["required"] == ["geojson"]
    props = c["function"]["parameters"]["properties"]
    assert props["n_classes"]["default"] == 5


def test_compress_truncates_long_enum_with_count_hint():
    """review R1 MAJOR 回归锁：截断提示进 description，绝不伪造 enum 成员。"""
    c = compress_schema(_sample_schema(), level="compact")
    method = c["function"]["parameters"]["properties"]["method"]
    assert len(method["enum"]) <= 12
    assert not any(str(x).startswith("…(") for x in method["enum"])
    assert "more valid values" in method.get("description", "")


def test_compress_prefers_summary():
    c = compress_schema(_sample_schema(), level="compact", summary="专题图制作")
    assert c["function"]["description"] == "专题图制作"


def test_compress_none_returns_detached_copy():
    """review R1 minor 回归锁：none 档也返回深拷贝（注册 schema 是活引用，
    投影消费方就地修改不得污染注册表）。"""
    s = _sample_schema()
    out = compress_schema(s, level="none")
    assert out == s
    assert out is not s
    out["function"]["parameters"]["required"].clear()
    assert s["function"]["parameters"]["required"] == ["geojson"]


def test_compression_report_measures():
    rep = compression_report([_sample_schema()], level="compact")
    assert rep["after_bytes"] < rep["before_bytes"]


# ---------------------------------------------------------------------------
# Pi 原生面快照
# ---------------------------------------------------------------------------

def test_native_surface_snapshot_from_live_registry(reg):
    proj = ToolSurfaceProjector(reg, catalog=None)
    snap = proj.native_surface_snapshot(
        ["query_local_poi", "list_available_tools"], compress="compact"
    )
    names = {s["function"]["name"] for s in snap}
    assert names == {"query_local_poi", "list_available_tools"}


# ---------------------------------------------------------------------------
# 离线工具选择基准（golden 用例，无 LLM —— Wave 9 全量 corpus 的种子）
# ---------------------------------------------------------------------------

_GOLDEN_CASES = [
    # (用户意图, 必须可达的工具, 禁止出现的干扰工具)
    ("成都市小学分布密度热力图", {"query_local_poi"}, set()),
    ("两个区县的泰森多边形分割", {"voronoi_polygons"}, set()),
    ("从甲地到乙地的最短路径", {"network_shortest_path"}, {"ndvi_calculate"}),
    ("计算这片植被的NDVI", {"ndvi_calculate"}, {"network_shortest_path"}),
]


def test_tool_selection_golden_benchmark(reg, catalog):
    proj = ToolSurfaceProjector(reg, catalog)
    total, hits, leakage = 0, 0, 0
    for query, required, forbidden in _GOLDEN_CASES:
        r = proj.project(SurfaceRequest(user_message=query, retrieval=True))
        names = {s["function"]["name"] for s in r.schemas}
        total += len(required)
        hits += len(required & names)
        leakage += len(forbidden & names)
        # tier-3 永不因检索/关键词泄漏（FakeCatalog 不选 tier-3；检索索引跳过不可见——
        # spawn_subagent 可见但 tier3 不在 FakeCatalog 语义内；投影自身不做 tier 闸）
    recall = hits / total
    assert recall >= 0.99, f"recall={recall}"
    assert leakage == 0


def test_tier3_never_leaks_via_retrieval(reg, catalog):
    """tier-3/destructive 绝不因检索被补进模型可见面 —— 安全红线。"""
    proj = ToolSurfaceProjector(reg, catalog)
    r = proj.project(SurfaceRequest(user_message="委派子代理 批量任务", retrieval=True))
    names = {s["function"]["name"] for s in r.schemas}
    assert "spawn_subagent" not in names
    assert "spawn_subagent" not in r.retrieval_added


def test_real_catalog_tier3_never_auto_included():
    """集成：真实 ToolCatalog 的 tier-3 永不自动纳入 + V2 投影保持该语义。"""
    from app.services.tool_catalog import ToolCatalog

    registry = ToolRegistry()
    registry.register(
        name="danger_tool", description="危险操作",
        func=lambda x=1: {"success": True}, tier=3, side_effect="destructive",
    )
    registry.register(
        name="safe_tool", description="安全操作",
        func=lambda: {"success": True}, tier=1,
    )
    catalog = ToolCatalog(registry)
    proj = ToolSurfaceProjector(registry, catalog)
    r = proj.project(SurfaceRequest(user_message="危险操作 危险", retrieval=True))
    names = {s["function"]["name"] for s in r.schemas}
    assert "danger_tool" not in names
    assert "safe_tool" in names
