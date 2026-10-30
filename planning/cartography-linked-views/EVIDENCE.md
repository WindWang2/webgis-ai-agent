# EVIDENCE — Track B（carto-linked-views-labels）

## 提交序列（本分支）

| SHA | 里程碑 | 内容 |
|---|---|---|
| 24d4c6a87 | M1/M2 | 查询契约 v2（order/where/total/位置 id 投影/聚合端点） |
| a69c4186c | M3 | MVT field 1 位置 id + feature_id_index O(1) |
| 072467dbc | M4 | TablePanel 服务端分页模式 |
| 6bf437f58 | M5/M6 | 位置 id 单要素兜底 + 联动收口 |
| 8b81571d7 | M7/M8 | ChartPanel MVT 视口聚合 + 证据行 |
| f1f2dca3b | M9/M10 | 导出标注三项收敛（双孪生） |
| a1782bab7 | M14 | Harness 浏览/聚合工具 |
| 1745e4f73 | M13 | map_critique 导出标注 findings |
| f5209d1c5 | M15 | 三档性能基准 |
| e958b6689 | M15 | 场景 5 端到端一致性 |

## 性能实测（tests/perf/test_trackb_paging_benchmark.py，本机 Python 3.17-venv/3.13，2026-10-31）

| n | 首页 p50/p95 | 排序首建 p50/p95（后续缓存） | 过滤页 p50/p95 | 精确 total | 聚合 count | MVT 瓦片（含位置 id） |
|---|---|---|---|---|---|---|
| 5k | 0.6/1.8ms | 17.5/21.5ms | 2.6/2.7ms | 14.6ms | 9.8ms | 1.3ms（10 点） |
| 50k | 0.7/0.8ms | 170/348ms | 2.4/2.9ms | 150ms | 100ms | 6.8ms（84 点） |
| 200k | 0.6/0.8ms | 689/1034ms | 2.3/2.4ms | 587ms | 365ms | 26.6ms（334 点） |

- 排序 permutation 按 (ref,revision,order) 缓存，首建一次后续页 O(1) 切片。
- total_matching 精确全扫同样缓存；>250k 返回 null（total_unknown，绝不假冒）。
- MVT 位置 id 开销：每要素 ~7 次 dict 查链，26.6ms/334 点瓦片内不可见。

## M0 六场景证据映射

1. **200k MVT 分页浏览**：tests/test_layer_query_v2_http.py（HTTP 契约）+ tests/unit/test_feature_query_v2.py（纯函数）+ paged-table-data.test.tsx（hook）+ table-panel-paged.test.tsx（DOM）+ 性能基准。不全量下载：fetchFeaturePage 每页 ≤200，hook 状态机只持已翻页行。
2. **框选→表格定位→图表高亮→反选**：selection-store 既有六向联动（inline）+ 新 $id 位置 id 链（table-panel-paged row click → publishSelection('$id') ↔ ['in',['to-string',['id']]] 地图过滤）；cross-view-scenarios.test.ts 回归绿。
3. **过滤切换/刷新/旧请求**：paged-table-data.test.tsx「sort change aborts in-flight; stale response never lands」+ viewport-aggregate generation 取消。
4. **视口统计**：chart-panel-agg.test.tsx（服务端窗口真值+证据行+truncated 徽标）+ test_layer_query_v2_http.py 聚合/分页 parity（两通道匹配集恒等）。
5. **各区分布+组件+标签+统计图**：tests/cartography/test_trackb_scenario_consistency.py（图表数据==聚合通道计数；组件齐全；labels 编译；分页元数据）。
6. **A4/PDF/SVG CJK + 标注边界 + budget**：test_label_trackb_vnext.py（CJK 折行/线 repeat/priority 名次）+ mapspec-to-svg.trackb.test.ts（TS 孪生同 invariants）+ 既有 test_export_sample_pdf_text.py（PDF CJK 文本层）回归绿。

错误路径：403（HTTP 测试 notFound/permission 分支）/409（revision guard 两端）/429（hook 单次退避）/404（越界位置 id）/bbox 倒置 400/畸形 where 400/未知 op 400/重复 id 披露（duplicate_id_count）/MVT tiles unavailable（前端 network 错误态）。

## 容许差异披露（live vs export，非伪一致）

- 碰撞引擎机制不同（MapLibre GPU 逐帧 vs 确定性单遍）——架构声明（label_engine.py:3-8）。
- 晕圈色：live haloMode=auto 随渲染期底图亮度自适应；导出遵循 spec 声明值（缺省 #ffffff）。两侧都是 spec 驱动，差异来源是渲染时机，已披露不假装收敛。
- 导出固定 zoom 一次裁决 vs live 动态 zoomBands（v1.6 方向 5 声明）。
- keep-upright 双语义分歧域受 parity corpus 冻结。

## 已知限制

1. 表格行→地图 flyTo 未实现：组件级相机通道不存在（map-panel.tsx/interaction/bus.ts 均为 #1873 占用区）；行点击已发布 selection（携带 id_field），相机消费留给后续。
2. chart 来源选择对分页表只过滤已加载页（诚实窗口语义，未下推类别 where）。
3. 磁盘/Redis 后端的 200k 端到端（含真实 json.loads）未在本机复现——HTTP 层以 mock store 验证协议，服务函数以真实 200k FC 验证计算。
4. openapi snapshot 待最终提交时统一刷新（与 #1871/#1873 的生成物冲突最小化策略）。
