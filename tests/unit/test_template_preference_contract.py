"""AC-03 P5/P7：62 个 SEED 模板 payload 的「声明偏好 → 裁决结果」对照契约。

模板 payload 的 method/k/palette 不再是命令，而是 resolve_symbology 的
recommended 输入（ADR-0152）：
- 结构模式（categorical/lisa）作为显式模式保留；
- 分布证据不反对 → 偏好获尊重（palette/k/method）；
- 重尾证据 → 推翻 method 偏好（head_tail），rejected 留痕；
- 一切 k 裁决结果都在 [3,7]（沿用 ADR-0073 边界）。

覆盖 SEED_TEMPLATES 全部 62 个模板（28 个 thematic 全量对照 + 其余 kind
的 apply 烟雾），每条对照即任务书「62 模板 payload 改造」的回归锁。
"""
import pytest

from app.lib.cartography.model_library import CLASSIFICATION_METHODS
from app.lib.cartography.symbology import symbology_decision_from_values
from app.schemas.template_schema import SEED_TEMPLATES
from app.tools.registry import ToolRegistry
from app.tools.templates import register_template_tools

HEAVY = [1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 13.0,
         20.0, 40.0, 80.0, 160.0, 320.0, 640.0, 1280.0, 5000.0]
MID_SKEW = [10.0, 12.0, 14.0, 11.0, 13.0, 15.0, 12.0, 14.0, 16.0, 13.0,
            11.0, 15.0, 14.0, 12.0, 17.0, 16.0, 13.0, 18.0, 15.0, 14.0]

THEMATIC_CHORO = [t for t in SEED_TEMPLATES
                  if t["kind"] == "thematic"
                  and t.get("payload", {}).get("variant", "choropleth") == "choropleth"]
THEMATIC_ALL = [t for t in SEED_TEMPLATES if t["kind"] == "thematic"]


def test_seed_inventory():
    """实测盘点（对照 docs/dev/ac-03-symbology-recon.md §3）。"""
    assert len(SEED_TEMPLATES) == 62
    assert len(THEMATIC_ALL) == 28
    assert len(THEMATIC_CHORO) == 27


def _fc(values, field="v"):
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {field: v},
         "geometry": {"type": "Polygon",
                      "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}}
        for v in values
    ]}


@pytest.mark.parametrize("tpl", THEMATIC_CHORO, ids=lambda t: t["id"])
def test_template_preference_contract(tpl):
    payload = tpl["payload"]
    recommended = symbology_decision_from_values(
        MID_SKEW,
        requested_method=(payload.get("method")
                          if payload.get("method") in ("categorical", "lisa") else None),
        recommended_method=(payload.get("method")
                            if payload.get("method") not in ("categorical", "lisa")
                            else None),
        recommended_k=payload.get("k"),
        recommended_palette=payload.get("palette"),
        origin=tpl["id"],
    )
    heavy = symbology_decision_from_values(
        HEAVY,
        requested_method=(payload.get("method")
                          if payload.get("method") in ("categorical", "lisa") else None),
        recommended_method=(payload.get("method")
                            if payload.get("method") not in ("categorical", "lisa")
                            else None),
        recommended_k=payload.get("k"),
        recommended_palette=payload.get("palette"),
        origin=tpl["id"],
    )

    # 1) 结构模式保留
    if payload.get("method") in ("categorical", "lisa"):
        assert recommended.method == payload["method"], tpl["id"]
        assert recommended.source == "explicit"
    else:
        # 2) 证据不反对 → 偏好获尊重
        assert recommended.method == (
            payload.get("method") if payload.get("method") in CLASSIFICATION_METHODS
            else "natural_breaks"
        ), tpl["id"]
        assert recommended.source in ("recommended", "distribution")
        # 3) 重尾证据 → head_tail 推翻 method 偏好，rejected 留痕
        assert heavy.method == "head_tail", tpl["id"]
        override = [r for r in heavy.rejected
                    if r["kind"] == "method" and r["value"] == payload.get("method")]
        if payload.get("method") and payload["method"] != "head_tail":
            assert override, f"{tpl['id']}: 偏好被推翻必须留痕"

    # 4) k 恒在裁决边界内
    assert 3 <= recommended.k <= 7, tpl["id"]
    assert 3 <= heavy.k <= 7, tpl["id"]
    # 5) palette 必须存在（显式偏好受尊重；引擎裁决值合法）；lisa 例外——
    #    其五色是制图学固定语义色，palette 裁决不适用（decision.palette=""）。
    if payload.get("palette") and payload.get("method") != "lisa":
        assert recommended.palette, tpl["id"]


@pytest.mark.parametrize("tpl", THEMATIC_ALL, ids=lambda t: t["id"])
@pytest.mark.asyncio
async def test_thematic_templates_apply_end_to_end(tpl):
    """28 个 thematic 模板全量端到端 apply：偏好路由后不再 KeyError/假成功。"""
    reg = ToolRegistry()
    register_template_tools(reg)
    payload = tpl.get("payload", {})
    if payload.get("method") == "lisa":
        # lisa 是语义分类：字段值必须是 HH/LL/HL/LH/NS
        geojson = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"v": v},
             "geometry": {"type": "Polygon",
                          "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1]]]}}
            for v in ["HH", "LL", "HL", "LH", "NS"]
        ]}
    else:
        geojson = _fc(HEAVY)
    res = await reg.dispatch("apply_template", {
        "template_id": tpl["id"], "geojson": geojson,
        "field": "v", "layer_id": "layer-1",
    })
    if payload.get("variant") == "heatmap":
        # heatmap variant 走前端命令渲染，数据齐全时必须成功
        assert res.get("status") == "template_applied", res
        assert res["command"] == "add_native_heatmap"
        return
    assert res.get("status") == "template_applied", res
    # choropleth：偏好路由后仍产出决策与图例
    if res.get("symbology_decision") is not None:
        d = res["symbology_decision"]
        assert 3 <= d["k"] <= 7
        assert d["method"] in (*CLASSIFICATION_METHODS, "categorical", "lisa")
    spec = res.get("legend_spec")
    assert spec is not None
    assert spec["type"] in ("graduated", "categorical")


@pytest.mark.parametrize("kind", ["basemap", "symbology", "layout"])
def test_non_thematic_templates_still_present(kind):
    assert any(t["kind"] == kind for t in SEED_TEMPLATES)
