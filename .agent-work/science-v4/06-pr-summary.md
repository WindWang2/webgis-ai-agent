# Science V4 — PR Summary（最终态）

## 验证结果（rebase 后复测，origin/master=445ad30 无新提交）

- **scoped 车道全绿**：tests/unit/lib + gis（1095+） / tests/quality（240） /
  oracle replay（1092）/ tools（65+）/ registry validate 192-0 / ruff clean
- **全量串行 sweep**（2428 tests）：2424-2425 passed；2-3 个失败为
  **负载敏感 flake**（`test_context_schema_bytes_gate`、
  `test_quality_scenario_corpus::test_slice_geometry_states_green[*]`）：
  - 两次全量 run 的失败集合不同（run1: 2 个未具名；run2: 3 个含不同 bytes-gate 参数）；
  - 隔离运行恒绿（5/5 passed）；
  - 两个文件本分支 **零改动**（`git diff origin/master` 为空），
    且 commit 27922b30 已在 master 记录过同类 jitter slack；
  - 与仓库基线提示「性能门对负载敏感」一致。证据：本文件 + 03-progress.md。

## Two-round review 修复（commit d0b7b8b2）

Round 1（架构/正确性）+ Round 2（性能/安全/UX）并行独立执行：
- C1 HAND receiver 哨兵 −1 负索引静默错值 → 守卫 + NaN 通道 + 负值回归
- C2 hydrology hypsometry 工具分支必崩 → 修 + 工具级 e2e 测试
- C3 ST 驱动样本/时间戳错位 → 单循环自解析对齐
- M：LMC nugget 保留 / rho 最近邻配对 / breach 防汇搬移延伸 / terrain 4 算法
  真实 checkpoint / KED raw-variogram 披露 + drift 计数 + 聚合覆盖 /
  ST 工具时间窗落地 / SGS 退化计数 + 上限 20 万 / hydrology apply_contract +
  persist_output / chunked_band 措辞诚实化 / 8 项 minor
