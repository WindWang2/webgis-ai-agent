# REVIEW_R2 — 复查（R1 修复后）

- 复查对象：origin/master(f34cb69dd)...HEAD（含 R1 修复 commit 3cebaaae6）
- 复查人：主 agent（R1 由独立 agent 完成；本轮为关闭证据复查 + 关键旅程重跑 + 冲突复检）

## 1. R1 关闭证据复查（17/17）

- P2×4：全部以新增回归测试关闭（见 REVIEW_R1.md 处置表；test_publication_r1_fixes.py 7 项 +
  Pi 旅程 2 项归属负例）。逐条核对代码：executor 发布门在 CAS 前（executor.py 发布门段）；
  reader spec 按层重写（reader.py build_reader_map_spec）；base 挂靠归属（service.py create_draft）；
  validate 双链归属（publication_tools.py _validate）。
- P3×13：P3-5/6/7/8/9/10/12/13/14/15/16/17 代码关闭并有测试或 lint 证据；P3-11 按 D11 决策记录。
- 遗留：无未处置 R1 发现。

## 2. 关键旅程重跑（修复后代码）

- journey_backend.py（真实 uvicorn+sqlite+HTTP，含新 reader spec 形状断言）→ ok:true
- 浏览器（生产 next build/start + Playwright chromium）：
  桌面（title/map_canvas/筛选 5 行/分母 12）✓；无 WebGL（横幅 + 表格 12 行）✓
  截图：r2-desktop.png / r2-nowebgl.png（/tmp/pub-journey/）
- Pi 链（registry.dispatch）旅程 4 项 ✓

## 3. 门禁终态

| 门 | 结果 |
|---|---|
| ruff（本线全部新/改文件） | clean |
| ruff 全仓 | 26 errors 全部位于基线既有文件（与本线 diff 零交集，comm 核验） |
| mypy 棘轮 | Success: no issues found in 38 source files |
| pytest（publication 39 + cartography 6 + CI 门契约 19 + scope-matrix 8） | 71 passed, 1 failed = 基线既有 scope-matrix（master 原样） |
| vitest | 18 passed |
| tsc（app config） | clean（基线 live-spec.test.ts 错同 master） |
| eslint（本线文件 --max-warnings 0） | clean（全仓 1 error 为基线 rasterize.test.ts） |
| 迁移 | 单 head g5202（锚 aa356ce2fcdc）；upgrade/downgrade 实测 |
| git diff --check | clean |
| 前端 next build | 成功（/p/[code] 在列） |

## 4. 冲突复检（PR 前）

- origin/master 未移动（f34cb69dd）；`merge-base --is-ancestor` 通过，无需 rebase。
- 开放 PR：#2290（01 线）/ #2291（03 线）/ #2292（02 线）/ #2293（05 线）。
  交集仅加性接线点：app/main.py（include_router）、app/tools/__init__.py（_TOOL_MODULES）、
  frontend/lib/i18n/messages.ts（namespace 注册）、（#2292 另有 map-studio-tab 子 tab）。
  全部为相邻行加性变更，合并序无关紧要；后合并者做平凡解决即可。

## 结论

R1 无 P0/P1；4×P2 与可行动 P3 全部修复并回归；关键旅程重跑全绿；门禁终态达标（基线既有
失败已归档 BASELINE.md 并与 master 原样对照）。可交付单 PR。
