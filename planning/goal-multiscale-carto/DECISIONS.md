# DECISIONS — GOAL 04 架构决策记录

D1 — **底座选择：扩展 v1.6 representation 协议，不建第二表达协议**。
`generalized`/`aggregate_grid` 已是协议词表的 artifact-backed 成员，只是无生产链。
本任务补：工件生产（multiscale 包）+ MapSpec additive v1.8（`window.source` 源覆盖 +
`layer.scale_profile` 声明）+ 薄消费适配（compiler/runtime/mapspec_to_svg）。
不新建第二窗口协议、不改 `representation_at_scale` 既有签名与行为。

D2 — **非破坏性 = 新 ref + 窗口源覆盖**。派生综合工件存为新 session ref
（`gen:` 前缀），MapSpec 增**新 source 条目**（id = `<layer>__gen@<z0>-<z1>`），
layer 主 source 不变；渲染副本（窗口子层）经 `window.source` 指向新源；
分析通道继续读原 ref ⇒ 统计口径不变。原 ref 的 content_revision/fingerprint
记录进 profile 作「综合时点」证据。

D3 — **绕开 filter 缺陷族（#1809/#1810/#1811）**。要素语义取舍（如只保留主干道路）
一律在**工件生成期物化**（预计算 geojson 已含选择结果），不改运行时 layer.filter。
窗口切换 = MapLibre 原生 minzoom/maxzoom 门（既有机制）。

D4 — **语义重要性以字段证据为前提（fail-honest）**。RuleEngine 输入 =
ref descriptor field_schema（类型）+ feature_aggregate 值分布（真实基数）+
词表（道路等级/行政级别中英词表）。任一证据缺失 → importance=None →
**不做要素取舍**（保留原始几何或聚合表达），披露 `no_trusted_importance_field`。
绝不伪造「主干道路」。

D5 — **拓扑安全分级**。综合引擎按输入风险选策略：
- 默认 `bounded_simplify`：shapely simplify(preserve_topology=True) + 每要素
  有效性复核（is_valid + ring/hole 数不变 + 面积相对误差界）——失败回退该要素原几何；
- `shared_boundary_protect`：相邻面共享边先提取锚点（shared arcs 顶点快照），
  简化后按容差回吸（snap）锚点，防行政边界裂缝；
- `safe_no_generalization`：输入含 multipart 行政边界/超高风险（洞>阈值、
  自交历史、用户声明不可动）→ 只做要素级取舍不改几何；
- 面积/共享边界误差阈值进 profile（topology risk 块），测试锁不变量。

D6 — **显示尺度 vs 计算尺度显式分口径（M1 硬要求）**。profile 每带同时携带
`display_scale`（MapLibre 512px worldSize 基准 1:N，供人读/图例）与
`computation_basis`（256px 瓦片基准 m/px，与 scale_rules.meters_per_pixel 同源，
供容差计算），并带 `basis` 标注。分析结果（分类 breaks/统计）**只在
original_source 上计算**，与像素/渲染分辨率解耦——不因屏幕像素改变分析。

D7 — **统计口径披露 = 图例/导出共守**。profile 每带记
`original_feature_count`/`displayed_feature_count`/`suppressed_count`/`degrade_reasons`；
图例组件与导出 metadata 消费同一声明（M5/M6），禁把 displayed 当 original。
分类 breaks 跨带固定（classification_basis=original_source + breaks 指纹），
数据资格变化 → 必须带 `classification_changed` 披露。

D8 — **user-wins 三层**：(a) 用户 pin 表达（既有 user_pin 通道）整程生效；
(b) 用户 mandatory features（显式点名要素）永不被综合删除；(c) 用户手动
paint/visibility 不被 apply 覆盖（复用 map_operation 的 user-wins 守卫语义）。

D9 — **apply = 事务 + provenance + receipt**。照 #1874 模式：preview 零突变；
commit = 工件生成 → session store（新 ref）→ UpsertSource + UpsertLayer(patch
representation+scale_profile) 经 apply_gis_mutation（CAS）→ provenance
kind=`scale_representation` → receipt 环存 map_state。幂等键 operation_id。

D10 — **测试与性能**。合成数据（学校点 50k/200k、带等级道路网、行政面）+
真实小型边界 fixture（仓库 examples/ 或 tests/fixtures 既有样本，缺失则用
确定性合成「真实形状」样本并如实披露）。性能对照同机、可复现脚本、
保存基线值；不用凭空数字。

D11 — **前端 multiscale 目录**：`frontend/lib/map-kit/multiscale/` 收口
窗口源覆盖的纯函数 + scale 披露文案模型（live 与导出共享）；compiler/runtime
只做最小 import 消费。

D12 — MapSpec 版本 → v1.9 纯 additive（v1.8 由 GOAL 03/#1877 时态层占用 —— 协调登记；`MapSpecRepresentationWindow.source:
Optional[str]` + `MapSpecLayer.scale_profile: Optional[Dict]` 开放声明）；
迁移注册表 additive；旧 spec 行为零变化（契约测试锁定）。
13 — **版本号协调（v1.8 撞号规避）**：open PR #1877（GOAL 03，先开）已占用 1.8=时态层；本 PR 登记 1.8 为协调占位（语义归 #1877）+ 1.9=多尺度档案，任意合并顺序不产生语义撞号。
