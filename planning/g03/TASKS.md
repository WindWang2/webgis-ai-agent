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
- [ ] scripts/check_tool_capability_conformance.py(CLI)
- [ ] catalog_discovery_tools.py 增 catalog_conformance 工具
- [ ] FALLBACK_CODE_XWALK + test_fallback_code_xwalk.py
- [ ] test_catalog_conformance_tool.py(经 registry.dispatch 真实调用)
- [ ] 受影响既有测试回归 → commit

## W3 E2E + 可靠性(子代理 B)
- [ ] test_e2e_poi_scenario.py(map_intent→POI→热力/统计,真实 ToolDispatchService)
- [ ] test_e2e_raster_ndvi_scenario.py(raster→NDVI→MapSpec)
- [ ] test_scale_synthetic.py(1000 工具/千次审计)
- [ ] test_live_registry_audit.py(活 registry 零 fail 基线 + 发现清偿)

## W4 审计发现清偿 + 性能对照
- [ ] 活 registry 审计发现逐条 done/deferred(修复或披露)
- [ ] surface 字节/延迟无退化证据

## W5 独立审查(子代理 C)
- [ ] 第一轮逐文件 review(P0-P3 定位)
- [ ] 修复 → 复审(同一审查员)
- [ ] REVIEW.md 落盘

## W6 终报 + PR
- [ ] docs/dev/tool-conformance.md
- [ ] PR.md(URL + 基线 SHA + 证据)
- [ ] gh pr create(不合并)
