# G05 独立对抗审查（Round 1）

- 审查员：独立 reviewer（未参与实现）；基线 `origin/master = ee8c14f8`，分支 `feat/g05-harness-task-acceptance-vnext`（worktree `../webgis-g05-task-acceptance`）。
- 审查对象：`git diff origin/master...HEAD`（24 文件，+6479 行，全部为新增路径 + .gitignore 白名单 1 行 + ledger 追加）。
- 方法：逐文件精读 + 全部验证命令实跑 + 3 项篡改演练（改后还原，`git status` 确认干净）+ 语义探针（评估器 op 边界、生产 dispatch 直调、文档热区图脚本比对）。

## 结论：FIX-FIRST

整体架构诚实（真实 dispatch 缝 + settle_turn + 权威判定面只读复用；无生产/共享基建改动；pin 机制有效），但存在 3 项 P1：CLI 回归对比漏报真回归、f09 负例的声明不变量与生产实际行为相反且被 vacuous 检查掩盖（假绿负例）、runner 的 verifiedTurnId 生产缝篡改对整套测试不可观测。合并前须清偿 P1。

## Findings

| # | 级别 | 位置 | 发现（含复现证据） | 建议修复 |
|---|---|---|---|---|
| 1 | **P1** | `scripts/g05_task_acceptance.py:144` | compare 对缺 `error` 键的基线报告漏报回归。`was_ok = prev.get("error") == "" and _ok_of(prev)`：旧格式报告无 error 键时 `None == ""` 为 False → was_ok 恒 False → 真回归（ok→fail）既不进 new_failures 也不进 fixed，**exit 0**。实测：手工构造 baseline（无 error 键，场景 ok）+ current（同场景 fail），`compare` 输出 `new_failures=0 fixed=0`，exit 0。`_ok_of` 内部已含 `not run.get("error")`，外层检查既冗余又有害；同行 `r["scenario_id"] and _ok_of(r)` 为死表达式（scenario_id 恒非空） | 改为 `was_ok = _ok_of(prev)`、`is_ok = _ok_of(r)`；补一条 compare 的回归单测（含无 error 键旧报告） |
| 2 | **P1** | `tests/acceptance/gis_agent/corpus.py:584-614`（f09） | f09 标题/docstring 声明「投影平面坐标谎称 WGS84 → 空间分析**确定性拒绝**」，但生产实际**不拒绝**：直调 `webgis_execute(spatial_stats, wrong_crs_points)` 实测 `isError: False`，文本 `{"summary": "Total features: 8"}`，仅有 polar warning；heavy 运行证据 `plan.progress.status_counts = {'pending': 5, 'complete': 1}`（分析行 complete=1，注入数据畅行无阻）。唯一注入特定检查 `f09-failure-visible` 用 `op="exists"` 指向 `status_counts`——空 dict `{}` 也判 exists PASS（实测），即该检查只断言「plan 投影存在」，与「失败可见」语义无关。负例场景全绿的同时坏数据通过分析 = 假绿负例；且 f09 无反转测试兜底（7 个负例中仅 f02b/f08 有行为反转，f15 为纯结构测试） | 二选一：(a) 按实际行为改写场景语义（断言 polar warning / 告警披露进 chapter_summary.methodology_warning_count，并在 not_evaluated 披露「产品级 CRS 域校验缺口」——与 f18 的 G05-F1 口径对齐）；(b) 若「确定性拒绝」是应然行为，标 xfail 并立 issue。`exists` 检查改为断言 `status_counts.failed ge 1` 或 `complete eq 0` |
| 3 | **P1** | `tests/acceptance/gis_agent/runner.py:179-185` | 篡改演练：`verifiedTurnId=turn.turn_id` 改为 `None` 后整套 lane **全绿**（test_task_e2e + test_task_failures 12/12 passed，exit 0）。runner 注释声称「requirement IR / 记忆等 session 绑定缝依赖该上下文」，但 f19 的 `requirement.revision >= 1` 等在无绑定时照常通过——该声明未被任何期望钉扎。生产侧 `dispatch_tool` 中 verifiedTurnId 绑定 turn RuntimeContext（agent_pi_bridge.py:489-505，18 阶段链发射据此解析，缺席时**静默跳过**）——即 runner 若未来悄悄丢失该缝，链发射证据静默消失而 G05 全绿 | 增加一个可观测钉扎：任一场景断言链发射/turn 绑定副作用（如 plan.turns[].turn_id 或 journal 事件含 turn 归属），或最小化修正注释、在 docs 诚实边界中声明「turn 绑定缝的效果在本 lane 不可观测」 |
| 4 | **P2** | `scripts/g05_task_acceptance.py:16,31`、`planning/g05/DECISIONS.md` D7 | CLI docstring 与 DECISIONS D7 声称「报告落 reports/g05/（gitignored）/ 不污染 git」，但已提交 `.gitignore` 无任何 `reports` 规则：`git check-ignore reports/g05/run-x.json` exit 1（未忽略）；实测 `touch reports/.probe` → `git status` 出现 `?? reports/.probe`。缺省 `run`（不带 --out）在任何 clone 上都会产生未跟踪文件 | `.gitignore` 增加 `/reports/`（或 `/reports/g05/`），或 CLI 缺省落 `tmp/`；同步修正 docstring/D7 |
| 5 | **P2** | `runner.py:89`、`fixtures_data.py:242-251` | 栅格残留：运行后 `tmp/g05-rasters/` 残留 6 个 tif（含工具产物 `g05_sim_nir_calc.tif` 等），runner/fixture 均不清理；且已提交 `.gitignore` **无** `tmp/` 规则——本机靠未提交的 `.git/info/exclude:9` 掩盖，fresh clone / CI 上是未跟踪污染 | `.gitignore` 增加 `/tmp/`；或 runner 改用 tmp_path 会话目录并清理；至少在 docs 披露残留语义 |
| 6 | **P2** | `docs/quality/g05-task-acceptance.md` §2 | 覆盖热区图与 corpus 不一致：脚本逐格比对 **14 格错 / 13 行受影响**。例：f01 method 列 ○（corpus 无任何 METHOD 期望）；f02a/f03/f04a/f05/f07 method 列 ●/○（实际仅 ●）；f08/f11 method 列 ○（实际 ●）；f04b/f14/f15 漏 method ●；f14 mapspec 列 ○（实际 ●）；f12/f13 render/export 列 ●/○（实际仅 ●）。所有 not_evaluated 期望（15 条）实际都在 final_answer 面 | 由 `report --coverage` 机器输出重生成 §2 表格，或人手逐格校正；把「表格生成口径」写进文档 |
| 7 | **P2** | `corpus.py:667-699`（f11） | f11 负例仅有通用 `_pending_honest_exps` 两条（与任何正例的离线诚实钉扎无差别）：单位混标数据无论被静默接受还是拒绝，场景都绿。not_evaluated 已声明检测器属 G01/G02——则「负例」标签名不副实，等价于一次 smoke 运行。无反转测试 | 加注入特定期望（如 spatial_stats 失败或告警留痕 `methodology_warning_count ge 1`），或降级改名为正例 smoke 并从负例计数中移除（docs「7 负例」同步） |
| 8 | **P2** | `docs/quality/g05-task-acceptance.md` §4 G05-F2 | 「三真实发现」之 G05-F2 称 not_in 假绿 bug「TestBehaviorInversion 在 **f18 反转**中抓住」——test_task_failures.py 不存在任何 f18 反转测试（反转仅 f02b/f08；f18 只出现在 TestNegativeScenariosCatchInjections）。证据归因失实 | 修正为 f02b/f08 反转或补充 f18 反转测试 |
| 9 | **P2** | `contracts.py:114-120` vs corpus | docstring「负例场景的期望即 fail」与实现不符：86 条期望 = 71 pass + 15 not_evaluated + **0 fail**。`expected_verdict="fail"` 机制在语料中零使用（仅单测合成构造触达）；负例实际靠「对降级结果的 pass 期望 + 反转测试」达成。设计口径与实现口径应统一陈述，否则读者会以为 fail 期望在跑 | 修正 docstring/文档口径（说明负例 = 降级不变量 pass 期望 + 反转防线）；或给 1-2 个场景真正用 expected_verdict="fail" 以覆盖该分支 |
| 10 | **P2** | `attribution.py:86-95` | runner 级异常（超时/基础设施 bug）被归因为 `governance_violation`（taxonomy 注释：权限/资源/租户边界跨越）——语义错位，判错报表会把 harness 故障计入治理违例热区 | 新增独立 code（如 taxonomy 外的 `infra_error`）或映射到 `unsupported` 并在 detail 标注 runner_error |
| 11 | **P3** | `test_task_failures.py:97-116` | `test_tier3_guard_inverted_must_error` 是纯结构测试：不执行 runner，最终断言 `danger.expect_error is False`（自己刚设的值，同义反复）。docstring 承诺「runner 层翻红，二者必居其一」却从未运行验证。f15 的真实防线是运行时 expect_error 断言（有效），但该测试本身给不出证据 | 要么真跑 runner 断言翻红，要么改名为结构一致性检查并删除行为性措辞 |
| 12 | **P3** | `projections.py:24-26` | `chapter = getattr(plan, "gis_chapter", ...)` 连续赋值两次（死行） | 删一行 |
| 13 | **P3** | `runner.py:45-49,97,148`、`conftest.py:22-27`、`scripts/g05_task_acceptance.py:35` | 死代码/残留：`build_registry()` 无调用方；`auto_layers` 收集后从未读取；conftest `g05_sid` fixture 无使用方；`SMOKO_IDS` 命名（应为 SMOKE_IDS） | 清理或接入 |
| 14 | **P3** | `docs/quality` §6、`.goal-loop-ledger.md`（未提交行）、`planning/g05/TEST_MATRIX.md` | 数字与矩阵陈旧：docs/ledger 称「41 绿」，实测 `tests/acceptance/gis_agent` 收集/通过 **38**；TEST_MATRIX 家族表与 corpus 错位（矩阵 F09=样本不足 vs 实际 F09=错误 CRS、F11=错误 CRS vs 实际单位冲突、F17=stale render 负例 vs 实际正例信封场景、F04 注入列 unit vs 实际 method），且「状态」列全部 ☐ 未对账 | 以实测数字/矩阵刷新；TEST_MATRIX 按 corpus 现名重排或标注「规划稿，以 corpus 为准」 |
| 15 | **P3** | `reporting.py:69-87` | 评估器 op 边界隐患（当前 corpus 未踩但无守卫）：`exists` 对 `{}` 判 PASS、`absent` 对 `{}` 判 FAIL（不对称）；`not_in` 对 actual=None 恒 PASS；`in` 不支持 list-in-list（`["a"] in ["a"]` False）。已有单测仅覆盖正例分支 | 补边界单测钉死语义；`exists`/`absent` 对空容器统一处理 |
| 16 | **P3** | `scripts/g05_task_acceptance.py:140-143` | compare 只对「两边都有的 scenario_id」对比：current 侧缺场景（profile 不同/未跑完）静默跳过，不报 missing | 增加 missing-in-current 判定或至少打印 skipped 数 |

