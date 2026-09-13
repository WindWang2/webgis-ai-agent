# ADR-0175: ads-v1 版本锁定与漂移治理（pin 快照 + 四类漂移 + 影响面 + 分级阻断）

- 状态: Accepted
- 日期: 2026-09-13
- 线: adaptive-data-supply/v1-master · DS5（自适应数据供给与接入 · 并行线 P1）
- 关联: ADR-0170（D1.version 语义）、`app/lib/data/versioning.py`（四维指纹/ChangeClass 复用）、`data_fabric/limits.py`（快照有界）、`lineage_service`（影响面）、迁移 0070（首用 70–79 段）

## 1. 背景（缺口 A5/A6）

同一请求两次取数不保证同一份数据（无版本 pin）；上游改列静默出错
（`versioning.py` 有 `schema_fingerprint` 字段但零检测逻辑——债清扫描确认
该模块生产依赖仅 1 处，可安全扩展）。

## 2. 决策一：版本 pin 语义（`data_fabric/versioning_gate.py`）

- **`latest`（缺省，显式标注）**：每次新鲜取数；若该数据集存在 pin 快照，
  取回后仍做漂移比对（advisory），新 schema **不**自动 pin；
- **`pinned`**：`(dataset_key, pin)` 命中快照 → 直接返回冻结负载
  （同 content_fingerprint，零取数）；未命中 → 取数并固化快照
  （四维指纹 + 字段表 + 修订证据 `SourceRevision` + 有界负载）。
  重放契约与 DS3 的 plan_id 确定性衔接：同 plan + 同 pin → 同哈希；
- 快照存储：进程内 `InMemorySnapshotStore`（测试/单 worker），alembic
  **0070 `ads_acquisition_snapshots`** 表同形（多 worker 持久层，DS8 随
  事实库一起接 DAO）——唯一迁移、additive、downgrade 反序。

## 3. 决策二：四类漂移检测（字段表 diff，非指纹黑盒）

对 pin 快照的字段表 vs 现取字段表做**列级 diff**：

| 类 | 判定 | severity |
|---|---|---|
| `added_column` | 新增列 | info |
| `removed_column` | 删列且无改名配对 | **breaking** |
| `type_change` | 同名列类型变化 | **breaking** |
| `rename_suspect` | 删列全部与同类型近似名列配对（SequenceMatcher ≥0.6） | degraded |

- **改名建议只建议**：`RenameSuggestion{from,to,confidence,needs_human_confirmation=True}`
  纯数据输出——**禁止自动改名**（静默改语义比报错更糟，任务书 DS5.5）；
- 指纹（`schema_fingerprint`）用于证据链与快速比对；`compare_revisions`
  （versioning.py）在证据集上语义一致（同 schema → NONE，有测试）。

## 4. 决策三：影响面分析（告警 → 行动清单）

漂移发生时经 lineage（`source_dataset_id` 关联）解析受影响的已注册产物清单
（`DriftReport.affected_artifacts`），注入漂移验证清单准确性；lineage 查询
失败不影响门禁本身（自吞并降级为空清单——影响面是增强不是闸）。

## 5. 决策四：分级阻断（可逆开关）

- severity 映射单点 `SEVERITY`（breaking=removed/type_change，degraded=
  rename_suspect，info=added）；
- **`ADS_DRIFT_BLOCKING` 环境开关，缺省关**（DS8 基线稳定前只告警不阻断）；
  开启时 breaking 漂移抛 typed `DriftBlockedError` 拒绝取数；关断即恢复
  （可逆性有测试）。漂移类别无论是否阻断都写入 D4 fact（`drift` 字段）。

## 6. 后果

- DS8 校准点：`SEVERITY` 映射与 `_RENAME_SIMILARITY`；快照 DAO 落 0070 表；
- 四类漂移各 golden（tests/unit/ads5_golden/，时间戳不入 golden 契约）；
- pin 语义与三层缓存不冲突：快照是显式 pin 的冻结证据，`latest` 照常走
  ref_payload_cache（TTL 语义不变，§8.3 风险项闭环）。
