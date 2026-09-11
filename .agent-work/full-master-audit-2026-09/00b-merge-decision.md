# 00b — 关键裁决：V7/V8 分支合并顺序

## 事实（2026-09-11 验证）

任务书前提："master 最近刚连续合并了 Harness V7 / Workflow V6 / Workbench V7 /
Science V6 / ModelOps V3 / Platform V4 / Cartography V7 / GeoCompute V8 /
Lakehouse V8 / Data Fabric V8"。

实际验证：master@2aabdc43 **不包含**这些内容。它们以 10 个本地 feature 分支存在，
且全部 rebase 在当前 HEAD 上（behind=0，ahead 5-15）：

| 分支 | ahead | 状态 |
|---|---|---|
| feat/harness-v7-agentic-runtime | 13 | 待合并（有 ADR-0130 + PR 终稿） |
| feat/modelops-v3-geoai-runtime | 13 | 待合并 |
| feat/platform-v4-production-control | 15 | 待合并 |
| feat/workflow-v6-durable-cluster | 11 | 待合并 |
| feat/workbench-v7-pro-gis | 11 | 待合并 |
| feat/geocompute-v8-runtime | 11 | 待合并 |
| feat/data-fabric-v8-federation | 10 | 待合并 |
| feat/cartography-v7-composition | 8 | 待合并 |
| feat/science-v6-spatial-intelligence | 7 | 待合并 |
| feat/lakehouse-v8-versioned-cubes | 5 | 待合并 |
| feat/gis-data-artifact-workspace-v3 | 0 | 已完全包含 |

（另有 methodology-v2 / modelops-v2 / science-v6 外部 worktree 已并入。）

## 裁决

任务书的"合并后集成审计"目标状态 = 合并这 10 个分支之后的 master。执行顺序：

1. Phase A 只读审计 master@2aabdc43 基线（进行中，4 agents）——大部分文件不受合并影响，findings 保留有效；受影响文件在合并后复审。
2. 合并阶段（Phase B 入口）：按依赖序逐个 merge 进 master（无 worktree、直接 master），每个 merge 解决跨分支冲突（ADR 编号用 scripts/allocate_adr.py、migration 用 allocate_migration.py、生成物用既有 generator 再生成），preflight+定向测试绿后再进行下一个。
   建议序（由基础设施到应用面）：
   platform-v4 → lakehouse-v8 → data-fabric-v8 → geocompute-v8 → science-v6 →
   modelops-v3 → workflow-v6 → cartography-v7 → workbench-v7 → harness-v7（最后，
   因其作为 runtime 顶层需看到全部下层注册面）。
3. 合并后执行"合并后 Integration 审计"（registry 冲突/ADR/migration/生成物/契约漂移）——这是任务书 §9 的真正对象。
## 已确认的跨分支冲突面（合并时处理）

1. **ADR 撞号**：三个分支各自新增 `docs/adr/0130-*.md`：
   - harness-v7 → 0130-gis-harness-v7-agentic-runtime.md
   - geocompute-v8 → 0130-geocompute-v8-distributed-spatial-compute-fabric.md
   - lakehouse-v8 → 0130-lakehouse-v8-versioned-cubes.md
   处理：按合并顺序保留第一个 0130，其余用 scripts/allocate_adr.py 分配 0131/0132
   重命名 + 更新引用（CHANGELOG、ADR 交叉引用、代码注释）+ 推进 ownership.json watermark。
2. **Migration 撞号**：三个分支各带 `0035_*`（lakehouse_dataset_versions /
   geocompute_v8_fabric / workflow_v6_durable）→ 合并后 3 个并行 head。
   处理：仿照 c0d8322aa2cb（mergepoint 模式）新建 0035 merge migration 收敛单 head
   （scripts/allocate_migration.py + 既有先例 064e297e）。
3. **CHANGELOG.md**：各分支都追加条目 → 文本级冲突，按时间序手工合并。
4. 其余文件级冲突在合并时逐个解决（保持"双方语义都保留"原则）。

## 依据

任务书要求全自动、默认最合理方案；用户明确"直接在 master 中处理"；
这 10 个分支是用户预先 rebase 到 HEAD 的待集成工作（与上一代 v6/v7 合并
历史模式一致：merge commit 信息见 git log --merges）。
