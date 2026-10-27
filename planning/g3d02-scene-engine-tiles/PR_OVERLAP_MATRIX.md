# G3D02 PR 重叠矩阵（初查 2026-10-26；PR 创建前复查 2026-10-27）

工作期间其他方向开出了 PR（初查时为空）。逐一对账：

| 开放 PR | 文件/功能重叠 | 处置 |
|---|---|---|
| #1771 g3d01 底图/DEM 资源治理 | 3 个共同文件：map-panel.tsx（其改 terrain 挂载入口/import；我改 scene assets 效应/拾取/检视卡）、renderer.ts（其改 DEM 兜底源头部；我改尾部 re-export）、runtime.ts（其扩展错误 handler 注释；我加 applySource model3d 分支） | 同文件不同区域、语义无交叉（DEM/底图 vs model3d 资产）；接口保持窄面（additive re-export + 单分支），无 cherry-pick；合并时预期小文本冲突可解 |
| #1773 g3d03 三维量测契约（地形剖面/LOS/垂直基准治理 ADR-0274） | 零文件重叠 | 无处置；垂直基准词表若其扩展 datum 面，scene.vertical 词表消费者按 schema 单源吸收 |
| #1772/#1764-1770 依赖 bump | 无 | 无 |

## 与近期合并的内容级对照
| PR | 重叠面 | G3D02 处置 |
|---|---|---|
| #1622 RenderWork | ACK/信封结构 | 只消费：新 3D 源种进入 data_dependencies/sources ACK，不改协议 |
| #1623 Interaction IR | 事件词表 | 只消费：3D pick 复用 feature_selected 语义（新 kind 需谨慎，见 DECISIONS） |
| #1621 项目恢复 | snapshot 持久化 | scene 域随 bounded spec doc 免费搭车，不另建持久化 |
| #1763 模板资产 | 资产生命周期 | 不同资产域（制图模板 vs 3D 场景资产），无接口交叉 |
| #1617 语义缩放 | RepresentationAtScale | 3D 资产不接入 representation 子块（M1 界限，避免范围膨胀） |
| G3D01 (open-basemap-dem) | 底图/DEM 生产 | 消费其 source readiness；不触碰 provider catalog / DEM 生产 |

## 结论
无重叠。本方向新增：MapSpec v1.7 scene assets + model3d source、frontend scene3d 模块、3D 交互与 LOD。
