# G41 LOOP LEDGER（执行账本）

base = `1276692737a8cf58d0604fb40ae0e70fc61779a2`（origin/master，2026-11-14 复核）
branch = `zcode/g41-bivariate-reliability-authoring-20261114T151807Z`
worktree = `/home/kevin/projects/webgis/webgis-worktrees/g41-bivariate-reliability-authoring-20261114T151807Z`
TASK_ID = `G41-BIVARIATE-RELIABILITY-AUTHORING`

## 预算与宿主

- 宿主**未暴露** ZCode 原生 `/goal` 或 `/goal-loop` 工具，也未提供 Goal usage budget 配置接口。
  因此无法配置「至少 320,000,000 tokens」的额度，**如实记录：可用额度未暴露/不可配置**。
  本任务按「达到验收即结束、不为消耗预算制造无关工作」执行，用本账本做上下文恢复与逐轮核查。
- 未等待线上 CI、未轮询 checks、未建 watcher（`CLAUDE.md:41` 与任务 A 节一致）。
- subagent 配额：本次用了 2 个只读 Explore（后端调用链 / 前端与导出链），保留第 3 个槽位给**未参与实现的独立 reviewer**。无递归派生、无旁路 swarm。
- 重阶段锁：`/home/kevin/projects/webgis/.bare/webgis-heavy-stage.lock`，媒体编码/导出回读用 `flock` 串行；pytest 单进程、`-j2` 上限、Vitest `--maxWorkers=2`（实测 vitest 4.1.11 **不支持** `--minWorkers`，已按实际版本修正）、OMP/OPENBLAS/MKL=1。

## 轮次

| 轮 | 用户闭环 / 目标 | 追到的生产调用链 | 增量 | 验证证据 | 修正 |
|---|---|---|---|---|---|
| R1 | 确认基线与去重 | remote/defaultBranch/open PR=0 / open issues=0 / #2094–#2116 reviews 空 / `planning/G29..G35/REVIEW.md`；Pi pin `b4afdddf` | `BASELINE.md`, `DEDUP_MATRIX.md`, `PLAN.md` | `git rev-parse origin/master`、`gh pr/issue list`、`git worktree list` | 主工作区有未提交内容 → 只用新 worktree，不清空 |
| R2 | 强版本成对统计 + 两类计划 | `session_data.py:436,505-507,934-944,621-651`；`distributed_lock.py`；`bivariate.py:82-89` vs `thematic_authoring/stats.py:45-59` | 新模块 `contract/snapshot/stats/classify/reliability/legend/expression/cache/service/errors` | 59 单元（首轮 51/59） | 手算 oracle 期望写错 1 处（仅 A 缺失应为 4）；修正测试而非放宽断言 |
| R3 | UI 与 Pi 共用入口 | `ToolRegistry` + `ToolDispatchService` + `require_owned_session` | 3 工具 + 3 路由 + `main.py`/`tools/__init__.py` 加性接线 | 70 单元全绿 | 发现 `breaks` 错误未落到轴 → 新增 `BivariateSpec._precheck_axis_breaks` 返回 `axis_a.breaks[index]` |
| R4 | 完整纵链真跑 | `apply_gis_mutation` → `MapSpecLifecycleEngine` → `UpsertLayerIntent(source_data)` | 冻结展示副本 + 单笔原子 source+layer 提交 | e2e 冒烟：inspect→preview→commit→读回 layer/paint/derived ref | 发现 `_preserve_durable_presentation` 会把上一模式 reliability `match` 表达式**重新注入**新层；修复：未启用 opacity 时显式写中性 `opacity=1.0`（B30 真实交互坑） |
| R5 | 前端作者界面 | `layer-style-panel.tsx` / `thematic-authoring-panel.tsx` 同款 epoch + session-cursor | 新面板 + contracts/api + i18n(zh/en) + 挂载 | 11 vitest | 「参数改变」改为保留旧预览并标记 stale（Apply 禁用），模式切换才丢弃 |
| R6 | 同一图例进 4 个输出面 | `legends.tsx` / `export-chrome.ts` / `vector-svg-export.ts` / `svg_marginalia.py` + `print_layout_authoring/legend.py` | canonical 适配器；SVG 不再省略矩阵 | 11 vitest + 4 publication readback | `legendBottom` 声明顺序错误 → 上移；typecheck 2 处类型错误已修；旧 bivariate 无 cells 仍如实发降级诊断 |
| R7 | 真实出版回读 | G34 `read_legend_content→layout_legend→render_legend_box_authored` + 生产 `compile_mapspec_to_svg_detailed` | `tests/integration/test_g41_publication_readback.py` | SVG/PNG/PDF 三格式都真实产出并独立回读 | PDF 页对象在压缩流里 → 改解压后计数；并发跑导致一次 SIGKILL → 单独串行重跑通过 |
| R8 | 真实 Pi 两模式旅程 | 固定 pin `b4afdddf` / `v1.5.2` 的 RPC entry + stub LLM + uvicorn(app) + `/chat/stream` + dispatch | `tests/integration/test_pi_g41_bivariate_reliability_journey.py` | 见 TEST_MATRIX | 首轮漏 `import json` → 已修并重跑 |

## 关键决定（不返工）

1. 不新建第二套框架；`bivariate.py` / `thematic_authoring` / `uncertainty.py` / `apply_gis_mutation` / G34 都是复用基线。
2. 强版本保证 = 权威单调 `content_revision` 前后核对 + 同会话锁 + 单次 `get()` 全量深拷贝；**不**用前后 fingerprint 相同做保证（ABA 由版本捕获）。descriptor 缺失/rev=0 一律 typed 拒绝。
3. 旧 bivariate 图零改写；新 `boundary_rule` 显式字段，缺省 `upper_inclusive`；`legacy_lower_inclusive` 可显式选用。
4. 派生字段独立命名（`__g41_pair_class` / `__g41_rel_bucket`）；源已有 `__biv_class` 只记录血缘；同名派生字段冲突拒绝。
5. 可靠性：只有估计值无误差事实 → `NO_UNCERTAINTY_EVIDENCE` + 可行下一步；`author_declared` 必须说明来源；缺失/无效/越界独立桶；不合成置信度。
6. 第三通道（两主变量 + 不确定性）**明确不支持**并写入图例 `disclosures`（读者负荷）。
7. 前端 `preview_digest` 必填；任何设置改变使旧预览不可提交；epoch + 请求 generation 隔离迟到响应。
8. 固定 pin 的 Pi 运行时：本工作树 `vendor/pi` 起初未初始化。按资源纪律从**同仓同 pin**（`g33` worktree，HEAD=`b4afdddf`，`describe=v1.5.2-0-gb4afdddf4`）复制已构建产物到本 worktree 的未初始化 gitlink 目录（git 不跟踪其内容，工作区 diff 干净），`pi_vendor_identity().matches_expected_pin is True`。**未改 pin、未改 .gitmodules、未改部署契约**。

## 已验证 / 未完成

见 `TEST_MATRIX.md`。上下文恢复顺序：本文件 → `git diff` → `TEST_MATRIX.md` → `REVIEW.md`。
