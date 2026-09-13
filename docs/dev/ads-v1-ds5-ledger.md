# ads-v1 · DS5 交付台账（任务 → 文件 → 测试 → 证据）

> 波次：DS5 · 版本锁定与漂移治理 · ADR-0175 · 里程碑 M3（与 DS4 同车）
> 状态：**完成** · 2026-09-13

| 任务 | 文件 | 测试 | 证据 |
|---|---|---|---|
| 版本 pin 语义 | `app/services/data_fabric/versioning_gate.py::pin_or_fetch` | `tests/unit/test_data_fabric_versioning_gate.py`（14 测） | **同 pin 两次取数 → 同 content_fingerprint**（第二次 snapshot 零取数，闸测试）；latest 显式新鲜 |
| 快照解析与固化 | 同上 + `InMemorySnapshotStore` + 迁移 `0070_ads_acquisition_snapshots`（同形持久层） | `test_pin_miss_freezes_snapshot_with_revision_evidence` | 未命中→固化（四维指纹+字段表+SourceRevision 证据+有界负载）；`alembic heads` 单 head（0070←0056），领号登记 .alloc.json |
| A6 漂移检测（4 类） | `versioning_gate.detect_drift`（列级 diff，非指纹黑盒） | `test_drift_classes_match_golden[...]` ×4 + `test_no_drift_when_schema_unchanged` | 四类 golden（tests/unit/ads5_golden/）：added_column(info)/removed_column(breaking)/type_change(breaking)/rename_suspect(degraded) |
| 兼容映射建议（只建议） | `suggest_renames`（同类型+名称相似度 ≥0.6） | `test_rename_suggestion_never_auto_applies` | `needs_human_confirmation=True`；报告永不改写 schema（闸测试） |
| 影响面分析 | `_affected`（lineage source_dataset_id 关联，注入式查询缝） | `test_impact_analysis_lists_affected_artifacts` / `test_impact_empty_without_drift` | 漂移→受影响产物清单（非仅告警）；无漂移→空清单；lineage 故障自吞不破门禁 |
| 分级阻断（可逆） | `SEVERITY` 单点 + `ADS_DRIFT_BLOCKING` 环境开关 + typed `DriftBlockedError` | `test_blocking_switch_reversible` | 缺省关（DS8 前只告警）；开启→breaking 阻断；关断→恢复（可逆闸测试） |
| versioning.py 复用 | `revision_from_fields` → `SourceRevision`；`compare_revisions` 语义一致 | `test_revision_evidence_uses_versioning_module` | 同 schema → ChangeClass.NONE（不新增第 6 份指纹实现——S1 债清结论落实） |

## 波次验收对照（§6 DS5 行）

- [x] pin 下两次取数结果一致（内容指纹断言）
- [x] 4 类漂移有检测与 golden（added/removed/type_change/rename_suspect）
- [x] rename 只建议不自动执行（闸测试）
- [x] 阻断开关可回滚（缺省关、开→阻断、关→恢复）

## 备注

- 迁移 0070 为本线 70–79 段首个（DS8 的 facts 表将用后续号）；`alembic heads` 单 head 断言通过。
- 快照负载有界（小数据集 pin 场景）；大体量走 ref: 引用语义不变。
