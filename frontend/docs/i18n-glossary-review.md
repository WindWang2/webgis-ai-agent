# 高风险术语表（foundation/i18n-responsive-v9 / P4 — 请人工审校）

> 状态：待人工审校。本表收录 en 翻译中「一词多译风险高 / 领域语义敏感」的术语。
> 基准：`UBIQUITOUS_LANGUAGE.md`（MapSpec / 评估域英文术语表）；GIS UI 术语以代码
> 现有英文名（工具参数、ErrCode、模型字段）为准回填。审校结论请回写本表。

## GIS 分析域

| zh（catalog 原文） | en（本线译法） | 风险 / 备注 |
|---|---|---|
| 缓冲区分析 | Buffer | 与工具参数 `buffer` 一致；低风险 |
| 叠加分析 | Overlay | 与 `overlay` 参数一致 |
| 裁剪 | Clip | `clip`；注意与「剪裁板」歧义无 |
| 等时圈（网络分析） | Isochrone (network analysis) | `isochrone`；UBIQUITOUS_LANGUAGE 无词条，行业通用 |
| 热力图 | Heatmap | `heatmap_data` 图层类型 |
| 指北针 / 比例尺 / 坐标格网 | North arrow / Scale bar / Graticules | 制图组件名；graticule 为专业用法（≠ grid） |
| 分级图例 / 分类图例 / 连续色条 | Graduated legend / Categorical legend / Continuous colorbar | 对应 legend_spec 变体 graduated/categorical/continuous_colorbar |
| 区位插图 | Inset map | inset_map 组件 |
| 空间目录 | Spatial catalog | data fabric 域 UI 用语 |
| 数据织网 | Data Fabric | 专有名词（子系统名），不意译 |

## 平台 / 架构域

| zh | en | 风险 / 备注 |
|---|---|---|
| 制图工坊 | Cartography studio | export_layout tab 展示名；rail 标签为「制图」(Map layout) |
| 制图规范 | MapSpec | UBIQUITOUS_LANGUAGE：MapSpec 即规范本体，别名禁用 style/spec JSON |
| 观测地图 | Observed Map | UBIQUITOUS_LANGUAGE 词条 |
| 有效性阶梯 | Validity ladder | 同上 |
| 深度探索 | Deep explore | SSE 任务命名 |
| 会话计划 | Session plan | session-plan 面板 |
| 血统 / 谱系 | Lineage | workflow artifact lineage；「血统」「谱系」两处 zh 原文并存（历史原因），en 统一 lineage |
| 产物 | Artifacts | workflow artifacts |
| 下推 | Pushdown | query plan pushdown（aggregation/projection/bbox） |
| 物化 | Materialize | dataset 物化 |
| 数据集指纹 | Dataset fingerprint | descriptor.fingerprint |
| 五维差异 | Five-dimension diff | map-product-versions diff（data/algorithm/parameter/style/output） |
| 硬约束否决 | Hard-constraint vetoes | decision panel |
| 制图记忆 | Cartographic memory | 项目级偏好沉淀面板 |
| 组织态（仍经服务端 CAS 持久化） | Organizational state (persists via server CAS) | collab 披露文案；CAS 术语保留 |
| 回放 / 续跑 | Replay / Resume | workflow recovery |
| 网格坐标（坐标格网） | Graticules | 见上 |

## 交互 / 状态域

| zh | en | 风险 / 备注 |
|---|---|---|
| 隔离显示（solo） | Solo | 图层隔离；保留 solo 括注 |
| 已过期 | Stale | 图层产物过期徽标；与 UBIQUITOUS_LANGUAGE "Superseded" 区分（Superseded 专指 verdict 代际） |
| 感知中 / 执行中 | Perceiving / Acting | agent 状态机 thinking/acting |
| 结果工作台 | Result workbench | results tab |
| 制图质量 | Cartographic quality | CartographicQuality 生产闸（大写专有） |

## 已知未决

- 「血统/谱系」zh 侧不一致（历史文案），en 已统一 lineage —— 如需 zh 收敛建议 v9.1 统一为「谱系」。
- 「当前屏幕比例 (Screen)」等纸张输出项保留英文括注（行业以英制标称）。
