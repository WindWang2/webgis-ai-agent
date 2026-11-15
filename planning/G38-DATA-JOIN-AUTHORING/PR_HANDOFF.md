# G38 PR_HANDOFF — 合并交接

## 身份

- Branch: `zcode/g38-data-join-authoring-20261114T151651Z`
- Base: `1276692737a8cf58d0604fb40ae0e70fc61779a2`（origin/master，fetch 复核无漂移）
- Head: 见 PR；提交序列：后端 → 前端 → Pi 旅程/能力登记 → 派生工件 → 账本 →
  评审修复
- Worktree: `/home/kevin/projects/webgis/webgis-worktrees/g38-data-join-authoring-20261114T151651Z`
  （含本地 `.venv-g38` 与 vendor/pi 构建产物 —— 均不入提交）

## 合并前检查单

1. 迁移：`alembic upgrade head` 落 0107（两新表；downgrade 反序删除）。
   生产 compose/k8s 走既有 entrypoint initContainer 迁移路径，无额外动作。
2. 派生工件已按 `check_generated_staleness` 依赖序再生成（openapi 快照、
   realtime 契约、quality manifest/report、drift、capability catalog、账本）。
3. 能力面：`data_join_authoring` capability + `data.join.authoring` algorithm
   已登记（启动严格词表闸 + 跨注册表校验通过，仅剩 base 既有
   platform.evidence_map_brief 项）。
4. DataObject kind 白名单新增 `data_join`（G18 `data_delivery` 同款先例，
   不入 DATA_OBJECT_KINDS 主四类 —— dr/orphan 扫描面不变）。

## 合并后验证建议（非阻塞）

- 部署环境带 pyarrow 时：GeoParquet 投影路径自动可用（运行时探测，无需配置）。
- 观察 `[data-join] data object publish failed` 日志应为零（F3 修复前恒 failed）。
- lifespan 日志出现 `[lifespan] data-join recovery: …`（崩溃窗口收敛钩子）。

## 已知边界 / 后续增强（REVIEW.md 详）

- UI 聚合列编辑器（后端/REST/工具面聚合语义完整，UI 仅空间 aggregate 模式
  + match_count；F11b 后续项）
- >10k 输出：地图渲染投影 typed 拒绝（G24 同口径），完整数据走结果分页/G18
  导出
- master 既有失败 6 组清单见 TEST_EVIDENCE.md（base 复现在案，非本分支引入）
