# G3D03 TEST_MATRIX

| 层 | 文件 | 覆盖 |
| --- | --- | --- |
| lib 解析 oracle | `tests/unit/lib/test_terrain_measurements.py`（23） | 平面精确（distance/area/profile/slope 逐点）、抛物线闭式、mask 限定 quad、LOS 平面/墙遮挡/曲率四场景/对称性、垂直基准词表与混算拒绝、ft 折算、observer_z 同 datum、nodata error/skip/阈值、LOS 端点 nodata、面积 nodata 阈值、超采样护栏、协作取消、退化几何、词表负例、确定性 dict 相等 |
| 工具接线 | `tests/unit/test_terrain_measurement_tools.py`（8） | 四工具成功路径（解析值断言）、科学证据块（algorithm/tool/inputs/transformations/parameters_applied）、GeoJSON 形状（3D LineString/Point/Polygon）、JSON 文本顶点、契约违规、datum 治理（拒/放+旗标）、收敛指示、地理 cos-lat+测地参考、ft 披露、nodata 阈值、无文件/越界/栅格外/顶点上限负例 |
| MapSpec 接线 | `tests/unit/test_measurement_mapspec_wiring.py`（6） | dispatch 真实形状 payload → converter → `MapSpecLayer.model_validate`；类别 line/fill；LOS 遮挡 Point 保留（3D 坐标）；四层组装 `MapSpecDocument` schema 有效 |
| oracle corpus | `tests/science_oracles/data/terrain.json`（229 cases，+13 `meas_*`） | 回放 + 文件级漂移 pin（manifest 同 PR regen） |
| 既有回归 | `test_terrain_science_vnext / test_terrain_v3 / test_terrain_compass / test_raster_resource_guard` | 既有 viewshed/terrain/护栏零漂移 |
| 治理 | `test_science_contract_v4_ratchet / test_g02_* / test_execution_catalog* / test_generated_artifact_graph / gis_tool_conformance(10 文件) / test_foundation_v2_infra` | 契约 ratchet、守门链、catalog/生成物账本、8 面对账、benchmark 投影 |

反证测试（撤掉防护应失败）：

- `_invalid_fraction_guard` 的 skip 阈值：`max_invalid_fraction` 收紧即红（工具级
  `test_surface_area_tool_polygon_and_nodata` 双分支）；
- 取消：撤掉段边界 `checkpoint()` 则 `test_cancellation_at_segment_boundary` 红；
- 垂直基准：撤掉 `resolve_vertical_reference` 的 mismatch 检查则
  lib/工具/oracle 三层 datum 负例红；
- 对偶网格 facet：换回归并角点（边界偏差）则 `test_surface_area_inclined_plane_exact`
  的全格精确断言红（曾实际抓出并修复 —— 见 DECISIONS D3）。
