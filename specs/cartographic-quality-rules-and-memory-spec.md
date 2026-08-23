# Spec: 制图质量确定性规则与项目级制图记忆 (Cartographic Deterministic Quality Rules & Project-Scoped Cartographic Memory)

将"具身 GIS"讨论收敛为可落地的三根支柱:**扩展 L4 确定性制图规则**、**项目级制图记忆**、
**制图环境事件**。全部复用现有栈(FastAPI / Postgres / Redis / Celery / 空间库),
不需要渲染管线、视觉模型或外部传感器。

- 基座:[docs/cartographic-closed-loop.md](../docs/cartographic-closed-loop.md) 的 L1–L5 证据阶梯、
  `evidence_class: deterministic | heuristic | visual`、typed `suggested_fix` 证据模型。
- 遵循:ADR-0001(Fetch-on-Demand / `ref_id`)、ADR-0060 / #659(validity 阶梯以 SEMANTIC_VALID
  为生产上限)、#644(诚实评估)、#788(有界 `[CARTOGRAPHY_VERDICT]` 注入)。
- 术语:`MapSpec`、`Spatial Meta Profile`、`SessionStore`、`CartographicQuality` 见
  [CONTEXT.md](../CONTEXT.md) 词汇表。
- 定位:eval 套件的 visual-judge 切片明确 **deferred**(待 `webgis-visual-judge`);本 spec 的
  确定性规则是视觉评审缺位期间的**可计算图面质量桥梁**,不是视觉评审的替代品——
  `visual` 类证据继续诚实保持 `not_evaluated`。

## 问题陈述 (Problem Statement)

1. **L4 规则集目前以结构性检查为主**(legend 存在、层级挂载、期望-实际一致),缺少
   制图学意义上的**量化质量规则**(负载量、色彩可分性、比例尺适宜性)。修复循环收到的
   失败原因笼统,无法映射到具体制图修复动作。
2. **制图记忆是 session 级失忆的**:verdict、样式偏好、分类断点不跨 session 持久。
   同一项目连续出图,每张图从默认值重新猜,违背"系列图分类一致"的制图原理。
3. **环境(数据)变化对 agent 不可见**:上传/数据刷新后,基于旧分布验证过的分类断点
   静默过期,下一 turn 无从得知"世界已经变了"。

三者合起来,就是制图语境下"感知—行动闭环"缺口中**不需要视觉资源**即可补齐的部分。

## 方案总览 (Solution)

```text
现有闭环(不变):
  dispatch → MapSpec 作者化 → lifecycle 期望态评审 → AUTO_SAFE 修复 → commit
  → 运行时观测 + ACK → harness 实际态复评 → CartographicQuality gate

本 spec 的三个挂点:
  [P1] quality_loop 新增 6 条确定性规则(量化图面质量) ──失败带 typed suggested_fix──→ runtime_repair
  [P2] gate 之后/用户修正时,将验证过的事实写入项目级事实账本(Postgres)
       ──下一 turn 经 context_assembler 项目级有界注入(与 #788 同款式)──→ 新 MapSpec 作者化
  [P3] 上传/数据刷新触发分布指纹漂移检测 ──→ 相关事实置 stale + [ENV_CHANGE] 有界注入
```

设计原则(全部继承自现有体系):fail-closed(数据缺失 → `not_evaluated`,绝不放行也绝不满分)、
有界注入(所有跨 turn 注入都有预算上限)、O(1) 姿态(规则只消费 dispatch 期已算好的
descriptor / Spatial Meta Profile,不在评审热路径上遍历要素)。

## 用户故事 (User Stories)

1. 作为制图质量门,我希望对每个候选 MapSpec 计算图面负载量,以便在要素密度超档时
   给出"抽稀/概括"的 typed 修复建议,而不是笼统的 invalid。
2. 作为制图质量门,我希望校验色带相邻类的感知色差(CIEDE2000)与主题层-底图明度序,
   以便分类在感知上真的可分、图面层级成立。
3. 作为制图质量门,我希望校验图例项与相机视口内实际可见层的一致性,缺项即 fail。
4. 作为系列图场景,我希望同一项目的多张图复用**项目级共享分类方案**(断点+色带),
   以保证跨图可比(时序专题图的核心规范)。
5. 作为反复修正样式的用户,我希望我的修正(色带方向、类数、符号基尺寸)被记为项目偏好,
   下次出图直接从已确认口味出发。
6. 作为居住在数据世界的 agent,我希望数据分布变化时收到显式的 `[ENV_CHANGE]` 提示,
   而不是拿着过期断点继续宣称"已验证"。
7. 作为 recipe 库维护者,我希望记录每个 recipe 应用后达到的 validity tier 与数据画像,
   让 recipe 推荐基于**自己的验证历史**而非静态优先级。

