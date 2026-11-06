# TEST_MATRIX — GOAL 04

| 里程碑 | 测试文件 | 数量 | 覆盖 |
|---|---|---|---|
| M1 | tests/cartography/multiscale/test_scale_profile_v1.py | 19 | 契约/双基准换算/校验/查询/投影/指纹 |
| M1 | tests/cartography/test_mapspec_schema_v18_multiscale.py | 10 | v1.8 additive schema/迁移/前向拒 |
| M2 | tests/cartography/multiscale/test_importance_v1.py | 16 | 词表全匹配/全拒路径/user-wins/确定性/带级取舍 |
| M3 | tests/cartography/multiscale/test_generalize_v1.py | 15 | 有界简化不变量/洞/微环/多部件/共享锚点/非破坏/确定性/容差单位 |
| M4 | tests/cartography/multiscale/test_plan_v1.py | 11 | 带计划编排/无证据不取舍/pin raw/工件回填两阶段 |
| M5 | tests/cartography/multiscale/test_semantic_checks_scale_profile.py | 5 | carto.scale.profile QA（悬空窗/口径/证据对偶） |
| M6 | tests/cartography/multiscale/test_export_contract_v1.py | 6 | export_adjudication + 后端 SVG 工件源消费 + 诊断 |
| M6 | frontend/lib/map-kit/multiscale/window-source.test.ts | 6 | 归一携带/编译器克隆/导出孪生（vitest） |
| M7 | tests/unit/gis_harness/scale_profile/test_service.py | 7 | inspect/preview/apply commit/幂等/非破坏快照 |
| M7 | tests/unit/gis_harness/scale_profile/test_tool.py | 6 | 注册面/tier/side_effect/派发 happy-sad |
| M8 | tests/cartography/multiscale/test_topology_perf_v1.py | 7(+1 heavy) | 拓扑不变量族/多带等价/50k/200k 基准（MSP_PERF=1） |
| M9 | tests/cartography/multiscale/test_scenarios_v1.py | 3 | 三场景端到端（真实引擎→apply→SVG 三尺度） |
| 回归 | cartography 全套 + scale_profile + 邻域 | 3079 | 含 v16 版本断言更新/corpus manifest 伴生 |
| 回归 | 前端 vitest（carto-ir/compiler/runtime/multiscale/symbol-law） | 714 | + tsc --noEmit 0 错误 |

heavy：TestPerformanceBenchmarks（MSP_PERF=1 显式；453s 全程，数字见 EVIDENCE.md）。
