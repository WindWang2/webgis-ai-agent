# INDEPENDENT_REVIEW —— GOAL-02

## R1（2026-11-01，对抗式，Reviewer 未参与实现）

- 范围：`git diff origin/master...HEAD` 全量（47 文件 +6072 行，含代码/测试/前端/接线/文档抽查）；关键结论由 Reviewer 用真实模块执行复现（非推测）。
- 方法：逐文件审查 + 7 维度（正确性/安全/异步/API 兼容/诚实性/性能/文档验收）。

### Findings 与清偿映射

| ID | 级别 | 摘要 | 清偿（代码/测试 + 验证） |
|---|---|---|---|
| P0-1 | P0 | coverage CRS 恒返回伪造常量 OGC:CRS84（"未声明则拒绝"是死代码）；轴名表含 e/n 把 UTM Easting/Northing 判成经纬轴 → 投影 coverage 被贴错误地理参考且可进栅格统计 | `_resolve_covjson_crs`（COVJSON domain.referencing 封闭识别表，不可判 → None → typed 拒绝）；e/n 移出轴名表；fixture 补 referencing 声明。测试：`test_review_clearance_r1_v1.py::TestP0_1CrsHonesty`（无声明拒绝/投影轴不判经纬/声明直达输出 3 用例） |
| P0-2 | P0 | 格式判定大小写敏感：`assess_coverage` 原样比较 vs adapter 归一化 → 同一 descriptor 自相矛盾（capability 拒绝但 formats_supported 全在） | `assess_coverage` 改归一化比较；`normalize_coverage_format` 升级为参数序无关（media type 参数排序）。测试：`TestP0_2FormatNormalization`（大小写+参数序 2 用例） |
| P1-1 | P1 | coverage `_fetch` 把一切失败（含 503）报 `oversize_response` | 按异常类型/details 分流：字节帽族（details 带 cap）→ oversize；HTTP/网络（含 requests.HTTPError 路径）→ 新增 RC_UNREACHABLE。测试：`TestP1_1ErrorClassification`（503=unreachable / 微帽=oversize）+ test_coverage_read_v1 的 5xx 用例改 typed 断言 |
| P1-2 | P1 | `RemoteReadBudgetExceeded` 被兜底 except 归为 unreachable | 显式 catch → `RC_BUDGET_EXCEEDED`。测试：`test_wire_budget_mid_read_is_budget_exceeded`（max_requests=0 → budget_exceeded） |
| P1-3 | P1 | 相对 tile 模板/相对 tiling-scheme href 过 `ensure_same_origin_url` 必炸（真实服务常见相对链接零覆盖） | urljoin(tileset_url, href) 解析绝对后再同源校验（fetch_tile 与 tiling-scheme 两处）；metadata 增加 tileset_url_raw 作为解析基。fixture 增加 `relative_hrefs=True`。测试：`TestP1_3RelativeHrefs::test_describe_and_fetch_with_relative_template` |
| P1-4 | P1 | manager `_invalidate_catalog_tiles` 漏 `ogc-tiles:` 前缀（同 item 变更后 OGC 瓦片缓存存活） | 追加第三前缀失效。测试：`TestP1_4CacheInvalidation`（三前缀全清） |
| P1-5 | P1 | capabilities 默认矩阵 `ogc_api_tiles vector_tiles=True` 等与 adapter typed 拒绝矛盾（planner 放行到运行时才炸） | 两协议回退 `_generic_defaults` 全 False（QuerySpec 通道零下推是事实）。回归：test_adapter_contract_v2 / planning 测试通过 |
| P1-6 | P1 | dataset_id 未编码拼 URL（../、# 注入） | tiles/coverages 两 adapter 全部段级 `quote(id, safe="")`。测试：`test_dataset_id_injection_neutralized`（发出的 URL 无裸 #/..，段内 / 已编码） |
| P1-7 | P1 | COVJSON/GeoTIFF 解析与 GTiff 写盘在事件循环线程同步执行（最大 128MB json/numpy 在 loop 上） | 解析与写盘全部 `asyncio.to_thread`。回归：coverage 套件全绿 |
| P1-8 | P1 | overview_level>0 时预算门按抽样后尺寸估算但网络读全分辨率窗口（"绝不整幅下载"承诺失守）；decimated 分支字节重复记账 | est 按 wire 尺寸（w×h）估算（details 同时披露 wire/output 双口径）；移除重复记账；docstring 修正为"读后 nearest 抽样，不读服务端 overview 层" |
| P1-9 | P1 | 永真断言（`pytest.raises(Exception)` + isinstance Exception） | 改为 CoverageReadError + reason_code 精确断言 |
| P1-10 | P1 | TEST_MATRIX/能力矩阵虚报（GeoTIFF 路径、字节帽拒绝、资产变化作废、时间 subset/scale-factor 未实现却声称） | 补齐 6 组测试（GeoTIFF 真值/微帽 oversize/读中预算/资产变化新指纹/相对链接/缓存失效）；TEST_MATRIX 修正 T5.3 路径 + T-R1 行；PROTOCOL_CAPABILITY_MATRIX 删除时间 subset/scale-factor 声明并补 R1 修正节 |
| P2×12 | P2 | 见下表（选择性清偿） | |

