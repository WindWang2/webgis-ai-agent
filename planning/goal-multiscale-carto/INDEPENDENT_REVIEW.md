# INDEPENDENT_REVIEW — GOAL 04

## R1（2026-11-02，独立对抗性评审员，未参与实现）

范围：`git diff origin/master...HEAD` 全量（generalize/importance/profile/
plan/export_adjudication/service 全文精读；mapspec_to_svg 接入点、前端 5 文件、
schema、8 个测试文件抽查；3 组只读复现脚本：快路径分歧 / 回退计数 /
容差量级）。评审员自跑：multiscale+scale_profile 102 passed；schema/corpus
112 passed；vitest window-source 6 passed。

### Findings 与清偿映射

| 级别 | 问题 | 清偿（commit 见 LOOP_STATE R8） |
|---|---|---|
| P1-1 | 线快路径（tol≥全长直构端点线）绕过长度损失不变量、回执虚报 0 损失、与单带 GEOS TopologyPreservingSimplifier 不等价（U 形实证 5 点 vs 2 点），且落在等价测试盲区 | **删除快路径**（correctness/equivalence 优先）；等价测试补 tol≥全长 U 形用例（`test_p1_equivalence_in_fast_tolerance_regime_u_shape`）；性能基准重跑（50k 13.4→16.4s、200k 56.1→66.1s，代价 ~18%） |
| P2-2 | 工件 data_fingerprint 哈希 ref_map（含服务端 ref_id）而非内容 → 同内容假阳性变更 | `_fc_content_fingerprint`（规范化 FC 内容哈希）+ `test_artifact_content_fingerprint_stable` |
| P2-3 | 链中断 hint 与实现不符（partial 无存证 → 重放全重跑产孤儿 ref） | 中断持久化 partial 回执（committed_slots/chain_revision/已存 refs）；重放断点续链（复用已存 ref、跳过已提交 intent）；回执环同 op 替换；`test_partial_chain_resume_after_supersede` |
| P2-4 | export_adjudication 越界回退取首窗，与前端孪生（末窗）/normalize（拒缺口）语义相反 | fallback 改取 max_zoom 最大窗（= 末窗）+ `test_p2_4_gapped_plan_fallback_is_last_window` |
| P2-5 | `anchor_lost_fallback` 不计入 geometry_fallback；多带路径投影失败静默跳过（与单带 project_failed 不等价） | 后缀表补 `_fallback`；多带 geom_active 门控下补 project_failed 同码同计数 |
| P2-6 | profile_id 只含 5 位 hex（20 bit 熵） | 取指纹 hex 段完整 16 位 + 测试 |
| P2-7 | dpi=inf ZeroDivisionError；keep_ranks 键名笔误静默 fail-open 全保留 | `math.isfinite` fail-closed；keep_ranks 与缺省合并（未提及带用缺省秩）+ 测试 |
| P2-8 | `_anchors_present` 阈值注释与赤道近似事实不符；fingerprint 排除面文档不全 | 注释修正（方向保守披露）；docstring 补充 |
| P2-8b | validate_profile 首带负 min_zoom 不拒 | 首带必须 ==0（`bands_do_not_cover_zoom_0`） |

### 未发现问题（评审员确认）

非破坏性（快照锁定）、importance fail-honest（中英/大小写/空串/bool/裸数值
全拒）、双基准换算（除 inf 外已 fail-closed）、CAS 链语义、mapspec_to_svg
接入点不破坏既有分支、前端无 source 计划零变化、v1.9 纯 additive、测试无
伪通过。

## R2（复审，2026-11-02）

（清偿后复审结论见下 —— 由独立评审员对修复 diff 复核）
