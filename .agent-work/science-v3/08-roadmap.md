# 08 Roadmap（收敛路线图）

> 输入：8 份域审计 + 本分支已落地工作。原则：先契约与修复后增量算法；
> 单一事实源不破；每个新能力必须「声明-实现-测试」三件套齐。

## 本分支（feat/spatial-science-geoai-platform-v3）范围

| Wave | 内容 | 状态 |
|---|---|---|
| Phase 0 | 8 域只读审计（domains/01-08）+ 综合文档 | ✅ |
| Wave 1 | Backend SDK V3（ADR-0117）：ResourceEnvelope/ApproximationClass/NumericalTolerance/CancellationProfile/BackendEvidence | ✅ 已提交 |
| Wave 3 修复 | geostat F1-F6（indicator 守卫/typed 块/fallback 语义/引用/变体窗口/complexity） | ✅ 已提交 |
| Wave 6 修复 | terrain P0（内存重构/水文组合链/引用归属/坐标约定复核） | ✅ 已提交 |
| Wave 4 修复 | strict 波段语义（位置猜波段默认拒绝） | ✅ 已提交 |
| Wave 8 | uncertainty_producer_tests 机器校验 | ✅ 已提交 |
| Wave 2 | 统计 P0：GWR/MGWR 局地 SE+t、h3_hotspot 接线、join_count 口径、hotspot 证据块 | 🔧 agent 进行中 |
| Wave 4 增强 | NDWI 拆名、MAD χ² dof、FCLS 线性解混 | 🔧 agent 进行中 |
| 第二批 | 点格局 P0（EHA + descriptor 修复）、网络 P0（引用归位 + MCLP MILP + 统一规模闸） | ⏳ |
| 第三批 | SAR P0（vh_ratio 文案 + 可比性接线）、地统计增强（prediction interval + 各向异性拟合 + robust variogram） | ⏳ |
| Wave 9 | 数值验证扩展（新增算法 golden/性质/守卫三件套已入各包；跨域 parity 补充） | ⏳ |
| Wave 10 | benchmark manifest 生成器（descriptor → 机器可读规模/复杂度投影） | ⏳ |
| Wave 11 | ADR-0117 + docs/science/ 同步 + catalog 再生成 | ⏳ |
| 验收 | validate/parity/ruff/compile sweep + 两轮独立 review | ⏳ |

## 后续分支（记录于 02-planned-algorithms.md）

1. **地统计深化**：KED、log/normal-score、nested variogram、SGS、全 CoK、
   时空克里金 foundation。
2. **地形/水文深化**：breaching、HAND、Shreve/流域层级、hypsometry、
   solar radiation、R3 viewshed、分块 Priority-Flood + numba 变体。
3. **网络深化**：容量选址、turn penalty 契约、accessibility inequality、
   E2SFCA 变体、multimodal/turn-restriction 契约。
4. **SAR 深化**：极化 RVI、Quegan、ray-casting、热噪声 LUT foundation。
5. **不确定性传播**：DEM 误差传播族、变异函数参数 CI、z-score 校准面推广。
6. **Backend 变体上收**：numba/GDAL 变体、windowed 化、lib 层取消点。

## 验收门（本分支 Definition of Done）

- AlgorithmRegistry.validate() = 0
- capability/tool/参数 parity = clean；catalog parity = clean（再生成后）
- registry validate 含科学契约（含 producer tests）= clean
- 新增/修改域单测 + oracle 回放全绿；ruff 干净；compile sweep 干净
- 两轮独立 review：BLOCKER/CRITICAL/MAJOR 全清
- PR body：目标/架构/实现/测试/benchmark/review findings/known limitations/
  兼容性/迁移说明
