# Open PR Overlap Matrix（recon 2026-10-16，base fd1acd7c）

| PR | 标题方向 | 与本方向文件重叠 | 语义重叠 | 避让策略 |
|---|---|---|---|---|
| #1600 fix/deep-review-2026-10-08 | 并发死锁/角色降级/数据丢失/资源界/SSE 契约/CI 部署 | `app/services/chat/execution_engine.py`（本分支不改）；`app/agent_pi_bridge.py`（本分支 additive 事件发射区段 ~2716-2771，其改动在 settle/lease 区段，冲突概率低） | 无澄清闭环内容 | 只做 additive 事件发射，不碰其修复面；若合并后冲突，rebase 时以 additive 块为单位重放 |
| #1589 fix/deep-review-2026-10-04 | 安全/Agent/GIS/制图/前端/CI 多路修复 | `execution_engine.py`、`pi_post_dispatch.py`（本分支可能小幅读 refusal 面但不改其修复逻辑） | 无 | 同上 |
| #1588 test/issue-1552-agent-swarm | coverage 补测（agent_swarm/storymap/evaluation） | tests/unit/ 不同目录 | 无 | 新测试独立文件 |
| #1587 refactor/issue-1548 | geo-analysis _scaffold 收拢 | app/lib/geo_analysis/ | 无 | 不触碰 |
| #1592 dependabot sqlalchemy | 依赖升级 | 无 | 无 | 不触碰 |

结论：无重复施工。若开发期间 #1600/#1589 合并 → `git fetch && git rebase origin/master`，additive 区段冲突按语义保留双方。
