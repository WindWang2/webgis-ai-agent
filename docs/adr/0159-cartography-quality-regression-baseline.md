# ADR-0159: 制图质量回归基座——质量事实库、ratchet 门禁、像素级 golden 与自适应验收集

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-cartography/10-quality-baseline（10 条自适应制图线的度量基座，建议最先启动）
- 关联: ADR-0065（场景晋升）、ADR-0069（制图记忆）、ADR-0078（语义检查配对）、cartographic-closed-loop.md（两级评审语义）、migrations/.alloc.json（0056 领号）

## 1. 背景

本仓已有丰富的度量素材（204 条闭环语料、≥5000 场景质量语料、9 个头照 runtime 场景、15 项 perf
基线、6 条量化质量规则、44 个确定性检查 id——P0 实测，任务书所称"17 项"实为 6 条量化规则 +
38 个结构检查），但**没有尺子**：

1. 无质量趋势持久化：`calibrate_cartography_thresholds.py` 只打印建议、绝不写文件；
   `_cartographic_review` 只落会话 map_state（内存/可选 Redis），cartographic-closed-loop.md
   明言"no database or migration is introduced"——无法画跨版本质量曲线，无法设 ratchet 闸。
2. 头照场景无像素级 golden：9 个 runtime 场景全在 nightly（ADR-0065），探针只有取色点断言，
   无整幅像素 diff。
3. 覆盖率闸对制图无效：CI 后端 lane 显式 `-m "not cartography"`（production.yml:202），
   75% 覆盖不含制图代码。
4. "自适应"无验收：现有记忆/收敛测试只验"是否收敛"，不验"下一张是否比上一张更好"。

P0 侦察详见 `docs/dev/ac-10-baseline-recon.md` + `docs/dev/ac-10-metrics-inventory.csv`
（19 类度量资产）+ 首轮规则基线（`ac-10-rule-distribution.csv` 102 行 / `ac-10-rule-baseline-first.json`）。

## 2. 决策一：质量事实库（唯一迁移 0056）

- `cartography_quality_runs`（run 是领域对象：lane × scene × 版本 × 判定）+
  `cartography_quality_metrics`（逐检查项观测行：check_id × value × evidence_class × verdict）+
  `cartography_quality_baselines`（ratchet 基线，图型×检查项唯一）+
  `cartography_quality_waivers`（带到期日的豁免）。全部 additive 新表，downgrade 反序回滚。
- **落库点是产出面的下游账本，不是新的判定者**：`cartography_runtime.py` 的
  `_cartographic_review` 持久化点之后与 `harness_runner.py` 的 `HarnessEvaluator` 产出之后
  各加一个写入钩子（fire-and-forget、自吞异常、`CARTO_METRICS_STORE_ENABLED` 可整体停用）。
  判定逻辑一字未改（semantic_checks.py 零改动）。
- 保留策略默认 90 天 / 5000 run（可配），双方言 SQL（SQLite 与 PostGIS 均可跑）。
- 其他 9 条线（03/05/06/07/08）的质量指标按 `app/services/cartography_metrics_store.record_quality_run`
  同一契约入账即可——lane 词表为 `desired_state/runtime/eval/adaptive/golden`。

## 3. 决策二：ratchet 门禁（只许变好）

- 基线取**分位数**（默认 p66）而非均值，抗离群；方向词表 `high_bad`/`low_bad`
  （未知检查项兜底 high_bad 保守拦截）。
- 新观测劣于基线超容差（默认 ±5%，`CARTO_RATCHET_TOLERANCE_PCT`/CLI 可配）→ 拦截。
- **provisional 纪律（§0.5）**：首轮基线（本 PR 已从 P0 分布 32 条有限观测 × 13 场景入库，
  42 条 provisional）只记录不拦截；显式 `activate` 后才开始拦截。建议激活时机：nightly
  连续 ~10 次绿后（与 ADR-0065 晋升节奏一致），由维护者执行
  `quality_ratchet_gate.py baseline --activate`。