## 实施决策 (Implementation Decisions)

### P1 — L4 量化确定性规则(规则注册表扩展)

规则统一形态:`{rule_id, status, evidence_class: "deterministic", evidence(有界结构化),
severity, repairability, suggested_fix: {type, params}}`,挂入
`app/lib/cartography/quality_loop.py` 的评审管线;阈值全部进 settings(默认值 + 可配置),
校准先保守(宁可 `warning` 不 `fail`),上线一个 milestone 后再收紧。

| rule_id | 计算 | 输入 | suggested_fix |
|---|---|---|---|
| `carto.load.ratio` | 视口内要素数×平均符号面积/视口面积,按比例尺分档 | Meta Profile 要素计数 + camera + 符号基尺寸 | `thin_features` / `generalize` / `resize_symbols` |
| `carto.color.separability` | 色带相邻类 CIEDE2000;主题层与底图明度对比 | `palettes.py` 色带定义 + basemap 目录已知色 | `change_palette` / `increase_class_gap` |
| `carto.legend.completeness` | 图例项 ↔ 相机下可见层双向核对 | MapSpec `legend_spec` + camera + 图层可见性 | `add_legend_item` / `remove_stale_item` |
| `carto.visualvar.overload` | 同层占用 Bertin 视觉变量通道计数 | MapSpec 样式字段 | `split_layer` / `reduce_channels` |
| `carto.label.collision_est` | 空间索引标签盒采样估计(有界采样,非全量) | 注记字段 + camera | `resize_labels` / `thin_labels` |
| `carto.scale.svs` | 目标比例尺下要素最小图上尺寸 vs 最小可视尺寸 | geometry 类型 + Meta Profile bbox | `generalize` / `switch_symbolization` |

- CIEDE2000 以纯函数落在 `app/lib/cartography/palettes.py`(无第三方依赖)。
- 所有规则是 `(MapSpec, Meta Profile, camera) → rule 结果` 的**纯函数**;数据缺字段 →
  该规则 `not_evaluated`,不许猜。
- 修复动作映射进 `app/lib/cartography/runtime_repair.py` 现有 AUTO_SAFE 白名单;
  超出白名单的(如 `switch_symbolization`)只作为建议上报,不自动执行。

### P2 — 项目级制图事实账本(carto memory)

- **存储**:Postgres 新表 `carto_project_facts`(alembic 迁移),字段:
  `(id, project_id, kind, subject, payload jsonb, fingerprint, validity_tier,
  evidence_digest, status, created_at, last_verified_at)`,
  `kind ∈ {preference, recipe_outcome, data_profile, shared_classification}`,
  `status ∈ {active, stale, conflicted, retired}`。
  每项目有界(默认上限 200 条,按 `last_verified_at` LRU 淘汰)——与 verdict 注入同样的
  "有界记忆"纪律。
- **写入点**:① CartographicQuality gate 通过后(deterministic 证据摘要自动落账);
  ② 用户手动修正样式(dispatch seam 可观测)→ `kind=preference`;
  ③ recipe 应用完成 → `kind=recipe_outcome`。
- **读取/注入**:`app/services/chat/context_assembler.py` 在现有 `[CARTOGRAPHY_VERDICT]`
  块旁增加**同款有界**的项目记忆块(偏好 + 共享分类方案 + 高置信 recipe),预算字符数
  与 verdict 块同量级;`status != active` 的事实绝不注入。
- **冲突语义(fail-closed)**:新 turn 产出的分类断点与账本中 `shared_classification`
  指纹不一致时,不静默覆盖——标记 `conflicted` 并在评审证据中显式记录分歧,由用户/下轮
  修复裁决。共享分类方案只允许**显式升级**(新方案验证通过且用户无异议)。
- 数据画像扩展现有 **Spatial Meta Profile**(已含 min/max/mean/histogram)增加分位断点
  与空值率,作为分类指纹的基础;不在本 spec 内另立画像格式。
- 实施前落 **ADR-0069**(项目级制图记忆与失效语义),防止后续重提为 session 级缓存。

### P3 — 制图环境事件(分布漂移失效)

- **指纹**:`shared_classification`/`data_profile` 事实携带分布指纹(分类字段分位数 +
  空值率的哈希)。上传/数据刷新(现有 upload / ingestion 路径)后,后台任务(Celery,复用
  现有 jobs 基建)重算指纹。
- **漂移判定**:分位数向量相对偏差超阈值(默认 15%,可配置)→ 相关事实 `status=stale`,
  `last_verified_at` 冻结。
