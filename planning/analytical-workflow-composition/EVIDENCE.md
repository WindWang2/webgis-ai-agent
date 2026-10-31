# EVIDENCE — 验证记录（随轮次追加）

## E0 M0/M1（2026-10-29）
- 3 路并行勘察完成（生产链路 8 点 / 算子与产物 9 点 / 规划历史与基建 7 点），关键结论亲读复核：workflow_instance.py（纯投影红线）、workflow_runtime/dispatch.py（geocompute 适配通道，非 ToolDispatchService）、scene_orchestration_tools.py:520-820（registry.dispatch 单一执行真相先例）。
- worktree @ 4b3629c90 = 任务书审计基线（本地 refs 实证）。

## E1 M2-M4（契约/裁决/执行器/工具面）
- 31+20+5 测试绿（contracts/families/orchestrator/state/executor E2E/tools）；tsc 0 错；vitest map-commands 139 绿（含 catalogue-contract #535 幽灵命令不变量）；conformance CLI rc=0（工具 warning 态与全注册表常态一致）
- 计数守恒 30=Σ区计数；边界点多方属（合计>总数）+族披露在案；幂等重跑 ref 不变（step_reused 事件）；工具失败 → verdict failed/partial 非 satisfied；预算门 typed blocked

## E2 M5-M7（五族 E2E/skill/重算）
- 66→72 测试绿；gis_harness 全量 2781P/3S；G05 lab 45 绿；gis 755 绿
- 真路网 Dijkstra 等时圈链 satisfied（reachable 布尔披露）；双期对齐差分 3 patch；H3 网格链（无人口 → grid_rate skipped + hotspot satisfied）
- 暴露族语义修正实测：overlay(polygon×point) 因 keep_geom_type 丢点几何 → 改 spatial_join(within) + M:N 披露
- skill 包全词表校验过（trigger/measure/task_type/ontology/domain/data_role）；解析选择不抢占领域技能（ontology_tasks 移除后 resolver 回归绿）
- 重算：style-only 空集；receptor 变更只失效 exposure_intersect；身份漂移对账 {zones_count, area_density}；取消零 receipt；持久恢复复用 ref

## E3 M8-M9（生产缝/F28/perf）
- 关键发现修复：内层步骤原走 registry.dispatch 绕过 ToolDispatchService → capability 记账/SessionPlan 行缺失 → webgis_map_product 无层可挂。改共享 ToolDispatchService 后 F28-01 mapspec 层正常。slim 纪律适配（ref 剥离 → result_ref/ref_descriptor 透传）
- G05 F28 五场景绿（45/45）；G02 终局门接线（科学硬拒 → G-SCIENCE-BLOCKED）
- perf 锁：真实 100,001 点 FC → AWF_BUDGET_EXCEEDED typed blocked 零执行（-m perf 5.2s）
- 在线去重（网络恢复）：master=4b3629c90 未前移；0 open PR；#1842-1866 审查 issue 无同质覆盖；gh token 有效
