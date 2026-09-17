# SPATIAL_DATA_QUALITY_SEMANTIC_HARMONIZATION_REVIEW

- 分支：`data/spatial-quality-harmonization-v1`（base `faa453a8`，origin/master @ 2026-09-16）
- review 方式：Subagent B 独立对抗性 review（read-only，含 inline python 证据探针）+ 主 agent 复现/修复
- review 时间：2026-09-16（代码冻结前最后一轮）

## 结论

初判 **FIX-FIRST** → 2×P1、4×P2 已全部 `失败测试 → 修复 → 回归` 关闭（commit `93a13557`）；P3 中 3 项修复（P3-2/3-4/3-6）、3 项记录为已知边界。修复后全套 DQH + 相邻回归 **217 passed / 1 skipped**，ruff 全绿。

## Findings 与处置

| ID | 维度 | 摘要 | 处置 |
|---|---|---|---|
| P1-1 | 并发/幂等 | apply 无锁 check-then-act（并发双执行→ref 链分叉）；dry-run 重放覆写终态会话（回退不可达） | **已修**：per-session asyncio.Lock + 终态缓存保护 + dry-run 无状态化；3 个新回归锁（并发恰一次执行 / dry-run 不 clobber / 回退可达） |
| P1-2 | async 红线 | 全载荷 canonical sha256 在事件循环（100k 行 ~392ms 停顿） | **已修**：source/preview digest 全部入 `asyncio.to_thread` |
| P2-1 | fail-open | gate 收敛 unknown 先于 blocked（不可修复 error 无检查事实 → unknown → V8 视为无增量）；nothing-checked 可收敛 ready | **已修**：issues/status 事实先行，ready 要求确有已执行检查；3 个新收敛锁 |
| P2-2 | 向后兼容 | `QualificationContext.to_dict` 无条件发新键 → plan memo key / situation_digest 默认参数下漂移 | **已修**：质量面键仅在 `quality_gate` 非空时进投影；新增默认键缺席锁 |
| P2-3 | 误伤面 | 语义闸族级宽判（denominator 被无关 count 绑定拖累）；reason 覆盖其他失败 | **已修**：role→语义键域映射（denominator 只看 population/area/denominator）；FIELD_ROLE_AMBIGUOUS 仅在唯一失败时为 headline；4 个新回归锁 |
| P2-4 | 契约漂移 | plan 推导路径会直接执行声明门控提案（requires_declared_unit 等） | **已修**：推导路径跳过 `auto_applicable=False`/`requires_*` 步，跳过事实进 preview 证据；新增回归锁 |
| P3-1 | 缓存语义 | FIFO vs 文档 LRU；无锁 | **随 P1-1 修复**（终态保护 + 每会话锁），文档改为「有界 FIFO + 终态保护」 |
| P3-2 | 误报 | 混层级行政区列（省/市/区/街道）因单一解析成功被误报 | **已修**：同层级后缀判定（同层变体才判 mismatch，混层级=覆盖缺口）；2 个新回归锁 |
| P3-3 | 性能 | `_bounded_value_samples` 双扫描 | **已修**：单次扫描供语义推理与检测器共用 |
| P3-4 | 鸭子类型 | issue code 投影 `.value` 直接访问 vs getattr 不一致 | **已修**：统一 getattr |
| P3-5 | 生产休眠 | 质量面生产调用方未接线（workflow compiler / plan_orchestrator 未传 semantic_profile / quality_gate） | **记录为已知边界**：additive rollout 首轮测试可达，生产接线需 profile 供给管道（见下） |
| P3-6 | 风格 | 常量定义晚于使用 | **已修** |

## 误报排查（已澄清，不再追）

- 分层纪律：lib 零新增 import；`semantic_checks` 对 guardrails 只读复用（verify_code/resolve_name），无重写。
- 词表单一来源：新码全部走 QualityIssueCode 受控扩展 + propose_repairs 单点映射；`REMEDIATION_OP_BACKING` 全部 `fn:app.services.spatial_repair_pipeline:*` 背书均在 `CANONICAL_OP_ORDER` 内（代码追踪确认）；`RepairStep.__post_init__` 词表校验完好。
- 兼容性：`qualify_data_role`/`build_situation`/`qualify_node` 缺省路径与基线逐字节一致（回归锁 + Subagent B 独立复跑 53 passed/1 skipped）。
- 源载荷不可变：dry-run deepcopy 演练 / apply 新 ref / rollback append-only，三路径均无源改写。
- 有界性与租户：有界投影无载荷泄漏；evidence ≤5 短样本；无码表暴露。
- 空数据集 fail-closed：`build_profile_for_payload` 空 FC → gate=blocked（empty_payload 不可修复）。

## 剩余 P3 / 已知边界

1. **生产接线休眠（P3-5）**：`semantic_profile` → `qualify_workflow_data_roles` 与 `quality_gate` → `build_situation` 的生产调用方未接线。需要「画像随 resolver profile 供给」的管道（`DatasetProfile.to_resolver_profile` 同族的画像出口），且 plan_orchestrator 属 hot-path（与 open PR #1335/#1336 相邻），不宜在本分支硬顶。建议后续 integration PR：把 `build_profile_for_payload` 挂到 ingest 完成点，画像 digest 随 `register_artifact(profile_digest=...)` 附着（ADR-0104 #4 先例），compiler 从画像缓存读取。
2. **gate=blocked 在 V8 层是硬失格**：与 DECISIONS D4 实现时细化一致（blocked→ineligible，degraded→soft）；workflow 层五态语义不变。
3. 管理码表仅省/市级覆盖（GB/T 2260 快照既有事实）；区县级值域检查需等表扩展，当前口径诚实跳过。
4. perf 数字为本地环境事实（100k 行画像 2.3s / 事务 digest ~0.4s→入线程），不冒充 SLO。

## 与最新 master / open PR 交叉

- base `faa453a8`；open PR #1335（fail-closed 面，未触本分支任何文件）、#1336（GeoAI 热区，未触）。
- 本分支触点与 #1335/#1336 无文件交集（见 `.agent-work/data-quality-harmonization-v1/PARALLEL_OWNERSHIP.md` 触点矩阵）。
- 基线同败（非本分支引入，PR body 同步记录）：`test_repair_plan_v4` 迁移×2、`test_wave1_promotion_gc`×3、`test_durable_blob_store`×2、`test_quota_retention_v5`×1、`test_component_lifecycle[trio]`×1 —— 全部在干净 `faa453a8` 同 TestId 复现。

## 是否需要 integration PR

需要（后续）：生产接线（上列第 1 条）建议单独 PR，携带 ingest 挂点 + 画像缓存 + compiler 读取三件套，并补 compiler 层 blocked→stage 的端到端测试。本分支不阻塞其合入（全部 additive）。
