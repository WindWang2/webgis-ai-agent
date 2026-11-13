# G30 TEST_MATRIX — 实际执行结果

## 新增测试（全绿）

| 套件 | 文件 | 数量 | 覆盖 |
|---|---|---|---|
| prepare 单元 | tests/unit/gis_harness/test_skill_job_prepare_v1.py | 7 | 事实抽取、三旅程 prepare、typed 拒绝矩阵（12 码）、幂等键、provenance |
| runner 集成 | tests/unit/gis_harness/test_skill_job_runner_v1.py | 8 | 三旅程真实运行（report/map×2）、双击幂等、INPUT_DRIFT、ASSET_DRIFT、取消、刷新回看 |
| 选择/政策/目录 | tests/unit/gis_harness/test_skill_selection_g30_v1.py | 12 | skill_name 三分支、显式 policy（execute_guided/eligibility 拒绝/弃用/未命中/legacy 不变）、session ctx、目录分型 |
| API 路由 | tests/unit/gis_harness/test_skill_job_api_v1.py | 5 | catalog/detail、typed 拒绝形状、prepare→start→runs→410、预设保存/重用/跨 org 隔离 |
| Pi 工具面 | tests/unit/gis_harness/test_skill_job_tools_v1.py | 4 | 注册面可见、经 registry.dispatch 真调、typed 回执非异常、选择证据记录 |
| Pi 真子进程旅程 | tests/integration/test_pi_g30_skill_job_journey.py | 1 | node stub LLM + uvicorn 生产 app + 真 Pi 子进程：skill_name 披露 → tool_search 发现 → prepare → start → durable 行 + 报告回读（PI_JOURNEY_E2E=1 门控） |
| 前端工作台 | frontend/components/settings/skill-workbench.test.tsx | 4 | 目录分型/guidance_only 无启动、预览（步骤/证据/未覆盖）、typed 错误 code 定位、启动→完成→回看 |

## 既有回归

| 范围 | 结果 | 备注 |
|---|---|---|
| tests/unit/gis_harness/ 全目录 | 3547 passed / 8 failed | 8 中 6 个 base 既有（纯净 base worktree 复现：binding_taxonomy×2、component_composition×2、semantic_v2×1、workflow_profile×1）；2 个为本任务暴露的 G27 遗留（见下） |
| G27 工具注册修复暴露 | 已修复 | memory_class="small"（非法值）修复后工具可注册，暴露 cartography_scale_portrayal_{inspect,apply} capability 悬空 → 已按 G22 先例补种（capabilities/platform.py），2 个 workflow_guards 回归转绿 |
| test_tool_meta_contract | 2 failed | base 既有（index.mjs 示例列表/prompt 路由断言），与本任务无关（未触碰 app/extensions） |
| skills 相邻套件（contract/library/policy/resolver/procedure_seam/registry/replay） | 121 passed | schema v2 升级兼容 |
| test_pi_dynamic_surface / ref_resolution_batching / g13_capability_execution | 49 passed | surface/dedup（含新 skip_keys）/能力派发无回归 |
| test_pi_durable_cancel + tests/jobs | 257 passed | durable job 语义（JobKind+=skill_job）无回归 |
| frontend components/settings 全部 | 31 passed（7 文件） | |
| tsc --noEmit | 0 errors | |

## 验证层级披露（诚实边界）

- **Pi 全链（真实）**：test_pi_g30_skill_job_journey —— 真 Pi 子进程（v1.5.2 pin）+ 生产 app + 真 ToolDispatchService + 真 durable job + 报告回读 ✓（本地实跑通过）
- **浏览器（Playwright）**：未运行 —— 工作台经组件测试（jsdom）+ TS + 手动 API 链验证；真浏览器 E2E 标记为未跑（无既有 Playwright 基线可复用，成本/收益已在轮内披露）
- **并发/恢复场景矩阵**：#13 双击、#15 刷新、#12 数据漂移、#8 资产漂移、#14 取消已覆盖；#24 权限撤销依赖 dispatch 期闸（既有 G13 单测面）+ 本任务 start 期复核语义
