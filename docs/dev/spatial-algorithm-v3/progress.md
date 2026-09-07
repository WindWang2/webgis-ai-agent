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
