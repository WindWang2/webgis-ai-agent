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
| 5 | M5+M6：GD-06 取消/幂等/回滚+GD-10 步级计时+基准(50/5k/100k 绿)；geojson_url 网络预算=1；GD-09 远端 429/5xx/超预算演练；GD-04 fail-closed 核验；**存储包装层遮蔽 CRS/features 两断缝修复（inspect_semantics + to_feature_collection 拆包）**；成都小学率值图零模拟旅程（真值对账 5/5 区）+ 切城阻断 + scope 复用失效 | g03 套件 47 passed；spatial_semantics 113 + normalizer 消费面 91 回归绿 | 通过 | 1M 基准后台跑 → INGEST_PERFORMANCE.md → M8 review |
| 6 | M5 收尾：1M 基准完成（峰值/载荷=1.79×，store 正确拒收 byte budget；guard 2.4s/profile 0.23s/指纹 I/O 主导）；INGEST_PERFORMANCE.md + SOURCE_QUALIFICATION_MATRIX.md + DATA_READINESS_CONTRACT.md 落盘 | tests/perf 1M heavy 1 passed（293s）；三文档真实数字 | 通过 | M8 独立 review |
| 7 | M8a：子代理 B 冷读评审（1 P1 + 4 P2 + 10 P3，F1/F3 实证复现）→ 全部清偿：守卫重写（独立预算+耗尽即拒）、NaN bbox 三处修复、ADR D2 修订、mapspec scope 接线测试+日志、adapter F6/F7/F13/F15、gate F8/F9、F10-F12 | review_fixes 14 passed；全家+邻接 190+185 passed；ruff/mypy 绿 | 通过 | M8b 复审 |
| 8 | M8b：复审 F1-F15 全 fixed（实证复核）；新发现 N1（GeometryCollection 内层 NaN 逃逸，实证）+N2/N3/N4 → 全部同 PR 清偿（_scan_geometry 迭代展开+反例测试、文档对齐、isfinite 纵深、typed 断流） | review_fixes 16 passed；全家 161 passed；ruff/mypy 绿 | 通过（评审签结：可合并，无未处理 P0/P1） | M9 推送+PR |
| 9 | M9：master 未动（585812ee4，无需 rebase）→ 推送分支 → **PR #2013 创建**；关键验证二次运行确认 | g03 核心 3 文件复跑 46 passed | 通过（全部 Oracle 达成，goal-loop 收官） |
