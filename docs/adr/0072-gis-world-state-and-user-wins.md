# ADR-0072: GISWorldState —— 统一读模型、GISMutation 门面与服务端 user-wins 守卫

日期: 2026-08-27
状态: accepted

## 背景

审计（Reviewer B）确认 desired state 真相源是后端 MapSpec，但**读侧**碎片化：
agent 感知需要拼 mapspec + map_state.layers + observation + review；**写侧**双轨
（用户路径强制 CAS、agent 工具 last-writer-wins）；"user interaction wins"
只在前端实现（`_userPinned` 豁免），服务端无强制——G6 场景（用户隐藏图层 →
agent 下一轮翻回可见）没有不变量保护。

## 决策

新增 `app/services/gis_world_state/`（**演化而非重造**——MapSpec 仍是唯一
desired state，MapSpecLifecycleEngine 仍是唯一写入者）：

1. **`build_world_state`** —— 统一有界快照：layers/sources/components 摘要
   （ref 与计数，绝无 payload）、viewport、用户交互决策
   （`user_hidden_layers`）、制图评审摘要、观察、provenance 决策链。
   Agent 经 `webgis_world_state` 工具感知（"地图现在什么状态/这层为什么隐藏"）。
2. **`apply_gis_mutation`** —— 统一 mutation 门面：守卫 → engine（一次调用，
   锁/CAS/COW 语义不变）→ provenance。用户路由与 `finalize_display` 已接入。
3. **provenance** —— 每会话有界环形（64 条，存 `map_state._gis_provenance`），
   记录 origin/actor/kind/target/revision。best-effort，绝不阻断主语义。
4. **UserPresentationGuard（服务端 user-wins）** —— `origin=agent` 的
   `PatchLayerPresentationIntent` 若把某层可见性**反转**为与"用户最后决策"
   相反的值 → 拒绝（is_error + correction_hint）；同值幂等重放允许；无用户
   决策记录的层允许（正常收口语义）。用户自己重新打开后恢复常态。

## 后果

- G6 不变量服务端强制：agent 无法覆盖用户显式显隐决策，但 agent 状态不失真
  ——QA/world state 如实暴露"该层由用户隐藏"，agent 可解释而非可覆盖。
- `finalize_display` 结果新增 `user_hidden_respected` 清单（诚实上报，不阻断）。
- opacity 暂不硬守卫（连续值难以判定"意图对抗"）；分布证据明确后可扩展。
- 长会话成本：provenance 读写 O(64)，快照摘要各有界（100/100/64/16）。