### 通过面（证据支持，不需改动）

- **越界（G）**：diff 无任何 `app/` 生产文件、`tests/conftest.py`、`pytest.ini`、`pyproject.toml` 改动（`git diff origin/master...HEAD -- pytest.ini tests/conftest.py` 为空）。与 #1759/#1760 声明的 0 文件交叠成立。未造第二 replay oracle：判定复用 `verify_export_semantic_consantic_consistency`/`build_product_manifest`/MapSpecStore 读 API（projections.py 只投影不判定）。
- **registry 隔离**：根 conftest `_reset_chat_engine_singletons`（tests/conftest.py:424-449）每个测试前后把 `agent_pi_bridge._tool_registry` 复位为 None，G05 的 `ensure_registry_injected` 按测试重注入——不外泄到其他测试。
- **session 清理**：runner `run()` 开头 + finally 双 `clear_session`（唯一残留是 tmp 栅格文件，见 #5）；每场景 sid 带 uuid 后缀，无跨场景串扰。
- **错误吞噬（B）大体诚实**：runner 级异常进 `error` 字段并使 `ok=False`（不洗成 not_evaluated/pass）；`expect_error` 注入步若未失败会 AssertionError 翻红；projections fail-open 的每个面都被至少一条 pass 期望锚定（mapspec 断裂→f01/f02a 红；plan 断裂→needs-execution-visible 红）。
- **pin 机制（D3 演练）**：corpus 加一个 tag → TestPinConsistency 红（exit 1）；还原后绿。真实漂移可抓。