- waiver 带理由 + 到期日，写入库、报告高亮、到期自动失效（过期行保留审计）。

## 4. 决策三：像素级 golden 与场景晋升

- 容差策略沿用 runtime probe 经验：逐通道 ±16；同场景多取色点两两通道距离 >48；
  容差内像素占比 ≥98% 通过；尺寸不一致直接失败（绝不静默 resize）。
  纯函数落 `app/lib/cartography/golden_diff.py`（numpy 向量化）。
- 9 个场景的 golden 全部落库（`tests/fixtures/runtime/<s>/golden/`）。本机实测渲染是
  **确定性**的：generate 后 verify 全部 `within_ratio=1.0, max_channel_diff=0`。
- 晋升分档（ADR-0065）：7 个正常场景 `pr-blocking`（本地 3×绿 + 全部 <14s，10 绿历史由
  事实库随 nightly 累积）；2 个 fault-* 为负路径夹具（expect=fail）→ `nightly-only`；
  quarantine 机制有测试，当前零 flaky（27 次运行零失败）。
- 失败处置：pr-blocking 失败硬拦截；quarantine 必须附失败样本
  （`last_failure.json`），禁直接删除。

## 5. 决策四：cartography lane 独立覆盖率闸

- 后端 75% 不含制图代码（CI 显式排除）；本线把 `app/lib/cartography` 单独计量、单独设下限
  （默认 60，与后端 75% 分开计）。harness 属 09 线，单独报告不单独设闸。
- 首轮实测（origin/master 1fd4b035 + 本线新增）：cartography **50.10%** / harness 46.33%。
- 落地采用 §0.5 provisional 纪律：`quality_gate_local.sh` 以 `CARTO_COV_FLOOR=50` 起步
  （低于当日实测会天天红，反而失去信息量），**下限只升不降**，提升到 60 后收口。

## 6. 决策五：自适应验收集（其余 9 条线的最终验收口径）

`tests/quality/test_adaptive_acceptance_suite.py`：同一需求连续 3 轮，全部走真实机制
（build_graduated_spec → spec_to_paint → review_and_repair_cartography → 记忆收割 →
事实库 adaptive lane），断言 (a) round2/3 质量分不劣于 round1；(b) round3 vs round2 的
符号方案决策距离 ≤0.25（决策投影 `symbology_decision_signature` 是 03 线
`SymbologyDecision` 的落位缝，其落地后替换该投影即可）；(c) 全轮无 repair_exhausted。

## 7. 后果与风险

- +1 迁移（0056，本线是 10 线中唯一允许新建迁移的线；0036–0055 v9 保留段未占用，
  领号见 `.alloc.json`）。回滚 = `alembic downgrade -1`（只删 4 张新表，不触碰既有表）。
- 写入钩子在热路径（每次 runtime 评审）落一行账——账本故障被吞掉、可整体停用，
  不影响评审判定与延迟（fire-and-forget task）。
- 像素 golden 对渲染栈版本敏感（MapLibre/字体升级可能全红）——届时按分档降级
  nightly-only + 记录样本，或显式 `generate` 刷新并入库 diff 说明。
- 覆盖率下限 50→60 的爬坡未完成前，本闸对"制图代码被删测试"类回归仍然有效
  （下限语义），但对渐进稀释不敏感——爬坡完成前由 ratchet 闸补位。

## 8. 其他线如何接入（协调点）

1. 质量指标入账：`record_quality_run(lane=..., checks=..., summary=...)`（契约见
   store docstring；03/05/06/07/08 在各自产出处调用即可）。
2. 自适应验收：改动会宣称"下一张更好"的线，跑
   `tests/quality/test_adaptive_acceptance_suite.py` 口径的 3 轮断言（或复用其
   signature/score 投影）。
3. 09 线 `record-only → 阻断` 切换：由本线基线稳定后决定（建议 nightly 10 绿 +
   本 PR 合入后首个 full-gate 全绿）。
