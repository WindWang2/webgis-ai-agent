"""MapCritique（V7 ADR-0130 D5）回归锁：地图感知批评闭环。

不变式：
1. 检查纯函数 —— 同输入同 findings；
2. blank_map：全层 rendered 但 feature_count 全 0 → error；计数缺席
   的层不参与判定（不猜）；无观察 → 零 finding；
3. invalid_bounds：倒置/非有限 bbox → error；合法/缺席 → 零；
4. 出版件完整性：仅「有结果图层 + 声明 png/pdf/svg 导出」时检查
   title/north_arrow/scale_bar；缺席组件带 family（add_component 修复
   路由）；交互-only 产品零要求（不对抗既有模板）；
5. label collision：遥测比率 vs CARTO_LABEL_* 阈值；遥测缺席零 finding
   （诚实降级）；
6. overlay mismatch：planned∩observed=∅ 且观察在场 → error；
7. critique_map_state 聚合有界（≤12）；finalizer 并轨（增值披露）。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.services.gis_harness.map_critique import (
    C_BLANK_MAP,
    C_EXPORT_COMPLETENESS,
    C_INVALID_BOUNDS,
    C_LABEL_COLLISION,
    C_OVERLAY_MISMATCH,
    check_blank_map,
    check_invalid_bounds,
    check_label_collision,
    check_overlay_mismatch,
    check_publication_components,
    critique_map_state,
)


def _chapter(*, layers: int = 2) -> Dict[str, Any]:
    return {
        "plan_id": "planE",
        "query": "成都学校分布地图",
        "map_layers": [
            {"layer_id": f"layer-{i}", "role": "primary" if i == 0 else "secondary"}
            for i in range(layers)
        ],
    }


def _mapspec(*, components: Optional[list] = None,
             exports: Optional[list] = None) -> Dict[str, Any]:
    return {
        "components": components if components is not None else [],
        "exports": exports if exports is not None else ["png"],
    }


def _observation(**overrides) -> Dict[str, Any]:
    obs: Dict[str, Any] = {
        "layers": {
            "layer-0": {"mounted": True, "render_complete": True,
                        "feature_count": 42},
            "layer-1": {"mounted": True, "render_complete": True,
                        "feature_count": 7},
        },
    }
    obs.update(overrides)
    return obs


# ── blank map ────────────────────────────────────────────────────────────


def test_blank_map_detected_when_all_counts_zero():
    obs = _observation()
    obs["layers"] = {
        "layer-0": {"mounted": True, "render_complete": True, "feature_count": 0},
        "layer-1": {"mounted": True, "render_complete": True, "feature_count": 0},
    }
    findings = check_blank_map(_chapter(), obs)
    assert len(findings) == 1
    assert findings[0].code == C_BLANK_MAP
    assert findings[0].severity == "error"


def test_blank_map_absent_when_data_present_or_counts_missing():
    assert check_blank_map(_chapter(), _observation()) == []
    # 计数缺席的层不参与（不猜）—— 只有一个带计数的层且 >0 → 无 finding
    obs = _observation()
    obs["layers"] = {
        "layer-0": {"mounted": True, "render_complete": True, "feature_count": 0},
        "layer-1": {"mounted": True, "render_complete": True},
    }
    assert check_blank_map(_chapter(), obs) == []
    # 无观察 → 零 finding（诚实缺席）
    assert check_blank_map(_chapter(), None) == []


# ── invalid bounds ───────────────────────────────────────────────────────


def test_invalid_bounds_detected():
    ch = _chapter()
    ch["map_product"] = {"result_bbox": [104.1, 30.6, 103.9, 30.2]}  # 倒置
    findings = check_invalid_bounds(ch, None)
    assert len(findings) == 1
    assert findings[0].code == C_INVALID_BOUNDS
    assert findings[0].severity == "error"
    # 非有限
    ch["map_product"] = {"result_bbox": [float("nan"), 0, 1, 1]}
    assert check_invalid_bounds(ch, None)[0].code == C_INVALID_BOUNDS
    # 合法 bbox / 缺席 → 零
    ch["map_product"] = {"result_bbox": [103.9, 30.2, 104.1, 30.6]}
    assert check_invalid_bounds(ch, None) == []
    assert check_invalid_bounds(_chapter(), None) == []


# ── 出版件完整性 ─────────────────────────────────────────────────────────


def test_publication_components_missing_flagged_with_family():
    ch = _chapter()
    spec = _mapspec(components=[{"type": "title", "id": "title"}],
                    exports=["png"])
    findings = check_publication_components(ch, spec)
    codes = {f.target for f in findings}
    assert codes == {"north_arrow", "scale_bar"}
    assert all(f.code == C_EXPORT_COMPLETENESS for f in findings)
    assert all(f.severity == "warning" for f in findings)
    assert all(f.family == [f.target] for f in findings)  # 修复路由


def test_publication_components_no_demand_without_export_or_layers():
    ch = _chapter()
    spec = _mapspec(exports=["png"])
    assert check_publication_components(ch, spec) != []  # 有层有导出 → 要求
    assert check_publication_components(ch, _mapspec(exports=[])) == []
    assert check_publication_components(_chapter(layers=0), spec) == []
    # 组件齐备 → 零 finding
    full = _mapspec(components=[{"type": t} for t in (
        "title", "north_arrow", "scale_bar")], exports=["png"])
    assert check_publication_components(ch, full) == []


# ── label collision ──────────────────────────────────────────────────────


def test_label_collision_thresholds_and_absent_telemetry():
    assert check_label_collision(None) == []
    assert check_label_collision(_observation()) == []  # 无遥测键
    warn = check_label_collision(_observation(label_collision_ratio=0.15))
    assert warn[0].code == C_LABEL_COLLISION and warn[0].severity == "warning"
    fail = check_label_collision(_observation(label_collision_ratio=0.4))
    assert fail[0].severity == "error"
    ok = check_label_collision(_observation(label_collision_ratio=0.02))
    assert ok == []


# ── overlay mismatch ─────────────────────────────────────────────────────


def test_overlay_mismatch_when_no_planned_layer_observed():
    obs = _observation()
    obs["layers"] = {"basemap": {"mounted": True}}
    findings = check_overlay_mismatch(_chapter(), obs)
    assert len(findings) == 1
    assert findings[0].code == C_OVERLAY_MISMATCH
    assert findings[0].severity == "error"
    # 有交集 / 无观察 → 零
    assert check_overlay_mismatch(_chapter(), _observation()) == []
    assert check_overlay_mismatch(_chapter(), None) == []


# ── 聚合 ─────────────────────────────────────────────────────────────────


def test_critique_aggregate_bounded_and_pure():
    ch = _chapter()
    ch["map_product"] = {"result_bbox": [104.1, 30.6, 103.9, 30.2]}
    obs = _observation(label_collision_ratio=0.5)
    obs["layers"] = {"basemap": {"mounted": True}}
    spec = _mapspec(exports=["png"])
    a = critique_map_state(ch, spec, obs)
    b = critique_map_state(ch, spec, obs)
    assert [f.code for f in a] == [f.code for f in b]  # 纯函数
    codes = {f.code for f in a}
    assert {C_INVALID_BOUNDS, C_EXPORT_COMPLETENESS,
            C_LABEL_COLLISION, C_OVERLAY_MISMATCH} <= codes
    assert len(a) <= 12
    # 空章节 → 零
    assert critique_map_state(None) == []
    assert critique_map_state({}) == []
