# PR_OVERLAP_MATRIX — 去重矩阵（2026-10-30 开工审计；PR 创建前复核）

## 已合并 PR（与本方向相关性核验）

| PR | 主题 | 与本方向关系 |
|---|---|---|
| #1867/#1836/#1835/#1834/#1830/#1829/#1773/#1771 | 三维（g3d 系列） | 无重叠。#1830 三维产品模板、#1829 SceneSpec 明确排除。共享面：mapspec_schema v1.7、component registry（只读复用） |
| #1763 | 组合模板资产生命周期 | 明确排除。本任务不触碰 template_assets 生命周期 |
| #1762 | G05 任务验收 lab | **复用**（tests/acceptance/gis_agent runner + fixtures），新增场景族不改动既有 61 场景 |
| #1761 | 工具能力 8 面对账 | 只读复用（新工具需登记能力描述符，遵守其 CLI 对账） |
| #1760 | 科学方法契约 MethodUseContract | **复用语义**：其 2D 等价物 AlgorithmSelectionRecord（planner.py:591-601）已在链上；本任务候选的 scientific risk 字段与其词表对齐，不另造 |
| #1759 | 数据集语义身份绑定 | 只读复用（denominator 词表 DENOMINATOR_FIELD_HINTS 单一事实源） |
| #1758 | 批量 issue 清偿 | 无关 |
| #1756 | H01 资格→完成判据 | 只读复用 |
| #1755 | 制图产品 conformance + 导出 parity | **边界邻接**：本任务 M4 新增语义检查码须走既有契约更新协议（显式改 JSON + 计数），不重复 export parity |
| #1623 | Interaction IR | 只读复用；本任务不给 IR 加新 kind（词表显式留白纪律） |
| #1622 | RenderWork Protocol | 无重叠 |
| #1621 | Durable Project Cartographic Runtime | 复用其快照通道持久化用户选择（若合适），不新建状态机 |
| #1620 | Pi ABI V2 | 新 agent 工具按其 tier/装饰器惯例注册 |
| #1617 | RepresentationAtScale v1.6 | **复用**：候选切换落 MapSpec layers[].representation 时沿用其 user-wins 窗口语义 |
| #1614 | User-Wins Representation Binding | **复用**：用户固定表达方案走既有 binding 机制，不另造 |
| #1615/#1616/#1618 | skills/requirement IR/结算 | 无重叠 |

## 开放 PR

| PR | 主题 | 关系 |
|---|---|---|
| #1868 | 多源空间数据准备与语义绑定（sdo） | **零文件重叠**。潜在共享：app/tools/__init__.py、app/tools/categories.py、app/main.py、tests/quality/snapshots/*、frontend/lib/i18n/messages.ts、docs/dev/endpoint-scope-matrix.csv——本任务避免/最小化触碰；若必须（如注册新工具到 __init__），做最小适配 patch，rebase 时留意 |

## 开放 issues（审查 backlog）——触碰路径风险隔离

与制图相关：#1779（cartography_runtime.py:487-503 UnboundLocalError）、#1809/#1810/#1811（mapspec filter 三连）、#1812（版式资产 422）、#1813（inset 纬度钳错）、#1822（制图导出 S5b 审查汇总）、#1826/#1827（前端 map/mapspec 审查）。
**纪律**：这些是既有问题，本任务不批量清偿（避免与在修 agent 抢文件）；但：
- 本任务新增代码不依赖 #1779 的坏路径（M2 接线时绕开该函数或先核实已修）；
- M4 新增检查若与 #1809-#1811 的 filter 语义正交，不修复它们；
- 若本任务测试撞上这些 bug，如实披露为 pre-existing，测试用例绕开并注明 issue 号。

## 未提 PR 的活跃分支（worktree 审计 2026-10-30）

gis-agent-edit（编辑方向）、gis-analysis-workflows（分析方向）、gis-data-onboarding（=PR #1868）、g3dv2-a/b（已合并）。无制图方向在途分支。本 worktree `gis-carto-design` 唯一。
