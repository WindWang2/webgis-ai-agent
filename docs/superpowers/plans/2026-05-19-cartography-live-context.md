# 制图实时上下文与可视反馈 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 WebGIS 地图视图自解释——专题图生成后立即在地图上显示对应图例、指北针、比例尺、标题；聊天里给出可视化制图结果卡。

**Architecture:** 后端每个产出专题图的工具在返回 payload 顶层多带一个标准化的 `legend_spec` 字段 + 可选 `layer_meta.title`。前端把 `ThematicLegend` 拆为 router + 四个子组件（graduated / continuous / categorical / divergent），按 type 分派；新增 `MapDecorations` 浮层与 `CartographyResultCard` 聊天卡，三者共用「至少有一个带 legend_spec 的可见图层」这一个显示条件。改动纯加法，任何环节缺失/失败都静默降级。

**Tech Stack:** Python 3 / pytest（后端）；TypeScript / React / Vitest / React Testing Library / MapLibre GL / Zustand（前端）。

---

## 设计相对 spec 的两处细化

- **legend_spec 入 store 的位置**：spec 提到「`map-action-handler.tsx` 从 tool 结果抽 `legend_spec` 写入 store」。实际 `map-action-handler` 只调 MapLibre 渲染，store 的 `addLayer` 在 `frontend/app/page.tsx` 处理 SSE `step_result` 事件时调用——`legend_spec` 与 `layer_meta.title` 在那一处抽取写入。
- **Layer 类型字段**：spec 写「图层 metadata 上存 legend_spec」。实际 `Layer` 类型没有 `metadata` 字段，当前前端用 `(l.source as any).metadata` 偶发读取。本计划直接在 `Layer` interface 加 `legend_spec?: LegendSpec` 顶层字段，避免 `as any`，类型干净。

## 文件结构

**Backend:**

| 文件 | 职责 | 动作 |
|------|------|------|
| `app/services/cartography_service.py` | `build_legend_spec` 把 `build_thematic_style` 输出映射成对外契约 | 修改 |
| `app/tools/cartography.py` | `create_thematic_map` 返回 `legend_spec` + `layer_meta`；修正 `apply_layer_style` 描述里的孤儿工具引用 | 修改 |
| `app/tools/spatial.py` | `heatmap_data` raster/grid 模式输出 `continuous` legend_spec | 修改 |
| `app/tools/advanced_spatial.py` | `h3_binning` 输出 `graduated` legend_spec | 修改 |
| `app/tools/spatial_stats.py` | `kde_contours` 输出 `continuous` legend_spec | 修改 |
| `tests/test_cartography_service.py` | `build_legend_spec` 契约测试 | 新建 |
| `tests/test_cartography_tools.py` | 各工具 legend_spec 返回契约测试 | 新建 |

**Frontend — 类型 / Store:**

| 文件 | 职责 | 动作 |
|------|------|------|
| `frontend/lib/map-kit/types.ts` | `LegendSpec` 联合类型导出 | 修改 |
| `frontend/lib/types/layer.ts` | `Layer.legend_spec?: LegendSpec` 字段 | 修改 |
| `frontend/lib/store/hud-types.ts` | `cartographyTitle: string \| null`、`setCartographyTitle`、`focusLayerId: string \| null`、`focusLayer` | 修改 |
| `frontend/lib/store/slices/uiSlice.ts` | 实现以上 store 字段 | 修改 |

**Frontend — 图例组件:**

| 文件 | 职责 | 动作 |
|------|------|------|
| `frontend/components/map/legends/graduated-legend.tsx` | 从当前 thematic-legend.tsx 抽出 choropleth UI | 新建 |
| `frontend/components/map/legends/continuous-legend.tsx` | 色带 + min/max 标签 | 新建 |
| `frontend/components/map/legends/categorical-legend.tsx` | 类块 + 标签（含 LISA 5 类） | 新建 |
| `frontend/components/map/legends/divergent-legend.tsx` | stub，渲染同 ContinuousLegend | 新建 |
| `frontend/components/map/thematic-legend.tsx` | 改造为 router，按 `legend_spec.type` 分派 | 修改 |
| `frontend/components/map/thematic-legend.test.tsx` | 抽出到 graduated-legend.test.tsx；router 行为新测试 | 修改 |
| `frontend/components/map/legends/graduated-legend.test.tsx` | 沿用原有 choropleth 测试 | 新建 |
| `frontend/components/map/legends/continuous-legend.test.tsx` | 色带 + min/max | 新建 |
| `frontend/components/map/legends/categorical-legend.test.tsx` | 类块渲染 | 新建 |

**Frontend — 地图装饰 + 聊天卡 + 集成:**

| 文件 | 职责 | 动作 |
|------|------|------|
| `frontend/components/map/map-decorations.tsx` | NorthArrow + ScaleBar + MapTitle 三个子组件 | 新建 |
| `frontend/components/map/map-decorations.test.tsx` | 可见性条件、刻度计算 | 新建 |
| `frontend/components/map/map-panel.tsx` | 挂载 MapDecorations + 多图层图例堆叠 | 修改 |
| `frontend/components/chat/cartography-result-card.tsx` | 制图结果卡 | 新建 |
| `frontend/components/chat/cartography-result-card.test.tsx` | 色板渲染 + 「高亮此图层」 | 新建 |
| `frontend/components/chat/tool-call-card.tsx` | 按工具名分派到 CartographyResultCard | 修改 |
| `frontend/app/page.tsx` | 抽 `data.result.legend_spec` 与 `data.result.layer_meta.title` 写入 store | 修改 |

---

### Task 1: 后端 `build_legend_spec` 契约转换函数

**Files:**
- Modify: `app/services/cartography_service.py`
- Create: `tests/test_cartography_service.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_cartography_service.py`：

```python
"""cartography_service.build_legend_spec 契约测试。"""
from app.services.cartography_service import CartographyService


def test_build_legend_spec_choropleth():
    style_def = {
        "type": "choropleth",
        "field": "pop",
        "breaks": [0.0, 100.0, 500.0, 1000.0],
        "colors": ["#fff", "#aaa", "#000"],
        "legend_labels": ["0-100", "100-500", "500-1000"],
    }
    spec = CartographyService.build_legend_spec(style_def, palette="YlOrRd")
    assert spec["type"] == "graduated"
    assert spec["field"] == "pop"
    assert spec["breaks"] == [0.0, 100.0, 500.0, 1000.0]
    assert spec["palette"] == "YlOrRd"
    assert spec["palette_colors"] == ["#fff", "#aaa", "#000"]


def test_build_legend_spec_lisa_to_categorical():
    style_def = {
        "type": "lisa",
        "field": "pop",
        "categories": ["HH", "LL", "HL", "LH", "NS"],
        "colors": {
            "HH": "#ff0000", "LL": "#0000ff",
            "HL": "#ffaaaa", "LH": "#aaaaff", "NS": "#cccccc",
        },
        "legend_labels": ["High-High", "Low-Low", "High-Low", "Low-High", "Not Significant"],
    }
    spec = CartographyService.build_legend_spec(style_def)
    assert spec["type"] == "categorical"
    assert spec["field"] == "pop"
    assert len(spec["categories"]) == 5
    hh = next(c for c in spec["categories"] if c["key"] == "HH")
    assert hh["color"] == "#ff0000"
    assert hh["label"] == "High-High"


def test_build_legend_spec_unknown_type_returns_none():
    assert CartographyService.build_legend_spec({"type": "what"}) is None
    assert CartographyService.build_legend_spec(None) is None
    assert CartographyService.build_legend_spec({}) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cartography_service.py -v`
