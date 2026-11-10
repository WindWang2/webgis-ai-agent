# DEDUP_MATRIX — G12-PI-NATIVE-INTEGRATION

Base: origin/master = `816705218a` (2026-11-10). Re-queried at execution time (2026-11-10).
Legend: `present`(已实现,转为回归保护) / `partial` / `missing`(本任务做) / `owned-by-other`(开放PR/其他任务) / `blocked`。

## 已知排重清单核对（任务书 §B.5）

| 项 | 状态 | 证据 |
|---|---|---|
| #1973 Pi v1.5.0 初次升级 | present | merge `4909adf`；gitlink `f9a26365`=v1.5.0；pi_compat/pi_doctor/build-vendor/契约测试/ADR-0282 全落地。G12 不重复，只做 v1.5.0→v1.5.1 增量 |
| #2012 G01 相位/幂等观测 | present | merge `2024dd4c`；pi_turn_state/pi_durable_cancel/SSE 幂等/三态披露/ADR-0286。G12 复用 TurnPhase + SSE 终态去重 |
| #2011–#2017 (G02–G07) | present | 执行编译/数据资格/情境工作台/制图组合器/能力生态/可靠性实验室均已 merge（planning/G01..G07）。G12 只消费，不重写 |
| #2033 命令清单 / #2034 Studio 单源 / #2035 Agent提交撤销 | present | merges `5ecb81f`/`a53b3b8`/`6767ea6` |
| #2036 Pi-only | present | merge `8167052`：USE_NEW_AGENT 删除，桥不可用→503；PI_AUTOSTART 仅测试。G12 **不恢复 ChatEngine 对话回退** |
| 开放 #2021 (G11 地图简报) | owned-by-other | 只加 `app/tools/brief_tools.py`（additive 注册）；不触 Pi core。G12 不动其文件 |
| 开放 #2022 (G10 数据刷新) | owned-by-other | 加 project_data_refresh_tools + 集成测试；不触 Pi core |
| 开放 #2032 (UI 重设计) | owned-by-other | frontend-only；G12 前端改动避开主题/布局，仅最小挂载 |
| G09 参数化批量分析 | 不存在 | 无 PR/无分支/无 planning 目录。G12 **不做计算编排**（任务书明令排重） |
| G13–G16 兄弟方向 | 不存在开放PR | 无任何开放 PR/未合并分支。G12 以当前 master 公开接口独立运行 |

## G12 工作包级排重

| G12 项 | 状态 | 证据/调用链 |
|---|---|---|
| Pi pin v1.5.0→v1.5.1 | missing | 无未合并分支改 vendor/pi（`git branch -r --no-merged` = 12 开放PR分支，均不触 Pi）。G12 独占执行 |
| 原生 exposure/namespace/annotations 投影 | missing | `index.mjs:296-308` registerTool 只传 name/label/description/promptSnippet/parameters；`pi_native_surface._pi_tool_definition` 同。S2 确认 |
| tool_search / codemode 启用 | missing | 扩展 `applyActiveTools` 过滤 `nativeNames ∪ alwaysActive`，vendor 内置扩展注册的工具从不激活；`--no-builtin-tools` 只清默认工具选择，不卸载内置扩展（v1.5.1 main.ts:589/sdk.ts:282 已核） |
| deferred 长尾声明面 | partial | 现在：全超集注册(默认 direct) + setActiveTools 收窄。缺 exposure:"deferred" 语义与发现闭环 |
| 发现后授权一致性 (discovery grant) | missing | `pi_surface_receipt.enforce_callback_scope` opt-in 默认 OFF（L44-73）；无 discovered 集合概念 |
| 嵌套调用 parentToolCallId 投影 | missing | 全仓零处 `parentToolCallId`（含前端）；`pi_event_mapper._base_step_payload` 只有 task/step/session |
| clear_queue + 有序停机 | partial | `PiBridge.abort` 只发 abort RPC；v1.5.1 提供 clear_queue（先清队列再 abort 的产品语义）；`stop()`=terminate→0.5s→kill，v1.5.1 dispose 需数秒（credential writes/tool settlement）→ 窗口不足 |
| 模型配置生效链 (#2061) | missing | issue OPEN、无 owner、无 linked PR、无 master commit；`update_config` 只写引擎+runtime override，`_spawn_process` 只读 Settings。G12 独占实现（W08） |
| compaction 生命周期接入 | missing | 扩展无 session_before_compact/after_compact 钩子；typed assembly 已有（#2014 G05 波及面），G12 在其上加 GIS 状态重建块 |
| worker 重启会话恢复 | partial | pool respawn/`respawn_if_dead` 已有；GIS 状态权威重建（隐藏层/锁定/待办）经 SessionPlan/artifact 已有持久面，缺 compaction/restart 后的 bounded rebuild 投影验收 |
| 真实 Pi + stub LLM 旅程 | partial | vendor contract 7 测 + journeys J1-J7 已有（A1 基础已覆盖大半）；缺 A2-A16 发现/嵌套/取消/配置/压缩场景 |
| 生产镜像/部署包验证 | partial | Dockerfile(s) pi-builder + bundle/jiti overlay 已有；v1.5.1 有 root package-lock.json（v1.5.0 无）→ build 可转 `npm ci`；需重新对账 |

## 相邻但不归 G12（记录边界）

- #2072 扩展工具名碰撞预检（extensions_platform/，Python 扩展平台域）— 不是 Pi vendor 扩展路径，本任务不动
- #2062 governor cancelled-flag 泄漏、#2060/#2058 ledger — governor 域，不在 Pi seam
- #2071 扩展 host 内存界 — extensions_platform 域
- #2006 工具结果 summary 丢 partition — dispatch result 域（若嵌套展示改动触及 summary 行为，只做兼容不重写）
