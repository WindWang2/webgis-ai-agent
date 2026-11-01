# LOOP_STATE —— GOAL-02

## 当前阶段
P0 完成（基线勘察+规划），进入 P1（M1 能力发现 + M7 fixture）。

## 轮次记录

### Round 0（2026-11-01）—— 基线与规划
- 结果：worktree 建立（6c5e71c2c）；双 subagent 勘察 + 主 Agent 亲读 12+ 关键文件；BASELINE/PROTOCOL_CAPABILITY_MATRIX/TASK_PLAN/PR_OVERLAP_MATRIX/DECISIONS/TEST_MATRIX/RISK_REGISTER 落盘。
- 客观证据：`git worktree list`；`gh pr list`（#1868-#1875 merged、无 open PR）；#1868 无 GitHub review（reviews=[]）。
- 关键判定：OGC Tiles/Coverages 零实现；COG 物化悬空（cog_adapter.py:163 指向不存在的 raster 物化）；渲染侧 vector/url 两条路径已可用，仅需 mapspec_source vector passthrough。
- 下一轮：P1 fixtures + 两 adapter（失败测试先行）。

### Round 1（2026-11-01）—— P1/M1+M7fixture 完成
- 结果：OGC Tiles/Coverages 发现 adapter + 三态能力评估 + fixtures + registry 四处接线全部落地；16 项新测试全绿；相邻回归（test_data_fabric_adapters/test_adapter_contract_v2/test_ogc_adapters_753）36 passed/1 skipped；ruff 全清。
- 客观证据：`python -m pytest -q tests/unit/test_ogc_tiles_discovery_v1.py tests/unit/test_ogc_coverages_discovery_v1.py` → 16 passed（见 EVIDENCE）。
- 缺陷修复记录：①CRS 判定集合大小写不一致（crs_not_webmercator 误报）→ 统一小写比较；②tileset 未内嵌 TMS 时 zoom unknown → 跟随 tiling-scheme 链接（同源校验）读服务声明；③fixture collections 列表缺 tilesets 链接（真实服务会声明）。
- 下一轮：P2 瓦片代理 + MapSpec 桥。

### Round 2（2026-11-01）—— P2/M2 完成
- 结果：OGC Tiles 消费链闭环 —— adapter.fetch_tile（SSRF 同源模板/display 门/zoom 矩阵/单瓦片字节帽/401/403/429 typed）+ ogc_tiles_proxy（DfTileCache `ogc-tiles:` 前缀/内容寻址 ETag/single-flight）+ 会话鉴权路由（API-01 鉴权先于缓存）+ build_mapspec_source（vector tiles / data_fabric url 双形态，不新增 schema 版本）+ mapspec_source vector passthrough 最小扩展。
- 测试：proxy/metadata 31 passed；前端 runtime 36 passed（含 2 新增：相对路径 vector 模板 + fabric url→addRasterTileSource）；mapspec_source 回归 57 passed。
- 缺陷修复记录：①路由测试依赖覆盖错对象（get_current_user vs optional 变体）→ 匿名访问被租户门正确 404（安全门工作正常的证据）；②parse_mapspec 产物访问 .document 而非属性；③zoom 范围断言改为"瓦片请求零增量"（describe 元数据调用属预期）。

### Round 3（2026-11-01）—— P3/M3 完成
- 结果：coverage_read.py（三态资格门→轴协商（轴名服务声明/封闭经纬名表）→像元预算门（请求前按声明网格+subset 收缩比，不静默降采样）→bounded_get 字节帽→COVJSON/GeoTIFF 封闭解析→COG 落地）+ raster_materialization.py（ref:fabric-cog/ 工件纪律：原子 move/sha256/CAS DataObject/台账）+ artifact_registry.fabric_cog_path。
- 真值对照：3×3 网格逐点断言（含 nodata 位、transform 半像元角点、north-up 翻转、subset 真子集语义）；ref 可被栅格统计消费（G01 numeric raster 实证）；9 测试绿 + artifact_registry 回归 12 passed。
- 缺陷修复记录：①north-up yres 符号推导错（降序轴步长已为负，多余取负）——被逐点 transform 断言拦截；②fixture subset 真值语义错（按子网格尺寸重算 ≠ 真实服务的真子集行为）——改为全网格按索引选取。

### Round 4（2026-11-01）—— P4/M4 完成
- 结果：cog_materialization.py 落地 —— discover（STAC search→COG 候选 declared/inferred 标注）/preview（SSRF 门+头读+cog_structure_ok advisory）/materialize（bbox→transform_bounds→窗口读，RemoteReadSession 请求/字节预算+取消，预算前置门）/publish（ref:fabric-cog + sha256 + CAS + 台账 + 签名 URL query 整段脱敏）。
- 测试：真实 loopback HTTP COG（自写 Range+HEAD 服务器）10 passed；幂等（同参同内容同 data_object_id + CAS deduped）；AOI 窗口语义（窗口 ≪ 整幅）+ 逐点真值；预算拒绝带窗口事实；默认 SSRF fail-closed（loopback 被拒的专门用例）；签名 URL query 脱敏断言；取消令牌。
- 已知伪影（RISK R2）：GDAL /vsicurl 对 Python http.server 的 .msk/.MSK 首访探测在本机恒定 ~5s×2（curl CLI 亚毫秒、服务端即时响应，GDAL 内部行为）；已用 GDAL_PAM_ENABLED=NO（远程只读正当优化）消除 25s/次，残余 ~10s/open 如实记录。CI 成本 ≈94s/模块。
- 缺陷修复记录：①ExitStack 改造时 rasterio.Env 用在 import 前；②rasterio read 的元组 window 是 (start,stop) 语义（测试写错为 (offset,size)）；③fixture 需自写 Range/HEAD handler（Python 3.13 http.server 仍不支持 Range）。

### Round 5（2026-11-01）—— P5/M5 完成
- 结果：ogc_service_tools.py 四工具（inspect_data_service/read_coverage_subset/materialize_remote_raster_asset/add_tile_service_layer）全量注册无重名 + service_qualification.py G01 双轨（tile→display_only 不可数值；coverage-ready/COG ref→numeric_raster）+ probe 语义收紧（conformance/端点可辨识，不再 collections-200 冒充 tiles）+ #1868 契约验证（raster ref → 类型化 InspectError，不伪造收据）。
- 测试：11 passed（注册纪律/全量装载查重/tiles 与 coverages 自动识别三态/资格双轨/#1868）；discovery 回归 16 passed 不受 probe 收紧影响。
