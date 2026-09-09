# Science V4 — Wave Plan（Phase C，12 waves）

| # | Wave | 核心产物 | 提交 |
|---|------|---------|------|
| 1 | Scientific contract V4 ratchet | interpolation(16)+terrain(26) 全部声明 resource_envelope/cancellation_profile/tolerance；`science_contract_v4.py` allowlist；ratchet 测试；manifests 再生成 | feat(science-contract) |
| 2 | Typed scientific errors V4 | +NumericalInstability/+ConvergenceFailure；core.py CRSError→InvalidCRS 收口；收敛 xfail KNOWN-GAP #1 | feat(scientific-errors) |
| 3 | Geometry repair disclosure | geometry_repair.py 唯一实现；zonal/aggregation 接线；strict mode；收敛 xfail KNOWN-GAP #2 | feat(geometry-repair) |
| 4 | Kriging V4 core | simple_kriging / external_drift_kriging(KED) / normal_score±back / 嵌套 variogram 拟合；descriptor+conformance+oracle | feat(kriging) |
| 5 | SGS | kriging_simulation.py：SGS+ensemble(P10/50/90)+seed 复现+取消+资源闸；descriptor+工具 | feat(kriging-sgs) |
| 6 | CoK/LMC | 两变量 LMC 拟合 + 全共克里金 + PSD 校验(NumericalInstability)；descriptor | feat(kriging-cok) |
| 7 | ST kriging | kriging_st.py：separable/product-sum + ST-OK + 时间单位；descriptor | feat(kriging-st) |
| 8 | Hydrology V4 | breaching/HAND/Shreve+Pfafstetter；flow consistency 测试；descriptor | feat(hydrology) |
| 9 | Terrain V4 | hypsometry/solar radiation；descriptor | feat(terrain) |
| 10 | Chunked PF + cancellation 下沉 | 分块 priority-flood（seam parity）；terrain 堆循环/扇区/D∞ 取消点；cancellation_profile 升级 | feat(terrain-chunked) |
| 11 | Backend variant parity harness | 通用双跑 parity 执行器（tolerance 契约消费）；新算法变体注册（含 PF full vs chunked） | test(backend-variants) |
| 12 | Oracle/perf corpus + manifests + docs | oracle 扩充（解析解/不变量/守恒）；perf scaling+cancellation latency；catalog/manifest/docs 再生成 | test(science-oracles)+docs |

每个 wave：failing test → 最小闭环 → targeted tests → lint → 小步 commit。

## 验收映射（prompt §10 验收指标）
- raw pyproj 泄漏 → W2（+xfail #1 收敛）
- geometry repair 可观测 → W3（+xfail #2 收敛）
- 新重算法 resource+cancellation+tolerance → W1（声明）+ W4-10（实现自带闸/checkpoint）
- reference vs optimized 在容差内一致 → W11
- 随机方法 seed 可复现 → W5
- uncertainty 输出严格来源 → W5/W12（producer tests + monte_carlo_summary）
- 科学 xfail 收敛 → W2/W3
- 不以提容差掩盖数值错误 → W11 parity atol=1e-9 级
