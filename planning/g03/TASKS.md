# G03 TASKS(checkpoint 台账;每完成一项打勾并记 commit)

## W0 基线
- [x] 启动侦察:工具/skills/gh/zcode 检查(git fetch 直连超时,gh API 可用且 SHA 一致 — DECISIONS D9)
- [x] origin/master 基线:b1b2ec23(gh API 复核)
- [x] open PR/issue 收集(#1758 OPEN 429 文件 / #1701 / #1698)
- [x] RECON_DATA.md(子代理 A,file:line 证据)
- [x] RECON.md / PR_OVERLAP_MATRIX.md / PLAN.md / DECISIONS.md / TEST_MATRIX.md
- [x] worktree + 分支 feat/g03-gis-tool-conformance-vnext
- [x] W0 commit (312f2631)

## W1 引擎(主 Agent)
- [x] app/lib/gis/tool_capability_conformance.py(8 面引擎 + 指纹 + live 收集器)
- [x] tests/unit/gis_tool_conformance/test_engine_faces.py(44 passed)
- [x] tests/unit/gis_tool_conformance/test_engine_determinism.py(8 passed)
- [x] ruff 通过;test_fallback_code_xwalk.py 提前完成(3 passed)→ 本 commit

## W2 接线(主 Agent)
- [x] scripts/check_tool_capability_conformance.py(CLI,W5 入库)
- [x] catalog_discovery_tools.py 增 catalog_conformance 工具
- [x] FALLBACK_CODE_XWALK + test_fallback_code_xwalk.py
- [x] test_catalog_conformance_tool.py(经 registry.dispatch 真实调用)
- [x] 受影响既有测试回归 → commit(b5f1e3ab)

## W3 E2E + 可靠性(子代理 B)
- [x] test_e2e_poi_scenario.py(9 passed)
- [x] test_e2e_raster_ndvi_scenario.py(8 passed)
- [x] test_scale_synthetic.py(7 passed)
- [x] test_live_registry_audit.py(棘轮:0 fail/0 分叉/披露集 4)

## W4 审计发现清偿 + 性能对照
- [x] 审计发现清偿:契约分叉 7→0、ref 缺口 41→4(披露);query_local_poi 凭据漂移披露于 PR
- [x] 审计性能 0.16s/357 工具;catalog_conformance 载荷 <4KB 有测试钉

## W5 独立审查(子代理 C)
- [x] 第一轮 review(P0×1+P1×2+P2×3+P3×4)
- [x] 修复(4200f29e)→ 复审改机械复验(SendMessage 缺席,如实披露,见 REVIEW.md)
- [x] REVIEW.md 落盘(两轮台账)

## W6 终报 + PR
- [x] docs/dev/tool-conformance.md(4182a93a)
- [ ] PR.md(URL + 基线 SHA + 证据)
- [ ] gh pr create(不合并)
