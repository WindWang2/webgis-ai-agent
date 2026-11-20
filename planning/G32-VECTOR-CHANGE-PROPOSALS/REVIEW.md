# G32 REVIEW — 独立评审与修复记录

## 第一轮(2026-11-13,未参与实现的独立 reviewer)

**Verdict: request-changes**(2×P0 + 3×P1 + 4×P2 + 10×P3;全部附实证探针)。

| Finding | 级别 | 摘要 | 修复(commit 4) | 回归测试 |
|---|---|---|---|---|
| P0-1 | 无界 append 破坏存储 + ProposalStoreCorrupt 构造器 TypeError 逃逸 | comments/decisions/resolutions 追加 op 均越 pydantic 上限(仅加载时校验)→ 下次读取整会话 500 | 变更内上限强制(comment_limit/decision_history_full/resolution 重新校验 ops 预算);全部 store 异常改 (code, message) 双参 | test_rp0_1a/1b/1c |
| P0-2 | 硬崩溃(kill -9)后 merging 永久卡死,四条出口全 409 | 进程内恢复判据存在,但崩溃后无人触发 | merge 入口对 merging 路由三态判据;recovery 视图/resolve 接受 merging | test_rp0_2 |
| P1-3 | UI 裁决载荷 {conflict_id, action} 被 schema 422(decided_by 必填) | decided_by/decided_at 可选 + 服务端 auth 上下文填充 | test_rp1_3 |
| P1-4 | split/merge 父上 delete_update 的 keep_current 是静默空操作 | keep_current 同步移除结构 op(split 按 op.wfid;merge 整 op) | test_rp1_4 |
| P1-5 | feature_ids 被接受但静默丢弃;merge 提案出生即死 | feature_ids 归一化持久;全路径消费;创建时落地检测(候选在 base 上必须可求值) | test_rp1_5 |
| P2-6 | withdraw 的 blob 永不物理删除(自引用) | 锁内先标记 released 再查共享 | test_rp2_6 |
| P2-7 | decide/merge 的状态检查 TOCTOU | 锁内 can_transition 复核 | (代码路径;竞态窗口无确定性测试) |
| P2-8 | 200 目标预算无强制(diff 400 vs plan 200 分叉) | 创建/更新时解析并集 ≤ MAX_PROPOSAL_TARGETS | test_rp2_8 |
| P2-9 | plan 级 op 的逐要素裁决误伤全体目标 | 无 wfids 的 set_fields/replace_geometry 裁决拒绝(诚实报错) | test_rp2_9 |
| P3 | 分层倒置(G14→G32 import)/_op_targets 死过滤/approve detail 误记 base rev/UI load-more 替换/恢复段无 resolve/keep-only 类型多出 take 选项 | 全部修复(normalize 移入 identity.py;UI 三处) | — |

reviewer 同时确认健全:**权限/所有权全端点**、**批准绑定不可绕过**、**合并幂等/进程内三态恢复**、**typed 值比较/几何确定性/自然 id 对齐**、**op_seed 身份稳定**、**存储原子性/租约**、**快照预算**、**与 #2094 无文件重叠**。

## 修复验证(复跑)

- 单元:`tests/unit/feature_proposals/` **43 passed**(33 原有 + 10 修复回归)
- G14 回归:`tests/unit/feature_edit/` **123 passed**
- Pi 全链:`PI_JOURNEY_E2E=1 tests/integration/test_pi_g32_feature_proposal_journey.py` **4 passed**(17.8s)
- ruff / eslint --max-warnings 0 / tsc:全绿
- 前端 vitest:**4 passed**

## 第二轮(复审)

**复审人**:第一轮同一独立 reviewer(未参与实现)。**审查对象**:commit `6cf8f699f`(fix(g32): 独立 review 修复),只读复审 + 独立探针复跑。

**Verdict: approve(附 2 项 P2 与若干 P3 遗留,均不阻断合入;建议随本 PR 或紧随其后处理)**

### 一、第一轮 finding 逐条复核(全部实证)

