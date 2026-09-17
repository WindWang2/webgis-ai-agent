# PARALLEL_OWNERSHIP — 并行方向占用与本方向边界（2026-09-16）

## Open PR / 分支占用（不碰）

| 占用者 | 内容 | 对本方向的约束 |
|---|---|---|
| PR #1335 `fix/harness-claim-mission-failclosed` | issue #1330–#1334：claim verify fail-closed、tenant scope、mission ownership、GIS_MISSION_RUNTIME kill-switch | 不修 claim/tenant/mission fail-closed 面；若 rebase 波及同文件（`gis_harness/hotpath*`）以 master+该 PR 语义为准做语义冲突评估 |
| PR #1336 `zcode/geoai-promptable-foundation-platform-11` | GeoPrompt、候选 mask、embedding cache、多模态 seam、`modelops/geoai/promptable/embedding-cache/frontend/components/geoai` | 热区禁改；本方向无需接口适配 |
| issue #1337–#1350 | audit 跟踪（依赖 CVE、npm audit 门禁、可观测 except-pass 等） | 非本方向 ownership；其中 ISSUE-042「except-pass 零日志」与本方向新代码相关处（自家代码不得 except-pass 静默） |

## 本方向拥有（本次新增/扩展）

- `QualityIssueCode` 受控新增 4 码：`timezone_missing`、`unit_ambiguous`、`field_role_ambiguous`、`admin_mismatch` + `_REMEDIATIONS` + `repair_planning._REPAIR_MAP` 映射
- 新检测器模块 `app/services/data_quality/semantic_checks.py`（timezone/unit/role/admin；有界、确定性、样本驱动）
- 统一聚合画像 `app/services/data_quality/profile.py`（`DataQualityProfile`，聚合非引擎）
- 资格层低置信角色闸：`data_qualification.qualify_data_role` additive 参数 `semantic_profile`
- 修复事务 `app/services/data_quality/repair_transaction.py`（RepairSession 状态机；复用 execute_repair/autofix）
- V8 资格 additive 质量面：`QualificationContext` 新字段 + `qualify_node` 消费（缺省零行为变化）
- 质量披露组装 + dirty corpus + E2E/边界/性能测试

## 明确不碰（禁止重复施工）

- Data Fabric adapter / fabric facts / versioning gate 重写
- spatial guardrail（admin_division_verifier 的层级校验）重写 —— 只**复用其码表基底**做数据质量侧的值域检查投影
- `REMEDIATION_OPS` 词表重写、第二套 RepairPlan/QualityReport/SessionPlan/EvidenceGraph
- #1335 tenant bug 面、#1336 GeoAI 热区
- 自动修改原始源数据（一切修复走新 ref + 血缘）

## 文件触点矩阵（预计与本方向相交的既有文件）

| 文件 | 修改性质 | 冲突风险 |
|---|---|---|
| `app/lib/data/quality.py` | additive（4 码 + 2 检查函数挂点） | 低（audit issue 未涉及） |
| `app/services/data_ingest/repair_planning.py` | additive（_REPAIR_MAP 4 条目） | 低 |
| `app/services/gis_harness/data_qualification.py` | additive（可选参数 + 检查） | 低（#1335 不碰此文件） |
| `app/services/gis_harness/qualification_v8.py` | additive（context 字段 + reason） | 低 |
| `app/services/gis_harness/capability_resolution.py` | additive（situation 透传） | 低-中（hotpath 相关，rebase 时复核 #1335） |
| 新文件 ×5（semantic_checks/profile/repair_transaction/测试/fixture） | 无冲突 | 无 |
