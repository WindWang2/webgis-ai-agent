# G3D03 PLAN — 科学计算算法与确定性空间量测

角色：全自动技术负责人 + 实施 Agent。基线/重叠/能力矩阵见 BASELINE.md、PR_OVERLAP_MATRIX.md。
契约决策见 docs/adr/0274-3d-measurement-contract.md（本 PR 的契约 ADR）。

## 里程碑

- **M1 ✅** 盘点完成（BASELINE.md 矩阵）。缺口 = 剖面/LOS/地表距离/地表面积/垂直基准治理。
- **M2 3D Measurement Contract**：`app/lib/geo_analysis/measurements.py`（VerticalReference +
  四个纯算法）+ 解析曲面 oracle（斜平面精确解、抛物线弧长闭式、阶梯遮挡）。
- **M3 LOS**：曲率/折射可选披露、双向、首遮挡点、nodata 策略；viewshed 不动。
- **M4 工具化**：`terrain_profile` / `line_of_sight_analysis` / `surface_distance_analysis` /
  `surface_area_analysis`（terrain_analysis.py 增量，同构既有模式）。
- **M5 接线**：dispatch FeatureCollection → ref → MapSpec 图层验证（converter 测试）；2D/3D 同感
  （GeoJSON 3D 坐标）。
- **M6 资源/确定性**：ResourceEnvelope + chunk_boundary 取消 + 参数全量入 analysis key（垂直参数
  是工具参数自然入键）+ 收敛指示。
- **M7 Oracle/负例/E2E**：terrain.json 增 cases + regen manifest；≥6 科学负例；工具级 E2E
  （真实 GeoTIFF fixture → 工具 → payload 断言）。
- **Review**：两轮独立 review（科学正确性/ABI/资源取消），INDEPENDENT_REVIEW.md。
- **交付**：原子 commits → push → PR（不合并）。

## 交付文件清单（预期）

| 文件 | 变更 |
| --- | --- |
| `app/lib/geo_analysis/measurements.py` | 新增：VerticalReference + profile/LOS/surface_distance/surface_area 纯算法 |
| `app/lib/gis/algorithms/terrain.py` | 新增 4 个 AlgorithmDescriptor（resource/cancel/tolerance ratchet） |
| `app/lib/gis/capabilities/terrain.py` | 新增 terrain_measurement 能力包 |
| `app/lib/gis/parameter_contracts.py` | 新增 4 个参数契约 |
| `app/tools/terrain_analysis.py` | 新增 4 个工具（同构模式） |
| `tests/unit/lib/test_terrain_measurements.py` | 解析曲面 oracle 单测 |
| `tests/unit/test_terrain_measurement_tools.py` | 工具级测试（真实 GeoTIFF fixture + 负例） |
| `tests/unit/test_measurement_mapspec_wiring.py` | M5 接线（converter/MapSpec） |
| `tests/science_oracles/data/terrain.json` + manifest | 新增 oracle cases（regen 同 PR） |
| `docs/catalog/execution-catalog/*` | regen（新算法/工具计数） |
| `docs/adr/0274-*.md`、`planning/g3d03-scientific-analysis/*` | 契约与证据 |

## 验证命令（本地，串行/受控）

```bash
export CMAKE_BUILD_PARALLEL_LEVEL=4 MAKEFLAGS=-j4 NINJAFLAGS=-j4 GDAL_NUM_THREADS=2 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2
python -m pytest tests/unit/lib/test_terrain_measurements.py tests/unit/test_terrain_measurement_tools.py tests/unit/test_measurement_mapspec_wiring.py tests/science_oracles/ -x -q
python -m pytest tests/unit/gis/test_science_contract_v4_ratchet.py tests/unit/gis/test_g02_science_oracles.py -q   # 契约 ratchet/守门不回归
python scripts/gen_science_catalog.py && python -m pytest tests/unit/gis/test_execution_catalog.py tests/unit/gis/test_execution_catalog_staleness.py -q
python scripts/check_tool_capability_conformance.py --tool terrain_profile --tool line_of_sight_analysis --tool surface_distance_analysis --tool surface_area_analysis
python -m pytest tests/unit/test_terrain_compass.py tests/unit/lib/test_terrain_science_vnext.py -q   # 既有 terrain 不漂移
```

前端不改动（M5 走既有 MapSpec 通道，GeoJSON 3D 坐标 MapLibre 原生兼容）；若 review 发现需要
前端消费改动则重新评估范围。
