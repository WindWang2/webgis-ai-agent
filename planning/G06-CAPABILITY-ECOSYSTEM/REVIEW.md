# G06 — 独立冷审记录

> 由子代理 B（`reviewer-b`）在代码定稿后逐文件冷读
> `git diff origin/master...HEAD`，**不复用主 Agent 的通过结论**。
> 本文件在冷审返回后填入实际 findings 与修复证据；P0/P1 清零后才进 M9。

## 审查范围

- 逐文件 review `git diff origin/master...HEAD`（28 个文件）。
- 维度：设计（唯一事实源）、科学语义（量纲/CRS/nodata/fallback）、
  权限与隔离、性能与预算、回归风险、测试有效性、文档真实性。
- 特别核查：
  1. 是否出现第二 registry / catalog / 认证 / Planner / 渲染器；
  2. `native_verified` 是否可能被伪造；
  3. 权限 fail-closed 是否泄露 descriptor 细节；
  4. 两项原语是否真实计算（非 no-op / mock）；
  5. 是否触碰 G01–G05 禁区（`pi_native_surface.py` / `agent_pi_bridge.py` /
     `llm_result_formatter.py` / `families.py` / 数据 fabric / MapSpec）；
  6. 测试是否出现「改断言让错误消失」或空测试；
  7. 生成物是否手工编辑、是否引入与本方向无关的大 diff。

## R1 冷审结论（子代理 B，只读冷读 `git diff origin/master...HEAD`）

结论：**APPROVE-WITH-FIXES**；P0 = 0，P1 = 4 未清零。
审查覆盖 35 文件 / 6 commit，确认无越界、无第二事实源、`native_verified`
不可伪造、脱敏字段 11/11 完整、两项原语真实计算、`geometry_mask(invert=True)`
语义正确、生成物由既有生成器产出、文档未宣称 CI 通过。

| ID | 级别 | 问题（文件:行号） | 处置 |
|---|---|---|---|
| F-01 | **P1** | `capability_ecosystem_tools.py:291` `rasterio.open(raster_path)` 无路径校验，`/vsi*` 可出网（SSRF），descriptor 却标 `network=False` | ✅ 修 |
| F-02 | **P1** | `:439-456,:489,:532-542` `cost_unit=seconds` 仍用几何长度作边权，science 却伪称 `time` / `travel time`；主路径无换算 | ✅ 修 |
| F-03 | **P1** | `:294` 先 `read(1)` 后判界可 OOM；`GIS_CAPABILITY_ECOSYSTEM.md:84` 误写 "windowed" | ✅ 修 |
| F-04 | **P1** | `test_..._oracles.py:624-649` 慢 provider 桩（sleep(30) patch 到 `get_record`）从未触发，`TEST_MATRIX.md` 却写「已验证」 | ✅ 修 |
| F-05 | P2 | `method_eligibility.py:279-287` `or`/`and` 优先级：`allow_transform=True` 时 REQUIRES_TRANSFORM 落到 eligible | ✅ 顺手修 |
| F-06 | P2 | 秒/米与 geographic 词表封闭，漏检 miles/ft/radius_units 等 | ✅ 顺手修 |
| F-07 | P2 | `capability_schema_parity.py:45` 对每个工具全量遍历 schemas = O(n²) | ✅ 顺手修 |
| F-09 | P2 | `availability_rerank.py` 输入侧无界 | ✅ 顺手修 |
| F-12 | P2 | 域包 docstring 引用不存在的 `capability_ecosystem_primitives.py`；gitignore 行号错 | ✅ 顺手修 |
| F-13 | P2 | 两处恒真/宽松断言 | ✅ 顺手修 |
| F-14 | P2 | 语料 `expect_status` 死字段，且把新原语错标 `native_verified` | ✅ 顺手修 |
| F-08 | P2 | 快照无缓存、主循环未早停、`truncated` 边缘 | 记账为后续（ADR-0287 已披露；不在生产热路径） |
| F-10 | P2 | `extension_lifecycle_view` 读私有 `_policy.version_pins`；`host` 参数部分冗余；trust 未纳入门禁 | 部分修（`host` 现在被 `qualify_provider` 真实消费、扩展停用 fail-closed）；私有 pins 读取保留为只读披露，列为后续 |
| F-11 | P2 | 多方案 Jaccard 只比前 2 且未披露 | ✅ 顺手修（全量两两矩阵 + science 披露） |

## R1 修复记录（逐条可复验）

- **F-01**：复用既有 `app/utils/path.validate_data_path`；显式拒绝
  `/vsi*` 与 `://`；路径不在契约内的错误为 typed `RASTER_PATH_FORBIDDEN`。
  新增 `test_r1_f01_raster_path_guard_rejects_escape_and_remote`（5 个恶意输入）。
- **F-02**：`_build_graph(time_mode=...)` 现在要求边属性提供
  `travel_time_s` / `duration_s` / `cost_s` / `speed_mps`；缺失抛
  `TimeCostMissing` → typed `TIME_COST_NOT_EVALUATED`（缺依赖不猜）。
  提供真实秒数时 science 明确 `cost_dimension=time` + 真实来源。
  新增 `test_r1_f02_time_mode_requires_real_time_cost`。
- **F-03**：`with rasterio.open()` 内先读 `ds.width*ds.height` 判界 +
  `RasterResourceGuard.check_grid`，**通过后才** `ds.read(1)`；文档改为
  如实描述「先判界再整幅单波段读取」。新增
  `test_r1_f03_large_raster_rejected_before_read`（mock dataset，断言
  `read()` 从未被调用）。
- **F-04**：`qualify_provider` 增加显式 `probe` 注入点（生产默认 None，
  不发起慢 I/O），协作式 deadline；测试改为注入真实慢探测并断言
  **探测被真实调用** + 降级 `not_evaluated` + 空权限仍 fail-closed。
  账本表述同步改为真实。
- **附带 mypy 发现的真实 API 误用**（冷审未列，主 Agent 自查）：
  `get_loaded_model_cache` / `get_provider_health_tracker` 不存在、
  `batch_for_budget` 签名不符。改为只读消费显式注入的既有事实 +
  按真实全关键字签名调用，不再靠 try/except 吞异常。

复验结果：
- Oracle 套件 **27 passed**（原 24 + 3 条 P1 回归用例）；
- `ruff check` **All checks passed!**；
- `mypy` 6 个新模块 **Success: no issues found**；
- `scripts/verify_capability_ecosystem.py` **6/6 passed**；
- `execution_catalog_docs --check` up to date；
- `tests/unit/gis/test_foundation_v2_infra.py` 60 passed。

## R2 二次 review

（对修复后的 diff 由同一 reviewer 再审，确认 P0/P1 清零、P2 已记账）
