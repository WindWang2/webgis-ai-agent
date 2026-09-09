# Science V4 — Architecture（Phase B）

## 目标态组件图（全部为既有主链路的纵向深化，零第二事实源）

```
app/lib/gis/algorithm_registry.py      ← 契约模型（V3 字段位已有；V4 加 ratchet 消费）
app/lib/gis/algorithms/{interpolation,terrain}.py  ← descriptor 唯一事实源（V4 填 envelope/cancellation/tolerance + 新算法 descriptor）
app/lib/gis/algorithms/science_contract_v4.py      ← NEW: ratchet 常量（allowlist）——数据非逻辑
app/lib/gis/scientific_errors.py        ← +NumericalInstability/+ConvergenceFailure（ValueError 子类不变）
app/lib/geo_analysis/geometry_repair.py ← NEW: 修复披露唯一实现（aggregation/raster_ops/geo_processor 复用）
app/lib/geo_analysis/kriging.py         ← +SK/+KED/+normal-score/+嵌套 variogram（复用 _gamma/_solve/cKDTree）
app/lib/geo_analysis/kriging_simulation.py ← NEW: SGS（import kriging 的 SK/transform；不复制）
app/lib/geo_analysis/kriging_st.py      ← NEW: separable/product-sum 时空克里金（复用 kriging 邻域/求解）
app/lib/geo_analysis/terrain.py         ← +breaching/+HAND/+Shreve/+Pfafstetter/+hypsometry/+solar/+分块 PF/+取消点
app/tools/advanced_spatial.py           ← 新工具：ked/sgs/cokriging/st_kriging/sgs_ensemble（validate→调实现→挂证据，同 indicator 模式）
app/tools/terrain_analysis.py           ← 新工具暴露（breach/hand/hypsometry/solar）+ 裸 rasterio.open 收口
app/services/geocompute/ops.py          ← INTERPOLATION op method 词表扩（idw|kriging|*v4 方法）—— 最小接线
```

## 关键决策

1. **SK/KED/normal-score/嵌套放 kriging.py**：同一 geostat 域、复用 `_gamma`/邻域/分块求解，拆文件会造成循环 import 或复制。SGS/ST 各自独立模块，单向 import kriging（SGS 复用 SK + 变换；ST 复用邻域搜索约定但不复制矩阵代码——ST 协方差结构不同，自建 K 矩阵）。
2. **Ratchet 模式**：复制 `GATE_THRESHOLDS`（app/lib/quality/manifest.py:49）思想——`science_contract_v4.py` 持冻结 allowlist（存量 heavy 算法未声明者），测试断言 (a) 所有 heavy 算法要么声明要么在 allowlist，(b) allowlist 成员必须真的 heavy 且真的未声明（防止洗白），(c) interpolation/terrain 域禁入 allowlist（本 Epic ownership 必须填平）。新增 heavy 算法不声明即红。**不**改 validate() 硬门（避免其他 9 个并行 Epic 的域瞬间变红）。
3. **几何修复披露**：唯一 helper 返回 `(geometry, RepairReport(count, methods, area_delta))`；strict=True 时 invalid→InvalidGeometry（不修复）。zonal stats 路径：invalid 输入在统计前披露修复并进 metadata.disclosures；修复失败→逐行 None+warning（既有语义）升级为 strict 可拒。geo_processor/core.py 只在 CRSError 收口处顺带接入计数（最小修改，避免共享文件大改）。
4. **分块 Priority-Flood**：tile+halo 通道式处理；seam 处以「边界种子取填充后邻域最小」再扫描，输出与全量路径在 atol≤1e-9（合成 DEM）一致；approximation_class 声明 approximate（epsilon 平地语义下两路径都是同一近似类），**不是**用提高容差掩盖——parity 测试钉死数值。全量路径保留为 reference variant。
5. **SGS 复现性**：`random_seed_policy="caller_seeded"`；numpy Generator(PCG64) 单流、随机路径确定性（排序后置换）；同 seed 双跑逐位一致测试；ensemble 输出 P10/P50/P90 + E-type mean/variance（monte_carlo_summary uncertainty 块）。resource cap：n_realizations×cells 预算进 ResourceEnvelope + 实现层 ResourceScaleMismatch。
6. **LMC CoK**：两变量；交叉变异函数拟合 + 半正定性校验（特征值 ≥ -tol，违例→NumericalInstability typed 拒绝）；与 MM1 collocated 共存但 descriptor limitations 明确区分（LMC=全 CoK 基础，MM1=近似）。
7. **ST 克里金**：separable 与 product-sum 两种时空协方差；时间单位秒（参数声明）；时空采样预算 cap；target (s0,t0) 邻域 = 空间 cKDTree 预筛 × 时间窗。
8. **错误词表收编**：KrigingCrsError/InterpolationResourceExceededError 保持兼容（ValueError 语义不变），新代码一律用 taxonomy；不强行改名（防 conformance 节点/契约漂移）。

## 数据/持久化/API 影响
- 无 DB schema 变化、无 migration（纯算法/契约/工具层）。
- 生成物再生成：ALGORITHM_CATALOG.md / BENCHMARK_MANIFEST.md / quality-manifest.json（parity 测试护航）。
- 工具面新增（全部 additive）：external_drift_kriging / sgs_simulation / cokriging_lmc / st_kriging / breach_depressions / hand / stream_ordering(扩展) / hypsometric_analysis / solar_radiation。geocompute INTERPOLATION method 词表 additive 扩展。

## 失败模式与预算
- 全部新重算法：ResourceEnvelope（实现常数对齐）+ cancellation_profile（与真实 checkpoint 密度一致——W10 下沉后 terrain 才能声明 chunk_boundary）+ NumericalTolerance（rtol/atol 与 conformance 断言一致）+ approximation_class。
- 性能预算：新测试全部小型合成数据（≤200×200 栅格、≤2k 点）；oracle 回放增量 ≤3s；不跑 xdist、串行 targeted。
