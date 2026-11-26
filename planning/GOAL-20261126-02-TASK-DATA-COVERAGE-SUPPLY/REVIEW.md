# REVIEW — GOAL-20261126-02 独立评审记录

## R1（三方并行，2026-11-27）

| 评审 | 范围 | 状态 |
|---|---|---|
| A 科学语义 | requirement/coverage/supply_plan + 测试 oracle 独立性 | 进行中 |
| B 安全权限 | discovery store/工具面/REST 归属/幂等/竞态/资源 | 进行中 |
| C 旅程接线 | 工具描述质量/前端闭环/i18n/快照披露/纪律 | 进行中 |

## R1 发现与处置

### A（科学）：1×P0 + 3×P1 + 8×P2 + 10×P3
- P0-1 跨线 geometry 整体平移错侧 → 修复（逐顶点帧归一 + 手算 oracle ×2）
- P1-1 期次缺口不设门 → 修复（temporal none → 替代方案+强制确认）
- P1-2 执行丢投影静默 500 行 → 修复（QuerySpec 投影 + 50k 行帽 + 回执披露）
- P1-3 perf 假规模（192 全局截断）→ 修复（截断移除+溢出披露；perf 改真实口径见 TEST_EVIDENCE）
- P2-1/2/3/4/5/6/7/8 → 全部修复（见 commit R1 修复批次说明）
- P3-1/2/3/4/8/9 → 修复（死代码/恒真空操作/completeness 注释/typo/纬度校验/弱断言）；P3-5 误差界、P3-6 预算未知成本披露 → P3 记录后续
### B（安全）：5×P1（无 P0）+ 4×P2 + 4×P3
- P1-1 fabric id 空间错位 → 修复（sync id 约定只读解析 + sqlite 端到端解析/租户测试）
- P1-2 绕过租户守卫 → 修复（_tenant_owns_source 同语义布尔化，404 不泄漏）
- P1-3 自我批准声明失实 → 修复（工具描述如实声明信任边界 = 既有 materialize_dataset 同级；签名封套核验列为后续线）
- P1-4 重复执行重复付费 → 修复（completed/partial 跳过 + executing 抢占 + skipped 回执）
- P1-5 全局 LRU 驱逐 → 修复（按会话分桶 10 + 200 会话）
- P2-1 取消通路 → P2 记录（materialize cancel_token 接线列为后续；record_cancel 服务层已可用）
- P2-2 鉴权负例缺失 → 修复（补未认证/他人会话用例）
- P2-3 异常文本无界 → 修复（[:300]）；P2-4 同步 ORM 事件循环 → P2 记录（与 #565 模式一致的已知欠账，列为后续）
- P3-1 network=True → 修复；P3-3 有界字典清理 → 修复
### C（旅程）：2×P1 + 5×P2 + 7×P3
- P1-1 = A-P1-2（同修复）
- P1-2 裸 switch 欺骗性放行 → 修复（逐候选展开 + 类型化拒绝 + UI radio 值=完整决策串）
- P2-1 参数静默丢弃 + typo → 修复（minx>maxx；解析失败保持 None 但 scope 侧类型化拒绝覆盖坏形状——bbox 形状错经 TaskScope 抛 REQUIREMENT_SCOPE_INVALID；字符串解析端 typed 化列入后续打磨）
- P2-2 search_more 死按钮 → 修复（入词表；执行面 typed 拒绝+指引）
- P2-3 鉴权负例 → 同 B-P2-2 修复
- P2-4 面板竞态 → 修复（函数式收集 + 失败回滚）
- P2-5 快照拆分披露 → PR 正文披露（数字可复现 + 干净 master 验证；git 拆分列为可选打磨）
- P3-1/2/3/7 → 修复（execution 服务层、disclosures 上屏、状态本地化、会话重置）；P3-4/6 → 修复/披露（not_serving 计数）

## R2（final review-fix-review）

（待 R1 修复后执行：完整 diff 复核 + 反向验证 + 关键用例重跑）
