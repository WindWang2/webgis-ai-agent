# G03-GEODATA-READINESS — 执行计划与 Oracle

目标：把 Explorer → data_fabric → ingest → dataset semantics → qualification → preflight → tool/mapspec 贯通为**可追踪的数据准备与资格判定链**。扩展既有底座（ADR-0265 语义契约、五态资格、16 类 adapter、IngestPipeline），**禁止第二事实源**。

## 里程碑

- **M0（完成）**：实时审计 → BASELINE.md；worktree 建立；冒烟通过。
- **M1**：characterization —— 对断链 1/2/3/5/7/8 各写一个**当前行为失败测试**（红灯先行），固定既有词表边界。
- **M2**：ADR-0286（数据资格判定链贯通）+ 接口契约 + 迁移/回滚 + feature flag。
- **M3–M5**：GD-01…GD-10 分三批实现（见下）。
- **M6–M7**：真实业务旅程（成都小学点位 + 人口率值图，本地 fixture 零模拟）+ 压力/异常/安全演练。
- **M8**：子代理 B 独立冷读 review `git diff origin/master...HEAD` → 修复 → 复审。
- **M9**：fetch 最新 master → rebase 检查 → 全量相关回归 → push → `gh pr create`。

## 实现批次

**批次一（M3）：语义身份与资格收口**
- GD-01：descriptor 维度级 provenance 三态（observed/inferred/unknown）——以既有 role_confidence/kind_confidence 语义为底，顶层加 `provenance` 映射；license/attribution/updated_at 从 ingest 正向路径流入（断链 8）；explorer rows → FC 桥接 → IngestPipeline → descriptor（断链 1，validate_stage 末端一跳 + builder 新入口）。
- GD-03：readiness verdict 映射文档（验收词表 ↔ 五态）+ 有限自动修复执行闭环验证（断链 4 的执行面）。
- GD-08（前半）：tool_dispatch_gates 薄资格闸（断链 2）——有语义记录且 stale/unknown → 阻断并给出可解释原因；feature flag 默认开、可关。

**批次二（M4）：多源接入与地理范围守卫**
- GD-02：远程 GeoJSON URL adapter（预算 + SSRF 复用 + Content-Type 校验，断链 3）；CSV+坐标 → FC 转换进 IngestPipeline（断链 7）。
- GD-05：bbox-地名一致性检查（preflight 增 task scope 比对，断链 5）；切城市失效（role binding 结果带 scope 指纹，viewport/city 变化 → 重判定，断链 6）；零记录诚实报告。
- GD-09（前半）：恶意 GeoJSON schema 校验器（深度/NaN/Inf/坐标域/属性文本），进 IngestPipeline validate 段。

**批次三（M5）：大数据、生命周期、观测**
- GD-06：大文件/大结果的有检查点物化（复用 streaming 批 + 新 checkpoint 状态），50/5000/百万要素有界资源证据。
- GD-07：license 访问策略消费 + 版本变化 → 既有 plan/mapspec 失效链验证补测试。
- GD-10：preflight 观测（时长/峰值内存/网络调用/缓存命中）+ `INGEST_PERFORMANCE.md` 本地基准。

**贯穿**：GD-04 字段映射核对（重名/冲突/单位不明 fail-closed 已有底座，补缺口测试）；三份文档 `DATA_READINESS_CONTRACT.md`、`SOURCE_QUALIFICATION_MATRIX.md`、`INGEST_PERFORMANCE.md`。

## Oracle 表（验收即关闭，不凑数）

| # | Oracle | 验证方式 |
|---|---|---|
| O1 | 恶意/缺失/不一致输入（深结构、NaN/Inf、bbox-地名不符、失效链接、无 license、假 Content-Type）**一律不产生 eligible/ready** | GD-09 安全演练测试全绿 |
| O2 | 每个身份证维度可标注 observed/inferred/unknown，未知不虚构 | GD-01 单测 + 遍历 descriptor 字段 |
| O3 | explorer 数据可被 role binding 找到（非 not_found）并产出资格 | 断链 1 集成测试 |
| O4 | 裸工具分发遇 stale/unknown 资格被阻断且原因可解释；flag 可关 | dispatch gate 单测 + flag 关闭对照 |
| O5 | 远程 GeoJSON URL 走预算+SSRF 防护完成探测/预览/受控物化 | adapter 单测 + SSRF 回归 |
| O6 | CSV+坐标上传产出 descriptor 并可绑定角色 | 断链 7 集成测试 |
| O7 | 切城市后旧 overlay ref 被判需重资格，零记录场景报告置信边界不造数据 | 断链 5/6 测试 |
| O8 | 50/5000/百万要素（fixture 生成）物化有界（内存/时间/调用次数记录在案） | GD-10 本地基准 |
| O9 | 至少一个**零模拟**旅程：本地输入 → 资格 → 真实算法 → 图层 ref（成都小学点位 / 人口率值图） | M6 端到端测试 |
| O10 | 独立 review 无未处理 P0/P1；真实 PR 创建成功 | M8/M9 记录 |

## 红线

- 最多 3 subagents；编译 ≤ `-j4`；不等待线上 CI；不 merge PR。
- 不新建第二工具注册中心/MapSpec/会话持久化/渲染器；扩展既有契约。
- 异常类型化、可解释；外网失败 ≠ 分析成功；cached ≠ 现时事实（须带 staleness 披露）。
