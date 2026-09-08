# Progress

## 2026-09-06
- Phase 1 契约层修复完成并 commit（7da8351）：A1 输出⊆能力校验、A2 include_planned、
  A3 raster_cells/estimated_bytes 消费、A6 过时注释、A5 remote.change.raster 元数据。
- method_references +24（99 total），commit。
- 点格局 V3 完成（本人实现）：space_time_k（Diggle 1995，时间置换+各向同性空间校正）、
  mantel_test（标准化 r + 时间置换）、cross_pair_correlation g12（随机标记包络）、
  G/F 边缘校正（none/border/isotropic，缺省行为逐位不变）。
  +3 算法 / +3 能力 / +3 契约 / g_f_j 契约 v2 / +3 工具 / 8 测试（44 过含回归）。
- Terrain V3 完成（agent）：terrain.horizon_angle / terrain.sky_view_factor（共用射线行走）、
  D8 flat_routing="epsilon"（默认逐位不变）、+1 能力 terrain_sky_view、flow 契约 v2、
  +2 工具、12 测试（域内 55 过）。
  注：该批文件被后续 `git add -A` 带入 A5 元数据 commit —— 最终 PR 阶段按路径重组提交。
- Oracle corpus 基础设施落地：runner + 生成器 + 3 域 41 case（回放全绿）。
- Network 首派限流失败，已减并发后重派（不 commit 约定）。
- 16 个 legacy 描述符元数据补齐（A5）；剩余 4 个在 statistics/network 包随 wave 合入。
- RS V3 合入（13 新算法 + 13 工具 + 29 测试）；registry 161 算法 / 109 能力 / 95 契约。
- Network 复查修复 MILP 需求权重正确性 bug（回归测试锁定）。
- gitignore data/ 吞掉 oracle JSON 的隐患修复（negation + 全部快照入库）。
- 进行中：SAR V3 agent、oracle 扩充 agent（303→目标 ≥1050）。
- 计划收尾：+4 小而标准的补齐算法（EB 率平滑/双色 join count/OLS HC 稳健协方差/
  经典季节分解 + 自适应 KDE），目录再生成，7 维 review，按路径重组提交，PR。

## 2026-09-07 收尾
- Review gate 完成（CRS/回归 PASS；架构 PASS；科学审查发现 1 BLOCKING + 1 MAJOR 均已修复：
  LJC 条件置换精确化 + join count 抽样模型披露更正）。
- 全部 minor 修复：kriging 族 crs_class 对齐、SAR 单位披露、EB 负计数拒绝、
  MGWR ENP 披露、indicator 阈值上限、geodetector df 措辞、doc 声明校正。
- 历史按域重组为 11 个逻辑提交（树与重组前逐位一致），rebase 到新 master
  （25f0cf4），解决 gitignore/spatial_stats/point_pattern_tools/terrain_analysis 四处冲突
  （master 新工具元数据 kwargs 与本分支 additive 参数合并保留双方）。
- 最终统计：171 算法 / 112 能力 / 106 契约 / oracle 1084 例（12 域）。

## 2026-09-08（science-v3 / Platform V3）

- Phase 0 完成：8 域只读审计（统计/地统计/点格局/网络/地形/光学/SAR/基建）
  + 综合文档，全部入库 `.agent-work/science-v3/`。结论：无 fake-native。
- Wave 1 Backend SDK V3（ADR-0117）：ResourceEnvelope/ApproximationClass/
  NumericalTolerance/CancellationProfile/BackendEvidence/变体级精度分类；
  select_backend 消费包络做估算+预警+近似披露（声明面/执行面口径分离）。
- Wave 8 不确定性契约：uncertainty_producer_tests 映射 + validate() AST 级
  「declared→produced→tested」闭环；kriging 与统计域 descriptor 接线。
- Wave 10：gen_science_benchmark_manifest.py + BENCHMARK_MANIFEST.md
  （57 heavy 算法声明面投影，parity 锁定，gitignore 白名单）。
- 修复包（自实现）：geostat F1-F6；terrain P0（openness/horizon/SVF 内存
  重构+联合包络、viewshed 护栏、水文组合链 persist_filled、D8 引用归属、
  坐标约定复核——审计 F3 经实测推翻并在代码留证）；strict 波段语义。
- 实现批次（并行 agent，≤2 并发约定）：
  statistics 72500f3（GWR/MGWR 局地 SE/t、h3 接线、join_count 口径、
  证据块、GWR envelope）；optical 5b3c22e（NDWI 拆名、MAD dof 2k→k、
  FCLS 解混、medoid）；network 87ea957（MCLP 精确 MILP + 枚举对拍抓
  coo 重复求和 bug、引文勘误、统一 OD 规模闸）；point-pattern 2b724f3
  （EHA 17+1 类、temporal.hotspot 语义落地、pcf 守卫、descriptor 修复）；
  sar 4a26e02（vh_ratio dB 宣称删除、可比性接线、ENL/coherence CI、
  ESA IPF 引用 + input_domain）；geostat b0c6796（自实现：克里金 95%
  预测区间面 + LOOCV z-score 校准）。
- 文档：ADR-0117 + docs/science/PLATFORM_V3.md + 审计文档入库。
- 待办：catalog/manifest 再生成 → 全量验收 → 两轮 review → rebase + PR。