- **注入**:下一 turn 的项目记忆块前缀有界 `[ENV_CHANGE]` 说明("路网层分布在 T 时刻已
  变化,原分类断点过期"),不带原始数据。
- **时序制图复验**:`app/services/temporal/` 新时段数据到达 → 对项目共享分类方案执行
  复验(各时间片断点可比性是时序专题图核心规范);不可比 → 标记并提示重建方案。
- 环境事件是"世界独立于 agent 变化"的最廉价实现:零外部数据源,只用系统自身已有的
  数据变更流。

## 分阶段实施计划

| 阶段 | 内容 | 规模 | 依赖 | 验收标准 |
|---|---|---|---|---|
| **Phase 1** ✅ 已实施 | 三条高杠杆规则(`load.ratio`/`color.separability`/`legend.completeness`)+ typed fix 映射 + 修复动作 | M | 无 | ① 规则单测(golden fixture:超载图/低色差图/缺图例图各自 fail 且 fix 类型正确);② 修复循环端到端:注入失败原因后下一候选通过;③ 现有 L4 评审无回归 |
| **Phase 2** ✅ 已实施 | 事实账本表 + alembic 迁移 + 写入点(gate 收割) + 项目级有界注入(legacy + Pi) + 冲突语义 + ADR-0069 | M | Phase 1(落账需要 deterministic 证据摘要) | ① 同项目第二张图复用共享分类(断点一致);② 指纹冲突时显式 `conflicted` 而非覆盖;③ 注入有界(字符预算测试);④ 无 project 上下文时零行为变化 |
| **Phase 3** ✅ 已实施 | 分布指纹漂移检测 + stale 失效 + `[ENV_CHANGE]` 注入 + 时序复验（与漂移同一判定） | S/M | Phase 2 | ① 上传新分布数据后相关事实转 stale 且下 turn 可见提示;② 分布未变时零误报(指纹稳定性测试);③ 时序新时段触发复验 |
| **Phase 4** ✅ 已实施 | 其余三条规则(视觉变量过载/注记碰撞估计/SVS 比例尺适宜性) | M | Phase 1 | 同 Phase 1 模式 |

各阶段独立可回滚(规则可按 rule_id 灰度开关;账本注入可整体关闭降级为现状)。

## 测试决策 (Testing Decisions)

- **测外不测内**:规则测"给定 MapSpec+画像+camera 的判定与 fix 类型",不测内部实现;
  注入测"下一 turn 收到的有界块内容",不测账本内部结构。
- **诚实性属性测试**:任一规则输入缺字段 → 恒 `not_evaluated`(fail-closed 不变式);
  空证据 ≠ 通过(沿用现有"positive checks 必须在规则调用点记录"原则)。
- **指纹稳定性**:同分布重算指纹不变;构造漂移 fixture(分位偏移>15%)必判漂移。
- **预算测试**:项目记忆块 + `[ENV_CHANGE]` 合计字符数不超过 verdict 块同级预算。
- 端到端回归沿用现有 cartography 套件(`tests/cartography/`)+ harness 契约测试。

## 非目标 (Non-goals)

- 不做渲染图像评审/视觉模型(visual 证据类维持 `not_evaluated` 直到 visual-judge 就绪);
- 不做物理传感器、实时外部数据流接入;
- 不做跨项目全局记忆(记忆严格 project 域,避免跨租户泄漏——沿用多租户隔离纪律);
- 不引入第二套 MapSpec/评审格式(全部扩展现有 lifecycle + quality_loop 证据模型)。

## 开放问题 (Open Questions) — 1/2 已落地

1. ✅ **阈值校准（已落地）**：全部规则阈值经 `CARTO_*` settings 键可运维调参
   （import 期一次解析，规则保持纯函数）；
   `scripts/calibrate_cartography_thresholds.py` 从实测评审证据
   （`checks[].evidence` 的 load_ratio/ΔE00/注记占比等）聚合分布并按分位数给
   warn/fail 建议值——low_bad 指标方向语义在脚本内固化（fail 阈 < warn 阈）。
   输出仅为建议（注释形态 .env 行），阈值变更影响 gate 行为，必须人工确认。
2. ✅ **记忆治理入口（已落地）**：`GET/DELETE /api/v1/projects/{id}/carto-memory`
   与 `POST …/{fact_id}/activate` 提供查看/撤销（retired 软删）/显式激活
   （conflicted/stale 的人工裁决，ADR-0069 决策 3 的入口）；前端项目面板内嵌
   「制图记忆」折叠面板（状态徽标 + 撤销/激活，登录门控与 #528 一致）。
   没有任何"凭记忆改评审"的入口——那在 ADR-0069 决策 2 下被禁止。
3. recipe_outcome 与现有 recipe 库推荐逻辑的合流点
   (gis-harness-cartography-recipes-spec) 在后续 recipe 推荐改造时对齐，
   避免双轨推荐。