| Finding | 复核结论 | 证据 |
|---|---|---|
| P0-1 存储破坏链 | **已修复**。101 条评论→第 102 条 400 `comment_limit_reached`,会话列表仍 200;8-op 满额 + use_new_value 追加→422 `resolution_rejected`(op_limit_exceeded),store 重载健康(ops=8);`ProposalStoreCorrupt` 全部 (code, message) 双参,损坏文件→typed 异常不再 TypeError | 探针 `[comments]`/`[nine5]`/`[nine6]`;test_rp0_1a/1b/1c |
| P0-2 merging 卡死 | **已修复**。硬崩溃(状态持久 merging、无进程内处理)后:recovery 视图 200(verdict=not_executed);merge 入口路由三态判据→409 `merge_failed_proven_not_executed` 回 approved;重试 merge→200 merged,数据真实落盘。recovery/resolve 均接受 merging | 探针 `[crash]` 五步全链;test_rp0_2 |
| P1-3 UI 裁决 422 | **已修复**。`{conflict_id, action}` 裸载荷→200;`decided_by` 服务端填充且落 audit 记录 | 探针 `[ui-payload]`;test_rp1_3 |
| P1-4 结构父裁决空操作 | **已修复**。split 父 delete_update + keep_current→split op 按 `op.wfid` 精确移除;merge 父保守整 op 移除并披露于 effect 文案 | test_rp1_4 + rebase.py:203-226 代码复核 |
| P1-5 feature_ids 丢弃 | **已修复**。feature_ids 归一化持久(schema `feature_ids` 字段),create/update/diff/conflicts/bridge `_proposal_target_ids` 全路径消费;merge 提案(feature_ids 父集)create→diff(3 deltas)→approve→merge 全链通;创建落地检测把不可求值候选在 201 前拒绝(含 snap 依赖 payload) | test_rp1_5 + 探针(merge+disjoint 组合在创建即 422 `merge_disjoint`,不再产出死提案) |
| P2-6 blob 永不删除 | **已修复**。唯一引用 withdraw→`snapshot_blob_deleted=True`(锁内先标 released 再查 live) | 探针 `[blob]`;test_rp2_6 |
| P2-7 状态 TOCTOU | **已修复(代码复核)**。decide/_to_merging/update_proposal/resolutions 的 `_mutate` 锁内 `can_transition`/状态复核,fn 抛 typed 冲突且未落盘 | service.py `_mutate` 各点 |
| P2-8 目标预算 | **已修复**。create+update 双点强制解析并集 ≤200(400 `target_limit_exceeded`);`MAX_PROPOSAL_TARGETS` 常量启用 | test_rp2_8 + service.py 两处 |
| P2-9 plan 级裁决误伤 | **已修复**。无 wfids 的 set_fields/replace_geometry 逐要素裁决→422 `resolution_rejected`(理由明确),ops 原样保留;`_replace_geometry_in_ops` 不再改写 plan 级 op(追加 op 级覆盖) | test_rp2_9 + rebase.py:52-70,304-318 |
| P3 清单 | **已修复**:分层(plan.py 不再 import feature_proposals;normalize 移入 identity.py,candidate 兼容 re-export)、`_op_targets` 死过滤(`or True` 移除)、approve detail 记真实当前 rev、UI 恢复段 resolve 按钮/keep-only 类型收敛/load-more 追加 | 各 diff 复核 |

### 二、修复引入/暴露的新问题

