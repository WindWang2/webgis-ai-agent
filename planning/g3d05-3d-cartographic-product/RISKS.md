# G3D05 风险登记

| # | 风险 | 等级 | 缓解 |
| --- | --- | --- | --- |
| R1 | 与 G3D01（#1771 未合并）在 map-panel.tsx / export-chrome.ts / exporter.ts 的合并冲突 | 中 | 只新增 hunk/函数；不 import 其新文件；PR 描述列明冲突预案（rebase 时按语义保留双方新增段）。 |
| R2 | 组件类型新增破坏 catalog parity / ABI fail-closed 纪律 | 高 | 严格走 COMPONENT_ABI_META + component_renderers + build_catalog 再生成 + 既有 parity 测试全跑。 |
| R3 | 用户 set_scene mutation 与 Agent 并发（锁竞争/409） | 中 | 复用既有 mutation 信封（expected_revision + client_mutation_id + 503/409 映射），不自造锁。 |
| R4 | 透视比例尺披露被误当"禁用比例尺"造成产品退化 | 中 | 披露而非移除：比例尺保留并标注"透视视角·仅供参考"；仅纯俯视 pitch≤10° 才免披露。 |
| R5 | StoryMap 持久化引入 DB 迁移负担 | 中 | 检查项目既有建表模式（启动时 create_all / alembic），跟随现范式；只加一张窄表。 |
| R6 | G3D03 剖面后端未合并导致剖面数据空转 | 低 | 组件契约先行（inline/ref:profile-*），fixture 驱动测试；不伪造真实 DEM 剖面声称来自实测。 |
| R7 | 大范围前端回归（map-panel 是 1642 行热文件） | 高 | 场景控制器独立文件+最小挂载点；不重排既有 effect；跑 map-panel 全部既有测试。 |
| R8 | 环境资源（五路 worktree 并发） | 中 | 复用 venv/node_modules 链接避免重复安装；所有验证受控小批；编译走互斥锁。 |
