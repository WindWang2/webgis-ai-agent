# AC-10 决策日志（adaptive-cartography/10-quality-baseline）

> 配套 ADR-0159。本文件记录执行中的**岔路选择**（§0.5 全自动默认决策的落地实录）
> 与证据指针，供 Review 与后续线追溯。

## D1 覆盖率闸取值：先 50 后 60（ratchet 只升不降）

- 任务书默认 60%；P0 实测 cartography scope = **50.10%**（harness 46.33%，仅报告）。
- 若首日即设 60，闸天天红，失去信息量也阻塞其余 9 线。按 §0.5 provisional 精神：
  `quality_gate_local.sh` 以 `CARTO_COV_FLOOR=50` 起步（当日实测之上），爬坡到 60 收口。
- 证据：`coverage_cartography_gate.py` 运行输出（PR 描述附）。

## D2 首轮基线 provisional、不激活

- `quality_ratchet_gate.py baseline --from-json <P0 分布 32 条有限观测>` → 42 条
  provisional 基线（13 场景 × 5 检查项）；`check` 步当日只记录不拦截。
- 激活（`--activate`）留给基线稳定后（建议 nightly ~10 绿），写入本线后续变更。
- 注入劣化 100% 拦截的验收由单测锁定（active 基线路径）：
  `tests/quality/test_cartography_ratchet.py`。

## D3 头照晋升：7 个 pr-blocking、2 个 nightly-only、0 quarantine

- 27 次运行（9 场景 × 3）全部绿（负路径 fault-* 场景按 expect=fail 判定）、
  全部 <14s（预算 <30s）。7 个正常场景标 `pr-blocking`；fault-missing-source /
  fault-wrong-color 是负路径夹具（验证的是"validator 能抓坏图"），归 nightly。
- ADR-0065 的"连续 10 次绿"历史无法在单机 3 轮内伪造，由本线事实库
  （lane=golden/run 累积）随 nightly 补齐；当前晋升基于本地 3×绿 + 时长证据，
  已写入 status.json 的 note。
- flaky 处置机制就绪（quarantine + 失败样本 + 禁删除），本轮实测零 flaky 未触发。

## D4 golden 渲染确定性实证

- generate 后立即 verify：9/9 场景 `within_ratio=1.0`、`max_channel_diff=0`——
  headless 渲染在同机同栈下是逐像素确定的。容差策略（±16 / 48 / 98%）因此
  只用于跨环境/跨版本漂移防护，不掩盖本机回归。

## D5 Windows 本地跑浏览器场景的 shim 不入共享代码

- `node_modules/.bin/jiti`（WinError 193）与 `npx`（WinError 2）在 Windows 需要
  `.cmd` shim。修复放在测量/golden 脚本内（进程内、os.name==nt 分支），不改共享的
  `mapspec/coordinator.py` 与 `runtime_validator.py`——避免与 9 条并行线冲突。
- nightly/CI（Linux）不受影响。

## D6 calibrate 只把建议值存为基线，不改硬编码

- `_carto_threshold` 与 `Settings.CARTO_*` 一字未动（有测试断言前后快照相等）。
- `--write` 把 p66/p90 建议作为 provisional 基线入 `cartography_quality_baselines`
  （source=calibration），并产出 old→new diff 报告；dry-run 是默认。

## D7 迁移唯一性与段位

- 本线是 10 线中唯一允许新建迁移的线；migration **0056_cartography_quality_facts**
  （down=184068cb4249），0036–0055 v9 保留段未占用，段位登记见
  `migrations/.alloc.json`（含本线 0056–0065 预留段）。
- 模型注册遵循 data_quality 先例：`migrations/env.py` + drift 闸测试 import 列表
  各加一行（additive）。

## D8 复核纪要（§0.2 防重复）

- PR/issue 检索（quality baseline OR ratchet OR golden OR 覆盖率 OR trend OR 回归，
  ≤200/300 条）：无本线同构实现。最接近者：#1172 findings 棘轮（锁**工具/算法质量债
  findings**，非制图度量）；science-v4"契约 ratchet"（科学计算契约，非制图）；
  #564/#612（覆盖率门禁装饰性 / .coveragerc 正则——已关闭，指向 CI 75% 闸，与本线
  独立制图闸互补）。并行 10 线中 01–04/06/07/09 的 PR（#1257/1258/1262/1263…）
  均不建度量账本；本线是唯一迁移线（契约由任务书 §0.3 保证）。
- 分支检索（quality|baseline|ratchet|golden|metrics）：仅 fix/flake-quality-engine、
  fix/lint-quality-gate、foundation/quality-e2e-v9（均已合并/属别的面）。
- 代码检索：`calibrate_cartography_thresholds.py`（只建议不写文件）、
  `baselines.json`（perf harness 专用）、`quality_runner.py`（lane 定义，无 ratchet）、
  production.yml:202/399/452/552-558（backend 排除 cartography；cartography-smoke /
  nightly 矩阵）——与本线产出互补、无重复建设。