| # | 级别 | 位置 | 问题 | 建议 |
|---|---|---|---|---|
| R2-1 | **P2(修复引入)** | `app/services/feature_edit/identity.py:199-201` | `natural_to_wfid_index` 迁移时把自然键提取从"跳过 `_wfid` 的链遍历"(原 candidate.`_natural_id_of`)换成 `resolve_feature_id`——后者走 FEATURE_ID_KEYS 链而 `_wfid` 居首(contracts.py:51),**物化后无顶层 id、仅有 properties 级自然键(OBJECTID 等)的要素不再进入索引**。函数级实证:同一物化数据,轮1实现映射 `OBJECTID→wfid`,新实现不映射→此类数据上按自然键起草提案 422 `feature_not_found`。缓解:G14 即时路径物化后同样不寻址此类键(先在行为,非本分支引入)、有顶层 id 的数据不受影响;但与该函数 docstring 仍宣称的"双向锚定"矛盾 | 链遍历时跳过 `WFID_PROP`(或复用 `_natural_id_of` 语义);补一个物化+无顶层 id 的寻址回归 |
| R2-2 | **P2(轮1遗留,本轮暴露)** | `app/services/feature_proposals/bridge.py:234`(`_proposal_target_ids`)、`conflicts.py:_op_targets`(merge 落到 plan 全体) | **merge_features 父集 = feature_ids ∪ 全部 op 级 wfids**:混排提案里无关 op 目标被静默吸并为 merge 父。"merge q0+q1 且编辑 q2"无法表达——不相邻时创建即 422 `merge_disjoint`(探针实证),相邻时 q2 被静默并入合并(diff 可见但语义非用户意图) | merge 父集收敛为 feature_ids(或显式 parents 键);或拒绝 merge 与带 op 级 wfids 的其他 op 混排 |
| R2-3 | P3 | `app/services/feature_proposals/rebase.py:~210-220` | delete_update keep_current 的 merge op 移除条件 `any(merge_features in new_ops)` 与冲突 wfid 无关(整删所有 merge op);当前因 R2-2(父集⊇op wfids)无可达危害,但条件本身过宽 + 注释块重复 | 当 R2-2 落地父集可判时收紧为 `wfid ∈ parents` |
| R2-4 | P3 | `service.py update_proposal` | 创建有落地检测,PATCH 无——仍可经 PATCH 写入不可求值候选(diff 诚实 blocked,但与新创建闸不一致) | update 路径复用同一 precheck |
| R2-5 | P3 | `service.py submit_resolutions`(stamped 循环) | 客户端若显式携带 `decided_by/decided_at` 会被保留(`if not item.get(...)` 才填充)——REST 面审计归属可被声明。建议服务端一律覆盖 | stamp 改为无条件覆盖 |
| R2-6 | P3 | `service.py merge()`(merging 路由) | 活跃合并窗口内并发的第二 merge 请求不再看租约、直接进恢复判据,可能在首个提交仍在飞行时把状态翻到 approved/recovery_required;数据安全仍由 G14 `_edit_lock`+journal 幂等+CAS 兜底,状态收敛,但决策日志可能出现虚假 merge_failed 记录 | 可选:非属主请求先查租约存活再 settle |
| R2-7 | P3 | `service.py create_proposal` precheck | precheck 走 derive 而非 build_plan——G14 独有闸(几何目标 ≤10、journal 字节预算)仍只在 merge 时显现;另注:precheck 继承 G14 验证器严格性(低基数字段 enum 拒新值),与即时路径语义一致、非误拒,但 Agent 起草会**更早**收到 422(轮1可存草稿、approve 才 blocked)——属行为收紧而非回归 | 可选:precheck 升级为 build_plan 复核;enum 严格性维持现状即可 |
| R2-8 | P3 | `frontend/components/workbench/feature-proposal-drawer.tsx` | 恢复段仍不调用 `GET /recovery` 展示证据(verdict/missing_evidence),只有 resolve 按钮;`use_new_value` 仍无 UI 入口(REST 可用) | 后续补恢复证据渲染与新值输入 |

### 三、复审复跑结果

- 单元:`tests/unit/feature_proposals/ + tests/unit/feature_edit/` → **166 passed**(9.7s;含 10 项 test_review_fixes 回归)
- Pi 全链:`PI_JOURNEY_E2E=1 tests/integration/test_pi_g32_feature_proposal_journey.py` → **4 passed**(16.4s)
- 独立探针(隔离脚本,-I,内存后端):P0-1 两变体、P0-2 五步链、P1-3、P2-6、九-op 追加、merge+disjoint、identity 索引函数级 delta——全部与上述结论一致
- 未跑:前端 vitest(worktree 无 node_modules;修复方自报 4 passed)

### 四、结论

两轮全部 P0/P1 与四项 P2 修复**真实、完整、有回归覆盖**;修复未引入阻断级新问题。R2-1/R2-2 两项 P2 属边缘数据形态与混排语义,诚实失败(422/blocked)且不损数据,同意随后续小修处理。**approve**。


## 第三轮(R2 P2 快速清偿,commit 5)

复审 approve 后同批清偿两项 P2 与一项 P3(附回归测试):

- **R2-1 已修**:`identity.natural_to_wfid_index` 自然键链遍历显式跳过 `WFID_PROP`(顶层 id 优先),物化后仅 properties 级自然键(OBJECTID 等)的要素恢复索引 — 回归 `test_r2_1_properties_level_natural_key_indexed_after_materialize`。
- **R2-2 已修**:merge 提案收敛 —— `merge_features` 必须是提案唯一 op 且显式 `feature_ids`(≥2 父);与其他 op 混排/缺父集在创建与更新时 typed 拒绝(不再静默吸并无关 op 目标为父)— 回归 `test_r2_2_merge_mixing_rejected_and_parents_explicit`(合法形态由 test_rp1_5 覆盖)。
- **R2-5 已修**:resolutions 的 `decided_by/decided_at` 服务端无条件覆盖(审计归属不可由客户端声明)。
- R2-3 随 R2-2 收敛(merge 单 op 语义下条件不再过宽);R2-4/6/7/8 维持复审评估(P3,边缘/可选,如实披露)。

复跑:单元+G14 **168 passed**(43+2 新增 + 123);本域 ruff 全绿。
