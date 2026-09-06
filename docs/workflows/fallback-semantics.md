# 回退语义（Fallback Semantics）

回退有三层，全部产出结构化证据（`FallbackDecision`），任何一层都**不得
静默**：机器可读 reason_code、from/to、语义降级分类、用户可见披露。

## 三层回退

| 层 | 触发者 | 声明处 | 例子 |
| --- | --- | --- | --- |
| 制图元素级（V1） | `check_eligibility`（几何/点数/字段） | `recipe.fallbacks: [RecipeFallback]` | 点数 <10 → heatmap 禁用 → point_distribution |
| 算法级 | `AlgorithmResolver`（前置条件/工具视图） | `algorithm.fallback_algorithms` + `fallback_semantics` | kriging → idw（approximation） |
| 工作流语义级（V2） | `evaluate_workflow_obligations` + `resolve_data_roles` | `workflow.fallback_policies: [WorkflowFallbackPolicy]` | 显著性检验不可用 → 仅描述密度（degraded + 披露） |

## 语义降级分类（DOWNGRADE_CLASSES）

与算法层 `FallbackSemanticsClass` 同词表（单一事实源）：

| class | 含义 | 例子 |
| --- | --- | --- |
| `equivalent` | 语义等价替换 | — |
| `approximation` | 近似（量级/精度损失） | KDE → 视觉热力 |
| `proxy` | 代理（语义改变） | 路网可达 → 欧氏直线 |
| `degraded` | 降级（结论强度下降） | 显著性热点 → 描述密度；两期差值 ≠ 趋势 |
| `not_allowed` | 禁止（必须阻断） | SLC 栈不足 → 禁止伪形变图（blocks_completion=True） |

## 触发与记录流（finalize 阶段）

```text
profile 到手
  → resolve_data_roles        # 角色 degraded → 候选回退
  → evaluate_workflow_obligations
       precondition INSUFFICIENT_DATA / INVALID_METHOD
         + on_violation=degrade_with_disclosure → 候选回退
         + on_violation=block_method          → method_blockers
  → 按 reason_code 匹配 workflow.fallback_policies
  → FallbackDecision(from, to, reason_code,
                     downgrade_class, disclosure)   # 追加进 plan.fallbacks
  → 同码方法论警告并入 plan.methodology_warnings（去重、幂等）
```

正交付证据被 profile **推翻**时的对称行为：规划期披露（如「分母未确认」）
在 finalize 的 profile 证明字段在场后确定性移除（同码、义务 satisfied →
过时披露刷新）—— 披露诚实双向。

## 反声明锚（anti-claim）

以下语义分界由语料库锁定（`tests/unit/gis_harness/test_conformance_corpus.py`
+ `app/evaluation/anti_claim.py`）：

```text
没有分母            → 不得声称 per-capita / equity     (EQUITY_MISSING_DENOMINATOR)
经纬度角度坐标       → 不得直接做米制距离并声称准确      (KRIGING_PROJECTED_CRS_REQUIRED 等)
样本太少            → 不得声称可靠 KDE / hotspot / kriging (KRIGING_INSUFFICIENT_SAMPLES)
只有两个时间点       → 不得称长期趋势                   (TREND_INSUFFICIENT_OBSERVATIONS)
无 SAR 定标证据      → 不得声称绝对 backscatter 定量比较 (SAR_CALIBRATION_EVIDENCE_REQUIRED)
视觉热力            → 不得自动当统计热点                (HOTSPOT_SCREENING_NOT_SIGNIFICANCE)
Euclidean 代理       → 不得自动当 network accessibility (NETWORK_DISCONNECTED proxy)
raw count           → 不得称 density/rate              (DENSITY_MISSING_AREA_DENOMINATOR)
hazard only         → 不得称 risk                      (RISK_RECEPTORS_UNCONFIRMED)
模型 fallback        → 不得隐去 semantic downgrade      (FallbackDecision.disclosure)
```
