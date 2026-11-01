# TASK_PLAN — GOAL 01 空间语义与量纲可信契约

分支：zcode/goal-spatial-semantics-contract-v1（基线 master@6c5e71c2c）
目录：app/services/gis_harness/spatial_semantics/（独占）+ 最小工具适配
验收 Oracle（完成条件，全部可复现）：
1. 单一 typed SpatialSemanticContract v1 可序列化/验证/版本化；CRS/单位/分母未知绝不静默伪造
2. ≥4 种真实工具生产路径消费同一 preflight（不支持者明确 not_evaluated）
3. 8 类典型错误在工具执行前有一致确定性判定（TEST_MATRIX S1-S15）
4. 成都小学 500m 对比可信基准；无 CRS 投影数据拒绝而非猜 4326
5. ref 输入/输出证据与 MapSpec 单位可对账；Pi/legacy 一致；用户 pin 保留
6. 错误时不执行危险转换；无网无密钥可重复运行 2 次
7. 无范围外大改；独立 review 两轮；真实 PR URL

## 里程碑

### M0 现状与覆盖矩阵（本文档 + BASELINE + PR_OVERLAP_MATRIX + TEST_MATRIX）— 完成
### M1 typed 契约 schema（spatial_semantics/vocabulary.py + contract.py）
- [ ] vocabulary.py：CRSSource/AxisOrder/DistanceUnit/AreaUnit/DenominatorKind/CalculationMode/SSC_* 理由码（封闭词表）
- [ ] contract.py：SpatialSemanticContract v1（pydantic，bounded，to_dict/from_dict fail-closed，VERSION=1）
### M2 preflight/decision 纯函数（inspect.py + resolve.py + validate.py + preflight.py）
- [ ] inspect_spatial_contract：declared crs/坐标结构证据 → 契约（descriptor 级，无全量几何）
- [ ] resolve_calculation_space：数据 CRS class × AOI × 计算目的 → projected_local/geodesic/projected_polar/raster_grid + working_crs + 失真估计
- [ ] validate_spatial_operation：操作 × 契约 → 五态判定 + 理由码 + 修复建议（显式授权/确定性安全分级）
- [ ] 单位解析 units.py：parse_distance("500米"/"0.5km")→(value_m, chain)；面积 m²↔km²↔ha；密度口径 per_km²/per_10k_persons
- [ ] 统一 preflight 入口 build_spatial_preflight
### M3 单位和测量科学性
- [ ] distance/area/raster cell size 不可混用判定；无意义操作闭合理由码 + 修复建议
- [ ] 修复建议分级：explicit_authorization_required / deterministic_safe_transform
### M4 工具消费（4 条真实路径 + geocompute G2 修复）
- [ ] buffer_analysis 适配（S1-S5）
- [ ] spatial_aggregate 分母适配（S6/S7）
- [ ] spatial_join 适配（S8/S9）
- [ ] zonal_stats 范围/单位适配（S12）
- [ ] geometry_ops 面积输出单位修复（S15/G3）
- [ ] geocompute ops crs member 保留（G2）
- [ ] Pi 工具发现面：inspect_spatial_semantics 只读工具（tools/spatial_semantics_tools.py + 注册 2 行）
### M5 MapSpec/制图反馈
- [ ] 适配层把 {working_crs, distance_unit, area_unit, denominator, density_unit, reason_codes} 写进工具结果 evidence（既有通道）
- [ ] create_thematic_map：crs 硬编码 4326 → 真实 crs；密度口径 unit 进 legend_spec
- [ ] 前端 graduated 图例显示 unit（最小改动 + vitest）
### M6 双运行时一致性
- [ ] Pi（registry.dispatch）与 legacy（直接函数调用）同一 preflight 判定的一致性测试
- [ ] 未知 CRS/有损变换/尺度不合适 → requires_clarification / degraded_with_disclosure；记录字段名/原因；不带敏感 geometry 全量
### M7 fixture + 性能
- [ ] fixtures：4326/3857/UTM/跨AM/缺CRS(度样/投影样)/GCJ/BD/无效几何
- [ ] 50k feature：仅 descriptor 评估（坐标采样 ≤200）+ 计时预算
### M8 收敛
- [ ] 三大场景端到端证据（500m 覆盖→聚合→display；per_km² 密度+legend unit；错误 CRS 阻断）
- [ ] docs：gis-harness.md 增补 + ADR-0279
- [ ] 独立 review → fix → re-review（两轮）→ PR

## 资源纪律
- pytest -q 定向；ruff check 定向；python -m compileall -q；并发 -j2
- 无网无密钥 fixture；两次重复运行验证
