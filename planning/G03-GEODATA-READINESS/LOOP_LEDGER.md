# G03-GEODATA-READINESS — 迭代账本

| 轮 | 改动 | 验证 | 结果 | 下一步 |
|---|---|---|---|---|
| 1 | M0 审计：fetch origin、PR/Issue/分支核查、worktree 建立、子代理 A 全链路审计、BASELINE.md/PLAN.md 落盘 | `test_data_qualification.py` 29 passed / 71.6s；merge-base 验证无在途冲突 | 通过 | M1 characterization 失败测试 |

## 决策日志（同步至 DECISIONS.md）

- D1: 不新增资格词表——验收词表映射到既有五态（见 PLAN GD-03）。
- D2: dispatch 资格闸做成薄对接 + feature flag（默认启用，可一键回滚到旧行为）。
- D3: explorer 桥接放在 validate_stage 末端 + dataset_semantics/builder 新入口，不动五段链结构。
| 2 | M1：断链 characterization 测试文件（5 绿 / 21 红）；修正断链 7 误判（CSV+坐标链路完整，纯表格不在契约内） | `pytest tests/data/test_g03_readiness_chain.py` → 5 passed, 21 failed（红灯符合预期） | 通过（M1 完成） | M2 ADR-0286 设计 |
| 3 | M3：B1 explorer 语义桥（rows→FC→IngestPipeline）/B2 dispatch_gate 模块+1.46 接线+集成测试/B8 license/attribution+dimension_provenance 三态贯通 ingest→builder→upload | g03 文件 10 passed；既有 qualification+ingest 套件 43 passed | 通过 | M4：B3 adapter/B5 scope/B6 失效/B9 守卫 |
| 4 | M4：B3 geojson_url adapter（SSRF/预算/Content-Type/typed 错误）+B5 preflight task_scope 判据+B6 task_scope 指纹模块+B9 geojson_guard（NaN/深度/超大属性） | g03 全 28 passed；dataset_semantics+explorer+ingest 113 passed；ads adapter/registry 66 passed | 通过 | M5：GD-06/07/10 + 三份文档 |
