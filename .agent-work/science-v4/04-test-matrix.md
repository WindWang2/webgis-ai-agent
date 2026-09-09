# Science V4 — Test Matrix（最终态）

## 车道与结果（Phase D + rebase 后复测）

| 车道 | 范围 | 结果 |
|---|---|---|
| broad sweep | tests/unit/lib + tests/unit/gis | 1095 passed, 2 skipped |
| quality gates | tests/quality/（manifest/drift/认证表/科学回归/取消/确定性） | 240 passed（2 xfailed = cartography KNOWN-GAP，非本 Epic） |
| oracle replay | 12 域 + science_v4 域 | 1092/1092（新增 7 例） |
| tools | catalog/surface_v3/backend_sdk/raster_tools + terrain 工具级 | 65+ passed |
| registry | validate() 结构自检 | 192 algorithms / 0 issues |
| lint | ruff app/ + tests/ | clean |
| V4 专项 | kriging_v4/simulation/st/cok/lmc/hydrology_v4/geometry_repair/errors_v4/ratchet | 全绿（约 60 用例） |
| rebase 后复测 | lib+gis+quality+oracles 全量 | 见 06-pr-summary（最终 green run） |

## 关键回归防线（本 Epic 新增）

- `test_science_contract_v4_ratchet`（48）：heavy 三声明 + allowlist 洗白检测 + ownership 硬门
- `test_scientific_regression`：2 个 xfail 转正（CRS 收口 / zonal strict 门）
- `test_oracle_replay`：新增 science_v4 域解析锚（FAO-56 / HI=0.5 / Blom 位置）
- parity：SGS seed 逐位复现；chunked PF 单侧偏差界 + 确定性双跑；KED 优于 OK；
  LMC PSD；ST τ=0 退化锚
- 工具级：hydrology_v4_analysis hypsometry 分支 e2e（review C2 回归）
