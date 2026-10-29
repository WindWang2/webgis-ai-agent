# LOOP_STATE — 主循环状态

## 当前轮次：R1（M3 实现中）

## 里程碑状态

- [x] M0 仓库核对/worktree/去重（本地证据齐；网络二次去重挂起 → R1 风险跟踪）
- [x] M1 勘察 + 20 失败场景矩阵（BASELINE.md）
- [x] M2 契约冻结（INTERFACE_CONTRACTS.md）
- [ ] M3 核心引擎（vocabulary/field_proposal/spatial_receipt）+ 单测
- [ ] M4 计划/执行器 + 单测
- [ ] M5 API + 负例 + 快照再生
- [ ] M6 工具接线 + dispatch 真链测试
- [ ] M7 前端工作台 + vitest/tsc/eslint
- [ ] M8 E2E 合成城市 + 恢复/替换
- [ ] M9 独立 review ×2 + 全量回归 + PR

## 阻塞/挂起

- 网络：/github 不通（监控中）。影响：二次去重、push、PR。不阻塞本地 M3–M8。
- open PR/issues 复核：挂起到网络恢复；PR 创建前为硬门禁。

## 下一步

M3：创建 `app/services/data_preparation/`（vocabulary.py → field_proposal.py → spatial_receipt.py），测试先行（提案双语语料用例清单已在 BASELINE §20）。
