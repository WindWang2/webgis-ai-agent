# R1 冷审简报 — goal-carto-design-series-20261125

你是**独立冷审 reviewer**（未参与实现）。仓库：`/home/kevin/projects/webgis/webgis-wt-carto-design-series-20261125`（worktree，分支 `zcode/goal-carto-design-series-20261125`）。审查对象：`git diff origin/master...HEAD`（基线 master 8fe3d7663，86 文件，+16504/-3）。

## 原始需求（逐项对照）

任务方向 04：可复用专题制图设计规范与系列地图成果。六个 WP：
- WP1 制图设计规范权威模型（typed spec/版本化/继承/digest/覆盖策略/命名查重/scope）
- WP2 数据驱动模板匹配 + role binding（单位/定义可比、shared classification 冻结一次）+ 提取（无数据值泄漏）+ 应用（dry-run diff、锁定保留、fail-closed）
- WP3 系列作者计划（独立 MapSpec 产品集合）+ 传播 preview/逐图 CAS + 跨图一致性 findings
- WP4 工具面 + 前端工作台（真实 MapSpec 预览、锁定/覆盖、i18n、stale/键盘）
- WP5 系列冻结计划 + 增量生产（指纹缓存）+ 成品 QA（真实产物机器解析）+ series manifest
- WP6 ≥5 类案例（ground truth）+ 负例 + 20/50 规模

## 审查清单（逐条给出 结论+证据 file:line）

1. **正确性**：digest/继承/CAS/缓存键/manifest 确定性逻辑是否有错；传播 receipts 语义与实际 mutation 是否一致。
2. **科学语义**：分类只带方法不带数据断点？density/rate 分母门控是否真的挡住？unit 契约是否贯穿 spec→binding→series？
3. **授权/越权**：scope 谓词（builtin ∪ creator ∪ org）在 REST/工具两面是否一致；跨租户 404 不泄露；写谓词 builtin 只读。
4. **幂等/并发/事务**：版本 max+1 重试；job 幂等键；传播逐图 CAS 的 superseded 路径。
5. **失败恢复**：单成员失败不伪装全成功；blocked/failed 状态词表；缓存 checksum 复核。
6. **数据/CRS 完整性**：ramp/method 是表达值不越成数据值；freeze 指纹对账算法与 store 一致。
7. **Pi/用户同链**：工具与 REST 是否同一 service；#2156 session fail-closed。
8. **可维护性/兼容**：不建第二 MapSpec/渲染器/资产库；additive migration；kill-switch/无 print_layout 时旧字节不变。
9. **前端**：i18n key 对齐；面板不过度；api client 与后端契约一致（抽查 3 个端点）。
10. **测试真实性**：抽查 3 个关键测试是否「值级对账」而非 HTTP-200 式假绿；负例是否真的负。

## 纪律

- **必须包含反例/故障注入验证**：至少实际运行 2 个负例（如：构造越权请求/构造泄漏 payload/构造 stale revision）确认 fail-closed，命令与输出写进报告。
- 不要修复任何代码——只报告。禁止改文件（只读审查 + 运行测试）。
- 已知既有基线失败（不是本分支引入，勿报）：pin consistency、scale_portrayal corpus、component_variants v3/v4 ×3、前端 19 vitest（pdf-dependency-security 等）、typecheck 7 项（5 与 master 逐签名一致 + 2 环境缺可选依赖）。
- 发现问题按 P0（阻断合并）/P1（必须修）/P2（应修）/P3（可延后+理由）分级；每条给 file:line + 建议修法。

## 输出格式（≤80 行）

```
## R1 冷审报告
### 结论：可合并 / 需修复后合并 / 阻断
### Findings
- [P1] file:line —— 问题 —— 建议修法 —— 复现命令
### 反例验证记录（至少 2 条实际运行）
### 测试抽查结论
```