Expected: FAIL — `AttributeError: type object 'CartographyService' has no attribute 'build_legend_spec'`

- [ ] **Step 3: 实现 build_legend_spec**

在 `app/services/cartography_service.py` 的 `CartographyService` 类末尾追加：

```python
    @classmethod
    def build_legend_spec(
        cls,
        style_def: Optional[Dict[str, Any]],
        palette: str = "YlOrRd",
    ) -> Optional[Dict[str, Any]]:
        """把 build_thematic_style 的输出映射为对外 legend_spec 契约。

        choropleth → graduated；lisa → categorical。未知 / 空输入返回 None。
        """
        if not isinstance(style_def, dict):
            return None
        t = style_def.get("type")
        if t == "choropleth":
            return {
                "type": "graduated",
                "field": style_def.get("field", ""),
                "breaks": style_def.get("breaks", []),
                "palette": palette,
                "palette_colors": style_def.get("colors", []),
            }
        if t == "lisa":
            colors = style_def.get("colors", {}) or {}
            labels = style_def.get("legend_labels", []) or []
            keys = style_def.get("categories", []) or []
            categories = []
            for i, key in enumerate(keys):
                categories.append({
                    "key": key,
                    "color": colors.get(key, "#999999"),
                    "label": labels[i] if i < len(labels) else key,
                })
            return {
                "type": "categorical",
                "field": style_def.get("field", ""),
                "categories": categories,
            }
        return None
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_cartography_service.py -v`
Expected: PASS（3 个测试）

- [ ] **Step 5: Commit**

```bash
git add app/services/cartography_service.py tests/test_cartography_service.py
git commit -m "feat(cartography): add build_legend_spec contract converter"
```

---

### Task 2: `create_thematic_map` 输出 legend_spec + layer_meta

**Files:**
- Modify: `app/tools/cartography.py`
- Create: `tests/test_cartography_tools.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_cartography_tools.py`：

```python
"""制图工具的 legend_spec 契约测试。"""
import pytest

from app.tools.registry import ToolRegistry
from app.tools.cartography import register_cartography_tools


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_cartography_tools(r)
    return r


@pytest.mark.asyncio
async def test_create_thematic_map_returns_legend_spec(registry):
    gj = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]},
             "properties": {"pop": 10.0}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 0]},
             "properties": {"pop": 100.0}},
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [2, 0]},
             "properties": {"pop": 500.0}},
        ],
    }
    out = await registry.dispatch("create_thematic_map", {
        "geojson": gj, "field": "pop", "method": "equal_interval", "k": 3,
    })
    assert "legend_spec" in out
    assert out["legend_spec"]["type"] == "graduated"
    assert out["legend_spec"]["field"] == "pop"
    assert "layer_meta" in out
    assert "title" in out["layer_meta"]
    assert "pop" in out["layer_meta"]["title"]  # 标题包含字段名
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cartography_tools.py::test_create_thematic_map_returns_legend_spec -v`
Expected: FAIL — `KeyError: 'legend_spec'` 或 `AssertionError`

- [ ] **Step 3: 修改 create_thematic_map 实现**

把 `app/tools/cartography.py` 中 `create_thematic_map` 函数体内的 `return` 整段替换为：

```python
            return_dict = {
                "geojson": data,  # return unmodified geojson
                "group": group,
                "style": style_def,
            }
            legend_spec = CartographyService.build_legend_spec(style_def, palette=palette)
            if legend_spec is not None:
                return_dict["legend_spec"] = legend_spec
                return_dict["layer_meta"] = {
                    "title": f"{field} 专题图",
                }
            return return_dict
```

（即在原来的 `return {"geojson": data, "group": group, "style": style_def}` 之后，新增 legend_spec 与 layer_meta 注入。）

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_cartography_tools.py::test_create_thematic_map_returns_legend_spec -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/cartography.py tests/test_cartography_tools.py
git commit -m "feat(tools): create_thematic_map emits legend_spec + layer_meta"
```

---

### Task 3: `heatmap_data` raster/grid 模式输出 continuous legend_spec

**Files:**
- Modify: `app/tools/spatial.py`
- Modify: `tests/test_cartography_tools.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_cartography_tools.py` 末尾追加：

```python
from app.tools.spatial import register_spatial_tools


@pytest.fixture
def spatial_registry():
    r = ToolRegistry()
    register_spatial_tools(r)
    return r


def _points(n: int):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [i * 0.001, i * 0.001]},
             "properties": {}}
            for i in range(n)
        ],
    }


@pytest.mark.asyncio
async def test_heatmap_native_no_legend_spec(spatial_registry):
    out = await spatial_registry.dispatch("heatmap_data", {
        "geojson": _points(20), "render_type": "native",
    })
    assert "legend_spec" not in out  # 原生渲染不产生离散图例


@pytest.mark.asyncio
async def test_heatmap_grid_emits_continuous_legend_spec(spatial_registry):
    out = await spatial_registry.dispatch("heatmap_data", {
        "geojson": _points(20), "render_type": "grid",
    })
    assert out.get("legend_spec", {}).get("type") == "continuous"
    spec = out["legend_spec"]
    assert "min" in spec and "max" in spec
    assert len(spec["palette_colors"]) >= 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cartography_tools.py -k heatmap -v`
Expected: FAIL — `legend_spec` 不在返回里

- [ ] **Step 3: 修改 heatmap_data 实现**

在 `app/tools/spatial.py` 的 `heatmap_data` 函数体里，找到 `return res_data` 之前（即 `res_data["command"] = ...` 块之后），插入 legend_spec 注入：

```python
        if result.get("success"):
            res_data = result.get("data")
            if isinstance(res_data, dict):
                if render_type == "raster":
                    res_data["command"] = "add_heatmap_raster"
                else:
                    res_data["command"] = "add_layer"
                # 非 native 模式输出 continuous legend_spec
                if render_type != "native":
                    from app.services.cartography_service import COLOR_PALETTES
                    palette_colors = COLOR_PALETTES.get(palette) \
                        or COLOR_PALETTES.get("YlOrRd", ["#ffffb2", "#feb24c", "#bd0026"])
                    res_data["legend_spec"] = {
                        "type": "continuous",
                        "min": float(res_data.get("min_value", 0.0)),
                        "max": float(res_data.get("max_value", 1.0)),
                        "palette": palette,
                        "palette_colors": list(palette_colors),
                    }
            return res_data
```

> 注：`COLOR_PALETTES` 是 service 里已有的调色板字典；若访问失败按 YlOrRd 兜底。`res_data` 里 `min_value`/`max_value` 由热力图生成器写入；若缺失则用 0/1 兜底（前端会忽略色带的数值标签，仅显示色块）。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_cartography_tools.py -k heatmap -v`
Expected: PASS（2 个测试）

- [ ] **Step 5: Commit**

```bash
git add app/tools/spatial.py tests/test_cartography_tools.py
git commit -m "feat(tools): heatmap_data emits continuous legend_spec (non-native modes)"
```

---

### Task 4: `h3_binning` 输出 graduated legend_spec

**Files:**
- Modify: `app/tools/advanced_spatial.py`
- Modify: `tests/test_cartography_tools.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_cartography_tools.py` 末尾追加：

