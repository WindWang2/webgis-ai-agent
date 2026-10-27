# G3D04 M1 · 3D 意图场景映射与断链缺口表

> 生成自 `app/services/gis_harness/scene3d/corpus.py`（32 场景；`tests/unit/gis_harness/scene3d/test_cues_corpus.py` 钉住 cue 投影）。

链路环节：cue（话语→typed 属性）→ ir（Requirement SceneSpec）→ decision（representation 决策）→ mutation（SetScene/SetView 事务）→ tool（编排工具真实 dispatch）→ 其余为相邻方向依赖面。`now` = 本方向交付并测试覆盖。

| id | 话语 | 族 | 期望 cue | 链路状态 |
|---|---|---|---|---|
| s01 | 展示成都市的立体地形 | dimension | dimension=3d; terrain_requested=True; analysis_goals=∅ | cue:now; ir:now; decision:now; mutation:now; terrain_source:g3d01; render:g3d02 |
| s02 | 把地图切换成 3D 视图 | dimension | dimension=3d | cue:now; ir:now; decision:now; mutation:now |
| s03 | 用三维地图显示小学分布 | dimension | dimension=3d | cue:now; ir:now; decision:now; mutation:now |
| s04 | 改成 2D 平面地图 | dimension | dimension=2d | cue:now; ir:now; decision:now; mutation:now |
| s05 | 换成二维视图 | dimension | dimension=2d | cue:now; ir:now; decision:now; mutation:now |
| s06 | 用 2.5D 晕渲表达地貌 | dimension | dimension=2.5d; terrain_requested=True | cue:now; ir:now; decision:now; mutation:now; terrain_source:g3d01 |
| s07 | 显示成都小学分布情况 | no_inference | dimension=; terrain_requested=None; analysis_goals=∅ | cue:now; ir:now; decision:now |
| s08 | 做一个人口密度热力图 | no_inference | dimension=; extrusion_requested=None | cue:now; ir:now; decision:now |
| s09 | 统计各区的 GDP 并制图 | no_inference | dimension= | cue:now; ir:now; decision:now |
| s10 | 添加建筑白模 | extrusion | extrusion_requested=True | cue:now; ir:now; decision:now; height_evidence:now; scene_asset:g3d02 |
| s11 | 用建筑物高度做立体拉伸展示 | extrusion | extrusion_requested=True | cue:now; ir:now; decision:now; height_evidence:now |
| s12 | 建筑白模没高度数据的话就用平面图 | extrusion_honesty | extrusion_requested=True | cue:now; ir:now; decision:now（诚实降级+披露） |
| s13 | 叠加地形起伏效果 | terrain | terrain_requested=True | cue:now; ir:now; decision:now; terrain_source:g3d01 |
| s14 | 加载 DEM 高程模型 | terrain | terrain_requested=True | cue:now; ir:now; decision:now; dem_pipeline:g3d01 |
| s15 | 地形夸张 3 倍 | exaggeration | terrain_requested=True; exaggeration=3.0 | cue:now; ir:now; decision:now（钳制+失真披露） |
| s16 | 垂直夸张 15 倍 | exaggeration | terrain_requested=True; exaggeration=15.0 | cue:now; ir:now; decision:now（钳到 10+披露） |
| s17 | 从西南方向看 | camera | camera_bearing=45.0 | cue:now; ir:now; decision:now; mutation:now（SetView） |
| s18 | 从北边看这座城市 | camera | camera_bearing=180.0 | cue:now; ir:now; decision:now |
| s19 | 朝东南方向看 | camera | camera_bearing=135.0 | cue:now; ir:now; decision:now |
| s20 | 压低视角俯仰角 30 度 | camera | camera_pitch=30.0 | cue:now; ir:now; decision:now（user-wins 锁） |
| s21 | 鸟瞰整个城区 | camera | camera_pitch=None | cue:now（意图词面）; ir:now; decision:now（推荐档） |
| s22 | 换个角度看 | camera | camera_bearing=None | cue:now（意图词面，不造角度）; decision:now |
| s23 | 导出一张打印用的立体地图 | medium | dimension=3d; medium=print | cue:now; ir:now; decision:now（静态媒介禁 3D→2.5d 降级+保留偏好） |
| s24 | 出一张 PDF 静态图 | medium | medium=export_pdf | cue:now; ir:now; decision:now |
| s25 | 计算塔台的通视范围 | analysis | analysis_goals=viewshed,line_of_sight | cue:now; ir:now; tool:now（viewshed_analysis 真实 dispatch）; algorithm:g3d03（增强）; result_attach:partial |
| s26 | 分析这个山脊的可视域 | analysis | analysis_goals=viewshed | cue:now; ir:now; tool:now; result_attach:partial |
| s27 | 看看两点之间的通视情况 | analysis | analysis_goals=viewshed,line_of_sight | cue:now; ir:now; tool:g3d03（LOS 算法待补） |
| s28 | 提取这条线的高程剖面 | analysis | analysis_goals=terrain_profile | cue:now; ir:now; tool:now（extract_contours 相邻）; profile_algo:g3d03 |
| s29 | 评估天际线影响 | analysis | analysis_goals=skyline | cue:now; ir:now; tool:g3d03（skyline 算法待补） |
| s30 | 成都小学分布加立体地形，从西南方向看，再算这个山头的可视域，最后导出 PNG | composite | dimension=3d; terrain_requested=True; camera_bearing=45.0; analysis_goals=viewshed; medium=export_png | cue:now; ir:now; decision:now; tool:now; export_parity:g3d02/g3d01 |
| s31 | 卫星底图上做建筑白模并夸张地形 2 倍 | composite | extrusion_requested=True; terrain_requested=True; exaggeration=2.0 | cue:now; ir:now; decision:now; basemap:g3d01; scene_asset:g3d02 |
| s32 | 3D 显示受体分布，DEM 作为高程底面 | composite | dimension=3d; terrain_requested=True | cue:now; ir:now; decision:now; dataset_roles:now; dem_pipeline:g3d01 |

## 断链缺口汇总（非 now 环节 → 归属）

| 环节 | 场景数 | 归属 |
|---|---|---|
| terrain_source | 3 | G3D01（DEM 资源画像/瓦片路由） |
| scene_asset | 2 | G3D02（3D Tiles/gltf） |
| dem_pipeline | 2 | G3D01 |
| result_attach | 2 | G3D03（科学产物上屏面；当前 stats+披露，attach 步骤 typed 占位） |
| tool | 2 | G3D03（LOS 算法到位后接入 run_3d_analysis） |
| render | 1 | G3D02（scene 渲染面/ACK 扩展） |
| algorithm | 1 | G3D03（LOS/skyline 科学算法） |
| profile_algo | 1 | G3D03 |
| export_parity | 1 | G3D02/G3D01（导出一致性） |
| basemap | 1 | G3D01（PR #1771） |

本方向已闭环环节：cue / ir / decision / mutation / tool（见 TEST_MATRIX）。
