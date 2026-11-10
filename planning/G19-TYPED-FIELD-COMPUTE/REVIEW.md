# G19-TYPED-FIELD-COMPUTE — REVIEW

独立 review → 修复 → 复审记录。**尚未启动**（功能验收通过后启动；reviewer 必须是未编写本轮核心实现的独立 subagent，且释放一个实现槽位以维持同时存活 subagent ≤3）。

## 待填结构（启动后回填）

- Reviewer：独立 subagent（名称/启动时间/是否参与核心实现 = 否）
- 输入：最新 base SHA、完整 diff、实际测试证据、21 场景、用户旅程、范围与所有权边界
- 检查面：调用链真实性、租户/会话/权限边界、科学与单位语义、数据/版本一致性、取消/异常/重试、内存与计算有界、用户可用性、Pi 真实入口、旧协议兼容、与最新 PR 重复/冲突
- 第一轮发现：file:line / 复现条件 / 影响 / 修复建议（P0/P1/P2/P3）
- 修复：commit hash + 对应回归
- 复审：结论 + 残余风险（本任务 P0/P1 或影响验收的 P2 必须清零）
- 既有仓库问题：附基线证据与隔离说明（不笼统写「全是历史问题」）

## 已知基线（reviewer 须知，非本任务引入）

- `tests/data/test_preparation_plan_v1.py` 在干净 `origin/master @8167052` 上 24 passed / 9 failed，根因 `app/services/dataset_semantics/store.py:252` 写 `dsd-v1:<sha>.json`（Windows 冒号非法，WinError 123）。见 `BASELINE.md`。
