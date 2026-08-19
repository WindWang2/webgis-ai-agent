---
name: heatmap
description: 热力图（密度分布）标准流程——POI 检索 → 原生热力图层挂载 → 制图 harness 导出，MapLibre 官方范式
---

# 热力图（Heatmap / 密度分布）

用户问「XX 的分布情况 / 密度 / 聚集程度」时走本技能。渲染采用 MapLibre
官方 create-a-heatmap-layer 范式：zoom 插值 radius/intensity + 密度多停靠点
色带（首段透明），单点/小簇/密集核分级可辨。

## Pi 调用路径（对话内即时渲染）

1. **取点数据**（中国境内 POI 用本地高德库）：
   `query_local_poi(district='成都市', category='科教文化服务', subtype='高等院校')`
   - subtype 是「大类;小类」的小类段：高校=`高等院校`（口语"大学/高校"自动
     别名映射）、小学/中学/幼儿园/职业技术学校/科研机构/培训机构；
   - 行政区查询用 `district`（adcode 精确归属），不要用 bbox；
   - 命中超 limit 时服务端按 fid 均匀采样，`total_matched` 为真实命中数。
2. **生成热力图**（首选 native）：
   `heatmap_data(geojson=<上一步 ref>, render_type='native', palette='classic')`
   - palette：`classic`（蓝→青→绿→黄→橙→红，默认）/ `magma` / `viridis` / `thermal`；
   - `radius` 是米制搜索半径，渲染端自动防御换算（MapLibre 用像素）；
   - 结果自动经 MapSpec 授权挂载为 **type=heatmap 图层**（`result_ref` +
     `layer_id` + `commands=[{add_layer}]`），前端原生渲染并带同源渐变图例——
     **不要**再用 display_layer 重复挂载。
3. **验证**：返回 `success=true` 且 `layer_id` 存在即已挂载；如需叠加区县统计
   （分级设色），另用 `get_local_child_districts` + `spatial_aggregate` +
   `create_thematic_map`，与热力图层并存。

## 制图 harness 路径（导出/离线渲染）

- **栅格热力图**：`heatmap_data(geojson=..., render_type='raster')` →
  服务端 matplotlib 预渲染 PNG（透明度按密度，非零格 98 分位归一）。
- **版面导出**：`export_thematic_map(...)`（A4/300DPI，含指北针比例尺），
  composite 的 `heatmap_density` 槽位承接热力图层。
- **语义检查**：harness 的 GEOMETRY_LAYER_TYPE 已认可 Point→heatmap；
  paint↔legend 由 `palettes.NATIVE_HEATMAP_COLORS` 单一色源保证一致
  （图例 6 色与前端 heatmap-color 停靠点逐色相同）。

## 常见错误

- ❌ subtype 传「大学」→ 已自动映射「高等院校」；其他杜撰子类会返回该范围
  真实子类分布提示，按提示重试一次即可。
- ❌ 点数极少（<10）时热力图不具统计意义——直接 circle 图层展示点即可。
- ❌ 需要每格统计值/等值面时不要用热力图：网格计数用 `h3_binning`，
  等值面用 `kde_contours`，连续概率面用 `kde_surface`。
