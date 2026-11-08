# MAP_PRODUCT_GOLDENS — 专业制图黄金案例集(G04-CARTOGRAPHY-COMPOSER)

> 状态:**可复现**(2026-11-08)。每个案例经真实生产链生成与断言:
> `project_selection_facts`(唯一投影器)→ `generate_design_candidates`(表达资格门)
> → `apply_contract`(契约重放)→ `compile_mapspec_to_svg_detailed`(真矢量编译)
> →(打印档)`prepare_publication_document`(solve_layout_v5 求解)。
> **不是截图摆样子** —— 断言的是结构事实、资格门与诚实披露;数据为确定性本地
> fixture(真实 GeoJSON 结构),无网络/无外部服务。

## 复现命令

```bash
# 全部 7 个黄金案例(确定性,可重复运行)
python -m pytest tests/cartography/test_map_product_goldens_g04.py -v

# 单案例(示例:A4 正式专题图)
python -m pytest tests/cartography/test_map_product_goldens_g04.py::test_golden_a4_formal_thematic_map -v
```

## 案例矩阵

| # | 案例 | intent/几何 | 真实链断言要点 |
|---|---|---|---|
| 0 | **empty-state 空数据诚实** | 无 profile/无图层 | `point_count=0/basis=unknown`(不虚构);空 spec 编译无装饰性图例(`chrome-legend` 缺席);契约重放不造假组件 |
| 1 | **成都小学点位+行政统计+图表** | distribution_overview / Point(24)+overlay Polygon | 点族候选在场(`point` family);overlay 面证据扩展 `admin_boundary_set`;图例标题+`单位:所`入图;点位入图 |
| 2 | **重庆医院可达性** | accessibility_analysis / LineString+overlay Point | 线网图例 `单位:分钟` 入图;overlay 点扩展可行域 |
| 3 | **四川自然资源专题(面分类)** | aggregation_overview / Polygon(数值+文本字段) | 面统计族(`area_stat`);数值字段在场 → continuous(测量层级粗判口径) |
| 4 | **行政区多变量地图** | comparison_analysis / 双数值字段 | bivariate 真实计算:3×3 色阵(行主序 9 色)+ 象限图例 spec;area_stat 族在场 |
| 5 | **时序变化** | change_detection / Polygon + temporal 双帧 | change 族在场;时序注记 `时间: 2024(粒度 year,窗口[…])` 进入导出图面 |
| 6 | **A4 正式专题图** | aggregation + a4_landscape 打印档 | 求解 `evaluated`+page_profile;锁 `legend-main` 族零触碰(`user_lock_kept`);**求解事实不落持久 spec**(`layout.constraint_layout` 缺席);求解克隆仍可编译且回执/图面同真 |

## 案例与能力的对应覆盖

- 图例锁(用户锁族语义)→ 案例 6
- A4 长中文标题/打印档 → 案例 6(+36 条既有 publication_cases golden)
- 单位与样式匹配 → 案例 1/2/6(单位入图断言)
- 空数据 → 案例 0
- 跨比例尺/多尺度 → 既有 `tests/cartography/multiscale/` + `test_export_representation_parity_v1`(zoom 门/表达窗口)
- 图例与实际分类同步 → `test_render_export_parity_g04.py::TestLegendClassificationSync`
- 实际导出 receipt → `TestCanvasConstraintReceiptSidecar` + `TestVectorPdfConstraintReceipt`
- 组件改色不重跑 GIS → `test_incremental_semantics_g04.py`

## 不同时空范围的扩展

案例函数参数化于 `_admin_fc/_points_fc/_lines_fc(cx, cy)` 坐标基点 —— 换城市/换范围即换基点重放(确定性)。时序案例的 frames 表可注入任意粒度(year/month/day)。