## 验证命令与退出码

| # | 命令 | 结果 | 退出码 |
|---|---|---|---|
| 1 | `.venv/bin/pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=300 --timeout-method=thread -q` | **38 passed**（25.67s；首次执行退出码因 zsh pipestatus 笔误未记录，无失败摘要；同 lane 复跑见下条佐证） | 0 |
| 1b | `.venv/bin/pytest tests/acceptance/gis_agent/test_task_failures.py -m cartography --no-cov --timeout=300 -q` | 11 passed（13.05s） | **0** |
| 2 | `.venv/bin/ruff check tests/acceptance/ scripts/g05_task_acceptance.py` | All checks passed! | **0** |
| 3 | `.venv/bin/python scripts/g05_task_acceptance.py run --profile smoke --out /tmp/g05-rev1.json` | 5/5 ok；checks pass=25 fail=0 not_evaluated=3 | **0** |
| 3b | （附加）`run --profile heavy --out /tmp/g05-rev1-heavy.json` | 21/21 ok；pass=71 fail=0 not_evaluated=15；mismatches=0 | **0** |
| 4 | 篡改演练 | 见下节（每项改后还原，最终 `git status` 仅剩审查前已存在的 `.goal-loop-ledger.md` 本地修改） | — |
| 5 | `git diff origin/master...HEAD --stat \| tail -5` | 末 5 行：4 个 test_*.py（130/112/29/200 行）+ `24 files changed, 6479 insertions(+)` | 0 |

