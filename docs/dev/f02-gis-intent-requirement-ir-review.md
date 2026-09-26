# F02 — GIS Intent & Requirement IR · Independent Review（Subagent C）

- Review 对象：`origin/master...HEAD`（9e5b4bcf 实现提交，review 后修复另列 commit）
- 方法：全量源码读 + 权威模块对照 + 3 组真实代码 smoke（P1 均实测复现）
- 总裁决：**APPROVE-WITH-FIXES**（架构红线成立，无 P0；3×P1 + P2 清单）

## 架构判定（single-truth / 第二执行器红线）

**成立。** core 内嵌 `MapRequestIntent` + 显式 SYNC 表（patch.py `_sync_core`）保证 patch 后理解面一致；`projection.intent_view` 原样外泄 core。classify 的词面 cue 表是**新增裁决面**（五类顶层归类 + 语气护栏 + `_FALLBACK_ANALYSIS` 显式排除 resolver 默认族），不产出 TaskType/Analysis 词汇、不替代权威。normalize 只服务 digest。IR 不写 MapSpec、不选工具、不算 grammar/completeness。

## P1（全部修复，附回归测试）

| # | 问题 | 修复 | 回归 |
|---|---|---|---|
| P1-1 | journal 折叠只在 apply_patch 内截半段，genesis 不前滚 → 折叠后 `replay`/`replay_document` 抛 PatchStale，回放不变式失效（实测复现） | 折叠上移 service 层 `_fold_if_needed`：genesis 前滚为 `replay(genesis, folded_part)`（清 journal、对齐 folded 计数），现网保留尾部 journal；`folded_op_ids` 补折叠历史幂等 | `test_review_fixes.py::TestJournalFoldingReplay`（205 patch 驱动折叠 → 不变量成立 + 旧 op_id 幂等跳过） |
| P1-2 | 超替重建只携带 user provenance 不携带值 → 幽灵 user-wins：旧值丢弃却带 user 护栏，agent 永远无法修正该字段（实测：agent `set aoi.name` 被拒） | `carry_user_state` 连值带 provenance 携带；`_restore_user_fields` 仅在「新派生无表达（默认态）且旧值非默认」时恢复（新话语显式表达的面以最新用户表达优先）；恢复失败不带 provenance | `TestSupersedeCarriesUserValues`（新表达胜出 + agent 可修正；未表达面连值恢复） |
| P1-3 | `answer_ambiguity` 只改歧义状态不回写寻址字段 → aoi_unresolved 答完 aoi.name 仍空、accept 门禁被静默放行 | 答案先经 path 白名单 validator 验证（fail-closed 无部分状态），后回写字段 + provenance + core 同步 | `TestClarificationWriteBack`（回写 + core 同步；非法答案状态零变化） |

## P2（已修）

- locks 运行时上限（`add_lock` 超 MAX_LOCKS 拒绝）
- tools 接线透出 `patch_errors`/`superseded`（patch 被拒不再无信号）
- `normalize_normalization` 死条件：非口径词面（同比/对比）一律 none，不再误触发分母 blocking 问
- `save_state` 传 `seq=doc.revision`（store 协议 best-effort fencing）+ 模块 docstring 披露单写者假设（Pi turn lease 串行）
- schema skew 有损重建升级为 warning 日志
- `superseded_by` 指向真正的后继文档 id
- edit 不再把 live_map 置 False（增量修订时 live map 仍在）
- `remove_lock` 值经 `normalize_text` 归一比较
- `_current_owner` 对 `measures.<id>.field` 精确归属（替代 measures[0] 近似，消除过度保护）
- journal 有界折叠由 envelope 层负责（apply_patch 保持纯函数）

## P2（记录为后续项，不在本 PR）

- representation 面（hidden_layer_ids/palette）尚无下游消费者接线——projection 六出口之外的 MapSpec mutation 接线属后续方向（已在 ADR-0215 后果节注明）。
- document_id 为无密钥 sha256 截断（48-bit）——仅 session 内自见，爆破面极小；未来可换 HMAC(session_key)。
- 归因匹配（前后缀 startswith）偏宽松；并发 gather 场景测试。
- `classify._EDIT_CUES` 的裸「导出/下载」在有文档时会把全新 export 任务判为 edit——当前语义恰为"给既有需求加交付义务"，与 corpus export_publication 场景一致；若未来出现"整单换图"话语再细分。

## 测试有效性补强

review 指出的同作者盲区（折叠路径零覆盖、超替 user 值无负例、澄清回写缺失）已全部以 review-fix 回归测试闭合（`test_review_fixes.py`，8 tests），测试由 review 的独立行为复现反推，非实现自评。
