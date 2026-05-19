---
name: spatial-pro
description: 专业 GIS 空间分析套件。支持 H3 空间聚类、LISA 热点分析、网络通达性及制图导出。当需要进行高级空间分析、网格统计或高质量专题图制图时调用。
---

# GIS Pro Suite v2 (Spatial-Pro)

## 功能概览

本技能旨在固化“精准分析协议”：
1. **行政区锁边界**：先拿边界 (get_admin_division)
2. **多边形搜索**：精准范围取点 (search_poi_polygon)
3. **空间统计**：执行 H3 网格化 (h3_binning)、LISA 聚类 (h3_lisa)
4. **高质量制图**：调用制图导出 (export_thematic_map)

## 核心工作流

### 1. 区域分析流
当用户要求“分析某区分布”时，直接执行以下序列：
1. get_admin_division(keywords='[区域名]', return_geometry='polygon')
2. search_poi_polygon(polygon=ref:last_result, keyword='[类型]')
3. 执行分析工具链 (h3_binning, moran_i_narrated)
4. create_thematic_map + export_thematic_map

### 2. 交互式探针
使用 query_map_features(location=[lng, lat]) 读取底图上任意点的属性信息，配合 apply_layer_filter 对分析结果进行实时筛选。