## 假绿演练结果（篡改 → 红 → 还原 → 绿）

| 演练 | 篡改内容 | 结果 | 还原 |
|---|---|---|---|
| D1 settle 不结算 | runner.py `_run_turns` 中 settle_turn 调用替换为 no-op 假 outcome | `test_all_scenarios_e2e` **FAILED**（exit 1）——settle 缝被真实钉扎。注意：test_task_failures 的 11 个测试仍绿（其期望不依赖 settle 产物），e2e 聚合测试是唯一守卫 | `cp` 备份还原，`git diff` 空 |
| D2 去 verifiedTurnId | `verifiedTurnId=turn.turn_id` → `None` | **12/12 全绿（exit 0）**——P1 #3：该生产缝的丢失对整套 lane 不可观测 | `git checkout` 还原 |
| D3 pin 漂移 | corpus.py f01 `tags` 加 `"drift-probe"` | `TestPinConsistency::test_pin_exists_and_matches_corpus` **FAILED**（exit 1）——pin 一致性测试能抓真实漂移 | 备份还原，`git status` 干净 |

## 附：语义探针实测（reporting._verdict_of）

- `exists({})` → PASS；`absent({})` → FAIL（不对称，见 #15）
- `not_in(None, ["satisfied"])` → PASS（None 泄漏，当前 corpus 未踩）
- `in(["a"], ["a"])` → FAIL（不支持 list-in-list；corpus 的 `in` 均用于 str actual，安全）
- `absent(MISSING)` → NOT_EVALUATED（缺路径不冒充 absent——诚实三态语义正确）

