# G32 PLAN — 实现计划与状态

## 模块布局(新增)

```
app/services/feature_proposals/
  __init__.py       公开导出
  contracts.py      状态机/常量上限/typed 异常/迁移表
  store.py          per-session JSON 原子持久 + 分布式锁 + merge 租约(同 review 模式)
  snapshot.py       base 快照证据: BlobStore CAS 写入/验证读取/释放标记
  candidate.py      候选求值(调 G14 derive_changes, seed=op_seed)→ 差异证据(有界)
  conflicts.py      纯函数三方冲突分类(read/write/birth/retire sets; typed 值比较)
  rebase.py         rebase 预览/执行(新 revision+digest, 旧批准失效)
  service.py        编排: 创建/列表/详情/差异/冲突/裁决/批准/合并/撤回/恢复/审计导出
  bridge.py         批准绑定 + G14 提交桥(build_plan(seed=op_seed) → commit_plan)
app/schemas/feature_proposal_schema.py
app/api/routes/feature_proposals.py      REST(prefix /chat, require_owned_session)
app/tools/feature_proposal_tools.py      Agent 工具(create/list/diff/rebase_preview)
frontend/lib/api/feature-proposals.ts
frontend/lib/proposals/store.ts
frontend/components/proposals/proposal-review-drawer.tsx
frontend/components/proposals/save-proposal-button.tsx
frontend/messages/{zh-CN,en-US}/featureProposals.json
tests/unit/feature_proposals/*.py
```

## G14 最小适配(3 点,均有测试,向后兼容)

1. `build_plan(..., plan_seed: Optional[str])` — plan_id 可由调用方 seed(fpr- 前缀);create/split/merge 出生身份由 seed 派生 → proposal 候选跨重载 child wfid 稳定。
2. op 级 `wfids` 可选键 — derive_changes 中 plan 级 op(set_fields/repair/move/snap/delete/replace_geometry)按 `targets ∩ op.wfids` 限定;build_plan 把 op.wfids 并入解析集;geometry 上限按几何 op 实际触及并集计。
3. `EditOpModel.wfids: Optional[List[str]]`。

## 工作包执行序

- [x] WP01 查重与单源设计(BASELINE/DEDUP_MATRIX)
- [x] WP02 contracts + store + schema
- [x] WP03 snapshot(BlobStore CAS)
- [x] WP04 candidate(derive 复用 + op_seed)
- [x] WP05 conflicts(纯函数矩阵)
- [x] WP06 rebase + resolutions
- [x] WP07 bridge(批准绑定 + 单 ref 受控提交)
- [x] WP08 回执/恢复(recovery_required、租约、幂等重放)
- [x] WP09 REST + Agent 工具
- [x] WP10 前端(drawer + 保存入口 + i18n)
- [x] WP11 审计导出 + 引用释放
- [x] WP12 验收测试(P01–P28 映射见 TEST_MATRIX)+ Pi 全链

## 验收场景映射(P01–P28 → 测试)

见 TEST_MATRIX.md。核心: tests/unit/feature_proposals/{test_conflict_matrix.py(手工 oracle), test_store_cas.py, test_service_journey.py, test_bridge_crash.py, test_routes_auth.py, test_tools_dispatch.py, test_reference_oracles.py}。E2E: 双实例 CAS、重启恢复、前端 vitest、Pi 子进程旅程。

## 决策记录

- D1 proposal 真相=JSON 文件(与 review 同);不建 DB 表(会话生命周期对象,与 mapspec/review 同理),不进 map_state(易失/有界)。
- D2 base 快照=BlobStore CAS 全量 canonical JSON(≤8MB 上限,超限拒绝建 proposal — editable dataset 本身 ≤20k 要素,快照与数据同量级,可接受);digest=sha256。
- D3 冲突比较用"快照 base vs 当前 ref 实时载荷"直接对比(不依赖 journal — journal 可能已逐出);read set 按 op 类型静态推导(snap 参考源/merge 字段政策/split 父)。
- D4 提交桥 = build_plan(seed=op_seed) + executor.commit_plan(idempotency_key=f"fpr:{pid}:{rev}:{digest12}") — journal 幂等 + proposal merge record 双保险。
- D5 agent 不可批准: decisions 端点 actor 恒 user(auth 上下文);工具无 decision 函数。
- D6 前端复用 layer-edit-bridge 的回执消费(refreshDatasetLayer/lineage/undo)。
