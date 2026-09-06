# Cartographic Design System V3

> ADR-0101 的实现总览。分支：`feat/cartographic-template-library-v3`。

## Map Product 组成

```text
Map Product
  = Map Model（表达模型 —— model_library + model_packs，50 个）
  + Layer Roles（primary/secondary/reference → layerId 绑定）
  + Composition Template（组合模板 —— seed 8 + domain packs 20）
  + Component Slots（槽位基数/位置/fallback/bind_scope）
  + Component Variants（变体模板 65+，一等公民目录事实）
  + Theme / Tokens（themes.py 描述层；色带真值 palettes.py，chrome 真值前端 token）
  + Layout Constraints（layout_solver V2 确定性求解）
  + Output Target（interactive / png / pdf / svg / print —— 同一 Map Product 的不同 renderer）
```

## 模块地图

| 域 | 真值 | 生成目录 |
|---|---|---|
| 表达模型 | `app/lib/cartography/model_library.py` + `model_packs/` | [map-model-catalog.md](map-model-catalog.md) |
| 组件/变体 | `component_registry.py` + `component_templates.py` | [component-catalog.md](component-catalog.md) |
| 组合模板 | `composition_templates.py` + `composition_packs/` | [composition-template-catalog.md](composition-template-catalog.md) |
| 主题/色带 | `themes.py`（描述）+ `palettes.py`（色带 hex）+ 前端 tokens（chrome） | [theme-palette-catalog.md](theme-palette-catalog.md) |
| 渲染 parity | `component_renderers.py` 单一矩阵 | [renderer-parity-matrix.md](renderer-parity-matrix.md) |
| 布局求解 | `layout_solver.py`（后端）+ `frontend/lib/map-components/resolve-layout.ts`（像素） | [layout-solver.md](layout-solver.md) |
| 导出 catalog | `export_component_catalog.py` → `frontend/lib/map-components/component-catalog.generated.json` | （JSON，schemaVersion 3） |
| 黄金语料 | `tests/cartography/golden_corpus/` | 141 用例 |

## 核心不变量

1. **单一真值**：每类事实只有一个权威（矩阵对账 / catalog 漂移检查强制）。
2. **诚实性**：native 只授予运行时机制齐备的模型/变体；其余 planned +
   强制 pitfalls 披露；planned 永不进入最终产品（resolver 双层门控）。
3. **确定性**：registry 查询、resolver/composer、布局求解、golden 语料
   全部同输入同输出。
4. **Additive**：seed id / 既有 fallback 链 / 旧消费者行为不变（测试锁定）。

## 与 ADR-0088 的关系

0088 的契约全部继承（图例族 binding 级冲突、all_thematic 逐层展开、
inset 纯 SVG 投影、annotation 三形态、floating placement、archetype
regression guard）。V3 只做扩容与补齐，不改语义。