## 清偿建议（合并前）

1. P1 #1：compare 一行修复 + 回归单测。
2. P1 #2：f09 按实际行为重写期望（推荐断言 polar warning 披露 + not_evaluated 声明产品级缺口），并给 f09/f11 补反转或注入特定期望。
3. P1 #3：最小修复为修正 runner 注释与 docs 诚实边界；理想修复为增加 turn 绑定副作用的可观测断言。
4. P2 #4/#5：`.gitignore` 补 `/reports/` 与 `/tmp/`（两行）。
5. P2 #6/#8/#14：文档以机器输出重生成热区图、修正 G05-F2 证据归因与「41→38」数字。

## Round 2 复审（2026-10-22）

- 复审员：第二轮独立 reviewer（未参与实现与 Round 1）；审查对象 = commit `f4474846`（清偿提交）相对 `e3ff7228` 的 diff + 当前代码实跑。
- 方法：逐项核销 Round 1 findings（diff 精读 + 实测复现）、全量命令链终验、docs §2 热区图 231 格脚本逐格比对、compare 五场景手工构造实测、f09/f11 真跑取证。

### 结论：APPROVE

P1×3：2 项全清、1 项实质清偿（残留悬空文档引用，降级为非阻塞）。P2×7：5 清 / 1 部分 / 1 未清（均为非阻塞文档/口径项）。P3×6：5 清 / 1 清偿留残留措辞。无新回归引入；关键命令链全绿。无阻塞项，可合并。

### 逐项核销表

