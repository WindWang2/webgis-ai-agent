# feat(spatial-semantics): 空间语义与量纲可信契约 SpatialSemanticContract v1（GOAL 01）

## 目标

建立贯穿 Pi GIS Harness、空间分析工具、ref 证据与 MapSpec 的**实例级空间
语义契约**：Agent 的坐标系/尺度/单位/范围/分母推断可解释、可验证、可回退。
回答"这次计算在哪个 CRS 里做、参数单位是什么、分母口径是什么、依据是什么"
——未知不猜（未声明 CRS 的投影样数据拒绝而非默认 4326）。

## 基线与差异

- master 起点/终点：`6c5e71c2c`（2026-10-31，无前进，无 rebase 需要）
- 36 files changed, +4049 −39；6 commits（M0 账本 → M1-M3 契约 → M4 工具 →
  M5-M7 链路/性能 → M8 收敛）

## 复用 vs 新增

**复用（import 组合，零复制）**：crs_safety.classify_crs（CRS 分类权威）、
measurement.CANONICAL_UNITS（单位词表）、data_preparation crs_source 四态、
utils/coord_transform.normalize_chinese_crs（偏移帧权威）、longitude.py
（跨 AM 判定）、aggregate_with_denominator（分母聚合）、extract_declared_crs。

**新增（独占目录 app/services/gis_harness/spatial_semantics/）**：
- `SpatialSemanticContract` v1（versioned/bounded/词表 fail-closed/to_dict
  from_dict 拒未知版本）；
- 统一 preflight：inspect（descriptor 级，采样 ≤200）→ resolve（UTM 单带/
  极区/测地 + 3857 闭式反算失真 + 跨 AM 环绕选带）→ validate（五态判定 +
  20 个 SSC_* 理由码 + 修复建议分级 explicit_required/deterministic_safe）；
- `tool_gate`：blocked/requires_clarification → typed error（不改写成成功）；
  pass/degraded → `spatial_semantics` 证据块并入响应。

**最小适配（不改签名不改结果语义）**：buffer_analysis / spatial_join /
spatial_aggregate（新可选参数 requested_density_unit，加法式）/
zonal_stats 四条生产路径；geocompute 载荷过管保留 crs member（度/米混淆
现行通路修复）；voronoi/convex_hull 面积按非米轴因子²换算（英尺系 km²
错 10.76× 修复）；create_thematic_map 量纲判定 crs 用真实声明（不再硬编码
4326）；前端 graduated 图例显示 legend_spec.unit（无 unit 渲染不变）。

## 里程碑

M0 基线矩阵（16 场景）→ M1 契约 → M2 preflight → M3 单位科学（含修复
per_m² 率值与个/km² 口径不匹配的真实 bug：×1e6 确定性换算 + 链入证据）→
M4 四工具 + Pi 发现面（inspect_spatial_semantics，tier 1）→ M5 MapSpec
单位链路（aggregate density_unit → thematic map unit → legend_spec.unit →
前端图例）→ M6 Pi/legacy 双入口一致 → M7 fixture 谱 + 50k descriptor 预算
→ M8 三场景端到端证据 + ADR-0279。

## 测试输出（本地真实运行）

- 新增 91 项：`tests/unit/gis_harness/spatial_semantics/`（契约 10 + 矩阵
  34 + 工具 15 + 链路 3 + fixture/性能 14 + R1 修复锁定 15）全绿
  （pytest -q --no-cov）
- 前端：`pnpm exec vitest run components/map/legends/graduated-legend.test.tsx`
  → 4 passed（含 2 项新增）
- 回归：buffer/crs_1110/passthrough/audit_764_765 → 38 passed；
  geocompute_1718/boundary → 222 passed；geometry_ops/spatial_analyzer →
  19 passed；cartography tools → 31 passed；api_contract → 57 passed
  （1 失败为基线既有）；tests/unit/gis_harness/ → 2822 passed + 4 失败
  —— **4 失败经独立基线 worktree（6c5e71c2c）同命令核验为 master 既有
  红灯**（test_component_composition×2 / test_semantic_v2_families
  registry 交叉验证 / test_workflow_guards profile_validation），与本 PR
  完全同集，非本 PR 引入（R2 评审曾发现本分支引入 2 个 manifest 失败 +
  api_contract 13 ERROR —— capability 注册遗漏所致，已修复并复验为 0）
