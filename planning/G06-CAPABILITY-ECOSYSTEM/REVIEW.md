# G06 — 独立冷审记录

> 由子代理 B（`reviewer-b`）在代码定稿后逐文件冷读
> `git diff origin/master...HEAD`，**不复用主 Agent 的通过结论**。
> 本文件在冷审返回后填入实际 findings 与修复证据；P0/P1 清零后才进 M9。

## 审查范围

- 逐文件 review `git diff origin/master...HEAD`（28 个文件）。
- 维度：设计（唯一事实源）、科学语义（量纲/CRS/nodata/fallback）、
  权限与隔离、性能与预算、回归风险、测试有效性、文档真实性。
- 特别核查：
  1. 是否出现第二 registry / catalog / 认证 / Planner / 渲染器；
  2. `native_verified` 是否可能被伪造；
  3. 权限 fail-closed 是否泄露 descriptor 细节；
  4. 两项原语是否真实计算（非 no-op / mock）；
  5. 是否触碰 G01–G05 禁区（`pi_native_surface.py` / `agent_pi_bridge.py` /
     `llm_result_formatter.py` / `families.py` / 数据 fabric / MapSpec）；
  6. 测试是否出现「改断言让错误消失」或空测试；
  7. 生成物是否手工编辑、是否引入与本方向无关的大 diff。

## R1 冷审结论

（待子代理 B 返回后填写：P0 / P1 / P2 分级表 + 文件/行号证据）

## R1 修复记录

（待填写：每条 finding → 修复 commit → 复验命令与结果）

## R2 二次 review

（待填写：对修复后的 `git diff` 再审，确认 P0/P1 清零、P2 已记账）
