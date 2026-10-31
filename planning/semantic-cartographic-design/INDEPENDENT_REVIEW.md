# INDEPENDENT_REVIEW — 独立评审轮次（未参与实现的上下文执行）

## Round 1（2026-10-30，agent_c756bcb1，已完成清偿 c16ed762a）

结论：**需修复后合并** → 全部清偿。

### Findings 与清偿映射

| # | 严重性 | 发现 | 清偿 |
|---|---|---|---|
| 1 | P1 | webgis_map_design_select 往返校验绕过 IR 桥表（visual_heatmap/isoline_contour 被误拒；错误提示词表 4/6 不可用） | 校验改走 `_resolve_representation_model`（与写入/读回同一 seam）+ registry canonical；错误消息改为实测可往返 id 清单；补 2 个 pin 往返正/负回归 |
| 2 | P1 | statistic 投影死分支（intent.task 恒无 density_analysis）→ R1 原始计数披露生产不可达 | 投影改 `intent.measure ∈ (density,rate,ratio)` 或任务 ∈ (analytical_density, concentration_analysis)；补 e2e 密度披露回归 |
| 3 | P1 | 率语义候选对 density/spatial_distribution 类目零效应（亲和表无率模型；fd2 验收走的是 fallback 类目路径） | 密度语义或分母在场 + 面表达面 → 率模型按扩展可行域直接注入 + 门控对（率模型 + 降级链目标）入池；补 3 个引擎回归（含无面负例） |
| 4 | P2 | cap-4 罚分排序把门控对挤出（入池 ≠ 入集） | 首个率模型（审定序 normalized 优先）+ 其直接降级目标占保留席位；实测修正过保留（3 率模型全占席反挤对照）与排序巧合（cartogram 字母序胜出）两版缺陷 |
| 5 | P2 | planner._TASK_CATEGORY_BY_INTENT 7 个 key 非法 TaskType（继承缺陷被新投影放大） | 补 analytical_density/concentration_analysis 真实映射（最小加法，历史 key 保留） |
| 6-14 | P3 | docstring R5/阈值诚实、契约文档 measurement/overlay vocab、时相词表单源（TIME_FIELD_HINTS）、分母 unit 按字段语义、_geom_kind_cache 死代码、引擎池降级可观测、测试 typo 容忍、词法边界披露、3 JSON 行尾 | 全部清偿 |

### 评审确认的良好面（摘）
- 复用纪律：composition_selection/_candidate_map_models/choose_classification/HEATMAP_MIN_POINTS 全部调用非复制；共享文件最小 patch。
- 生成物真实性：契约 v3 计数如实；catalog 重生成 byte-identical；golden 有生成入口；scenarios pin 走 CLI。
- 幂等修复安全性：apply_patch 仅 op_id 跳过返回 False，早退只发生在良性重放/空表；真实写入失败仍上浮 fenced。
- 有界与安全：全字段截断、无新增网络面、pin path 硬编码、降级 logger.warning 不阻断主链。

## Round 2（2026-10-30，agent_56ee4180，已清偿）

结论：**close call——R1 清偿全部实证通过（含 80 模型 pin 往返枚举：71/80 可往返，9 拒绝均诚实）**；新发现 2 P2 + 6 P3，全部清偿。

| # | 严重性 | 发现 | 清偿 |
|---|---|---|---|
| F1 | P2 | planned 模型（cartogram_map）被注入为 SUITABLE 且可 pin | 注入循环与 `_model_feasible_extended` 增加 native 过滤（与组合层 native_ids 同款）；回归锁 planned 不入集 |
| F2 | P2 | 率语义模型（normalized_choropleth）永远无法被 pin（子串塌缩改道） | `normalize_geometry_kind` 增加精确注册 id 预检（整串恰为 canonical id 时跳过同义词子串塌缩；NL 输入永不命中该分支）；dot_density_map/别名输入均如实 pin；mismatch 分支保留为防御守卫 |
| F3 | P3 | 席位溢出角落（pin+bare_density+primary+对=5>4）门控对破约 | 率成员硬席位（从尾部换入，对照成员可被换出——其表达保留在 degradation_path，成对语义不丢失）；溢出场景回归锁 |
| F4 | P3 | validate_design_candidates_payload 死代码 | tools.py 追加组件前调用（坏形状拒绝落组件+warning）；__all__ 登记 |
| F5 | P3 | 前端 defaultPosition 无效 prop | 已在 tsc 修（f54f6cb99）先行清偿（评审基线为修前代码） |
| F6 | P3 | 分支抹掉共享 goal-loop 总账 619 行历史 | 恢复 master 原文 + 末尾追加本目标节 |
| F7 | P3 | polygon+密度语义时候选面退化为单一 area_stat 族 | 率注入只取一对（审定序首个 native 率模型 + 降级目标）；族多样性回归锁 |
| F8 | P3 | DenominatorEvidence.source 词表漂移 | 注释同步 overlay |

### R2 专项扫描确认（无新问题）
并发/幂等（select 锁内写/锁外读回 fail-honest；overlay descriptor miss 优雅跳过；外层 best_effort 有测试）；载荷上界（digest 最坏 571 chars ≪ 2500；折半循环 JSON 恒合法）；前端防御（畸形 digest 跳过/空态卡/前缀匹配安全）；G05 details 合并双 isinstance 守卫且必要；契约 v3 W8 无冲突；requirement_ir 幂等早退安全（op_id 去重 ⇒ doc 与持久化态逐字节一致 ⇒ record_ambiguities 重放 no-op）。

### R2 后全量验证
后端 5701 passed（cartography + design + gis_harness + clarification）+ G05 45 passed + 前端 134 passed + tsc 零错 + ruff 全绿。两轮负例翻转验证均真实翻红（R1×4 + R2×2）。
