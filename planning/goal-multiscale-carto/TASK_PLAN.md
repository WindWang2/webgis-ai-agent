# GOAL 04 — 多尺度地理综合、比例尺表达与专业图件一致性

- 分支：`zcode/goal-multiscale-cartography-v1`（worktree `../webgis-wt-goal-multiscale-carto`）
- master 起点：`6c5e71c2cb04afe31b40bf66ca96e6dd6480b4e7`（2026-10-31，PR #1875 合并）
- 规划目录：`planning/goal-multiscale-carto/`
- 所有权：`app/lib/cartography/multiscale/`（新）、`frontend/lib/map-kit/multiscale/`（新）、
  受控 MapSpec extension（additive v1.8）、专项测试；
  `app/services/mvt.py`、`frontend/lib/map-kit/symbol-law.ts`、`label-geometry.ts`、
  `mapspec_to_svg.py` 仅经证据确认的最小消费适配，严禁整体改写。

## 核心定位（对既有能力的边界）

master 已有（**不重做**）：
- v1.6 语义缩放表达协议：`app/lib/cartography/representation.py`（窗口计划、
  user_pin、导出裁决）+ `frontend/lib/carto-ir/representation.ts` + compiler
  minzoom/maxzoom 门。模式词表含 `generalized`/`aggregate_grid` —— 但为
  **artifact-backed capability seam，工件生产方不存在**。
- `symbol-law.ts`（zoom/密度 → paint 表达式 + evidence ring）。
- `mvt.py` 按瓦片 zoom 的 shapely simplify（半像素、preserve_topology、z≥14 跳过）。
- #1870 design candidates / decision trace；#1875 标签 typography/export parity；
  #1874 map_operation.v1 受控事务；#1871 atlas。

本方向唯一增量（**new**）：
1. `ScaleRepresentationProfile.v1` 纯数据契约（M1）：尺度区间/分辨率/source
   revision/geometry significance/feature hierarchy/decluttering/simplify
   tolerance+units/topology risk/suppress reason/mandatory features/user
   overrides/semantic checks。
2. 语义重要性 RuleEngine（M2）：读真实 schema 字段（道路等级/行政级别），
   缺字段宁不综合不伪造；确定性、可解释、用户 override 赢。
3. 非破坏性几何综合（M3）：派生视图（render copy），拓扑安全（自交/断裂/
   洞丢失/共享边界），高风险 safe_no_generalization；产 `generalized`
   artifact 供 v1.6 协议消费。
4. 显示规则与统计口径披露（M4）：displayed vs original counts、图例"被综合
   要素"披露、degrade_reason。
5. 版式/图例尺度一致性（M5）：与 legend_spec/components 受控联动，分类
   break 跨视图固定。
6. 实时与导出同合同（M6）：scale representation contract 进导出 metadata，
  PNG/SVG/PDF 复用。
7. Agent 工具闭环（M7）：inspect_map_scale_profile / preview_scale_representation
   / apply_scale_representation 经 ToolRegistry；provenance 经 mutation 事务。
8. 数学/性能/视觉回归（M8）：50k/200k synthetic + 真实小样本拓扑测试。
9. 闭环制图与审计（M9）：三场景成品 + QA + 两轮独立 review。

## 里程碑与验收 Oracle

见任务书 §3。当前状态：M0 进行中（BASELINE.md / PR_OVERLAP_MATRIX.md）。

## 环境约束

- 并发 -j2（有其他 Agent 时 -j1）；最多 3 个 ZCode subagents。
- 本地验证：pytest 聚焦路径 / ruff / pnpm typecheck / vitest 单文件。
- 不等线上 CI；不自行合并 PR。
