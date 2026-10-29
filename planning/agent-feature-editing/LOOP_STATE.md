# LOOP_STATE

## 状态
- [x] M0 基线核对 + worktree（网络失败已记录 R1）
- [x] M1 数据所有权表（BASELINE.md）
- [ ] M2 核心模块 → 进行中
- [ ] M3 REST+鉴权 / M4 属性 / M5 几何 / M6 工具+situation / M7 前端 / M8 并发恢复 / M9 E2E+review+PR

## 每轮循环
recon → gap → plan → implement → local test → independent review → fix → regression → next

## 挂起事项
- [ ] 网络恢复后：git fetch + gh pr list/issue 二次去重（PR 创建前必须）
- [ ] durability snapshot 对 `_feature_edit_*` 键的透传验证（R10）
- [ ] vendor/pi 子模块网络不可达 → pi-bridge 相关测试若红为环境基线（既有记录）

## 提交纪律
每里程碑原子 commit；PR 前二次去重 + diff --check + 敏感扫描 + 生成物一致性。
