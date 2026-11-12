# G30 REVIEW — 独立评审记录

## Round 1（reviewer：独立 subagent，未参与实现）

- 评审对象：commit `60dbd94d3`（base `ca7b3ec27` 全量 diff + 真实测试证据）
- 结论：**APPROVE-WITH-FIXES**
- P1×5（全部修复于 commit `fecd2b7a9`）：
  1. 预设跨 org 更新无归属校验（篡改+回显泄露）→ update 分支补 org 检查 + 回归测试
  2. GET /runs/{job_id} 裸 id 读无归属验证（IDOR；前端轮询依赖该洞）→ 行内 session 归属闸（API+Pi 工具+前端三处）+ 回归测试
  3. choropleth rate/density 语义校验但从不计算（把分子总量着成率）→ 执行面枚举诚实收敛 count/percentage，rate/density typed 拒绝并指向规划路径
  4. 预设重用读 64 字符截断视图（静默篡改配置）→ get_preset_raw 原始行 + 70 字符回归
  5. 内联 GeoJSON 无字节上限（DoS 面）→ 8MB 预算 + PAYLOAD_TOO_LARGE（要素超限不再误报形状错误）
- P2×7：pop 会话不符不消费 / busy 先于数据解析 / 显式选择按 turn 清除 / repeated 幂等成功 / 迁移索引对齐 / 取消双发令牌 / 预设 16KB 上限 —— 全部修复；死码词表保留并披露
- 评审同时验证为正确的关键面：入口真实可达、幂等/重试 G23 语义、步间取消、显式选择不越权（eligibility/弃用/隔离照常）、skip_keys 正确、map/report 完成诚实、回执有界、迁移头正确、39 技能兼容、Pi 契约（不开新 turn/不递归）、既有缺陷修复最小正确

## Round 2（修复后复审）

- 复审对象：commit `fecd2b7a9`
- 状态：已提交复审（重点：P1-2 路由逻辑、P1-3 YAML 语义）；结论以复审回执为准，定稿时回填。