```python
from app.tools.advanced_spatial import register_advanced_spatial_tools


@pytest.fixture
def advanced_registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


@pytest.mark.asyncio
async def test_h3_binning_emits_graduated_legend_spec(advanced_registry):
    pts = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0 + i*0.01, 30.0 + i*0.01]},
             "properties": {}}
            for i in range(40)
        ],
    }
    out = await advanced_registry.dispatch("h3_binning", {
        "geojson": pts, "resolution": 7, "stat_method": "count",
    })
    spec = out.get("legend_spec")
    assert spec is not None
    assert spec["type"] == "graduated"
    assert len(spec["breaks"]) >= 2
    assert len(spec["palette_colors"]) >= 2
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cartography_tools.py -k h3_binning -v`
Expected: FAIL — `spec is None`

- [ ] **Step 3: 修改 h3_binning 实现**

把 `app/tools/advanced_spatial.py` 中 `h3_binning` 函数体替换为：

```python
    def h3_binning(geojson: Any, resolution: int = 8, stat_field: str = None, stat_method: str = 'count') -> dict:
        from app.lib.geo_analysis.aggregation import h3_binning as _h3_binning
        data = safe_parse_geojson(geojson)
        res = _h3_binning(data, resolution, stat_field, stat_method)
        payload = res.to_llm_response()
        # 从聚合后的要素计算分位数 breaks，输出 graduated legend_spec
        try:
            out_geojson = payload.get("geojson") if isinstance(payload, dict) else None
            stat_field_name = stat_field or "count"
            if isinstance(out_geojson, dict):
                values = [
                    float(f.get("properties", {}).get(stat_field_name))
                    for f in out_geojson.get("features", [])
                    if isinstance(f.get("properties", {}).get(stat_field_name), (int, float))
                ]
                if len(values) >= 2:
                    from app.services.cartography_service import CartographyService, COLOR_PALETTES
                    breaks = CartographyService.classify(values, "quantiles", 5)
                    palette = "YlOrRd"
                    palette_colors = list(COLOR_PALETTES.get(palette, []))[:5]
                    if isinstance(payload, dict):
                        payload["legend_spec"] = {
                            "type": "graduated",
                            "field": stat_field_name,
                            "breaks": breaks,
                            "palette": palette,
                            "palette_colors": palette_colors,
                        }
        except Exception as e:  # noqa: BLE001 — legend 失败不影响主结果
            import logging
            logging.getLogger(__name__).warning(f"[h3_binning] legend_spec 构造失败: {e}")
        return payload
```

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_cartography_tools.py -k h3_binning -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/tools/advanced_spatial.py tests/test_cartography_tools.py
git commit -m "feat(tools): h3_binning emits graduated legend_spec"
```

---

### Task 5: `kde_contours` 输出 continuous legend_spec

**Files:**
- Modify: `app/tools/spatial_stats.py`
- Modify: `tests/test_cartography_tools.py`

- [ ] **Step 1: 追加失败测试**

在 `tests/test_cartography_tools.py` 末尾追加：

```python
from app.tools.spatial_stats import register_spatial_stats_tools


@pytest.fixture
def stats_registry():
    r = ToolRegistry()
    register_spatial_stats_tools(r)
    return r


@pytest.mark.asyncio
async def test_kde_contours_emits_continuous_legend_spec(stats_registry):
    pts = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0 + i*0.001, 30.0 + i*0.001]},
             "properties": {}}
            for i in range(20)
        ],
    }
    out = await stats_registry.dispatch("kde_contours", {
        "geojson": pts, "levels": 6,
    })
    if "error" in out:  # scipy / matplotlib 不可用时跳过
        pytest.skip(out["error"])
    spec = out.get("legend_spec")
    assert spec is not None
    assert spec["type"] == "continuous"
    assert spec["min"] < spec["max"]
    assert len(spec["palette_colors"]) >= 3
```

- [ ] **Step 2: 运行测试确认失败**

Run: `pytest tests/test_cartography_tools.py -k kde -v`
Expected: FAIL — `legend_spec is None`（或 SKIP 若环境无 scipy/matplotlib）

- [ ] **Step 3: 修改 kde_contours 实现**

在 `app/tools/spatial_stats.py` 的 `kde_contours` 函数体里，找到最终 `return ...`（返回带 `geojson` 的 dict 那行）之前，插入：

```python
        # 输出 continuous legend_spec（等值面的 level 值即为色带 min/max）
        if out_features:
            level_vals = [float(f.get("properties", {}).get("level", 0.0)) for f in out_features]
            try:
                from app.services.cartography_service import COLOR_PALETTES
                palette = "Viridis"
                palette_colors = list(COLOR_PALETTES.get(palette, []))
                legend_spec = {
                    "type": "continuous",
                    "min": min(level_vals),
                    "max": max(level_vals),
                    "palette": palette,
                    "palette_colors": palette_colors[:5] if palette_colors else ["#440154", "#21908c", "#fde725"],
                }
            except Exception:
                legend_spec = None
        else:
            legend_spec = None
```

然后把最终 `return {...}` 改为同时包含 `legend_spec`：

```python
        result_dict = {
            "type": "FeatureCollection",
            "features": out_features,
            "command": "add_layer",
        }
        if legend_spec is not None:
            result_dict["legend_spec"] = legend_spec
        return result_dict
```

> 注：原 return 的具体字段以代码现状为准（保留 type/features/command 等）；本步只是在返回 dict 上加一个可选字段。

- [ ] **Step 4: 运行测试确认通过**

Run: `pytest tests/test_cartography_tools.py -k kde -v`
Expected: PASS 或 SKIP（环境依赖未装时）

- [ ] **Step 5: Commit**

```bash
git add app/tools/spatial_stats.py tests/test_cartography_tools.py
git commit -m "feat(tools): kde_contours emits continuous legend_spec"
```

---

### Task 6: 修正 `apply_layer_style` 描述里的孤儿工具引用

**Files:**
- Modify: `app/tools/cartography.py`

- [ ] **Step 1: 确认问题**

Run: `grep -n 'apply_thematic_style\|update_layer_appearance' app/tools/cartography.py`
Expected: 命中 `apply_layer_style` 描述里 `何时不用` 段的两处引用——这两个工具实际不存在。

- [ ] **Step 2: 修改描述**

把 `app/tools/cartography.py` 中 `apply_layer_style` 的 `description` 参数中以下两行：

```
"\n何时不用：(1) 按属性值分级着色 (主题图) — 用 apply_thematic_style；"
"(2) 修改已加载图层的样式 — 用 update_layer_appearance；"
```

替换为：

```
"\n何时不用：(1) 按属性值分级着色 (主题图) — 用 create_thematic_map；"
"(2) 想做交互过滤 — 用 apply_layer_filter；"
```

并删除原来的 `(3) 想做交互过滤 — 用 apply_layer_filter。` 这一行（已合并到 (2)）。

- [ ] **Step 3: 验证修复**

Run: `grep -n 'apply_thematic_style\|update_layer_appearance' app/tools/cartography.py`
Expected: 无命中

- [ ] **Step 4: Commit**

```bash
git add app/tools/cartography.py
git commit -m "fix(tools): apply_layer_style description points to real tool names"
```

---

### Task 7: 前端 `LegendSpec` 类型 + `Layer.legend_spec` 字段

**Files:**
- Modify: `frontend/lib/map-kit/types.ts`
- Modify: `frontend/lib/types/layer.ts`

- [ ] **Step 1: 在 `frontend/lib/map-kit/types.ts` 末尾追加 LegendSpec 类型导出**

```typescript
export type LegendCategoryEntry = { key: string; color: string; label: string };

export type GraduatedLegendSpec = {
  type: 'graduated';
  field: string;
  breaks: number[];
  palette: string;
  palette_colors: string[];
  unit?: string;
  format?: 'number' | 'percent' | 'currency';
};

