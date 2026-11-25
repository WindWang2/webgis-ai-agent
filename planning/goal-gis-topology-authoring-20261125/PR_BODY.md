# feat(gis-edit): constraint-preserving spatial authoring and topology review

任务 02｜专业 WebGIS 拓扑约束编辑、空间要素生产与质量闭环（goal-gis-topology-authoring-20261125）。

把既有单要素/结构编辑（feature_edit, G14）与多源拓扑规则（topology_workbench, G31）连成面向生产的**跨要素约束编辑**：共边/线网的顶点级变更按共享结构同步传播，约束违规在预览期以「基线存量 / 本次新增」分离呈现，提交走既有唯一写路径（CAS + journal + 合同冷复核 + MapSpec 盖章），undo/redo 语义不变。

**明确不做**（避让声明）：数据融合/多源实体匹配（#2273 方向）、多尺度综合（#2274）、情境多方案 Harness（#2275）、字段契约重写（G44 已有，仅消费）、Pi vendor/bridge（G12 所有）。

## 基线
- origin/master `8fe3d76634bfb7052f438325c7db3e0ef3dad040`（2026-11-26 fetch 核查，含 #2266）
- 分支 `zcode/goal-gis-topology-authoring-20261125`，独立 worktree；开放 PR 避让矩阵见 `planning/goal-gis-topology-authoring-20261125/baseline.md`（4 个功能 PR 与本域零文件交集；共享接线策略见下）
- Pi pin：**未改动**。master 的 `vendor/pi` 为指向已删除路径的符号链接（blob `dd645f50`），本机 `entry_built=False`，既有 Pi journey 测试按设计显式 skip（`PI_JOURNEY_E2E=1` 门）→ **Pi live 旅程：blocked（基线打包问题，归 G12/方向 01 所有）**。同链证据以真实 ToolRegistry.dispatch + 真实 session store 的工具链测试与 REST 事务链测试替代。

## 新增用户能力（全部经真实服务执行）
| 能力 | 入口 | 语义 |
|---|---|---|
| 共享顶点移动 | op `move_shared_vertex`（坐标或 vertex ref 定位） | 三面交汇节点移动同步所有引用要素；Z 保留；环闭合同步 |
| 共享边插点 | op `insert_shared_vertex`（edge between/near + snap_to_edge） | 两侧插入点逐字节一致（细分）或同步弯折（snap=false） |
| 共享节点删除 | op `delete_shared_vertex` | 双侧同步删 + 环自动再闭合；最少顶点守卫 |
| 共享边重塑 | op `reshape_shared_edge`（between + path） | 按各要素环序（正/反）同步替换直边为新路径 |
| 受控端点连接 | op `connect_endpoint` | 容差 fail-closed（no_snap_candidate）+ level_field 守卫（connect_level_conflict 拒绝桥/隧误连） |
| 约束面检查 | 工具/REST `inspect_edit_constraints` | 声明规则 + 共享结构摘要（共享边/交汇度/悬挂端点）+ 基线违规 + 邻接关系 |
| 影响投影 | 工具/REST `get_impact` | 受影响邻接对象（shares_edge/incident_node）、逐要素测地面积/长度变化、原子性范围（跨数据集 blocked）——纯读不落 plan store |
| 复检 | 工具/REST `revalidate` | 当前修订版声明规则复检（declared-rules-only，未声明明确说明） |

匹配规则用户可选：`exact`（默认）/ `tolerance_m ≤100m`（歧义 typed 拒绝）/ `level_field` 层级守卫（值不同永不合并）。全部边界常量入 `contracts.py` 单一登记点。

## 用户旅程（3 + 负例，均可复现）
1. **面边界**：REST 事务链 `test_full_journey_atomic_commit_and_undo`（preview→approve→两面同步原子提交→undo 字节级恢复→redo）；前端共享拖拽（客户端 provisional 高亮 + 服务端权威传播，`feature-layer-editor.test.tsx`）。
2. **线网**：`connect_endpoint` 工具/REST 双面（`TestConnectEndpoint`）；线网自触经 `no_self_intersection` 声明规则阻断（`test_topology_new_violation_blocks_commit`）。
3. **拒绝/撤销**：Playwright j50 第二旅程（reject→rejected 卡片，决策仅 reject）+ REST reject 零写入 + undo/redo。
4. **负例**：stale CAS 409、越容差拒绝、层级冲突拒绝、新增违规阻断不可 approve、重复 approve 幂等零二次写、容差歧义 typed 拒绝。

