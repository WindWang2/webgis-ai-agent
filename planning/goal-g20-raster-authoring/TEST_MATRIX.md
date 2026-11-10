# TEST_MATRIX — G20 定向验证（持续更新）

命令模板（worktree 根，单 worker / 有界数值线程）：

```
$env:OMP_NUM_THREADS='1'; $env:OPENBLAS_NUM_THREADS='1'; $env:MKL_NUM_THREADS='1'
python -m pytest <files> -q -p no:cacheprovider
python -m app.lib.cartography.ts_projection   # 生成物漂移
npm exec --yes -- pnpm@9 --dir frontend exec vitest run <files> --pool=threads --poolOptions.threads.singleThread
```

## 复用（兼容回归，基线已有）

| 测试 | 目的 | 结果 |
|---|---|---|
| `tests/unit/test_raster_spec.py` | 旧 `RasterStyleSpec` 兼容 | 待跑 |
| `tests/unit/test_raster_tile_service.py` | 窗口读/全局归一/零值/nodata/失效竞态 | 待跑 |
| `tests/unit/test_raster_runtime_v3.py` | descriptor/fingerprint 兼容 | 待跑 |
| `tests/cartography/test_raster_stretch_payload.py` | 量化 payload golden 不变 | 待跑 |
| `tests/cartography/test_ts_projection_contract.py` | 生成 TS 类型与 schema 一致 | 待跑 |
| `tests/unit/test_mutation_registry_consistency.py` | 未动 intent 集合 | 待跑 |
| `tests/cartography/test_mutation_parity_corpus.py` | 未动 parity golden | 待跑 |
| `frontend/lib/mapspec-compiler/*.test.ts`（compiler/runtime/reconciler） | 旧 imageRef/底图回归 | 待跑 |
| `frontend/components/hud/layer-style-panel.test.tsx` | 旧禁用控件回归 | 待跑 |

## 新增（真实业务行为）

| ID | 用例 | 断言 | 状态 |
|---|---|---|---|
| A1 | 表达契约：2 波段默认 | 默认单波段 `[1]`，显式二元 RGB → `BAND_COUNT_UNSUPPORTED` | pending |
| A2 | 表达契约：非法 band / NaN / 反向范围 / 未知色带 / 不支持重采样 / 分类+插值 | 各自具体 code，不产生 spec 写入 | pending |
| A3 | 4 波段 3/2/1 → 4/3/2 | 已知 fixture 的 tile PNG 指定像素按通道顺序变化 | pending |
| A4 | 单波段指定色带+范围 | 像素变化、图例端点同步、数据指纹不变、分析调用计数 0 | pending |
| A5 | 手动 stretch 跨相邻 tile | 同值区域颜色一致（无局部接缝）；自动范围方法/sample_count 冻结 | pending |
| A6 | 缓存隔离 | 同源两层不同 style_id → 不同缓存条目，改一层不影响另一层 | pending |
| A7 | 提交/恢复/撤销 | `UpsertLayerIntent` 一次提交，reload 后 `raster_style.style_id` 一致；undo 回到上一表达 | pending |
| A8 | stale_style / 数据修订 | tile/metadata/probe 对旧 style_id/旧 data_revision 返回冲突或 stale，不出旧像素 | pending |
| A9 | probe 源值 | 非 WGS84 CRS 转换、行列、原值、scale/offset、单位 unknown 如实 | pending |
| A10 | probe 状态 | 点在外 / mask 洞 / 无 CRS 三种可区分 state，不以 0 代替未知 | pending |
| A11 | 0 值有效 vs nodata=0 | 前者有效参与拉伸，后者被 mask，统计与检视一致 | pending |
| A12 | 全 nodata / 常值 | 无除零、无伪极值、图例与探针明确实际状态 | pending |
| A13 | 分类 nearest | 缩放不造中间类别；声明连续插值时拒绝 | pending |
| A14 | 展开 clip 域 | 重新读取真实窗口；uint8 载荷不能恢复截断极值 | pending |
| A15 | 权限 | 跨会话猜 ref 的 descriptor/tile/probe 均失败且不泄漏路径/像元 | pending |
| A16 | 预算/失效 | 点数/波段/桶/窗口上限返回 truncated；失效后完成请求不回灌缓存 | pending |
| A17 | 图例同源 | 端点与 tile 与导出说明的 min/max/palette/bands 完全一致；RGB 不伪造连续色标 | pending |
| A18 | 导出冻结 | 导出中继续改样式，文件仍只对应冻结 revision；PNG 可读、含栅格与图例 | pending |
| A19 | SVG/PDF 离线 | 栅格不因 token 过期消失；不支持格式显式降级 | pending |
| A20 | Pi stubLLM 旅程 | 「查看波段→设置表达→点位解释→导出」调用真实注册工具；结果有界 | pending |
| A21 | 旧 PNG / 外部底图 / 旧 tile 回归 | 三条旧路径行为不变；无数值源的 PNG 不出现伪波段 | pending |

## 固定测试数据

`tests/fixtures/raster_authoring/`（由测试 helper 现场生成，不签入二进制）：
- `one_band.tif`：EPSG:4326，float32，常值区 + 梯度区，无 nodata，含合法 0。
- `two_band.tif`：2 波段 uint16（验证 2 波段默认与二元 RGB 拒绝）。
- `four_band.tif`：4 波段，第 1..4 通道取已知互不相同的阶梯值（验证 RGB 顺序）。
- `nodata_zero.tif`：nodata=0（0 被排除）。
- `classified.tif`：整数类别编码 + 声明类别语义。
- `epsg3857_rotated.tif`：非 WGS84 / 旋转网格（CRS 转换与行列匹配）。
- `all_nodata.tif`：全 nodata。

## 证据落盘

`planning/goal-g20-raster-authoring/evidence/`：MapSpec 前后 diff、tile 像素采样、
probe 期望值、图例 JSON、导出 PNG/SVG 与命令日志。
