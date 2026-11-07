# G04-CARTOGRAPHY-COMPOSER QA 报告

日期:2026-11-08 · branch `zcode/goal-intelligent-carto-composer-20261107`(6 commits,35 files,+2650/−207)

## 1. 测试命令与结果(可复现)

| 门 | 命令 | 结果(第 1 轮) | 结果(第 2 轮) |
|---|---|---|---|
| 后端制图全量 | `python -m pytest tests/cartography --deselect tests/cartography/test_catalog_docs.py::test_catalog_docs_fresh` | **3333 passed**/2 skipped/3 xfailed | **3333 passed**(逐项一致) |
| 后端 harness+相关 | `python -m pytest tests/unit/gis_harness tests/unit/test_pi_gis_usability_v2.py tests/unit/test_mapspec_svg_chrome_layout.py tests/unit/agent_swarm tests/test_export_phase3.py tests/test_export_frames_v5.py tests/unit/gis/test_platform_capabilities.py` | 1 failed(断言更新后重跑)→ **3828 passed** | 并入上轮 |
| 前端全量 | `cd frontend && pnpm vitest run` | **534 files / 5003 passed** + 1 expected-fail | (依赖隔离修复后一次全绿) |
| 前端类型 | `pnpm typecheck` | 绿(tsc 双配置) | — |
| 前端 lint | `pnpm eslint <7 个改动文件> --max-warnings 0` | 0 告警 | — |
| Ruff | `ruff check <19 个改动 .py>` | 全绿 | — |
| Mypy | `mypy app/tools/cartography.py …` | 改动文件 = 基线 5 个既有错误(无新增) | — |
| 黄金案例 | `pytest tests/cartography/test_map_product_goldens_g04.py -v` | 7/7 | 7/7 |

解释器:`/home/kevin/projects/webgis/webgis-ai-agent/.venv`(Python 3.13.17);前端 pnpm 10.34.9(worktree 自有 node_modules,master 锁)。

## 2. 归因记录(非本方向引入的失败)

1. `tests/cartography/test_catalog_docs.py::test_catalog_docs_fresh` —— **基线已坏**(pristine origin/master 复现:theme-palette-catalog.md 漂移;stash 验证法)。属全仓文档生成债,避让。
2. 前端 `test/pdf-dependency-security.test.ts` 15 失败 —— **环境混杂**(符号链接的主仓 node_modules 携旧分支 dompurify 3.4.12,master 锁要 3.4.17)。worktree 自装依赖后 20/20 绿。已修环境,记录成因。

## 3. Oracle 核验表(对照 PLAN.md O1–O14)

| # | Oracle | 状态 | 证据 |
|---|---|---|---|
| O1 | #1999 打印档真实求解 | ✅ | test_export_constraint_chain_g04.py(5 用例:A4/A3×横竖/无会话诚实/批量);前端 pageProfile 推导 + apply 测试 |
| O2 | #2000 默认导出字节稳定 | ✅ | cartographer 无标记断言 + SVG marker 无差别断言 + 指纹稳定断言 |
| O3 | #2002 族锁 | ✅ | zz-chart/zz-chart__2 过饱和场景(实测鉴别力) |
| O4 | #2003 指北针旋转 | ✅ | rotate(-45)/rotate(-30)/缺省无 transform |
| O5 | CM-01 单一投影器 | ✅ | test_selection_context_v1.py 11 用例(双侧一致锁) |
| O6 | CM-02 契约机读闭包 | ✅ | test_template_contract_closure_v1.py 5 用例;168 处 cardinality 归一 |
| O7 | 图例/分类同步 | ✅ | test_render_export_parity_g04.py(重分类不留旧)+ 单位贯通 |
| O8 | 跨格式 parity | ✅ | 同上 + RENDER_EXPORT_PARITY.md 矩阵 |
| O9 | CM-08 改色不重跑 | ✅ | test_incremental_semantics_g04.py(图层/源逐字节不变) |
| O10 | CM-09 回执链 | ✅ | canvas sidecar(constraint_layout_evaluated 持久化)+ 矢量 result 报告 |
| O11 | CM-10 黄金案例 | ✅ | 7 案例(含 empty-state)真实链 |
| O12 | 三文档 | ✅ | docs/cartography/ 三份(关键声明可核) |
| O13 | 独立 review | 见 REVIEW.md | 子代理 B 冷读(本文件完成后出结论) |
| O14 | 真实 PR | 见 PR_HANDOFF.md | 推送后填 URL |

## 4. 覆盖的最少 Oracle 清单(任务书「最少覆盖」逐项)

组件改色不重跑 GIS ✅(O9)· 图例锁 ✅(O3)· 旋转指北针 ✅(O4)· A4 长中文标题 ✅(#1975 既有 36 golden 保持绿)· 空数据 ✅(案例 0)· 异常字段 ✅(变量粗判/未知几何 Value ERROR/typed 拒绝)· 跨比例尺 ✅(既有 multiscale+representation parity 保持绿,未回退)· 图例与实际分类同步 ✅(O7)· 实际导出 receipt ✅(O10)。

## 5. 行为变化披露(评审者/合并者须知)

1. **SVG chrome 挪位门变化**:master 上"无全局标记 → 组件非历史 position 被忽略";现在非历史锚点(用户挪动/求解写入)导出遵循锚点(user-wins 修正)。锁旧行为的 1 个测试已按新语义更新(意图保持:默认坐标路径不因标记改变)。
2. **A3 入求解器词表**:a3_landscape/portrait 从 profile_unsupported 变 evaluated(既有测试断言更新)。
3. **variable_kind 粗判**:数值字段在场 → continuous(原 design 面恒缺省 continuous,planner 面"任一文本→categorical");name+count 数据不再降级。
4. **图例单位行**:legend_spec.unit 在场时 SVG 图例多一行「单位:…」(+13px 高;缺席字节不变)。
5. **export 工具转 async + 新 session_id 形参**(dispatch 自动注入;无直接同步调用方,grep 验证)。

## 6. 未验证边界(诚实披露)

- 线上 CI 未等待未声明(仓库纪律);本地验证为本 PR 的质量门。
- 全栈浏览器 E2E 未跑(延续 #1975 NOT_RUN 项;画布链应用点经纯函数单测+引擎测试锁定)。
- Atlas 多页与 constraint 报告的交互(报告为单帧求解;多帧导出逐张同 zones)——后续增强面。
