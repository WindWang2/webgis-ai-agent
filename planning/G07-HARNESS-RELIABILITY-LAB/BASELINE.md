# 实时基线与冲突避让（M0）

审计时间：2026-11-07（Asia/Shanghai）。仓库：`https://github.com/WindWang2/webgis-ai-agent.git`。

## Git 事实

- 执行 `git fetch origin master --prune` 后，`origin/master=585812ee4e2d75612f2b124e6fe6587af3deca5c`，提交时间 `2026-11-07T17:54:14+08:00`，即 PR #2010 merge。
- 参考 SHA 与实时 SHA 一致，但不据此冻结后续同步。
- 原工作树已有 `vendor/pi` submodule 修改和多项 `.claude/skills/*` 未跟踪项；本任务未 stash/reset/clean，也未修改这些内容。
- 新 worktree：`/home/kevin/projects/webgis/webgis-wt-harness-reliability-lab-20261107`。
- 新 branch：`zcode/goal-harness-reliability-lab-20261107`，起点 `origin/master`。
- 本地 Python `3.14.9`、Node `v24.21.0`、gh `2.102.1`；新 worktree 的 `vendor/pi` 尚未初始化。

## 开放 PR

实时查询到 8 个开放 PR，均为 Dependabot：#1978、#1979、#1980、#1982、#1983、#1984、#1985、#1986。改动仅限依赖清单/lockfile/Actions；本线不修改 Actions，暂不产生代码文件重叠。最终同步时重新查询。

## 已合并 PR 与 Review

读取最近 50 个 merged PR（#2010 回溯至 #1877）及指定 PR 的 files/reviews。

| PR | 已存在事实 | 本线避让/复用 |
|---|---|---|
| #1972 | 类型化分析封套、真实科学 Oracle、F29 语料、性能门 | 复用 envelope 与科学事实，不重建算法/工作流 |
| #1973 | Pi v1.5.0 vendor/Host、stub LLM 与 RPC 合约 | 已集成，绝不再把升级当功能；不改宿主生命周期 |
| #1974 | 情境交互、turn 链、真实 ref 与跨会话隔离修复 | 只读使用 turn/ref 事实，不接管 UI/选择集 |
| #1975 | 约束版面、导出、tile-miss 账目与出版测试 | 复用出版证据，不新建布局/渲染器 |
| #1976 | 五条功能线集成与大量契约修复 | 以 master 实际代码为准，不按旧分支假设开发 |
| #1988 | 离线 evaluator 修复与百万要素遍历有界化 | 作为性能/离线基线，不重复遍历优化 |
| #1989 | Pi/GIS 完结门、真实渲染观察 | 复用完结门，不重写 |
| #1993 | 城市范围与显式出版 layout | 跨城/A4 案例先对拍，已覆盖则只补 Harness 证据 |
| #1995 | Lab/settlement 诚实完成契约 | 新投影不得绕过 settlement |
| #1998 | payload UTF-8 bytes 与 scope disclosure 防回归 | 作为 bytes 预算回归锁，不以字符数替代 |
| #2008 | 真实 Story 浏览器旅程修复 | 复用本地浏览器车道，不新建 UI |
| #2009 | session 内层锁重入修复 | 严禁重写锁；只做故障对拍 |
| #2010 | session restore 后命令可用性 | 不改前端命令 UI |

Review 记录：#1972 有 5 项分析正确性修复说明；#1973 有运行镜像/jiti 修复；#1974 有 11 项跨会话/ref/范围修复；#1975 有输出折行/tile 生命周期等修复；#1976 有最终集成审查与本地验证统计。指定其余 PR 的 `reviews` 查询为空；13 个指定 PR 的行内 review comments 查询也均为空。#1999–#2007 的 Issue 正文已逐条读取，未发现 comments；具体代码证据按开放基线债务记录，不在本线冒充新发现。

## Issues

#1999–#2007 均实时为 OPEN：A4 约束入口、layout 标记污染、跨城旧图层、组件族锁、旋转指北针、选择截断、网络秒/米混淆、summary 数值丢失、生成快照/质量文档漂移。

决策：它们是输入和负例，不作为本线“新功能”抢修。只有直接阻断 G07 证据链时才做稳定 seam 的最小修复并记录。#2007 是 baseline debt，禁止无条件刷快照或改门槛求绿。

## 分支与活跃工作树冲突矩阵

- `git branch -r --no-merged origin/master` 无输出；检查的远端 Harness 相关分支相对 master 均为 0 commits。
- G01 活跃 branch 修改 `app/agent_pi_bridge.py`、`app/services/chat/pi_*` 与对应测试：本线默认完全避开。
- G02/G03 活跃工作树不与本线主所有权重叠；G03 仅见其新增 `tests/data/test_g03_readiness_chain.py`。
- 开放 PR 当前不拥有 `app/lib/harness/{lab,replay}`、`app/lib/harness/result_contracts.py`、本线新 scripts/tests、`docs/quality/GIS_HARNESS_RELIABILITY.md` 或本规划目录。

