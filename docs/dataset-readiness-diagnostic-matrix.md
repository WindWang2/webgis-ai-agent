# 用户数据问题诊断矩阵（G01 · dataset readiness）

> 一页速查：用户任务因数据问题不能正确制图/分析时，系统给出什么结构化判据、
> 需要用户补什么。全部判据 metadata-only（不扫描数据）；unknown = 证据不足，
> 绝不等于「没问题」。

数据链路：**数据集 → 语义 descriptor（GISDatasetDescriptor，指纹身份）→
角色绑定（哪个数据集扮演哪个角色）→ 资格裁决（这个数据行不行）→
兼容预检（这些数据能不能联到一起）**。三层各有独立词表，不混用：

| 层 | 问题 | 状态/码 | Agent 可消费的修复路径 |
|---|---|---|---|
| 绑定 | 两个同名/同证据数据集，不知道用哪个 | `ambiguous`（`ROLE_BINDING_AMBIGUOUS`，候选清单 ≤4 带指纹） | 向用户展示候选让其一选；**绝不自动取首个** |
| 绑定 | 任务需要的数据不在会话 | `not_found`（`ROLE_BINDING_NOT_FOUND`） | typed 澄清：需要什么数据（见下方角色表） |
| 绑定 | 引用的数据属于其他会话/项目或已被淘汰 | `not_authorized`（`ROLE_BINDING_REF_OUT_OF_SCOPE`） | fail-closed 拒绝跨作用域解析；提示重新导入数据 |
| 绑定 | 绑定成立但缺角色关键事实（如 boundary 无几何证据） | `partial`（`ROLE_BINDING_FACTS_INCOMPLETE`） | 事实补齐后自动复评；不虚构事实 |
| 绑定 | 记录面指纹（当时用的语义版本）≠ 当前 | `stale`（`ROLE_BINDING_STALE` → 资格层 `DESCRIPTOR_STALE_*`） | 按新语义重评旧结论/制图；不静默续渲染 |
| 预检 | 数据集 CRS 不一致 | `PREFLIGHT_CRS_MISMATCH` | `reproject`：统一投影后联接 |
| 预检 | 数据集 CRS 未知 | `PREFLIGHT_CRS_UNKNOWN` | `clarify_crs`：提供 .prj/元数据 |
| 预检 | 空间范围不相交（bbox 无重叠） | `PREFLIGHT_SPATIAL_DISJOINT` | `provide_overlapping_data` 或 `clip_to_intersection` |
| 预检 | 双期数据时间不重叠 | `PREFLIGHT_TEMPORAL_DISJOINT` | `provide_matching_period`（如 2020 与 2025 各一期） |
| 预检 | 时间证据不足（open-ended/年粒度/单侧） | 判据 `unknown`（不判 compatible） | 补时间元数据；证据不足不虚构兼容 |
| 预检 | 同名字段单位冲突 | `PREFLIGHT_UNIT_CONFLICT` | `clarify_units`：统一单位或给换算 |
| 预检 | 数量字段当密度用、无分母证据 | `PREFLIGHT_DENSITY_NEEDS_DENOMINATOR` | `provide_denominator`（人口/面单元）**或** `switch_to_count`（改数量制图，不声称密度） |
| 预检 | 必需角色未绑定（如缺行政边界） | `PREFLIGHT_ROLE_MISSING` | `provide_role_data`：按角色表给所需数据 |
| 资格 | 必选角色无数据且策略 block | `blocked`（`DATA_ROLE_MISSING_*`） | 不得产出依赖该数据的结论/伪制图 |

## 角色所需数据速查（复用 DATA_ROLES 词表）

| 角色 | 需要什么 |
|---|---|
| subject | 分析主体（POI/要素点云等） |
| boundary | 行政/自然边界（面状分区，如区县边界） |
| denominator | 归一化分母（人口/户数；面单元可派生面积分母） |
| baseline / target_time / comparison_time | 基期/目标期/对比期数据（时间覆盖需重叠） |
| network / elevation / population / hazard / receptor / criteria / constraint / reference | 路网 / DEM / 人口 / 危险源 / 承灾体 / 准则 / 约束 / 参照 |

## 典型场景（成都示例，见 tests/unit/dataset_semantics/test_scenarios_chengdu_v1.py）

1. **成都小学的分布情况**：单数据 → 绑定 bound → 预检 compatible；
2. **成都各区小学数量**：schools + 区界面 → 各归其位（subject/boundary）；
3. **每平方公里学校密度**：只有学校 → `DENSITY_NEEDS_DENOMINATOR`（供分母或改数量两条 typed 路）；有行政面 → 面积分母可派生，密度成立；有人口 → 分母字段强证据；
4. **2020/2025 学校变化**：双期数据对 baseline 角色证据并列 → `ambiguous`（需显式指定各期）→ 指定后时间不重叠 → `TEMPORAL_DISJOINT`；
5. **缺行政边界**：`ROLE_MISSING` + 资格 `blocked`（需要面状边界数据）；
6. **两个同名数据集**：`ambiguous` 候选清单（带指纹），资格不拿任何一个顶替。

## 后台数据依赖拓扑（恢复链）

```
ingest / mapspec 绑定 ──铸──> 语义 store（descriptor 指纹，≤8 版本/数据集）
        │                              │
        └──记录面──> durable facts（source_fingerprints ≤16）
                                       │
freeze 快照（source 冻结 descriptor_fingerprint）
        │
resume ──> 载荷重水合 → 语义迁移（指针改写指纹不变）→ 记录面对账
          （evaluate_reuse: valid=可复用 / recompute=语义漂移需重评 /
            unknown=不可对账）→ ResumeReceipt.descriptor_reconciliation
```

migration/rollback：全部改动 additive（可选参数/新字段默认缺席 = 基线行为）；
回退 = revert 本 PR，无 schema 迁移。
