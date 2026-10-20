# H01 LOOP_STATE

- base_sha: 0415ad8d3a03096ab405ece643753bc93974a760 (origin/master)
- branch: feat/h01-gis-harness-capability-foundation-vnext
- worktree: /home/kevin/project/webgis-ai-agent-h01-harness
- completed waves: Wave 0（Phase 0 侦察 + 规划文档冻结）
- delta files: planning/h01-harness-capability-foundation/{RECON,BASELINE,PLAN,CONTRACT_MATRIX,LOOP_STATE,WORKLOG}.md
- test commands & exit codes: 基线见 BASELINE.md（全部 exit=0）
- known baseline failures: 全量执行 #1753 存量挂起（不跑全量）；#1707 p4_test 不收集（规避）
- open concerns: EXTERNAL_UNAVAILABLE dispatch 不拒（登记不修）；registry_validation 双轨（登记）；durable_worker 探针默认 OFF（登记）
- next exact step: Wave 1 —— 写 WP1 失败测试（resultAbi evidence 键契约 / provider_unavailable 分类 / 桥拒绝机器码），确认红
- budget: 会话内持续；子代理并发 ≤3
- evidence links: RECON.md §2/§7