## WP 验收表
| WP | 交付 | 关键证据 |
|---|---|---|
| WP1 结构建模 | 节点/边索引（≤500k 顶点，typed index_too_large）+ typed impact plan（plan.impact/impact_summary） | 三面交汇/洞环/悬挂端点索引用例；impact 原子性披露 |
| WP2 共边编辑 | 4 op 双侧精确同步；基线/新增违规分离（新增阻断、存量披露不阻断） | moved 坐标逐字节相等；intersection 面积 0；baseline split 用例 |
| WP3 线网约束 | connect_endpoint + 悬挂端点披露 + level 守卫 | tolerance 双侧用例；level_conflict 用例 |
| WP4 原子提交 | 单 ref 多要素事务闭环；跨数据集显式 blocked（不做半提交） | 事务链/幂等/CAS/reject 用例 |
| WP5 同链 | 3 只读工具 + 3 REST 端点（require_owned_session）+ 前端工作台 + i18n 独立命名空间 | 工具链测试；j50；卡片 impact 区块 |
| WP6 质量 | 五 op 逆元恒等/同种子重放/交换序恒等/结构不变量；1k/10k perf 实测；预算诚实拒绝 | test_shared_edit_quality.py |

## 顺带修复的既有域内缺陷（基线复现后修复）
1. **journal 逆 delta 顺序（P1）**：同要素多几何 op 的 plan undo 停在中间态（derive 的 deltas 是顺序累积态）。`invert_deltas` 改为逆序回放；单 delta/跨要素顺序无感，created 原位重插按 index 升序不受影响。复现：replace_geometry+move_by 双 op undo ≠ 原字节 → 修复后相等。
2. **pyproj ≥3.8 数组广播**：`move_by` 对面/线要素在本机 pyproj 3.8.0 崩溃（GeodError: Array lengths are not the same；既有测试只覆盖点要素所以绿）。改为显式全数组调用，标量路径不变。

## 复现步骤（本地验证）
```bash
# 后端（venv: python3.12 / shapely 2.2.0 / pyproj 3.8.0）
pytest tests/unit/feature_edit/ --no-cov -q          # 187 passed
ruff check app/services/feature_edit/ app/schemas/feature_edit_schema.py \
  app/schemas/feature_proposal_schema.py app/tools/feature_edit_tools.py \
  app/tools/categories.py app/api/routes/feature_edits.py tests/unit/feature_edit/
pytest tests/unit/feature_proposals/ tests/topology_workbench/ --no-cov -q
# 前端
cd frontend && pnpm vitest run components/chat/edit-proposal-card.test.tsx \
  components/map/feature-layer-editor.test.tsx lib/api/feature-edit.constraints.test.ts
pnpm typecheck && pnpm lint
E2E_PORT=3331 pnpm exec playwright test j50          # 2 passed（mock 档）
```

## 资源与性能测量
- 1k 地块（32²网格）preview ≈2.5s、10k（100²）≈22s（process_time，含两次索引构建：op 求值 + impact 投影；本机 8 核）；顶点预算 500k typed 拒绝不无界加载。
- 无 PostGIS/Redis 依赖（每 worker 隔离 SQLite/内存 store）；未触发线上 CI。

## 独立评审
- R1 冷审（未参与实现的独立 agent，从 diff+验收逐项+对抗探测）：结论见 `planning/goal-gis-topology-authoring-20261125/review.md`（含 P0–P3 清单与修复轮）。
- R2 复审：R1 修复后由另一 reviewer 从 diff 与证据独立复核。

## 未完成 / 明示 blocked
- **Pi live 旅程 blocked**（见基线节；替代证据为 ToolRegistry/REST 真实链路测试）。若 G12 修复 vendor 打包，`tests/integration/test_pi_g31_topology_journey.py` 同款旅程可直接复用本分支工具面。
- real 档 Playwright journey 未新增（本地无 Redis/Celery；mock 档 j50 + 组件/事务测试覆盖同等断言面；j24 模式的 real 旅程可作为后续补充）。
- 容差匹配不跨反经线（exact 模式可用）；Z 路径的 insert/delete/reshape typed 拒绝（move/connect 保留 Z）。

## 共享接线与合并顺序建议
本分支触碰的共享文件（均 additive、最小行数）：
- `app/schemas/feature_edit_schema.py`（op 词表 + `to` 别名分流 validator）
- `app/schemas/feature_proposal_schema.py`（提案 op 词表 additive）
- `app/api/routes/feature_edits.py`（3 端点 + import）
- `app/tools/categories.py`（3 分类覆写）
- `app/services/feature_edit/{contracts,plan,topology,identity,geometry_ops,journal,wire_types}.py`
- 前端：`lib/types/feature-edit.ts`、`lib/api/feature-edit.ts`、`lib/chat/adapters.ts`、`lib/i18n/messages.ts`、`components/map/feature-layer-editor.tsx`、`components/chat/edit-proposal-card.tsx`、`e2e/helpers/registry.ts`
- **未触碰**：`app/tools/__init__.py`、`app/main.py`、`app/models/__init__.py`、共享 i18n message 文件 —— 与 #2272–2275 的公共接线面（main.py / tools/__init__ / page.tsx / 各自 i18n 命名空间）零交集，合并顺序无约束。
- journal/geometry_ops 两处基线修复若与并行分支冲突，以本分支语义为准（逆序回放 / 全数组 fwd），建议后合并本分支。
