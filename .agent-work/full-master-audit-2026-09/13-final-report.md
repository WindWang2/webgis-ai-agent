# full-master-audit-2026-09 最终报告（§34 十九项）

执行：ZCode 全自动 / 2026-09-11。审计工作区：`.agent-work/full-master-audit-2026-09/`。

## 1. master baseline commit

`2aabdc436842ffe0fe66419ea50fde3d1bfcceda`（审计基线；10 个 V7/V8 epic
分支当时已 rebase 于其上待集成 —— 裁决记录 00b-merge-decision.md）。

## 2. reviewed files / LOC

- 生产源码 census：1,588 文件 / 481,838 LOC（backend 952 py / 359,516；
  frontend 636 ts+tsx / 122,289）。
- 审查覆盖：4 个方向 agent 的 coverage 台账（agents/{A,B,C,D}/coverage.md）
  + 主 agent 自审（bridge/chat/registry/graph 链路）——生产逻辑全量进
  census，high/medium 逐行、low 结构检查（详证见各 coverage 文件）。

## 3. findings summary

**47 条独立 findings：P0=0 ｜ P1=8 ｜ P2=13 ｜ P3=26**（汇总
06/07/08/09 + findings-main-agent.md）。核心 P1：modelops 工具 import 崩、
reuse 驱逐失效（磁盘无界）、并发 accumulator 数据竞争、agent remove_layer
durability 永不发出、迟到响应跨会话污染、agent reorder 不持久化、SSRF
启动校验环境硬失败、16 生成物 stale+手改指纹。

## 4. issues created

26 个（#1197-#1222）：8 P1 独立 + 13 P2 独立 + 4 P3 umbrella + 1 集成。

## 5-6. issues fixed / closed

**26/26 全部关闭**（commit "Fixes" 自动关闭 24 + 手动关闭 #1213/#1220/#1212
附工程理由；#1221 fcntl 项在 Batch 0 完成）。

## 7. commits（本会话 master 线，全部已推送）

| commit | 内容 |
|---|---|
| 723c3b2d | fix(config) SSRF allowlist + fcntl 守卫（Batch 0） |
| 80e1b453…4f9d8853 | 10 个 epic 分支按序合并（merge commits） |
| 6e34e38f | fix(integration) ADR 撞号重编号 + 0035 mergepoint + 生成物再生成 |
| 3ffcca96 | fix(modelops) per-run 通道/reuse/stitching/registry（7 issues） |
| cbbc202b | fix(frontend) C-1/C-2/C-3/C-9 |
| bb8ff6f5 | fix(harness) TOOL_TIMEOUT 分类/driver 卸载/execute_plan 预算 |
| cf47ad0d+6a56b8ba | fix(platform) D-4/D-9/D-13/D-14/D-15 + 测试 |
| bd3012d8 | fix(tooling) D-5/D-6/C-4/C-5 |
| 6a1e3d96 | fix(modelops) P3 加固批（B-11..B-19） |
| 55b93c55 | fix(cleanup) P3 umbrella 清扫（A/C/D 域） |
| 6ce1b962 | fix(quality) 宽域回归缺口闭合 |
| 88d4ecde | feat(harness) **V8 统一能力运行时**（ADR→0137） |
| a85c7aab | docs(audit) V8 交付记录 |
| 7119c83b | fix(harness) Review A/B findings 全清（4 MAJOR + 4 MINOR） |
| de843dd0 | merge origin/master（并行集成调和：ADR/alembic/域对齐） |
| 58b8ae97 | merge origin/master（PR #1231）→ **final HEAD（已推送）** |

## 8. remaining known limitations

- V8 planner 未接 tool_surface_v3 热路径（零生产成本；接线为下一迭代，
  ADR-0137 已披露）；workflow/methodology/template/component 图段未投影。
- ExecutionEstimate 的 measured basis 待性能台账回填。
- vector-pdf 前端接线（#1213 披露关闭）、ST-P3-5/ST-P2-3（#1220 附理由）。
- Review B deferred MINORs（RB-3 锁内 modelops 全扫、RB-6 字节预算、
  RB-12 空租户全扫、RA-5/RA-11 文档措辞）——phase-c/review-{a,b}.md 台账。

## 9. performance findings / improvements

D-4/D-15 事件循环阻塞与带宽、B-9 driver 卸载、B-7 句柄泄漏、B-19 LRU、
A-2 别名指标、B-18 召回、A-4 计划预算截断（详证 05-performance-findings.md）。

## 10. Harness current production flow

03-harness-flow.md（从代码重建：chat/stream → PiBridge 池亲和 → turn
lease → dispatch_tool（tier/native 面 gate）→ ToolDispatchService（去重/
wave gate/execution_policy）→ ref 化 + map_action → SSE step_result →
MapSpec CAS 提交 → 观察/critique → durable context commit；含每边
truth/durability/cancellation 判定）。

## 11. Tool/Capability/Model architecture

04-tool-model-registry-map.md（9 个 truth source + manifest 投影表）。

## 12-13. Harness V8 architecture / implementation

ADR-0137 + 12-v8-delivery.md：Unified Capability Graph（742 节点/1228 边、
指纹缓存零重建、词表封闭、validate 机器闸）、Model 一等实体（8 caps +
8 algos + model 节点 + 查询链）、Qualification Engine（四态 + 结构化
reasons）、ExecutionEstimate（basis 披露）、可靠性罚分、候选规划
（资格+成本+可靠性+确定性排序）。Review A 4 MAJOR 全修（kind 过滤/去重/
scope 限定 id/UNKNOWN 可达）+ 回归锁定。

## 14. tests executed（本地）

- 定向：~1,900+（modelops 157+161、harness V8 23+parity 29、workflow 156、
  geocompute 190、cartography 1,194、platform、SSRF 19、upload…）
- 宽域：tests/unit 全量 9,607（9535 passed）+ 72 失败逐条基线归因
  （38+6+3+5 环境基线、14 顺序污染、1 预存在、11 本会话修复 → 零真实回归）
- gis/gis_harness 全量 1,651 passed（4 失败：3 基线环境 + 1 修复后重验绿）
- 前端：vitest 全量 **2,862 passed** + typecheck 双配置零错
- gates：integration preflight 六项 PASS、tool descriptor coverage 100%、
  API compat 12/12、alembic 单 head、registry validation 零 error

## 15. baseline environmental failures

unit-sweep-failures.txt + 基线 worktree 对比台账（fake-IP DNS、torch DLL、
fcntl 历史面、顺序污染清单——均已逐条与 2aabdc43 对齐证明非本线回归）。

## 16. review round findings

Phase C 跨域复审 + Review A（0 BLOCKER/4 MAJOR/7 MINOR/4 NIT）+
Review B（0/0/7/5）——findings 在 phase-c/；4 MAJOR + 4 高值 MINOR 已修
（7119c83b），其余 deferred 附理由。

## 17. final BLOCKER/MAJOR count

**BLOCKER = 0 ｜ MAJOR = 0**（修复后复验：V8+parity 29/29、preflight PASS）。

## 18. git status

`git status --short` → **空**（工作区干净，无未跟踪文件）。

## 19. final master HEAD

`58b8ae97a300e4fe8697ad01c09e7a24dae1a726`（= origin/master，已推送）。
