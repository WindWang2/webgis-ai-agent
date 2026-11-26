# EVIDENCE — 04 多尺度制图综合（20261124 轮）

全部数字为**同机实测**（本 worktree，Python 3.12.15 / shapely 2.2.0，
本机为多任务共享机；跑批时其余 Agent 有轻负载），非捏造百分比。

## 性能（tests/cartography/multiscale/test_paper_engines_perf_v1.py，
`MSP_PERF=1 pytest -k TestPerfHeavy` 显式跑，确定性合成数据）

| 场景 | 输入 | wall time | 备注 |
|---|---|---|---|
| network_select 20k | 20k 段路网（400 行链 + 399 纵向连接），国道保留 + 桥接 | **1.29s** | bridges=13333、fully_connected=True；全队列 BFS（此前逐对 BFS 重建超时） |
| eliminate + topology | 506 面 22×23 共享边网格（容差 120m） | **0.09s** | chains_shared_multi=829（结点切分后）、fallback=0、无缝审计过 |
| point_aggregate | 5k 点（格 2000m） | **0.01s** | reps=18、Σ守恒 |

对照既有引擎（master M8 基准，同套件 test_topology_perf_v1）：50k 单遍多带
16.4s / 200k 66.1s —— 本轮新引擎在其邻接面（消除/桥接/聚合）为亚秒~秒级，
无既有路径回归（cartography 全量 3781 passed 不变）。

## 正确性 oracle（独立几何/图审计，非被测函数自证）

- 共享边两侧输出坐标逐字节一致（byte-identical 测试，抖动中点两侧同塌）；
- 逐对 intersection 面积 < 1 m² 且 union 覆盖差 < 1 m²（2/3/三链/对边共享/
  6×6 抖动网格/12×12 网格）；
- 22×23 网格 chains_shared_multi=829（结点切分修复前为 0 —— 修复有真实
  效力，非空洞通过）；
- 数值等级归一化匹配（2/2.0/"2" 同义）、20k 全连通、计数/总量 Σ守恒
  （2.5k/5k 点、行政区人口字段）；
- 面积口径 = 外环−洞（与 shapely 同口径）。

## 已知误差界

- 拓扑引擎面积预算缺省 2%（契约可覆盖）；抖动 oracle 用 ~5m 偏移
  （≈0.1% 面积），远低于预算 —— 预算阻断路径由 budget=1e-9 用例真实触发
  （链保护传播 → 第二轮原坐标重建）。

## 未执行 / not_evaluated

- 真实浏览器 journey（断连重连/截图视觉对照）：无浏览器环境，导出证据
  为真实 SVG 编译 + 结构化 feature/legend 断言（test_paper_export_journey_v1）；
  视觉对照标 not_evaluated。
- 线上 CI：未等待（本地可复现证据为准）。
