# GOAL-20261127-03 TEST MATRIX — 测试矩阵与本地验证记录

环境：Linux x64，Python 3.12 venv（主 worktree 共享），pytest -q --no-cov。
命令前缀：`cd /home/kevin/projects/webgis/webgis-probability-sampling-map-accuracy-20261127 && JWT_SECRET_KEY=ci-test-secret-key-32-chars-ok ENV=development LLM_API_KEY=test-key-not-real .venv-bin-python -m pytest <target> --no-cov -q`

## 本任务测试面（49+5 全绿，2026-11-27/28 实测）

| 文件 | 覆盖 | 关键断言 |
|---|---|---|
| tests/unit/survey_sampling/test_estimation_oracle.py (11) | 手算 oracle/全查/独立性 | 矩阵 1e-12；PA 方差推导对照；全查 SE=0；随机总体交叉核对；n_h=0→not_evaluated；n_h=1→partial；全 n_h<2→typed 拒绝；类字典漂移阻断；CI 越界双披露；非响应越限拒绝；ΣW≠1 拒绝 |
| tests/unit/survey_sampling/test_coverage_simulation.py (2) | CI 覆盖模拟 | 400 次重复：OA 覆盖 0.968/面积覆盖 0.963（±0.06 容差）；无偏（3×MC 误差）；SE/SD=1.045 ∈[0.85,1.15]；顶替=上限→partial 披露 |
| tests/unit/survey_sampling/test_design.py (12) | 设计合同 | Eq.13 目标 SE 反推（S=0.05→100）；等额/比例/Neyman；稀有类不跳过；硬下限 2；digest 稳定/敏感；overdraw/空层/非法参数 typed 拒绝 |
| tests/unit/survey_sampling/test_selection.py (6) | 可复现选样 | 同 seed 逐位一致；不放回；层独立 seed；π=nh/Nh、w=1/π；sample_id=uuid5(digest‖层‖单元)；幂等 |
| tests/unit/survey_sampling/test_frame.py (7) | 抽样框 | 真实 GeoTIFF（EPSG:6933）：计数/中心坐标/digest 幂等/NoData 排除；4326 大跨纬 19.45% 面积变化 typed 拒绝；空类/超 dtype/空 CRS 拒绝 |
| tests/survey_sampling/test_service_journeys.py (4) | **必过旅程 1-4** | J1 全链手算一致（1e-12）+真值落 CI+盲标视图；J2 stale/重设计/历史保留；J3 双入口同源+CAS 409+记录版本化；J4 3.84M 像元分块+取消无半成品 |
| tests/survey_sampling/test_publish_and_tools.py (2) | Pi 工具 | 8 工具注册/schema（session 不外泄）；survey_frame_preview 经 registry.dispatch 真跑 |
| tests/survey_sampling/test_api_routes.py (2) | REST | frame→preview→create→progress→detail(盲/管)→estimate(typed 422 信封)→清单；入参边界（空字典/未知 ref） |
| tests/survey_sampling/test_survey_perf.py (2) | 性能 | 1e6 像元 frame=0.06s/draw=14ms/estimate=0.4ms/RSS+3MB（实测打印）；4e10-cell 守卫拒绝 0.1ms |

## 基线相关回归（本任务改动触碰的共享面）

| 套件 | 结果 | 基线对照 |
|---|---|---|
| tests/test_tool_meta_contract.py | 35 passed, 2 failed | master 同 2 failed（extension example/route distribution，基线既有） |
| tests/test_ci_local_gate_contract.py | passed | — |
| tests/test_import_boundaries.py | passed | — |
| tests/test_skills_tool_names_registered.py | passed | — |
| tests/test_api_docs_drift.py | passed（重生成后） | master 红（drift），本分支修复 |
| tests/unit/test_execution_catalog_docs_drift.py | 13 passed + 1 shape 红 | master：drift 红；本分支 drift 绿 + shape 红（诚实暴露基线 28 dangling fatal——兄弟线工具引用未注册能力，strict 启动闸 master 即红） |
| tests/test_alembic_metadata.py | passed | 无新迁移（单 head 保持） |
| scripts/check_import_boundaries.py | OK | — |
| scripts/debt_ratchet.py | 4 god_modules 超（同 master） | 基线既有 |
| scripts/check_tool_capability_conformance.py | survey 工具自身全 pass；整体 exit=1 同 master | 基线既有 |
| scripts/check_tool_descriptor_coverage.py | 80% 同 master（capabilities 补齐后缺口持平） | 基线既有 |

## 未执行项（诚实披露）

- 前端 vitest/typecheck/eslint：前端子代理执行中，结果记 PROGRESS-FRONTEND.md
- 全量后端套件（-n4 分片 50min+）：本任务 focused + 契约层已绿；全量留给
  CI（goal §0：不等待线上 CI，但 PR 说明标注未跑全量）
- Playwright journey（mock 档）：面板 e2e 未新建（面板级 vitest 覆盖交互）；
  理由：双入口语义已由 Pi dispatch 真跑 + REST TestClient 覆盖
- real_services / browser journey lanes：CI nightly 专属（ci-local.sh :14-15）