export type ContinuousLegendSpec = {
  type: 'continuous';
  field?: string;
  min: number;
  max: number;
  palette: string;
  palette_colors: string[];
};

export type CategoricalLegendSpec = {
  type: 'categorical';
  field: string;
  categories: LegendCategoryEntry[];
};

export type DivergentLegendSpec = {
  type: 'divergent';
  field?: string;
  center: number;
  min: number;
  max: number;
  palette: string;
  palette_colors: string[];
};

export type LegendSpec =
  | GraduatedLegendSpec
  | ContinuousLegendSpec
  | CategoricalLegendSpec
  | DivergentLegendSpec;
```

- [ ] **Step 2: 修改 `Layer` 类型加入 `legend_spec`**

在 `frontend/lib/types/layer.ts` 顶部 import 段补充：

```typescript
import type { LegendSpec } from '@/lib/map-kit/types';
```

在 `Layer` interface 末尾（`updated_at?: string;` 之后、闭括号之前）追加一行：

```typescript
  legend_spec?: LegendSpec;
```

- [ ] **Step 3: TypeScript 编译验证**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无新增错误（类型纯加，不破坏既有代码）

- [ ] **Step 4: Commit**

```bash
git add frontend/lib/map-kit/types.ts frontend/lib/types/layer.ts
git commit -m "feat(types): add LegendSpec union and Layer.legend_spec field"
```

---

### Task 8: Store 新增 `cartographyTitle` + `focusLayer`

**Files:**
- Modify: `frontend/lib/store/hud-types.ts`
- Modify: `frontend/lib/store/slices/uiSlice.ts`

- [ ] **Step 1: 在 hud-types.ts 的 HudState 接口里追加字段**

找到 `frontend/lib/store/hud-types.ts` 中 `interface HudState` 内 UI slice 相关字段段落，追加：

```typescript
  /* ─── Cartography Live Context ─── */
  cartographyTitle: string | null;
  setCartographyTitle: (title: string | null) => void;
  focusLayerId: string | null;
  focusLayer: (layerId: string | null) => void;
```

- [ ] **Step 2: 在 uiSlice.ts 里实现**

在 `frontend/lib/store/slices/uiSlice.ts` 的 slice 返回对象里（与 `viewport`、`baseLayer` 等同层）追加：

```typescript
  cartographyTitle: null,
  setCartographyTitle: (title) => set({ cartographyTitle: title }),
  focusLayerId: null,
  focusLayer: (layerId) => set({ focusLayerId: layerId }),
```

- [ ] **Step 3: 单元测试**

在 `frontend/lib/store/slices.test.ts`（或新建 `uiSlice.test.ts`）追加：

```typescript
describe('cartography slice', () => {
  beforeEach(() => {
    useHudStore.setState({ cartographyTitle: null, focusLayerId: null });
  });

  it('setCartographyTitle updates title', () => {
    useHudStore.getState().setCartographyTitle('成都人口分布');
    expect(useHudStore.getState().cartographyTitle).toBe('成都人口分布');
  });

  it('focusLayer sets focusLayerId', () => {
    useHudStore.getState().focusLayer('layer-1');
    expect(useHudStore.getState().focusLayerId).toBe('layer-1');
  });

  it('focusLayer(null) clears focusLayerId', () => {
    useHudStore.getState().focusLayer('layer-1');
    useHudStore.getState().focusLayer(null);
    expect(useHudStore.getState().focusLayerId).toBeNull();
  });
});
```

> 若 `slices.test.ts` 中已有相应 import（`useHudStore` 等），沿用即可；否则按文件首部已有 import 风格补全。

- [ ] **Step 4: 运行测试**

Run: `cd frontend && npm test -- slices.test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/lib/store/hud-types.ts frontend/lib/store/slices/uiSlice.ts frontend/lib/store/slices.test.ts
git commit -m "feat(store): add cartographyTitle and focusLayer to ui slice"
```

---

### Task 9: 抽出 `GraduatedLegend` 组件（保留原 choropleth UI）

**Files:**
- Create: `frontend/components/map/legends/graduated-legend.tsx`
- Create: `frontend/components/map/legends/graduated-legend.test.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/components/map/legends/graduated-legend.test.tsx`：

```typescript
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { GraduatedLegend } from './graduated-legend';

const spec = {
  type: 'graduated' as const,
  field: 'pop',
  breaks: [0, 100, 500, 1000],
  palette: 'YlOrRd',
  palette_colors: ['#fff', '#aaa', '#000'],
};