- 端到端：`python scripts/spatial_semantics_e2e_evidence.py` → EXIT=0
  三场景 PASS（500m 测地基准 500.1m / per_km² 率值 1.177 换算链在案 /
  错误 CRS 全部阻断或降级披露）
- 静态：ruff（app/ 全量改动面）All checks passed；compileall OK；
  前端 typecheck 本 PR 文件干净（2 个既有错误文件为 #1875 遗留，隔离记录）

## 未跑项（诚实披露）

- 线上 CI/远程环境（按任务约束不依赖）
- scene3d 单元目录（无改动面，未跑）
- zonal_stats 的栅格侧 CRS/分辨率（分析器内部读取元数据，preflight 按
  not_evaluated 披露——诚实缺省，不虚构栅格事实）

## 性能/科学正确性证据

- 50k feature preflight：采样 200（打满即停）/ 扫描 ≤2000 / <1.0s 预算 /
  50k:5k 耗时比 <20（有界非 O(n)，测试锁定）
- 3857@30.66°N 失真估计 16.25%（闭式反算，对照 1/cos(30.66°)−1 理论值）
- 跨 AM 簇选带 EPSG:32760（非格林尼治带，#709 同源修复）
- 披露投影零坐标转储（repr 检查测试锁定）—— 大数据经 ref: 纪律不变

## review-fix-review（两轮终态）

- R1（独立 agent，全量 adversarial）：P0×1 + P1×6 + P2×8 → 全部清偿
  （P2-3 缓存窗口按披露接受），新增 15 项锁定测试；
- R2（另一独立 agent，复审）：核验 R1 清偿全部通过；新发现 R2-1（P0
  capability 未注册 → manifest fatal，含本分支引入的 2 个测试失败与
  api_contract 13 ERROR）/ R2-2（P1 占据弧退化形态选带回归）/ R2-3、
  R2-4、R2-5（P2）→ 全部修复并复验（api_contract 57 passed 0 ERROR；
  gis_harness 失败集与真实基线 worktree 完全一致；91 项测试绿）。
  详细映射见 `planning/goal-spatial-semantics/INDEPENDENT_REVIEW.md`。

## issue 重叠排除

- open PR：无；#1868/#1869/#1870/#1871/#1873/#1874/#1875 全部已合并，
  本 PR 与其改动面交叠仅为组合消费（import 其函数/词表），无文件冲突
  （共享文件 app/tools/__init__.py + categories.py 各 1-2 行追加）
- open issues（S 系列审查汇总 + #1809-#1847）：无直接重叠；#1841-#1843
  信任边界问题未在本 PR 复制

## 风险

- 行为收紧：未声明 CRS 的投影样数据从"warn 后静默按 4326"变为 typed
  拒绝（这是修复；受影响生产面已排查——geocompute 管线修复后传递声明
  CRS，其余调用面走度样/声明路径）；空 FC × 度量操作从"空结果"变为
  requires_clarification（CRS 无证据不执行，预期收紧）
- **Redis 缓存过渡窗口（≤24h）**：buffer_analysis 带 `@cached_tool(ttl=
  86400)`，缓存键=(tool_name, args) 无版本盐可 bump —— 部署后 24h 内，
  此前已缓存的输入继续返回旧结果（无 spatial_semantics 证据块/旧判定）。
  自愈型（TTL 过期后新语义生效）；本地/无 Redis 环境无此窗口（评审 R1
  P2-3 / R2 R2-5 披露）
- 前端 unit chip 为纯增显示，无既有渲染路径变化

## 手工验证

```bash
python scripts/spatial_semantics_e2e_evidence.py          # 三场景 EXIT=0
python -m pytest tests/unit/gis_harness/spatial_semantics/ -q --no-cov
cd frontend && pnpm exec vitest run components/map/legends/graduated-legend.test.tsx
```
