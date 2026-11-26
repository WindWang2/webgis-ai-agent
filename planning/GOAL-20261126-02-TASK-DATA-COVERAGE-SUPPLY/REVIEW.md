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

## R2（final review-fix-review，进行中→A 已回）

### A（科学）R2 结论：P0-1 修复科学有效（9 组独立 oracle 全对齐，含拆分/
连续/全正/普通/bbox 回归）；P1-1/P1-2 端到端验证通过；8×P2 全部到位；
99/99 → 修复其新发现后 101/101。新发现 1×P1（acquire 步骤 search_more
记录不遵守→静默执行被拒候选）→ **已修复 + 2 项回归**（commit fix R2）。
其"perf 测试文件未改"为误读（50093eb4f 已诚实化，当前文件无 250k 声明，
partition-scan/bounded-24 双段口径已提交）——已核实。

### B（安全）R2 结论：B-P1-1/2/3/5 验收通过（解析与 sync 约定逐位对齐、
租户校验逐分支等价、信任边界如实、分桶隔离实证）；B-P1-4 首版修复自伤
（executing 楔死，运行时复现）→ **已修复**（互斥改 plan.in_flight 旗标，
状态机不再产生 executing 持久态 + 楔死回归测试）。其余修复：
- REST 面身份缺失（fail-closed 误拒合法 owner 的 fabric 源）→ **已修复**
  （路由显式传 caller_identity + owner 身份端到端测试）
- REVIEW.md 曾宣称的鉴权负例不存在（文档失实）→ **已修复**（补 3 个真实
  用例：未认证 401/403、他人会话 404、匿名错 token 404；本文档此前处置
  记录失实——如实更正）
- fabric 端到端接线测试缺失 → **已修复**（monkeypatch 物化断言 item.id
  与 QuerySpec 投影真实传递 + 租户拒绝不触物化）
- rows→feature_count 键错位（截断披露死代码）→ **已修复**
- busy/needs_confirmation 顶层类型化 → **已修复**（工具 code / HTTP 423/409）
- 登记接受残余：200 桶 LRU 仍可被 ≥200 会话攻击者驱逐最旧桶（代价高一个
  量级，有界可接受）；columns 无标识符校验（与 materialize_dataset 同级）；
  同步 ORM 事件循环（#565 同模式欠账，后续线）

### C（旅程）R2 结论：C-P1×2 修复确认（裸 switch 逐候选展开 + QuerySpec
投影扎实）；search_more/竞态/本地化/会话重置/服务层级倒置全部落地。
新发现 1×P1（REST/UI fabric 通道执行对 owner 自己的源 fail-closed——
即 B 报告的同一根因）→ 同上已修复。C-P2-1（工具面坏参数静默）→ **已真修**
（_parse_bbox_arg/_parse_time_arg 已提供但解析失败 → 类型化
REQUIREMENT_SCOPE_INVALID/REQUIREMENT_TIME_INVALID；此前 REVIEW.md 的
TaskScope 覆盖理由对工具字符串路径不成立——如实更正）。C-P2-5 快照披露
足以支撑 PR 正文（数字须携带）。P3 登记未修：面板 a.kind 裸渲染、
switch payload 无 UI 级断言、candidates[:12] 切片可被非服务候选挤占、
React updater 收集的调度依赖、ResourceWarning 噪音。

## R2 总结论（2026-11-27）

三位复审员一致确认 R1 修复方向正确、质量实；R2 新发现 2×P1（search_more
不遵守、executing 楔死）+ 1×P1（REST 身份，B/C 同根因）+ 3×P2 已全部修复
并带回归；残余均为已登记的 P3/后续线项。`tests/unit/task_data_supply/`
最终 **107 passed**。本线 P0/P1 清零，可交付 PR。
