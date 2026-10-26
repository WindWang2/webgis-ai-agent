# G3D02 PR 重叠矩阵（2026-10-26）

| 开放 PR | 文件/功能重叠 | 处置 |
|---|---|---|
| （无开放 PR） | — | — |

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