| 路径 | 所有者/风险 | 决策 |
|---|---|---|
| `app/lib/harness/lab,replay` | 本线主所有权；既有资产丰富 | additive，先 characterization，不宣称新建 |
| `app/lib/harness/result_contracts.py` | 本线 additive 投影 | 保留旧字段与判决器 |
| `app/services/gis_harness/recovery_*` | 既有 #1879/#1989 资产 | 复用策略，仅测试/必要局部修复 |
| Pi bridge/chat/锁/前端/MapSpec/cartography | G01/G02/G04/G05 或已合并 PR | 只读；阻断时最小 seam 修复并说明 |
| `perf/budgets.json` | 既有性能事实 | 只有原始实测支持才改，不放宽门 |
| Actions/线上 CI | 开放 dependabot 与 baseline debt | 不修改、不等待 |

## 初始架构事实

ADR-0256 已定义 ReplayTrace 为打包层、T1/T2/T3 与 provisional ratchet；ADR-0262 已定义 T4 真实 ToolDispatchService 沙箱、record-as-expect、结构化 diff 与 shrink；ADR-0281 已有三层失败词表、RecoveryPolicy、GoalVerification 和恢复语料。真正缺口必须通过代码/测试审计重新定位，不能把这些既有能力重复包装成交付物。

## M1 既有能力与断链结论

### 本地基线

- 主 Agent 在隔离 worktree 使用共享 venv 执行 `python -m pytest tests/lab tests/harness_replay --no-cov -q`：**239 passed, 4 warnings, 67.22s**。警告是多线程进程 fork 的 DeprecationWarning，未掩盖。
- 子代理 A 审计恢复域并报告恢复/goal verification 测试 **194 passed**；主 Agent 后续会对改动面独立复跑，不把子代理结论当独立冷审。

### 已存在（不得重命名冒充新增）

- Replay：T1 真实 PiAgentHarness/evaluator/L5，T2 真实 MapSpec lifecycle，T3 生产 bind gate，T4 真实 ToolDispatchService + recorded provider + 内存 store；140 场景、fault contract、ratchet、diff、shrink、explain。
- Lab：8 个 JSON 旅程、统一 adapters、9 维裁决、declared-not-evaluated 不绿、双跑 digest、显式 baseline；VisualFixtureAdapter 已明确不评价场景地图。
- Recovery：28 个诊断 reason、6 处置、29 个 corpus 案例、5 组真实 dispatch 对照、GoalVerification 8 slot 对账、TTL/LRU durable ledger。

### 可证明缺口

1. `result_contracts.py` 没有跨 intent→plan→dispatch→data/ref→mutation→render→export→settlement 的只读阶梯投影；现有 digest diff 不能回答“最后有效阶段/悬空边/伪成功”。
2. TurnEvidence/ReplayTrace 只有 request/session/turn/run，goal/operation/correlation 缺席时必须诊断，不能换名伪造；chain 只有规范阶段序，不能还原真实时序。
3. 既有场景没有统一四模式覆盖声明，静态 replay、真实 dispatch、真实浏览器和 live services 的结论容易在汇总时混淆。
4. `context_bytes_proxy` 以 `len(str(arguments))` 计字符，测试也按同公式自证；它不是 UTF-8 bytes，需独立中文样例锁。
5. 生产 recovery 接线缺 tool_call/capability 归因；跨会话 owner 没有可信全局解析器，保持 continuation fail-closed 与 TDS 未确证的边界，不新建 registry。
6. 现有 perf 预算只有四个传输/数据/首 token 面；G07 需独立环境、样本、重复数、p50/p95、CPU/RSS/bytes 的原始报告。

### 执行模式边界

- `offline_replay`：真实确定性下游与 evaluator，但工具 provider 是 canned，不是生产 GIS 服务。
- `live_dispatch_stub_llm`：真实 ToolDispatchService/GIS fixture；stub 只替模型/外部 provider，必须逐层标明。
- `browser_renderer`：真实本地 MapLibre 观察才可证明 render；合成截图/FakeVLM 仅证裁判接线。
- `live_services_opt_in`：外部数据/模型/服务；默认不运行，不作为本地 PR 通过条件。

## M9 最终在途接触点（复审时刷新）

- `origin/master` 再次 fetch 仍为 `585812ee4e2d75612f2b124e6fe6587af3deca5c`，无需 rebase。
- #2013（G03）也修改 `app/services/tool_dispatch_service.py`，其新增位于数据资格闸（约 671 行）和结果字段（约 1609 行）；本线仅复用既有 recovery payload 参数（约 1046 行），代码块和语义均不冲突。
- #2012（G01）修改 `scripts/pi_doctor.py` 与 `tests/integration/test_pi_stub_llm_journeys.py`，本线未改这些文件；未来 master 合并后需重跑 Pi doctor/stub 验证，不提前声称其新测试通过。
- #2011（G02）与本线无重叠文件。Dependabot 的 pyproject 依赖变更与本线 mypy whitelist 不同块，需在最终 merge 时正常冲突审查。
- 不读取或等待这些 PR 的线上 CI；其失败/成功状态不作为本线证据。