### P2 清偿决策（逐条）

| P2 摘要 | 处置 |
|---|---|
| 死常量 RC_TILESET_UNREACHABLE/RC_NO_TILES_CONFORMANCE | 已删除（伪封闭枚举成员） |
| normalize 参数序敏感 | 已修（参数排序归一） |
| _describe_cache 无上限 | 已修（64 条 FIFO 上限） |
| probe/health 裸 session.get | 已修（bounded_get/safe_json_get + 帽） |
| 空 body+唯一声明类型 → 0 字节 200；gzip-PNG 嗅探歧义 | 接受（与 pmtiles 同源启发式；route X-Content-Type-Options: nosniff 已在）——记录 |
| _content_types 旁路 LRU 漂移；404 无负缓存 | 接受（嗅探兜底已在；负缓存属性能优化，非正确性）——记录 |
| route z≤24 vs 注册 TMS 0..29 | 已修（build_mapspec_source clamp maxzoom≤24，MapLibre 声明面=服务面） |
| registry is_raster_tile 语义 vs display_only | 保留（registry 语义="非矢量查询源"，与 wms/pmtiles 同款先例；display_only 双轨由 service_qualification 承载）——记录 |
| coverage 预算无 count 静默跳过/nodata 首波段/float32/缺波段静默 | 部分已修（bands_missing 进 provenance）；其余接受为封闭子集已知限制（矩阵披露） |
| inspect 短路只报首个协议 | 已修（双协议全探测 + also_available 披露） |
| lid 无校验/session_id 未 quote | 已修（ASCII 白名单 + URL 段级编码）。注（R2 N6 修正）：清偿过程中的一个中间态正则曾在本地运行时报 sre bad character range（不可见字符），该状态从未进入任何 git 提交，最终实现为显式白名单过滤 —— 早期"修复历史正则"的叙事不准确，以此为准 |
| mapspec vector tiles 元素不校验字符串 | 已修（all isinstance str） |

### 已知限制（披露，不掩盖）
1. GDAL /vsicurl 进程级 URL 缓存内，同 URL 远端内容变化可能命中旧缓存（跨进程/跨 URL 不受影响）；变化作废语义经 URL 版本参数验证（test_asset_change_invalidates_fingerprint）。
2. GDAL 对 Python http.server 的 .msk 首访探测 ~5s×2（loopback 测试环境伪影；GDAL_PAM_ENABLED=NO 已消除 PAM 族 -25s/open）。
3. 空 body 瓦片返回声明类型、gzip-PNG 魔数嗅探歧义 —— 与既有 pmtiles 服务同源启发式。

## R2（2026-11-01，第二轮独立复审，新 Reviewer 未参与实现与 R1）

- 方法：逐 finding 读代码 + 逐个读测试 + 4 组可执行复现 + 全量运行（107 passed/0 failed，与 LOOP_STATE 数字一致）。
- R1 清偿复核：P0-1 及 7 条 P1 清偿确认；**2 条清偿不足**（P0-2 残留 N2：coverage_read 格式选择未走归一化；P1-6 残留 N1：coverages 读取链/tiles tilesets 列表的 id 段未编码，实证 ../ 逃逸）；P1-1 部分清偿（N3：网络层异常未分流）。
- 新发现：N1/N2（P1，阻塞）、N3/N4/N5（P2）、N6（文档叙事不准确）。
- **N1–N6 已全部清偿**（见 Round 10）：id 编码补 4 处 + coverages 注入断言；格式选择归一化 + 大小写变体 E2E 断言；网络层/SSRF 异常分流 unreachable；inspect 构造入 try + 单条 describe 失败不丢协议结果；畸形数值 typed；文档叙事修正 + also_available 双协议测试 + P1-5 专项断言。
- Verdict（R2 原文）："需再修，修完可提 PR"——阻塞项 N1/N2 及 N3-N6 已全部修复并带回归断言（tests/unit/test_review_clearance_r1_v1.py 17 项）。
