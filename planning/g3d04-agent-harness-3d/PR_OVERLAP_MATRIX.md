# G3D04 · PR 重叠矩阵（2026-10-26，基线 1115d683a）

## 开放 PR 对照（2026-10-26 末次复核，10 个）

| PR | 主题 | 与 G3D04 重叠 | 裁决 |
|---|---|---|---|
| #1773 G3D03 | 三维量测契约 V1：地形剖面/LOS 通视/地表距离/地表面积 + 垂直基准治理 | **互补（依赖方向）**：本方向 `run_3d_analysis` 对 line_of_sight/skyline/terrain_profile 的 typed unavailable 正是其合入后的接入点 | 文件重叠仅 4 个生成物（catalog/账本/科学目录——两侧都再生，合并后重跑生成器即可）+ G02 算法计数棘轮（取 max）+ 本方向未触碰的 `capabilities/terrain.py`。语义零重叠 |
| #1771 G3D01 | 开源底图/DEM 资源治理：basemap_profiles 单一真相、OpenFreeMap、PMTiles→XYZ、合成 DEM、terrain 治理 | **低（互补）**：G3D01 管 terrain provider 的资源/许可画像与瓦片路由；G3D04 管意图→选择→编排。共享概念：terrain source 资格 | 只读消费：G3D04 的 terrain 选择以"spec.sources 内已有合格 raster-dem 源/数据集 elevation 角色"为输入面，不重复画像、不改其 41 个文件中任何一个 |
| #1770/#1769/#1768/#1767/#1766/#1765/#1764 dependabot ×7 | 依赖升级 | 无 | 不碰 |

## 未合并分支对照（并行 G3D 方向）

| 分支 | 状态 | 与 G3D04 重叠 | 裁决 |
|---|---|---|---|
| `zcode/g3d02-scene-engine-tiles-v1` | 71 文件 +5942/-367，含 `mapspec_schema.py`、`app/services/mapspec/*`、`app/tools/scene_tools.py`、`render_work_envelope.py`、`renderer_capability.py`、`scene_quality.py`、前端 `scene3d/`、`mapspec-runtime/runtime.ts` | **中（文件邻接，功能正交）**：G3D02 = 渲染引擎/3D Tiles/资产/scene mutation 面；G3D04 = Agent 意图理解/编排/验收 | **本方向不改** G3D02 已改文件：不碰 `mapspec_schema.py`、`app/services/mapspec/`、`scene_tools.py`、`render_work_envelope.py`、`scene_quality.py`、`app/contracts/mapspec_intents.py`、前端 scene3d/mapspec-runtime。新代码全部落 `app/services/gis_harness/scene3d/`（新包）+ 新工具文件 `app/tools/scene_orchestration_tools.py`；对 scene 面只经既有公共 API（`mapspec_store.set_scene`、SetView/SetScene intent、evidence keys） |
| `zcode/g3d03-scientific-analysis-v1` | 已开 **PR #1773**（三维量测契约 V1） | **互补（见开放 PR 表）** | G3D04 的 LOS/skyline/terrain_profile typed decline 即为其接入 seam；合入后 capability snapshot 与 run_3d_analysis 直接受益 |

## 近期合并 PR 对照（内容级）

| PR | 与 G3D04 关系 |
|---|---|
| #1763 模板资产生命周期 | 无重叠 |
| #1762 G05 任务验收 lab | **复用**：G3D04 新增 3D 场景族挂入该 lab（corpus.py additive + 重新 pin），不改其判定内核 |
| #1761 工具能力 8 面对账 | **复用**：新工具注册后过活 registry 棘轮 + CLI 自查 |
| #1760 科学方法契约 | **复用**：viewshed 编排走 MethodUseContract 投影 + science gate 语义 |
| #1759 数据集语义身份/角色绑定 | **复用**：elevation 角色资格作为 3D 数据资格输入 |
| #1756 H01 资格→完成判据 | **对齐**：3D 完成判据沿用其 evidence 键纪律 |
| #1755 导出一致性 | 无直接重叠（scene 导出面属 G3D02） |
| #1620/#1621/#1622/#1623 | **复用**：Pi tool ABI V2 / project runtime / RenderWork ACK / Interaction IR 全部作为既有事实源 |
| #1617 语义缩放 | RepresentationAtScale 与本方向 scene dimension 正交（词表不动） |
| #1614/#1616 表达绑定/澄清 | **扩展点**：taxonomy 对 SetSceneIntent 的 unbound→bound 改造属 gis_harness 包内 additive |

## 禁区自查

- 不实现底图/3D Tiles/渲染引擎/科学算法本体（G3D01/02/03 主责）。
- 不造第二工具注册表/第二执行状态机/第二地图格式/第二会话真相（Pi 契约唯一）。
- 不扩大 MutationIntent union（fly_camera→既有 SetViewIntent；set_scene_mode/select_terrain_provider→既有 SetSceneIntent）。
- 不碰 G3D02 分支上的任何文件路径（见上表）。
- issue #1694 批量审计不在授权范围。