| Round 1 # | 级别 | 判定 | 证据（实测） |
|---|---|---|---|
| 1 | P1 | **已清偿** | `scripts/g05_task_acceptance.py:145` `was_ok, is_ok = _ok_of(prev), _ok_of(r)`；死表达式已删。手工构造旧格式基线（无 error 键）+ 同场景 fail → 输出 `NEW-FAIL f01... first_failing_stage=intent/f01-intent`，**exit 1**；missing 场景 → `MISSING f02a...`，**exit 1**；无变化 → exit 0；error 键回归 → `NEW-FAIL ... first_failing_stage=runner` exit 1；fixed+added → exit 0（语义正确）。单测 `TestCliCompareRegression` ×2 进 lane（45 内） |
| 2 | P1 | **已清偿** | f09 重写为缺口披露型：title/docstring 与实测一致（分析放行）；假绿 `f09-failure-visible`（exists on status_counts）已删；新增 `f09-analysis-passes-through`（`plan.progress.status_counts.complete ge 1`）——**真跑实测 actual=1 pass**，polar warning 在场（`to_utm_gdf: ... polar ... EPSG:3413`），`f09-needs-execution-visible` actual=5（判据不放行）；not_evaluated 声明 `crs_domain_validation_gap` + tag `gap-disclosure`；note 明示「CRS 域校验落地后本期望需重校准」（tripwire 语义成立：校验落地 complete→0 时本期望翻红提醒重校准）。pin 同步重生成（21 scenarios，无漂移） |
| 3 | P1 | **部分清偿（实质已诚实，残留非阻塞）** | runner.py:168-171 注释已删除虚假的「requirement IR / 记忆依赖」声明，改为诚实口径「该绑定的效果在本 lane 的证据面内不可直接观测」——失实陈述本身已清偿。**但**注释引用「见 docs/quality §3 边界声明」为悬空引用：docs §3（离线诚实语义）不含任何 turn 绑定边界声明（grep 全 docs/planning 无），commit message 所称「docs §3 边界声明」未落地。未增加可观测钉扎（Round 1 已标注为可选的理想修复） |
| 4 | P2 | **已清偿** | `.gitignore:19 /reports/`：`git check-ignore reports/g05/run-x.json` 命中 exit 0；live probe `touch reports/.probe` 不出现在 git status |
| 5 | P2 | **已清偿** | `.gitignore:20 /tmp/`：`git check-ignore tmp/g05-rasters/probe.tif` 命中 exit 0；live probe 不污染 status（采用 finding 建议的第一方案；栅格物理残留仍是本机 tmp 下 gitignored 文件，可接受） |
| 6 | P2 | **已清偿** | 脚本逐格比对 docs §2 表格 vs corpus（21 行 × 11 列 = **231 格，0 错**，含 7 面 × 21 = 147 面格 + neg/多轮/恢复取消/缺口披露 4 标记列）；文档写入生成口径注记。21 行齐全无缺行 |
| 7 | P2 | **部分清偿** | f11 诚实重标注：title「缺口披露（判据不放行）」+ tag `gap-disclosure` + not_evaluated 扩充 + docs §2 负例分层（注入敏感 f02b/f08/f15 vs 缺口披露 f04b/f09/f11/f18）——「负例标签名不副实」已修复。残留：f11 仍无注入差异化 tripwire 期望（单位检测落地不会使任何 f11 期望翻红），与 f09 的 complete-ge-1 tripwire 不对称 |
| 8 | P2 | **已清偿** | G05-F2 行改为「W2 校准 f18 期望时发现…评审 Round 1 确认」+「f02b/f08 反转测试常驻保护」——与 test_task_failures.py 实际（TestBehaviorInversion 恰为 f02b/f08 两反转）+ 新增 TestEvaluatorOpBoundaries 一致 |
| 9 | P2 | **未清偿** | `contracts.py:118` docstring「负例场景的期望即 fail」原文未动（git diff e3ff7228..f4474846 -- contracts.py 为空）；corpus 仍 0 条 fail 期望（71 pass + 15 not_evaluated）。docs §2 负例分层部分缓解了误读，但设计口径 vs 实现口径的矛盾陈述仍在。commit message 亦未声称清偿本项 |
| 10 | P2 | **已清偿** | attribution.py:86-95 改为独立 `infra_error` 桶（authority 标注 "runner infrastructure (not the system under test)"）；单测 `test_runner_error_not_governance` 断言 governance_violation 缺席 / infra_error 在场。残留（轻微）：docs §5 仍写「九类」未提及 taxonomy 外的 infra_error 桶 |
| 11 | P3 | **已清偿** | 新增 `TestTier3GuardBehavioral::test_tier3_inverted_actually_errors`：真跑 g05_runner + `assert not result.ok`（行为证据成立）；旧结构测试保留为补充（其「必居其一」承诺现有兄弟测试背书） |
| 12 | P3 | **已清偿** | projections.py 重复赋值死行已删（diff 确认） |
| 13 | P3 | **已清偿** | `build_registry()` 删、`auto_layers` 收集链删、conftest `g05_sid` fixture 删、`SMOKO_IDS`→`SMOKE_IDS`（diff 逐一确认） |
| 14 | P3 | **已清偿** | docs §6「45 绿」与实测一致（本复审批 45 passed）；TEST_MATRIX 顶部加「规划稿，以 corpus 为准」口径注记；ledger 补 W1–W5 记账行 |
| 15 | P3 | **已清偿** | reporting.py exists/absent 对 `{}` 对称处理（`!= {}` / `== {}`）；`TestEvaluatorOpBoundaries` ×3 钉死空容器对称 / not_in None=PASS·MISSING=NOT_EVALUATED / not_in 集合式互斥语义 |
| 16 | P3 | **已清偿** | compare 增加 missing/added 判定与披露行；missing 使 exit 1（实测见 #1） |

### 无新问题引入（diff e3ff7228..f4474846 审查）

