# G34 PR HANDOFF — 最终交付台账

状态：**已推送并创建 PR**。

| 项 | 值 |
|---|---|
| TASK_ID | `G34-PRINT-LAYOUT-AUTHORING` |
| branch | `zcode/g34-print-layout-authoring-20261112T213200Z` |
| worktree | `/home/kevin/projects/webgis/webgis-worktrees/g34-print-layout-authoring-20261112T213200Z` |
| base SHA | `ca7b3ec273f2b43336852951e2292a416b2238e6`（PR 前重新 fetch 核对） |
| head SHA | `ae5aea1d2aad374de00cd9638efae05c59696828` |
| PR URL | https://github.com/WindWang2/webgis-ai-agent/pull/2096 |
| 是否 Draft | 否（核心验收已本地通过；未跑项在正文如实标注） |
| 线上 CI | 不等待、不宣称已绿 |
| 漂移核对 | PR 前 fetch：`origin/master` 仍为 `ca7b3ec27`，与 base 一致，无漂移 |

## 交付内容

**能力**：在真实毫米纸面上编排出版页——纸张/组件框/字体/文字框/对齐分组/模板/图册页序与页级覆写；预览、Pi、保存重开、PDF/SVG/PNG 共用同一份作者决定。

**新增**（约 3.9k 行）
- `app/lib/cartography/print_layout_schema.py` — `print_layout.v1`，未知版本/未知字段显式拒绝，硬帽（32 组件 / 20 页 / 2KB 文本 / 8 模板）
- `app/services/print_layout_authoring/` — units / fonts / legend / template / operations / pages / validate / compile / html / service
- `app/tools/print_layout_tools.py`（7 个 `webgis_print_layout_*`）、`app/api/routes/print_layout.py`
- `frontend/lib/print-layout/*`、`frontend/components/workbench/print-layout-panel.tsx`、Studio 入口、双 locale 文案
- `docs/adr/0290-print-layout-authoring.md`

**改动既有消费点**（真实读取，不是只填 IR）
`mapspec_to_svg.py`（`print_layout` kwarg、`_render_chrome_groups`、contain/crop 投影）、`mapspec_svg_chrome_layout.py`、`svg_marginalia.py`、`publication_export.py`（`:not([font-family])`）、`publication_ir.py`、`atlas_batch/{planner,page_compiler,executor}.py`、`mixed_map_export.py`、`mapspec_schema.py`、mutation（`SetLayoutIntent` / registry / handler）、`tools/__init__.py`、`tools/categories.py`、`main.py`。

## 关键缺陷与修复（评审驱动）

| 缺陷 | 影响 | 状态 |
|---|---|---|
| `apply(initialize=True)` 复用会重读 store 的 `propose` | 首建必失败，功能上线即不可用 | 已修（纯 `_evaluate` 共用） |
| `_spec_revision` 读不存在的 `mapspec["revision"]` 恒得 0 | 任何非零 revision 会话都 SUPERSEDED | 已修（读 session_data `_cartographic_mutation_revision`） |
| `bound`/`current` 双向绑定无 rebase 出口 | 保存一次后永久改不动 | 已修（CAS 为准 + 漂移披露） |
| 锁三处实现不一致；`replace_plan`/`apply_template` 可删锁组件 | 静默数据丢失 | 已修（单一谓词 + 收口不变量） |
| 页级 override 绕过锁 | 逐页出图仍可移动锁组件 | 已修（生效值比对） |
| 缺字按 0.6em 估、兜底族 1.0em | 窄框换行低估约 40% | 已修（兜底族真实 hmtx） |

## 验证（分层，未跑不标绿）

| 层 | 结果 | 证据 |
|---|---|---|
| G34 自有 oracle + 写链路 | **34 passed** | `evidence/pytest_g34_round3.log` |
| Pi 真实子进程全链（node + stub LLM + uvicorn 生产 app + 生产 dispatch） | **1 passed**（62s） | `evidence/pytest_pi_journey.log` |
| cartography 域回归 | 见 `evidence/pytest_cartography_regression3.log` | 唯一失败 `scale_portrayal_cases` 在干净 base `ca7b3ec27` **同样失败**（fixture 缺 `spec` 键），既有问题 |
| 前端类型 | `tsc --noEmit` 0 error | — |
| 前端单测 + i18n | 20 passed（3 files） | — |
| 浏览器 E2E | **未运行**（本机无 Playwright 浏览器二进制） | 不标绿 |

Pi 运行时自检：`scripts/pi_doctor.py` → `no FAIL`（vendor pin ok / bundle ready / extension loads clean 2 tools / 440 tools surface / RPC handshake+settled ok）。

## 未测 / 外部阻塞 / 已知限制

1. **浏览器 E2E 未运行**（无 Playwright 二进制）。UI 仅由 `tsc` + 组件级 reducer 单测覆盖。
2. **大规模性能未探针**（T28）。上限由 schema 硬帽约束。
3. 图例 `continue` 续排盒几何错误、无跨页（`legend.py:293-299`）。
4. 比例符号不计入排版高度、画在框外无标签（`legend.py:234-235`）。
5. 页级模板仍是全局组件替换（`template.py:121-151`）；`reset_overrides` 不恢复模板前基线。
6. 预览/导出仍用全局 `mapspec.view.bounds`，不消费 frame 级 bounds。
7. `submit_export` 不跑 `validate_authoring`（溢出只降级不阻断）。
8. **锁与 undo/模板的历史回放**：`replace_plan` 回放「加锁之前」的计划、或应用含锁组件的旧模板快照，会被锁不变量整笔拒绝。取舍是「宁可拒绝并要求显式解锁，也不静默改锁组件」；后续应加 `override_locks=true` 二次确认。

## 迁移 / 回退

additive：`layout.print_layout` 缺席时所有既有导出路径与字节不变（T01 锁定）；移除该分支即回到旧 publication/atlas 链路。

## 停止条件核对

- [x] 用户和 Pi 均可编辑/验证/保存/重开/撤销/导出真实纸面成果
- [x] 图册页面顺序与数据绑定正确
- [x] 字体/文字/图例完整性可由产物验证
- [x] 所选字段穿过 模型 → IR → 绘制器 的调用图已给出
- [x] 独立评审无阻塞（Round 2 = SHIP-WITH-FIXES，P0 全关，新增 P1 已修）
- [x] 已推送并创建 PR（#2096）
