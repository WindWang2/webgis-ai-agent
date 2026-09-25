# F01 — GIS Dataset Semantic Contract vNext · Independent Review（2026-09-26）

Review 执行：Subagent C（Independent Reviewer，独立于实现者），只读深审
`origin/master(9e1ad229)...HEAD`（3 commits，+4098/−13）。验证手段：全量 diff 阅读、
新测试 85 项实测、关联回归（gis_harness qualification/context 51 绿、gis_memory 54 绿、
api_docs_drift + compiler_v4 10 绿）、两个关键怀疑点可复现实验。

## 总体结论

**修复后可合入** → 全部 P1/P2 已在本分支修复（见下"清偿记录"）。架构面达标：
单一指纹原语（复用 `canonical_fingerprint`）、单向投影（resolver 词表唯一出口未破）、
有界采样（10 万要素实测只触 first-200 窗口）、fail-closed store（损坏/篡改/未知版本
全部显式错误码 + 负例测试真实触发）、#1488 三模块与 data_fabric/contracts.py/
artifact_registry.py/governor/* 冻结面零触碰、8 个 open PR 热区全部 additive 小缝。

## 发现与清偿

| 级别 | 发现 | 清偿 |
|---|---|---|
| P1-1 | `to_dict`/`from_dict` 漏 `crosses_antimeridian` → 跨反子午线数据集 descriptor 入库即永久 corrupt（实测复现；同指纹 no-op 无自愈路径） | ✅ 两处补齐 + 富 descriptor（全可选字段填满）store roundtrip 回归测试 |
| P1-2 | 改 mapspec_schema 未再生成 `types.generated.ts` → 投影幂等契约测试确定性失败 | ✅ `python -m app.lib.cartography.ts_projection` 再生成（+2 行 additive） |
| P1-3 | ADR/设计文档声称 replay/restore、context 桥、qualification guard 有生产调用点，实际为库面 + 可选参数（文档承诺 > 实现事实） | ✅ 修订 ADR-0215 Decision 4/5 与设计文档 D6：如实声明"库面 + seams 已交付，生产消费接线为后续工作"（Out of Scope 列明）；commit 措辞以文档为准 |
| P1-4 | `_prune_sync` 跨写者竞态：keep 集合仅含本次写入者历史，可删除 head 正指向的载荷 | ✅ prune 先重读 head，keep = history ∪ {head 指纹}；head 不可读时保守不删；竞态回归测试锁定 |
| P2-1 | 指纹含证据域字段 → ingest 富证据与 fabric/inline 薄证据指纹如实不同，文档"四路径同指纹"过强 | ✅ 文档口径修正：同一证据域内一致（ingest↔store↔mapspec-ref 主链）；跨证据域必须经 `evaluate_reuse`/`compare_descriptors`，不得裸字符串相等 |
| P2-2 | 同指纹 no-op 不校验载荷存在 → 无自愈能力 | ✅ no-op 早退前查载荷在盘；缺失则重写修复（回归测试） |
| P2-3 | put/get_by_fingerprint 不校验指纹格式 → 路径逃逸面 | ✅ `^dsd-v1:[0-9a-f]{64}$` 格式闸（测试含 `../evil`、`../../etc/passwd` 负例） |
| P2-4 | harvest `[:64]` 截断 71 字符指纹 → token 无法回指 descriptor | ✅ 放宽到 `[:96]`（三处） |
| P2-5 | 设计文档 D3（source refs 入指纹）/D6.1（descriptor_version 键）/D6.2（fabric→store）三处与实现不符 | ✅ 文档改为与实现一致 |
| P3-1 | `stale_reason_for`/`_STALE_REASON_BY_CLASS` 是死代码副本 | ✅ 删除 |
| P3-2 | `_semantic_view_from_descriptor` 空 role_confidence 兜底 `metadata_derived` 放大证据 | ✅ 兜底改 `unknown` |
| P3-3 | prune 竞态下 FileNotFoundError 被误报 CORRUPT | ✅ 单独捕 FileNotFoundError → MISSING |
| P3-6 | `derive_descriptor` 未透出 `user_roles`（用户显式角色声明通道断裂） | ✅ 透传至 `derive_semantic_profile`（user-wins 直达 descriptor）+ 测试 |
| P3-7 | `time_field[:MAX_PROFILE_FIELDS]` 拿字段数上限当字符上限 | ✅ 独立常量 `TIME_FIELD_MAX_CHARS` |
| P3-9 | ChangeClass CONTENT/METADATA_ONLY 路径无 descriptor 级测试 | ✅ 补 `quality_signals` 仅变 → CONTENT → `DESCRIPTOR_STALE_CONTENT` + derived_at 恒 NONE 测试 |

P3-4（>128 字符共享前缀字段名截断碰撞）、P3-5（-0.0 规范化）、P3-8（三域
`descriptor_fingerprint` 命名提示）为备注级：均需病态输入或多域协同才触发，
当前有界且 fail-closed（canonical_fingerprint 对 NaN 直接 raise → additive 路径
catch 降级），记录不修，后续方向可取。

## 修复后验证

- 新增/修订测试：**92 项全绿**（含 7 项 review-fix 回归）。
- 邻域回归：ingest/upload/offload/qualification 90 绿；context/profiler/measurement/
  resolver 97 绿；mapspec schema/mutation 27 绿；gis_memory 27 绿；fabric 29/30 绿
  （唯一失败 `test_create_data_source_stores_real_profile` 为 **master 基线已确认的
  本机环境失败**：SSRF 防护把 `db.example.com` 解析到被禁私有 IPv6，与本分支无关，
  干净 origin/master 上可复现）。
- ruff：`app/ tests/` 全绿。
- `python -m app.lib.cartography.ts_projection` 再生成幂等（+2 行已提交）。