- **范围**：15 文件全部落在 G05 路径（tests/acceptance/gis_agent、scripts/g05_task_acceptance.py、planning/g05、docs/quality/g05、.gitignore、.goal-loop-ledger.md）；无生产/共享基建改动（out-of-scope grep 为空）。
- **compare 重写**：五场景手工实测（真回归 exit 1 / missing exit 1 / 恒等 exit 0 / error 回归 exit 1+stage=runner / fixed+added exit 0）语义全部正确；added-only 不判失败（新场景非回归，合理）。
- **exists/absent `{}` 语义变化**：heavy 全量真跑 21/21 ok、pass=71 fail=0 not_evaluated=15、mismatches=0（与 Round 1 同口径数字）——无既有期望被新语义误伤；唯一受 `exists({})` 行为影响的消费方正是被删除的假绿检查 f09-failure-visible。
- **attribution infra_error**：TAXONOMY_CODES 九类原样（test_taxonomy_vocabulary 仍绿）；infra_error 仅在 attribute_run 输出层新增，attribute_runs 聚合按输出键迭代，自然携带；无既有归因路径回归。
- **runner _run_turns 签名收窄**（去 auto_layers）：全 lane 45 绿证实无遗漏调用方。

### 关键命令链终验（退出码实录）

| # | 命令 | 结果 | 退出码 |
|---|---|---|---|
| 1 | `.venv/bin/pytest tests/acceptance/gis_agent -m cartography --no-cov --timeout=300 --timeout-method=thread -q` | **45 passed**（25.52s） | **0** |
| 2 | `.venv/bin/ruff check tests/acceptance/ scripts/g05_task_acceptance.py` | All checks passed! | **0** |
| 3 | `.venv/bin/python scripts/g05_task_acceptance.py run --profile smoke --out /tmp/g05-r2.json` | 5/5 ok；pass=25 fail=0 ne=3；mismatches=0 | **0** |
| 4 | `.venv/bin/python scripts/g05_task_acceptance.py pin` | pin up-to-date（21 scenarios）无漂移 | **0** |
| 5 | `.venv/bin/pytest tests/test_ci_local_gate_contract.py tests/test_pytest_module_name_uniqueness.py --no-cov --timeout=120 -q` | 19 passed（5.33s） | **0** |
| 6 | （附加）`run --profile heavy --out /tmp/g05r2-heavy.json` | 21/21 ok；pass=71 fail=0 ne=15；mismatches=0 | **0** |
| 7 | （附加）`git check-ignore reports/g05/run-x.json tmp/g05-rasters/probe.tif` | 两规则均命中（.gitignore:19,20） | **0** |

### 残留风险表（合并风险评估）

| 项 | 级别 | 风险 | 评估 |
|---|---|---|---|
| P1-3 残留 | P3（降级） | runner.py:171 注释引用「docs/quality §3 边界声明」悬空——docs §3 无 turn 绑定边界声明；commit message 声称的 docs 落地未兑现 | **non-blocker**：注释主体已诚实（不可观测性就地声明），虚假归因已删；建议合并前顺手在 docs §3 补两行声明或改注释指向本 REVIEW |
| P2-9 未清偿 | P3（降级） | contracts.py:118「负例场景的期望即 fail」与 0 条 fail 期望的实现矛盾 | **non-blocker**：纯 docstring 口径；docs §2 分层已部分消歧 |
| P2-7 残留 | P3（降级） | f11 无注入差异化 tripwire（单位检测落地不会翻红任何 f11 期望）；与 f09 的 tripwire 不对称 | **non-blocker**：诚实重标注已完成，无假绿；可作为 data-qualification 方向接线后的重校准备忘 |
| 本轮新发现（轻微） | P3 | (a) docs §2「缺口披露」列（tag 驱动：f09/f11）与 §2 末分层 prose（f04b/f09/f11/f18）口径不一；(b) docs §5「九类」未提及 infra_error 第十桶 | **non-blocker**：纯文档一致性，不影响判定面 |
| 旧结构测试残留 | P3 | test_tier3_guard_inverted_must_error（同义反复断言）仍在，行为性措辞由新兄弟测试背书 | **non-blocker** |

**最终 verdict：APPROVE**——P1 全部实质清偿（P1-3 残留为悬空文档引用，非阻塞）、命令链全绿、无新回归、无越界。残留 5 项均为 non-blocker 文档/口径级，可合并后随手清偿。
