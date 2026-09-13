# qc-loop Round 4（2026-09-13）

起点 tag `qc-loop-r3`

## P · Prioritize（top-3）

| id | 位置 | 类别 | 分数 |
|---|---|---|---|
| R2-12 | completion/pipeline.py:415 | correctness P1 | 16.7 |
| R2-3 | recipes.py:233 | correctness P1 | 16.1 |
| R2-10 | completion/pipeline.py:121 | correctness P1 | 16.1 |

三条同属"终验/资格链路对真实数据形状失明"主题（dict verdict、snake_case 键、全量 findings）。

## F · Fix（每条独立 commit）

1. `30f873eb` fix(qc-loop): recognize dict-shaped verdict in final_gate dedup so READY sessions skip (round 4, correctness)
   - `_dedup_gate_blocks` 裸 `str(dict)` 比对 READY 词表恒 False → READY 会话每轮全量重跑终验（gather/validators/locked plan save），文档承诺的"happy path 零开销"失效。改用同文件 `_product_verdict_token`（其 docstring 即为此坑而写）。+回归测试 3 条（dict READY / dict 非 READY / 字符串兼容）。
2. `a77400a9` fix(qc-loop): read producer null_ratio key so eligibility gates stop failing open (round 4, correctness)
   - `EligibilityContext.from_profile` 只读 camelCase `uniqueRatio`/`missingRatio`，生产 profile 产出口（`dataset_profile._resolver_fields`）发 `type`+`null_ratio` → 字段基数/缺失率门恒 unknown 放行。新增 `_meta_ratio` 按序双键同读。+回归测试 3 条（producer 键 / camelCase 兼容 / 缺席仍 unknown）。
3. `66506193` fix(qc-loop): judge finalization status on full findings before disclosure truncation (round 4, correctness)
   - `_validate_all` 末尾截断抢在 F6 承诺的全量状态判定之前 → 第 13 条起的 error 被静默丢弃、可能误判 complete。改为返回全量（截断仅保留在 `result.findings` 披露层）。+回归测试（monkeypatch validators 造 13 条 error，断言全量返回）。

无公共 API 语义变更，免 ADR。

## V · Verify

- ruff 全部通过；新回归测试 7 条全绿。
- 既有测试：test_product_verdict + test_eligibility_v4 + test_recipe_downgrade_regression（92 条）+ finalize/completion/pipeline 选择集（99 条）全绿 —— `_validate_all` 全量化与幂等门修复无既有预期漂移。
- 门禁：SKIP_BROWSER=1（round-4.gate.log）。

## L · Log · 指标

| 指标 | round-3 末（open） | round-4 修后（open） |
|---|---|---|
| P0 | 0 | **0** |
| P1 | 10 | **7** |（勘误：初版绝对水平多报 3，Δ 正确）
| P2 | 142 | 142 |

Δ = (0−0)×3 + (13−10)×1 + (142−142)×0.3 = **3.0**。Δ ≥ 1.0，E2 未触发。棘轮：无回退。

## 遗留（Round 5 输入）

open P1 10 条，按分数头部：R2-8 components.py:1234 rebind 旧 inline chart（13.4）、Q037 render_scene.py:210 舍入 parity（14.4，需含 golden 完整门禁轮）、R2-7 trace_store.py:327 逐出顺序（11.0）、R2-4 tools.py:608 overlay_refs 消费缺失（约 13，需实现消费逻辑，成本高于均值）、R2-5 tools.py:806 converter 警告接回（约 12）；其余 P2 142 条按分数就绪。
