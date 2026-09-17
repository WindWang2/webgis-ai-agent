# GAP_ANALYSIS — 任务书方向 vs master 现状

结论：master **没有**已存在的制图规范 Rule Graph / 版本化 StandardsPack / purpose×audience×medium profile / 确定性 QA pack。方向无需 rescope，但实现必须以 adapter/委托 面貌叠加在既有引擎上。

## 逐项差距

| 任务书要求 | master 现状 | 差距 → 本分支动作 |
|---|---|---|
| 可声明 CartographicRule（applies_when/requires/forbids/severity/evidence/fix_hint/references） | 规则内联在 semantic_checks 2686 行 + required_components_for 硬编码表 | 新建 `standards/rule.py` 冻结 dataclass；check kind 有界词表，测量委托既有函数 |
| 版本化 StandardsPack | 无（pack 先例仅 model_packs/composition_packs 数据包） | `standards/pack.py`：frozen、semver、fingerprint、registry、resolve(version) |
| purpose/audience/medium profile（exploration/analysis/publication/briefing/mobile/print） | 仅 cartographic_profile（5 值规则 profile）+ OUTPUT_PURPOSES（画布） | `standards/profile.py`：三轴词表 + 推断（OUTPUT_PURPOSES→medium、frame.pageSize→print、cartographic_profile→purpose）+ 显式覆盖 |
| 规则覆盖 map type、count/rate/density、classification、palette/CVD、legend-title-unit、label density、scale/north/source/time/uncertainty | classification/CVD/legend 存在性/label collision 已有引擎；**无** count-vs-rate、legend unit、source/time/uncertainty disclosure、label density 义务、audience 级收紧 | 新增声明规则 ~12 条；每条挂测量源（engine:// refs） |
| RuleGraph 依赖/冲突 | 无 | `standards/graph.py`：拓扑排序 + 环检测（构建期 fail-closed）+ 冲突双披露 + 依赖失败 → 依赖方 not_evaluated |
| 编译前 gate + 编译后 QA | 无 standards 面（quality_loop 是 desired-state 修复循环） | `standards/qa.py`：precompile gate（block 需要 explicit strict profile）+ postcompile QA 报告 |
| fix hints 走现有 mutation/self-heal | AUTO_SAFE 词表/autofill/advisory 已存在 | fix_hint 三路由（quality_loop 操作/component_autofill/advisory）+ 跨模块契约测试 |
| QA 产出 rule ids + evidence refs | CartographyCheck 有 evidence 无 refs 词汇 | StandardsViolation: rule_id/severity/message/evidence_refs（`mapspec://` `profile://` `engine://`） |
| standards catalog + bounded Pi card | catalog_docs 生成器 doctrine；pi_card 属 harness 热区 | catalog 生成 + drift 测试；Pi card 为报告上的有界投影字段（不接 harness 热区文件） |
| 200+ fixtures + intentional violations | quality_cases 先例（input+expected.json+_generate.py） | combinatorial 生成（rule×medium×present/absent），确定性坐标/字段 |
