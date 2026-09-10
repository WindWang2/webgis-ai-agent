# 04 — Test Matrix（真实执行结果）

## 前端（全部本地，无 CI 依赖）

| 套件 | 命令 | 结果 |
|---|---|---|
| 全量 | `vitest run` | **288 文件 / 2762 例全绿** |
| 覆盖闸 | `vitest run --coverage`（exit 0） | lines 79.77 / branches 69.63 / functions 79.49 / statements 82.66 ≥ ratchet 75/70/75/60 |
| lint | `eslint . --max-warnings 0` | 0 错误 0 警告 |
| typecheck | `tsc --noEmit && tsc -p tsconfig.test.json --noEmit` | 0 错误 |
| build | `next build` | 成功（静态预渲染完成） |

### 新增测试（V6）
- `lib/workbench/delta.test.ts`（11）：部分字段/创建需名/级联删除/引用检查/Set 优先/
  重放幂等/diff 最小性/mode 出域/round-trip/**同节点异字段并发 undo**（§15）/删除子树反演/锁反演
- `lib/collab/collab-v6.test.ts`（11）：协议边界/隐私裁剪/journal 有界/presence 动作/
  认证 subprotocol/**hello doc 落地不回声提交**/**revision 门控（乱序/重复事件）**/
  **pong mismatch → 对账 refetch**/跨会话不串道
- `lib/workbench/workbench-scale.test.ts`（5）：100k 遍历确定性终止/叶子高度语义/
  reparent 守卫/**100k 投影结构守恒 + O(n²) 退化上界**（10k<200ms，100k<2000ms，实测 391ms 总）
  /**20k membership diff=1 键**（全量 >256KB，delta <1KB）
- `layers-tab.workspace.test.tsx` V6 块（3）：**键盘 reparent（Shift+←）**/stale 徽标 join/CollabBar 披露

### §15 必测 → 落点映射
| 必测项 | 后端（test_collab_v6.py） | 前端 |
|---|---|---|
| 两浏览器并发 | 门面挂钩事件 + 扇出测试 | delta 并发 undo 测试 |
| user vs agent visibility | 既有 user-wins + 新 lock 守卫（单发/batch/family） | — |
| stale revision | base_workbench_revision CAS 测试 | 409 rebase 测试（bounded 单次） |
| 重连补事件 | sync 回放（ws ping/sync 环回） | pong mismatch → refetch 测试 |
| 事件重复/乱序 | envelope 单调 seq | revision 门控忽略旧事件测试 |
| lock expiry | TTL=0 过期获取测试 | — |
| browser crash / server restart | finally 清理 + TTL 兜底；进程内降级模式全程即此 oracle | 重连 sync（mock WS） |
| 未授权订阅 | 认证矩阵 7 场景（4001/4003） | — |
| 跨 owner 数据 | 交叉使用 4003（JWT×匿名会话等） | — |
| undo 后他人变更存活 | — | delta.test 同节点异字段并发用例 |
| 100k projection | — | workbench-scale（结构守恒+预算） |
| BroadcastChannel 向后兼容 | — | V5 collab.test.ts 全绿（43 例 workbench） |
| 键盘操作 | — | Shift+←/→ reparent 测试 |

## 后端
| 套件 | 结果 |
|---|---|
| tests/test_collab_v6.py | **29 passed**（含认证矩阵/守卫/CAS/delta/事件/扇出/artifact 投影） |
| tests/unit/test_mapspec_lifecycle_engine.py + test_ws_auth + test_ws_service | 33 passed |
| tests/unit/test_gis_world_state.py | 9 passed（与 collab 合跑 36） |
| test_ref_lifecycle_v5 + test_artifact_registry + cache_chaos + cache_lineage + review_fixes | 68 passed |
| tests/quality（全目录，含 api-compat/contract-drift/realtime-contract/delete-ownership/generated-artifact-graph） | **333 passed + 2 xpassed + 7 skipped** |
| tests/test_alembic_metadata.py | passed（无新增 migration；head 不变 0031） |
| ruff（全部改动文件） | All checks passed |

## 已知披露
- Redis 主路径（pubsub Lua/跨 pod 扇出）在 USE_REDIS=false 套件中不可执行 —— 以
  同语义的进程内降级实现为 oracle（键布局/Lua 逻辑经代码审查 + 2 个 subagent 评审覆盖）；
  real-services lane 本 Epic 不需要（无新外部服务依赖；Redis 本身是既有部署组件）。
- MemorySessionStore 落盘导致跨 pytest 进程状态残留 → 本套件测试用 uuid sid 隔离
  （repo 既有引擎测试用固定 sid，CI 全新环境无感，潜在 flake 记 follow-up）。
