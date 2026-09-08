# Science V4 — Progress（持续更新，最终态）

## 提交链（12 waves + 收口）

| commit | wave |
|---|---|
| 25460e0 | Phase A/B：审计基线 + 架构 + 计划 |
| 16fd7de | W1 契约 ratchet + 42 descriptor 三声明 + ratchet 测试（48） |
| 953956f/b0f5f38 | W2 typed errors + CRSError 边界收口（KNOWN-GAP #1 转正） |
| 10183b3 | W3 geometry repair 披露 + zonal strict 门（KNOWN-GAP #2 转正） |
| 9a548a5 | W4 SK/KED/normal-score/嵌套 variogram（+驱动/工具/契约/descriptor） |
| f25a709 | W5 SGS（kriging_simulation 模块 + sgs_simulation 工具 + ensemble 契约） |
| 980da83 | W6 LMC 全共克里金（PSD 按构造 + cokriging_lmc_surface 工具） |
| 7312e2b0 | W7 时空克里金（separable/product_sum + st_kriging_surface 工具） |
| 94752805 + bdbf0ed5 + 9b53c7fc + 3ed4da06 | W8/W9 水文与地形 V4（6 算法 + hydrology_v4_analysis 工具 + 修复链） |
| 4a8d800d | W10 分块 PF（approximate variant）+ terrain 取消下沉 + variant 对 |
| e53fbc4b | W12 PLATFORM_V4.md + science_v4 oracle 域 + CHANGELOG |

## 验证汇总（Phase D）

- oracle replay：**1092/1092 绿**（新增 science_v4 域 7 例：FAO-56 解析锚 ×4、
  线性坡 HI=0.5 不变量、normal-score 秩-分位锚 ×2）
- broad sweep（tests/unit/lib + tests/unit/gis）：**1095 passed, 2 skipped**
- tests/quality/：**240 passed**（2 xfailed = cartography KNOWN-GAP，非本 Epic ownership）
- tools 车道（catalog/surface_v3/backend_sdk/raster_tools）：**65 passed**
- registry validate：**192 algorithms / 0 issues**
- ruff：全绿
- 字节 parity 门：catalog / benchmark manifest / quality manifest / 认证表 ×3 /
  drift report / quality report 全部再生成且绿

## 本 Epic 解决的 P0/P1

1. resource_envelope / cancellation_profile / tolerance 0/181 → ratchet + ownership 域全量
2. raw pyproj.CRSError 泄漏（RuntimeError 逃逸 ValueError 映射）
3. geometry 静默 make_valid（zonal bowtie 静默统计）
4. InvalidGeometry 0 raise / NumericalInstability / ConvergenceFailure 无生产者
5. terrain.py 0 取消点
6. KED/SK/SGS/CoK-LMC/ST-kriging/breaching/HAND/Shreve/Pfafstetter/
   hypsometry/solar 全缺
7. 非插值域 0 backend variants
