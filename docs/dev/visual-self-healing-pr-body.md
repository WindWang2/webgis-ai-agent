# PR: feat(mapspec): 视觉驱动的 MapSpec 自愈微变异引擎（ADR-0186）

## Summary

把 Direction 01 的结构化 `VisualJudgeReport` 缺陷清单编译成**事务性 MapSpec 微变异**，
补齐自适应制图闭环"视觉裁判 → 图面自愈"的最后一公里：

- **归一化桥**（`normalize_visual_report`）：维度级 critique（无 layer_id）→ 可定位
  `VisualCritiqueItem`；维度+关键词映射矩阵 + 证据文本图层定位；映射不出/定位不到
  一律诚实丢弃/跳过并披露，绝不臆测整图。
- **确定性规划器**（`VisualHealStrategyPlanner`）：severity（error>warning>info）×
  影响域（layer_order>contrast>label>opacity）× 缺陷指纹字典序稳定排序；同图层
  属性面冲突消解（一单一缺陷）。
- **四个原子操作**（微变异词汇表）：
  `MUTATION_HEAL_LABEL_COLLISION`（text-allow-overlap 强制 + padding 翻倍 +
  ignore-placement，二轮步进 text-size×0.85 floor 8）、
  `MUTATION_HEAL_CONTRAST`（画布感知 WCAG 门限选色 + min-ΔE 严格更优才动，
  镜像 `_apply_palette_change` 长度匹配语义，分类断点/键不动）、
  `MUTATION_HEAL_LAYER_ORDER`（抬升到上方最高遮挡型层正上方，background 恒居首）、
  `MUTATION_HEAL_OPACITY`（+0.3 抬靶层 / ×0.6 压垫底者）。
- **事务挂载**（`lifecycle_engine.py`）：`ApplyVisualHealPatchIntent` 在锁内用权威
  spec 重规划（TOCTOU 安全）后 COW 应用，白嫖既有 CAS / mutation_id 幂等 /
  checkpoint / blocking 校验 / revision 单调 / 失败回滚全套；锁面把全部靶图层 +
  occluder 纳入 `intent_lock_targets`（用户锁定图层整笔 `layer_locked` 拒绝）。
- **收敛防护（≤2 次迭代，防震荡死循环）**：同一缺陷指纹至多 2 次提交自愈；
  quality_score 连续 2 次不提升提前熔断；同一补丁签名拒绝重放。第三次请求触发
  `SelfHealConvergenceExhausted`（reason ∈ max_iterations / no_improvement /
  repeated_patch），支持 raise 或 `on_exhausted="degrade"` 优雅降级
  （`HEAL_CONVERGENCE_EXHAUSTED`，spec/revision 纹丝不动）；空计划诚实拒绝
  （`HEAL_PLAN_EMPTY`，不提交不推 revision）。

## Files

- `app/services/mapspec/visual_healer.py`（新增，纯函数层，无 service 依赖）
- `app/services/mapspec/lifecycle_engine.py`（8 处注册面 + 分发分支 + `apply_visual_heal_patch`）
- `app/services/mapspec/__init__.py`（导出）
- `tests/unit/test_visual_self_healing.py`（25 用例）
- `docs/adr/0186-visual-self-healing-mapspec.md`、`docs/dev/visual-self-healing-rules.md`
- `review/AGENT-02-REVIEW.md`、CHANGELOG 条目

## Gates

- `pytest tests/unit/test_visual_self_healing.py`：25/25（×2 稳定，hermetic）
- `ruff check`：0 告警
- 邻域回归 96/97（唯一失败为干净基线预存）；全量 `tests/unit` 11705 通过、
  27 失败经基线 stash 复核**完全一致**（extensions 沙箱 / pmtiles 真实文件 /
  Windows symlink / 真实 socket / 双进程，环境型预存）。

## Honest boundaries（ADR-0186 D7）

收敛账本为进程内状态（多 pod 独立计数）；入口预检读无锁快照（锁内权威重规划兜底）；
图层定位依赖证据文本提及 id（Direction 01 结构化 layer_ids 预留直通）。
