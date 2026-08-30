# ADR-0083: Cost-aware Algorithm Resolution

- Status: accepted
- Date: 2026-08-30
- Extends: ADR-0079（harness runtime v2 §8 resolver 规则）、ADR-0080

## Context

`AlgorithmResolver`（app/lib/gis/algorithm_resolver.py）已实现确定性
capability → algorithm → tool 裁决：native 状态、工具注册、几何/字段/
最小样本量兼容，然后**按注册序取第一个合格候选**。

`AlgorithmDescriptor` 其实早已声明了成本字段 —— `cpu_cost` /
`memory_cost` / `io_cost`（CostLevel）、`complexity`、`approximate`、
`max_features_hint`、`preferred_execution_policy`（transport：INLINE/
THREAD/ASYNC/CELERY）—— 但 resolver 从未消费它们；`max_features_hint`
在全部种子数据里为零使用。

## Problem

同一能力在不同数据规模/语境下需要不同算法通道，现状无法表达：

- 15 万个点的「分布」请求仍解析到 `density.visual.heatmap`（原生渲染
  通道）—— 而前端的 `FETCH_FEATURE_CAP = 20_000` 会拒绝挂载该 ref，
  图层永远不上图（spec-visible-but-not-rendered 路径之一，见
  docs/dev/map-product-completion-runtime-audit.md §6）；
- 多候选合格时"注册序第一"没有可解释的裁决证据；
- 交互轮次与导出语境的成本偏好（时延 vs 精度 vs 内存）无处表达。

## Decision

升级为四级流水线（全部确定性、零 I/O、零 LLM）：

```
Capability → 候选算法 → 约束过滤（新增 max_features_hint 渲染上限门）
           → 成本模型（ExecutionPolicy 加权）→ 稳定排序 → 选定
```

1. **成本模型独立成模块**（`app/lib/gis/cost_model.py`，纯函数）：
   - 阈值全部有代码库出处，不拍脑袋：`HEATMAP_MIN_POINTS=10`
     （config 默认 / heatmap_data 硬门槛）、`INTERACTIVE_FEATURE_CAP=5k`
     （PiToolResponse.details ~1MiB/回调边界）、`FETCH_FEATURE_CAP=20k`
     （前端挂载硬上限 = 原生渲染通道上限）、`DATA_FABRIC_MAX_FEATURES=50k`
     （数据通道保护上限）；
   - **ExecutionPolicy**：`interactive_fast / balanced / analysis_quality /
     export_quality / large_data`，自动推断（hint > 导出 > 规模 > 定量
     输出 > 小数据 > balanced），用户与 LLM 都不选；
   - 成本分 = Σ(级别×策略权重) + 近似惩罚 − 服务端卸载加成；级别
     low/medium/high → 1/3/9；breakdown 进 resolution evidence。
2. **渲染上限门**：候选声明 `max_features_hint` 且画像 featureCount 超限
   → 拒绝（`over_render_cap`），触发算法级 / 能力级 fallback。
   `density.visual.heatmap` 声明 `max_features_hint=20_000`
   （FETCH_FEATURE_CAP 出处）。
3. **大规模确定性降级**：`density_surface` 能力新增
   `fallback_capabilities=["grid_binning"]`（聚合通道承接超限点数据）。
   与 grid_binning 既有的反向 fallback（稀疏点 → 视觉热力）构成双向边，
   环路由 resolver 既有 `_visited` 守卫截断。
4. **选择序 = (priority, 成本分, id)**：registry 声明的 priority 仍是
   主偏好序（既有默认解析的前缀兼容承诺不变，如 service_area →
   isochrone_analysis）；成本模型在同 priority 竞争者间裁决并提供
   policy/scale evidence。大规模切换由硬门 + fallback 承担，不靠软成本。
5. **Resolution additive 字段**：`execution_policy` / `cost_score` /
   `cost_breakdown`（单候选直通时留空 —— 无竞争不表演）。

## Alternatives

- **成本分为主序（score, priority）**：拒绝 —— 推翻 phase2 锁定的默认
  解析前缀兼容承诺（service_area 默认 isochrone_analysis），下游稳定性
  代价大于收益。
- **ML cost predictor**：拒绝 —— 无训练数据、不可解释、不确定时延；
  规则式 estimator 的每项都可审计。
- **把 policy 塞进既有 `preferred_execution_policy` 字段**：拒绝 —— 该
  字段语义是执行 transport（INLINE/THREAD/ASYNC/CELERY），与策略
  （interactive_fast…）是两个正交概念，混用会破坏工具调度。

## Trade-offs

- 同 priority 平局才走成本裁决 —— 成本模型对默认解析的影响收敛到
  零（这是特性：升级不改变既有行为，只补规模硬门与证据）。
- 阈值是常量而非配置 —— 若前端 FETCH_FEATURE_CAP 变化需两处同步；
  以注释互相引用，后续可提炼为共享配置（见 Future work）。
- 策略推断不读运行时负载（CPU/内存水位）—— 刻意：resolver 保持纯函数、
  可测试；运行时负载属调度器职责。

## Compatibility

- 单候选能力：解析结果与 evidence 完全不变（直通路径）。
- 多候选能力：同 priority 内可能翻转（仅平局竞争者），reject 理由与
  cost evidence 完整披露；`AlgorithmResolution` 字段纯增量。
- 注册表 schema 零变化（只补种子声明值与一条 fallback 边）。

## Performance

- 打分 O(1)/候选（纯算术）；resolve 总开销与升级前同阶（候选集有界）。

## Failure semantics

- 画像 featureCount 未知 → 上限门不生效（未知 ≠ 超限），策略 balanced。
- 非法 policy_hint → 忽略并走推断。
- fallback 环 → `_visited` 截断，返回 unavailable（既有语义）。

## Migration

无迁移：默认解析行为不变；大规模场景从「静默不可渲染」变为
「over_render_cap 拒绝 + grid_binning 降级建议」。

## Future work

- FETCH_FEATURE_CAP 提炼为前后端共享契约（registry manifest）；
- planner 把 turn 语境（导出意图）作为 policy_hint 传入 resolve；
- `max_features_hint` 补齐其余原生渲染族（大 GeoJSON 线/面图层）。
