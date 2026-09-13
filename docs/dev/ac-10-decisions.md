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

## D9 §6 双轴 Review 的处置记录（Standards / Spec）

**修复（Fixed）**

1. 【硬】`docs/cartographic-closed-loop.md` "no database or migration is introduced"
   与事实库新增矛盾 → 该段改写：事实库是评审**事后镜像的下游账本**，
   不参与评审，判定仍由既有 in-memory/session/harness retention 治理。
2. ratchet 容差回退 bug：基线显式 `tolerance_pct=0` 被吞成默认 5 →
   `is not None` 判定。
3. golden verify 盲区：像素通过线（98%）对小要素消失是盲的（实测墨量
   0.0007–0.0062 vs 预算 2%）→ 新增 `ink_ratio` + 墨量带校验
   （`|Δ| ≤ max(0.001, 0.4×ink)`），golden.json 记录墨量；缺 golden 从
   "警告放行"改为退出码 2。
4. `query_quality_trend` 前缀 LIKE 未转义 `%`/`_` → `_like_prefix` + escape。
5. runtime 钩子 gate score 的 bool 透传（与 harness_runner 口径不一致）→ 排除 bool。
6. coverage gate 死常量 `FLOOR_RATCHET_HISTORY` → 删除（下限只升不降为流程纪律，
   记录于本文件 D1，不做半吊子强制）。
7. 聚合循环重复（aggregate_observations / build_baseline_entries）→ 抽
   `_group_finite_rows`；四份 facts_db fixture 重复 → 收敛到
   `tests/quality/conftest.py:make_facts_db`；测试内死行与误名清理。
8. "全自动率"口径冒充 → 看板口径表与代码注释明确"人工介入次数暂无独立
   度量源"，名称保留（任务书规定六项名）。
9. 趋势窗口魔数 `last*5` → `_EVIDENCE_KEYS_PER_RUN` 常量。
10. quarantine 机制零测试 → `_is_blocking` 谓词 + 墨量带/消失场景单测补齐
    （tests/quality/test_golden_image_diff.py）。
11. recon 文档缺 P0 第 4 项（覆盖率现状）→ (e) 节补 50.10%/46.33% + 确定性
    实证 + 墨量实测。

**反驳（Rebutted，留档）**

1. "P4 下限应为 60 而非 50"：任务书 §0.5 明文 provisional-first；首日即 60
   天天红，反而失去闸的信息量。`--floor` 默认 60 保留，本地闸 50 起步爬坡，
   见 D1 与 ADR-0159 §5。
2. "P5 未用真 SymbologyDecision"：该类型在 master 尚不存在（03 线 PR #1258
   未合）；套件定义的 `symbology_decision_signature` 即其落位缝，03 合入后
   替换投影即可（套件 docstring 明示）。
3. "quarantine 未触发=验收空转"：任务书 §5 原文是"flaky 场景为 quarantine"——
   27 次运行零 flaky，无可隔离对象；机制本身已有测试（本次补齐谓词测试）。
4. "8/9 golden 是空白画布"：不成立。`canvas_blank` 是 validator 的保守
   近单色启发式（背景 99.3% 即判 blank），PNG 实际含渲染要素（heatmap-basic
   人工目检为红热区+控件）。但该 finding 的定量内核成立——小要素墨量
   （0.0007–0.0062）低于像素预算（2%）， vanish 检测确有盲区 → 以墨量带
   修复（见 Fixed#3），并在 recon (e) 节如实记录。
5. "pr-blocking 未满足 ADR-0065 的连续 10 绿"：10 绿历史是 nightly 累积量，
   本 PR 的意义恰是把这段历史记下来；status.json note 与看板均已如实披露
   "本地 3×绿"证据。任务书 §5 验收线是"至少 6 个场景标记 pr-blocking"。
6. 范围外文件（golden_diff.py / 新脚本 / .gitignore / config 开关等）：
   均为 P2/P3/P7 交付的必要件——任务书 §8 可改清单未穷举其自身要求的
   交付物；`golden_diff.py` 是 P3"像素级 golden"的纯函数实现面。

## D10 gate 自愈：lane 测试面与事实库的库隔离

- 发现：coverage gate 步骤以默认 `DATABASE_URL` 跑 `-m cartography` lane 时，
  lane 内既有的 DB 面测试会重建/清理 dev 库表——把 ratchet 步骤依赖的基线
  一并清掉（gate-3 现场捕获：基线 42→0，gate 亦如实汇报"无 active 基线"）。
- 处置：`quality_gate_local.sh` 步骤 1 改用一次性临时库
  （`DATABASE_URL=sqlite:///<tmp>/lane.db`）跑 lane，事实库与 ratchet 步骤
  用真实 dev 账本，互不践踏。基线重建是脚本化一条命令（`baseline
  --from-json <P0 分布>`），无手工恢复成本。
