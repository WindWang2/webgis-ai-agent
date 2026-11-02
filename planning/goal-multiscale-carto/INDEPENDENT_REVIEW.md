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

## R2（复审，2026-11-02，同一评审员对清偿 diff 复核）

结论：**有条件不通过** —— P1/P2-2/4/5/6/7/8 清偿真实（逐项 PASS）；但
P2-3 的 CAS 基线实现两个方向皆坏（`or` 吞掉存量 0 → 重放无 CAS 盲踩
外部写者；存量 >0 过期 → 永久 superseded 卡死，旗舰测试因事故而非正确
逻辑通过），且 P2-5 修复引入空几何误报 project_failed。三项新发现 +
一项既有分叉（strategy=none 不可解析几何）附实证。

## R3（终审，2026-11-02，commit fa83e2e1b 清偿后）

清偿：CAS 基线改为「显式 expected_revision > `_current_revision` 重读
（与 engine 印章同源）> None」；空几何 `not geom_ll.is_empty` 门控；
空循环守卫（last_res None → status=error）；unparseable 对齐 geom_active
门控。回归锁 ×4（过期存量不卡死/显式基线优先/空几何全等/strategy=none
零码）+ 评审后补空循环守卫锁（`test_resume_shrunk_intents_reports_error`）。

评审员实测（含手工竞态复现：重读后外部写者推进 → 首 intent 诚实
superseded → 再重放 committed）：**R3 通过**，四项全部真实清偿，无新
问题引入。指定套件 104 passed 1 skipped；全量 scale_profile+multiscale
= 105 passed 1 skipped（加守卫锁后 18+87）。

**两轮独立评审 + 终审全部关闭；无未清偿 P0/P1。**