describe('GraduatedLegend', () => {
  it('renders all class rows with break ranges', () => {
    render(<GraduatedLegend spec={spec} />);
    expect(screen.getByText(/0/)).toBeInTheDocument();
    expect(screen.getByText(/pop/)).toBeInTheDocument();
    // 3 classes => 3 rows
    expect(screen.getAllByRole('button').length).toBe(3);
  });

  it('clicking a row toggles visibility and fires onFilterChange', () => {
    const onFilterChange = vi.fn();
    render(<GraduatedLegend spec={spec} onFilterChange={onFilterChange} />);
    const rows = screen.getAllByRole('button');
    fireEvent.click(rows[0]);
    expect(onFilterChange).toHaveBeenCalled();
    // 第一类被隐藏后剩 2 个 range
    const ranges = onFilterChange.mock.calls.at(-1)?.[0];
    expect(ranges).toHaveLength(2);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- graduated-legend`
Expected: FAIL — 找不到 `./graduated-legend`

- [ ] **Step 3: 实现 GraduatedLegend**

创建 `frontend/components/map/legends/graduated-legend.tsx`：

```typescript
'use client';

import React, { useEffect, useState } from 'react';
import { Info, Eye, EyeOff } from 'lucide-react';
import type { GraduatedLegendSpec } from '@/lib/map-kit/types';

interface Props {
  spec: GraduatedLegendSpec;
  onFilterChange?: (visibleBreaks: number[][]) => void;
}

const formatNum = (n: number) => {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return n.toFixed(1);
};

export function GraduatedLegend({ spec, onFilterChange }: Props) {
  const { field, breaks, palette_colors } = spec;
  const classCount = Math.max(0, breaks.length - 1);
  const [visible, setVisible] = useState<boolean[]>(() => new Array(classCount).fill(true));

  useEffect(() => {
    setVisible(new Array(classCount).fill(true));
  }, [classCount]);

  if (!breaks || breaks.length < 2) return null;

  const toggle = (idx: number) => {
    const next = [...visible];
    next[idx] = !next[idx];
    setVisible(next);
    if (onFilterChange) {
      const ranges = breaks.slice(0, -1)
        .map((v, i) => (next[i] ? [v, breaks[i + 1]] : null))
        .filter((r): r is number[] => r !== null);
      onFilterChange(ranges);
    }
  };

  return (
    <div className="bg-card/90 backdrop-blur-md border border-border p-4 rounded-xl shadow-2xl min-w-[200px] animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 mb-3 border-b border-border pb-2">
        <div className="p-1 bg-primary/10 rounded-md">
          <Info className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] uppercase font-bold tracking-widest text-muted-foreground/80">图例说明</span>
          <span className="text-xs font-semibold text-foreground truncate max-w-[140px]" title={field}>
            字段: {field}
          </span>
        </div>
      </div>
      <div className="space-y-2">
        {breaks.slice(0, -1).map((val, idx) => {
          const nextVal = breaks[idx + 1];
          const colorIdx = Math.min(idx, palette_colors.length - 1);
          const isVisible = visible[idx];
          return (
            <div
              key={idx}
              role="button"
              tabIndex={0}
              className={`flex items-center justify-between group transition-all cursor-pointer hover:bg-muted/30 p-1 rounded-md ${!isVisible ? 'opacity-50' : ''}`}
              onClick={() => toggle(idx)}
              onKeyDown={(e) => { if (e.key === 'Enter') toggle(idx); }}
            >
              <div className="flex items-center gap-3">
                <div
                  className="w-3.5 h-3.5 rounded-sm shadow-sm ring-1 ring-black/10 group-hover:scale-110 transition-transform"
                  style={{ backgroundColor: palette_colors[colorIdx] }}
                />
                <span className="text-[11px] font-medium text-muted-foreground group-hover:text-foreground transition-colors">
                  {formatNum(val)} — {formatNum(nextVal)}
                </span>
              </div>
              <div className="flex items-center">
                {isVisible
                  ? <Eye className="h-3 w-3 text-primary/70" />
                  : <EyeOff className="h-3 w-3 text-muted-foreground/50" />}
              </div>
            </div>
          );
        })}
      </div>
      <div className="mt-4 pt-2 border-t border-border/40 text-[9px] text-muted-foreground/60 italic text-center">
        数据驱动专题渲染
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- graduated-legend`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/map/legends/graduated-legend.tsx frontend/components/map/legends/graduated-legend.test.tsx
git commit -m "feat(legends): add GraduatedLegend extracted from ThematicLegend"
```

---

### Task 10: 新增 `ContinuousLegend`

**Files:**
- Create: `frontend/components/map/legends/continuous-legend.tsx`
- Create: `frontend/components/map/legends/continuous-legend.test.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/components/map/legends/continuous-legend.test.tsx`：

```typescript
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ContinuousLegend } from './continuous-legend';

const spec = {
  type: 'continuous' as const,
  field: 'density',
  min: 0,
  max: 100,
  palette: 'Viridis',
  palette_colors: ['#440154', '#21908c', '#fde725'],
};

describe('ContinuousLegend', () => {
  it('renders min and max labels', () => {
    render(<ContinuousLegend spec={spec} />);
    expect(screen.getByText('0.0')).toBeInTheDocument();
    expect(screen.getByText('100.0')).toBeInTheDocument();
  });

  it('renders the field name', () => {
    render(<ContinuousLegend spec={spec} />);
    expect(screen.getByText(/density/)).toBeInTheDocument();
  });

  it('omits field row when no field given', () => {
    render(<ContinuousLegend spec={{ ...spec, field: undefined }} />);
    expect(screen.queryByText(/字段:/)).toBeNull();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- continuous-legend`
Expected: FAIL

- [ ] **Step 3: 实现 ContinuousLegend**

创建 `frontend/components/map/legends/continuous-legend.tsx`：

```typescript
'use client';

import { Info } from 'lucide-react';
import type { ContinuousLegendSpec } from '@/lib/map-kit/types';

interface Props {
  spec: ContinuousLegendSpec;
}

const fmt = (n: number) => {
  if (Math.abs(n) >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (Math.abs(n) >= 1_000) return (n / 1_000).toFixed(1) + 'k';
  return n.toFixed(1);
};

export function ContinuousLegend({ spec }: Props) {
  const { field, min, max, palette_colors } = spec;
  const gradient = `linear-gradient(to right, ${palette_colors.join(', ')})`;
  return (
    <div className="bg-card/90 backdrop-blur-md border border-border p-4 rounded-xl shadow-2xl min-w-[200px] animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 mb-3 border-b border-border pb-2">
        <div className="p-1 bg-primary/10 rounded-md">
          <Info className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] uppercase font-bold tracking-widest text-muted-foreground/80">图例说明</span>
          {field && (
            <span className="text-xs font-semibold text-foreground truncate max-w-[140px]" title={field}>
              字段: {field}
            </span>
          )}
        </div>
      </div>
      <div className="space-y-2">
        <div className="h-3 rounded-sm shadow-inner" style={{ background: gradient }} />
        <div className="flex justify-between text-[11px] text-muted-foreground">
          <span>{fmt(min)}</span>
          <span>{fmt(max)}</span>
        </div>
      </div>
      <div className="mt-4 pt-2 border-t border-border/40 text-[9px] text-muted-foreground/60 italic text-center">
        连续密度渲染
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- continuous-legend`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/map/legends/continuous-legend.tsx frontend/components/map/legends/continuous-legend.test.tsx
git commit -m "feat(legends): add ContinuousLegend with gradient bar"
```

---

### Task 11: 新增 `CategoricalLegend`

**Files:**
- Create: `frontend/components/map/legends/categorical-legend.tsx`
- Create: `frontend/components/map/legends/categorical-legend.test.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/components/map/legends/categorical-legend.test.tsx`：

```typescript
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { CategoricalLegend } from './categorical-legend';

const lisaSpec = {
  type: 'categorical' as const,
  field: 'pop',
  categories: [
    { key: 'HH', color: '#ff0000', label: 'High-High' },
    { key: 'LL', color: '#0000ff', label: 'Low-Low' },
    { key: 'HL', color: '#ffaaaa', label: 'High-Low' },
    { key: 'LH', color: '#aaaaff', label: 'Low-High' },
    { key: 'NS', color: '#cccccc', label: 'Not Significant' },
  ],
};

describe('CategoricalLegend', () => {
  it('renders all category labels', () => {
    render(<CategoricalLegend spec={lisaSpec} />);
    expect(screen.getByText('High-High')).toBeInTheDocument();
    expect(screen.getByText('Low-Low')).toBeInTheDocument();
    expect(screen.getByText('Not Significant')).toBeInTheDocument();
  });

  it('renders 5 color swatches', () => {
    const { container } = render(<CategoricalLegend spec={lisaSpec} />);
    const swatches = container.querySelectorAll('[data-testid="cat-swatch"]');
    expect(swatches.length).toBe(5);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- categorical-legend`
Expected: FAIL

- [ ] **Step 3: 实现 CategoricalLegend**

创建 `frontend/components/map/legends/categorical-legend.tsx`：

```typescript
'use client';

import { Info } from 'lucide-react';
import type { CategoricalLegendSpec } from '@/lib/map-kit/types';

interface Props {
  spec: CategoricalLegendSpec;
}

export function CategoricalLegend({ spec }: Props) {
  const { field, categories } = spec;
  return (
    <div className="bg-card/90 backdrop-blur-md border border-border p-4 rounded-xl shadow-2xl min-w-[200px] animate-in slide-in-from-right-4 duration-500">
      <div className="flex items-center gap-2 mb-3 border-b border-border pb-2">
        <div className="p-1 bg-primary/10 rounded-md">
          <Info className="h-3.5 w-3.5 text-primary" />
        </div>
        <div className="flex flex-col">
          <span className="text-[10px] uppercase font-bold tracking-widest text-muted-foreground/80">图例说明</span>
          <span className="text-xs font-semibold text-foreground truncate max-w-[140px]" title={field}>
            字段: {field}
          </span>
        </div>
      </div>
      <div className="space-y-2">
        {categories.map((c) => (
          <div key={c.key} className="flex items-center gap-3 p-1">
            <div
              data-testid="cat-swatch"
              className="w-3.5 h-3.5 rounded-sm shadow-sm ring-1 ring-black/10"
              style={{ backgroundColor: c.color }}
            />
            <span className="text-[11px] font-medium text-muted-foreground">{c.label}</span>
          </div>
        ))}
      </div>
      <div className="mt-4 pt-2 border-t border-border/40 text-[9px] text-muted-foreground/60 italic text-center">
        分类专题
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- categorical-legend`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/map/legends/categorical-legend.tsx frontend/components/map/legends/categorical-legend.test.tsx
git commit -m "feat(legends): add CategoricalLegend for LISA and category thematics"
```

---

### Task 12: `DivergentLegend` stub

**Files:**
- Create: `frontend/components/map/legends/divergent-legend.tsx`

- [ ] **Step 1: 实现（直接复用 ContinuousLegend 的色带 UI，等未来再补 center 标签）**

创建 `frontend/components/map/legends/divergent-legend.tsx`：

```typescript
'use client';

import type { DivergentLegendSpec, ContinuousLegendSpec } from '@/lib/map-kit/types';
import { ContinuousLegend } from './continuous-legend';

interface Props {
  spec: DivergentLegendSpec;
}

export function DivergentLegend({ spec }: Props) {
  // Stub：把 divergent 当作 continuous 显示。center 字段先忽略，
  // 等具体 divergent 工具（hotspot z-score）落地时再补 center 标记的 UI。
  const asContinuous: ContinuousLegendSpec = {
    type: 'continuous',
    field: spec.field,
    min: spec.min,
    max: spec.max,
    palette: spec.palette,
    palette_colors: spec.palette_colors,
  };
  return <ContinuousLegend spec={asContinuous} />;
}
```

- [ ] **Step 2: TypeScript 编译验证**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 3: Commit**

```bash
git add frontend/components/map/legends/divergent-legend.tsx
git commit -m "feat(legends): add DivergentLegend stub (renders as continuous)"
```

---

### Task 13: 把 `ThematicLegend` 改造为 router

**Files:**
- Modify: `frontend/components/map/thematic-legend.tsx`
- Modify: `frontend/components/map/thematic-legend.test.tsx`（若不存在则新建）

- [ ] **Step 1: 写 router 测试**

替换 `frontend/components/map/thematic-legend.test.tsx` 全文为：

```typescript
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ThematicLegend } from './thematic-legend';

describe('ThematicLegend (router)', () => {
  it('routes graduated spec to GraduatedLegend', () => {
    render(<ThematicLegend spec={{
      type: 'graduated', field: 'pop',
      breaks: [0, 50, 100],
      palette: 'YlOrRd',
      palette_colors: ['#ff0', '#f00'],
    }} />);
    expect(screen.getByText(/pop/)).toBeInTheDocument();
  });

  it('routes continuous spec to ContinuousLegend', () => {
    render(<ThematicLegend spec={{
      type: 'continuous', field: 'd',
      min: 0, max: 1, palette: 'Viridis',
      palette_colors: ['#440154', '#fde725'],
    }} />);
    expect(screen.getByText('0.0')).toBeInTheDocument();
    expect(screen.getByText('1.0')).toBeInTheDocument();
  });

  it('routes categorical spec to CategoricalLegend', () => {
    render(<ThematicLegend spec={{
      type: 'categorical', field: 'pop',
      categories: [{ key: 'HH', color: '#f00', label: 'High-High' }],
    }} />);
    expect(screen.getByText('High-High')).toBeInTheDocument();
  });

  it('returns null for unknown legend_spec', () => {
    const { container } = render(<ThematicLegend spec={null as any} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- thematic-legend`
Expected: FAIL —— 旧组件签名不接受 `spec` prop（仍是 metadata + breaks）

- [ ] **Step 3: 替换 ThematicLegend 实现为 router**

把 `frontend/components/map/thematic-legend.tsx` 全文替换为：

```typescript
'use client';

import type { LegendSpec } from '@/lib/map-kit/types';
import { GraduatedLegend } from './legends/graduated-legend';
import { ContinuousLegend } from './legends/continuous-legend';
import { CategoricalLegend } from './legends/categorical-legend';
import { DivergentLegend } from './legends/divergent-legend';

interface Props {
  spec: LegendSpec | null | undefined;
  onFilterChange?: (visibleBreaks: number[][]) => void;
}

export function ThematicLegend({ spec, onFilterChange }: Props) {
  if (!spec) return null;
  switch (spec.type) {
    case 'graduated':
      return <GraduatedLegend spec={spec} onFilterChange={onFilterChange} />;
    case 'continuous':
      return <ContinuousLegend spec={spec} />;
    case 'categorical':
      return <CategoricalLegend spec={spec} />;
    case 'divergent':
      return <DivergentLegend spec={spec} />;
    default: {
      // exhaustive check
      const _exhaustive: never = spec;
      void _exhaustive;
      console.warn('[ThematicLegend] 未知 legend_spec 类型', spec);
      return null;
    }
  }
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- thematic-legend`
Expected: PASS（4 个测试）

- [ ] **Step 5: Commit**

```bash
git add frontend/components/map/thematic-legend.tsx frontend/components/map/thematic-legend.test.tsx
git commit -m "refactor(legend): convert ThematicLegend into router by spec.type"
```

---

### Task 14: `app/page.tsx` 写入 legend_spec 与标题

**Files:**
- Modify: `frontend/app/page.tsx`

- [ ] **Step 1: 定位 SSE 处理段**

Run: `grep -n 'addLayer\|geojson_ref\|data\.tool' frontend/app/page.tsx | head -20`
Expected: 找到约 330 行的 `useHudStore.getState().addLayer({...})`。

- [ ] **Step 2: 修改 SSE 处理段**

在 `frontend/app/page.tsx` 中 `useHudStore.getState().addLayer({...})` 的对象字面量末尾加一个 `legend_spec` 字段（从 `data.result?.legend_spec` 读取），并在 addLayer 调用之前/之后写入标题。修改后整段大致如下：

```typescript
      if (data.geojson_ref || data.result?.image) {
        const layerId = `layer-${Date.now()}`;
        const layerName = data.tool === 'search_poi' ? `搜索结果: ${data.name || 'POI'}` :
                         data.tool === 'heatmap_data' ? '热力图分析' : `分析结果: ${data.tool}`;
        const accentColor = useHudStore.getState().accentColor;
        const legendSpec = data.result?.legend_spec ?? null;
        const layerMetaTitle = data.result?.layer_meta?.title ?? null;
        useHudStore.getState().addLayer({
          id: layerId,
          name: layerName,
          type: data.result?.image ? 'heatmap' : 'vector',
          visible: true,
          opacity: 1,
          group: 'analysis',
          source: data.geojson_ref ? { type: 'FeatureCollection', features: [], metadata: { ref_id: data.geojson_ref } } as any : data.result,
          style: { color: accentColor },
          _refId: data.geojson_ref,
          legend_spec: legendSpec || undefined,
        });
        if (layerMetaTitle) {
          useHudStore.getState().setCartographyTitle(layerMetaTitle);
        }
```

（其余 fetch geojson 代码原样保留。）

- [ ] **Step 3: 验证 TypeScript 编译**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 4: Commit**

```bash
git add frontend/app/page.tsx
git commit -m "feat(chat): plumb legend_spec and layer_meta.title from tool result into store"
```

---

### Task 15: 新增 `MapDecorations` 浮层组件

**Files:**
- Create: `frontend/components/map/map-decorations.tsx`
- Create: `frontend/components/map/map-decorations.test.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/components/map/map-decorations.test.tsx`：

```typescript
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { MapDecorations } from './map-decorations';

describe('MapDecorations', () => {
  it('renders nothing when show=false', () => {
    const { container } = render(<MapDecorations show={false} title="X" zoom={10} centerLat={30} bearing={0} />);
    expect(container.firstChild).toBeNull();
  });

  it('renders north arrow, scale bar, and title when show=true', () => {
    render(<MapDecorations show={true} title="成都人口分布" zoom={10} centerLat={30} bearing={0} />);
    expect(screen.getByText('成都人口分布')).toBeInTheDocument();
    expect(screen.getByTestId('north-arrow')).toBeInTheDocument();
    expect(screen.getByTestId('scale-bar')).toBeInTheDocument();
  });

  it('hides title chip when title is null', () => {
    render(<MapDecorations show={true} title={null} zoom={10} centerLat={30} bearing={0} />);
    expect(screen.queryByTestId('map-title')).toBeNull();
    // 但指北针和比例尺仍在
    expect(screen.getByTestId('north-arrow')).toBeInTheDocument();
  });

  it('scale bar reflects zoom level (smaller meters/px at higher zoom)', () => {
    const { rerender } = render(<MapDecorations show={true} title={null} zoom={10} centerLat={30} bearing={0} />);
    const text10 = screen.getByTestId('scale-bar').textContent ?? '';
    rerender(<MapDecorations show={true} title={null} zoom={16} centerLat={30} bearing={0} />);
    const text16 = screen.getByTestId('scale-bar').textContent ?? '';
    expect(text10).not.toBe(text16);
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- map-decorations`
Expected: FAIL

- [ ] **Step 3: 实现 MapDecorations**

创建 `frontend/components/map/map-decorations.tsx`：

```typescript
'use client';

import React from 'react';
import { Compass } from 'lucide-react';

interface Props {
  show: boolean;
  title: string | null;
  zoom: number;
  centerLat: number;
  bearing: number;
}

// 把 zoom + 纬度换算成米/像素，再算成最近的友好刻度（50/100/200/500/1000/2000/5000/10000m）
function computeScale(zoom: number, lat: number): { meters: number; pixels: number } {
  const EARTH_CIRCUMFERENCE = 40_075_016.686;
  const metersPerPixel = (EARTH_CIRCUMFERENCE * Math.cos((lat * Math.PI) / 180)) / Math.pow(2, zoom + 8);
  const targetPixels = 100;
  const targetMeters = metersPerPixel * targetPixels;
  const candidates = [50, 100, 200, 500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000];
  let best = candidates[0];
  for (const c of candidates) {
    if (c <= targetMeters) best = c;
  }
  return { meters: best, pixels: best / metersPerPixel };
}

function formatMeters(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(m % 1000 === 0 ? 0 : 1)} km` : `${m} m`;
}

export function MapDecorations({ show, title, zoom, centerLat, bearing }: Props) {
  if (!show) return null;
  const { meters, pixels } = computeScale(zoom, centerLat);

  return (
    <>
      {title && (
        <div
          data-testid="map-title"
          className="absolute top-3 left-1/2 -translate-x-1/2 z-30 px-4 py-1.5 rounded-full bg-card/90 backdrop-blur-md border border-border shadow-lg text-sm font-semibold text-foreground"
        >
          {title}
        </div>
      )}
      <div
        data-testid="north-arrow"
        className="absolute top-3 right-3 z-30 p-2 rounded-full bg-card/90 backdrop-blur-md border border-border shadow-lg"
        style={{ transform: `rotate(${-bearing}deg)` }}
        aria-label="指北针"
      >
        <Compass className="h-4 w-4 text-foreground" />
      </div>
      <div
        data-testid="scale-bar"
        className="absolute bottom-10 right-3 z-30 px-2 py-1 rounded-md bg-card/90 backdrop-blur-md border border-border shadow-lg text-[11px] font-medium text-foreground flex items-center gap-2"
      >
        <div className="border-b-2 border-l-2 border-r-2 border-foreground" style={{ width: `${Math.round(pixels)}px`, height: '6px' }} />
        <span>{formatMeters(meters)}</span>
      </div>
    </>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `cd frontend && npm test -- map-decorations`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/components/map/map-decorations.tsx frontend/components/map/map-decorations.test.tsx
git commit -m "feat(map): add MapDecorations (north arrow, scale bar, title chip)"
```

---

### Task 16: 在 `map-panel.tsx` 挂载装饰 + 多图层图例堆叠

**Files:**
- Modify: `frontend/components/map/map-panel.tsx`

- [ ] **Step 1: 定位现有图例渲染段**

Run: `grep -n 'ThematicLegend\|Legend' frontend/components/map/map-panel.tsx`
Expected: 找到约 436-447 行原 `{/* Legend — floating bottom left */}` 段。

- [ ] **Step 2: 替换图例渲染段并挂载 MapDecorations**

把 `frontend/components/map/map-panel.tsx` 中：

```typescript
      {/* Legend — floating bottom left */}
      {layers.find((l) => l.visible && (l.source as any)?.metadata?.thematic_type === "choropleth") && (
        <div className="absolute bottom-4 left-4 z-30">
          {(() => {
            const tl = layers.find((l) => l.visible && (l.source as any)?.metadata?.thematic_type === "choropleth")
            // ... 现有内部
              <ThematicLegend
```

整段替换为下面的多图层堆叠版本（保留 import；旁边按需引入 `MapDecorations` 与 store hooks）：

```typescript
      {/* Live cartography overlays — driven by layer.legend_spec */}
      {(() => {
        const thematicLayers = layers.filter((l) => l.visible && l.legend_spec);
        if (thematicLayers.length === 0) return null;
        return (
          <>
            <div className="absolute bottom-4 left-4 z-30 space-y-3">
              {thematicLayers.map((l) => (
                <div key={l.id}>
                  <div className="text-[10px] uppercase tracking-widest text-muted-foreground/60 mb-1 px-1">{l.name}</div>
                  <ThematicLegend spec={l.legend_spec!} />
                </div>
              ))}
            </div>
            <MapDecorations
              show={true}
              title={cartographyTitle ?? thematicLayers[0]?.name ?? null}
              zoom={viewport?.zoom ?? 10}
              centerLat={viewport?.center?.[1] ?? 30}
              bearing={viewport?.bearing ?? 0}
            />
          </>
        );
      })()}
```

并在文件顶部 import 区追加：

```typescript
import { MapDecorations } from "./map-decorations"
```

并在组件函数体顶部已有 `const ... = useHudStore(...)` 同一处追加：

```typescript
const cartographyTitle = useHudStore((s) => s.cartographyTitle);
const viewport = useHudStore((s) => s.viewport);
```

> `viewport` 在 `uiSlice` 已存在为 `{ center: [lng, lat], zoom, bearing, pitch, bounds? }`（已验证），直接读取即可。

- [ ] **Step 3: 验证 TypeScript 编译 + 跑相关测试**

Run: `cd frontend && npx tsc --noEmit && npm test -- map-panel`
Expected: 无类型错误；现有 map-panel 测试若有则通过

- [ ] **Step 4: Commit**

```bash
git add frontend/components/map/map-panel.tsx
git commit -m "feat(map): stack legends per thematic layer and mount MapDecorations"
```

---

### Task 17: 新增 `CartographyResultCard` 并接入 `tool-call-card`

**Files:**
- Create: `frontend/components/chat/cartography-result-card.tsx`
- Create: `frontend/components/chat/cartography-result-card.test.tsx`
- Modify: `frontend/components/chat/tool-call-card.tsx`

- [ ] **Step 1: 写失败测试**

创建 `frontend/components/chat/cartography-result-card.test.tsx`：

```typescript
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { CartographyResultCard } from './cartography-result-card';

const result = {
  legend_spec: {
    type: 'graduated' as const,
    field: 'pop',
    breaks: [0, 100, 500, 1000],
    palette: 'YlOrRd',
    palette_colors: ['#fff', '#aaa', '#000'],
  },
  layer_meta: { title: '成都人口分布' },
};

describe('CartographyResultCard', () => {
  it('renders title and field info', () => {
    render(<CartographyResultCard result={result} layerId="layer-1" />);
    expect(screen.getByText('成都人口分布')).toBeInTheDocument();
    expect(screen.getByText(/pop/)).toBeInTheDocument();
  });

  it('renders palette swatches', () => {
    const { container } = render(<CartographyResultCard result={result} layerId="layer-1" />);
    const swatches = container.querySelectorAll('[data-testid="card-swatch"]');
    expect(swatches.length).toBe(3);
  });

  it('clicking 高亮 button calls focusLayer with layerId', () => {
    const focusLayer = vi.fn();
    render(<CartographyResultCard result={result} layerId="layer-1" onFocus={focusLayer} />);
    fireEvent.click(screen.getByText(/高亮此图层/));
    expect(focusLayer).toHaveBeenCalledWith('layer-1');
  });

  it('returns null when no legend_spec', () => {
    const { container } = render(<CartographyResultCard result={{}} layerId="x" />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: 运行测试确认失败**

Run: `cd frontend && npm test -- cartography-result-card`
Expected: FAIL

- [ ] **Step 3: 实现 CartographyResultCard**

创建 `frontend/components/chat/cartography-result-card.tsx`：

```typescript
'use client';

import { Palette, Target } from 'lucide-react';
import type { LegendSpec } from '@/lib/map-kit/types';

interface Props {
  result: { legend_spec?: LegendSpec; layer_meta?: { title?: string } } | null | undefined;
  layerId: string;
  onFocus?: (layerId: string) => void;
}

function summarize(spec: LegendSpec): string {
  switch (spec.type) {
    case 'graduated':
      return `${spec.field} · ${spec.breaks.length - 1} 分级`;
    case 'continuous':
      return `${spec.field ?? '密度'} · 连续色带`;
    case 'categorical':
      return `${spec.field} · ${spec.categories.length} 类`;
    case 'divergent':
      return `${spec.field ?? '指标'} · 发散色带`;
  }
}

function swatches(spec: LegendSpec): string[] {
  switch (spec.type) {
    case 'graduated':
    case 'continuous':
    case 'divergent':
      return spec.palette_colors;
    case 'categorical':
      return spec.categories.map((c) => c.color);
  }
}

export function CartographyResultCard({ result, layerId, onFocus }: Props) {
  const spec = result?.legend_spec;
  if (!spec) return null;
  const title = result?.layer_meta?.title ?? '专题图';
  const colors = swatches(spec);
  return (
    <div className="my-2 p-3 rounded-lg border border-border bg-card/70">
      <div className="flex items-center gap-2 mb-2">
        <Palette className="h-4 w-4 text-primary" />
        <span className="text-sm font-semibold text-foreground truncate">{title}</span>
      </div>
      <div className="flex items-center gap-1 mb-2">
        {colors.map((c, i) => (
          <div
            key={i}
            data-testid="card-swatch"
            className="w-5 h-3 rounded-sm ring-1 ring-black/10"
            style={{ backgroundColor: c }}
          />
        ))}
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[11px] text-muted-foreground">{summarize(spec)}</span>
        <button
          type="button"
          onClick={() => onFocus?.(layerId)}
          className="inline-flex items-center gap-1 text-[11px] font-medium text-primary hover:underline"
        >
          <Target className="h-3 w-3" />
          高亮此图层
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 在 tool-call-card 里按工具名分派**

修改 `frontend/components/chat/tool-call-card.tsx`：

1) 在顶部 import 区追加：

```typescript
import { CartographyResultCard } from './cartography-result-card';
import { useHudStore } from '@/lib/store/useHudStore';
```

2) 在 `ToolCallRow` 组件函数体内（`const parsedArgs = parseArgs(call.arguments);` 这一行之后）追加：

```typescript
  const CARTO_TOOLS = new Set(['create_thematic_map', 'h3_binning', 'kde_contours', 'heatmap_data']);
  const focusLayer = useHudStore((s) => s.focusLayer);
  const layers = useHudStore((s) => s.layers);
  const isCarto = CARTO_TOOLS.has(call.tool);
  // 取最近一个带 legend_spec 的图层做高亮目标（addLayer 把最新图层 push 到 layers[0]）
  const latestCartoLayerId = isCarto
    ? layers.find((l) => l.legend_spec)?.id ?? ''
    : '';
```

3) 在 `ToolCallRow` 的 JSX 里，**`</button>` 关闭后、`{open && (`  打开之前** 插入条件渲染（让 carto 卡常驻可见，不依赖折叠展开）：

```typescript
        {isCarto && call.result && (
          <CartographyResultCard
            result={call.result as any}
            layerId={latestCartoLayerId}
            onFocus={(id) => id && focusLayer(id)}
          />
        )}
```

- [ ] **Step 5: 运行测试确认通过**

Run: `cd frontend && npm test -- cartography-result-card`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/components/chat/cartography-result-card.tsx frontend/components/chat/cartography-result-card.test.tsx frontend/components/chat/tool-call-card.tsx
git commit -m "feat(chat): add CartographyResultCard and dispatch from tool-call-card"
```

---

### Task 18: 全量回归

**Files:** 无（仅验证）

- [ ] **Step 1: 后端完整测试**

Run: `pytest tests/ -q`
Expected: 全部通过；新增的 `test_cartography_service.py`、`test_cartography_tools.py` 在内

- [ ] **Step 2: 前端完整测试**

Run: `cd frontend && npm test`
Expected: 全部通过；新增 graduated/continuous/categorical-legend、thematic-legend、map-decorations、cartography-result-card 测试在内

- [ ] **Step 3: TypeScript 检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: 无错误

- [ ] **Step 4: 人工冒烟（可选，启动 dev server）**

Run（两个终端）：

```bash
# 后端
uvicorn app.main:app --reload

# 前端
cd frontend && npm run dev
```

在浏览器对话框输入「画一个成都市三甲医院的密度热力图」/「做某 GeoJSON 的人口分位专题图」/「跑一次 h3_binning」，确认：

- 地图右下出现指北针，右下方出现比例尺，顶部出现标题片
- 左下角出现对应类型的图例
- 聊天里工具结果上方出现带色板的制图反馈卡，点「高亮此图层」会 fitBounds 并把 store.focusLayerId 设为该图层

---

## 验收标准

- 触发 `create_thematic_map` / `h3_binning` / `kde_contours` / `heatmap_data`（非 native）任一工具后，前端三处同步出现：
  - 浮窗图例（按 type 匹配的子组件）
  - 指北针 + 比例尺 + 标题片
  - 聊天里的 `CartographyResultCard`
- 隐藏全部带 `legend_spec` 的图层后，三处装饰同步消失
- LISA 专题图渲染出 5 类 categorical 图例，含 HH/LL/HL/LH/NS 标签
- 工具返回里没有 `legend_spec`（旧行为）时，地图不报错、不显示新组件
- 既有 `thematic-legend.test.tsx` 旧用例迁移到 `graduated-legend.test.tsx` 后全部通过
- `apply_layer_style` 工具描述里不再引用 `apply_thematic_style` / `update_layer_appearance`
