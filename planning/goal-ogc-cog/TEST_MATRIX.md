# TEST_MATRIX —— GOAL-02

约定：全部离线、无密钥、确定性。执行命令统一 `python -m pytest -q <path>`（-j 语义单进程，符合 A3）；前端 `pnpm exec vitest run <file> --maxWorkers=2`。

| ID | 阶段 | 测试文件 | 验证点 | Oracle |
|---|---|---|---|---|
| T1.1 | P1 | tests/unit/test_ogc_tiles_discovery_v1.py | fixture 真响应往返：probe/list_datasets(=tilesets)/describe；conformance 三态；crs 未声明→None | canned JSON 断言 |
| T1.2 | P1 | 同上 | 恶意/失败：404 collections、5xx、超大 conformance、tileset 无 TMS 链接 → typed 错误不落 stub | 抛 typed error |
| T1.3 | P1 | tests/unit/test_ogc_coverages_discovery_v1.py | domainset/rangetype 发现、格式声明三态、itemType 非 coverage 不误报 | 同上 |
| T1.4 | P1 | tests/unit/test_registry_protocols_v1.py（新增部分） | ogc_api_tiles/ogc_api_coverages 注册与 resolve；KNOWN_PROTOCOLS 接受 | registry API |
| T2.1 | P2 | tests/unit/test_ogc_tiles_proxy_v1.py | 代理：缓存 hit/miss、ETag/304、远端 404→204、字节帽、范围外 z/x/y 拒绝、非 WebMercatorQuad 拒绝 | 服务单测 |
| T2.2 | P2 | tests/unit/test_ogc_tiles_mapspec_v1.py | vector tileset → MapSpec vector source（tiles/minzoom/maxzoom）落库 + parse_mapspec 校验；raster tileset → data_fabric url | mapspec 断言 |
| T2.3 | P2 | frontend runtime vitest（runtime.test.ts 增例） | vector source 应用到 mock renderer（tiles 数组透传） | vitest |
| T2.4 | P2 | tests/unit/test_ogc_tiles_route_v1.py | 路由鉴权先于缓存（未授权 401 不产生缓存条目）、tenant 隔离键 | route 测试 |
| T3.1 | P3 | tests/unit/test_coverage_read_v1.py | COVJSON subset→COG ref：像元值/nodata/transform/分辨率与 fixture 真值一致 | 数值断言 |
| T3.2 | P3 | 同上 | GeoTIFF 输出格式路径；预算拒绝（超 max cells/bytes→reason=oversized）；未声明格式→capability_not_declared | typed reason |
| T3.3 | P3 | 同上 | ref 可被栅格工具消费（zonal 统计往返） | 消费链 |
| T4.1 | P4 | tests/unit/test_cog_materialization_v1.py | 127.0.0.1 真实 HTTP COG：discover→select→preview→bounded materialize→ref；AOI 窗口读字节数 << 整幅 | requests/bytes 账本 |
| T4.2 | P4 | 同上 | 幂等（同参数重跑→CAS dedup 同 data_object_id）；变化作废（文件变更→新指纹） | id 断言 |
| T4.3 | P4 | 同上 | fail-closed：404/429/5xx/私人地址(默认 allow_private=False)/炸弹压缩 → typed reason；取消令牌中断 | typed reason |
| T4.4 | P4 | 同上 | 签名 URL 脱敏（descriptor/回执不含 token 参数）；provenance/fingerprint 字段在场 | 字符串断言 |
| T5.1 | P5 | tests/unit/test_ogc_service_tools_v1.py | inspect_data_service/read_coverage_subset/materialize_remote_raster_asset 注册、descriptor 纪律、tier/side_effect 正确 | registry |
| T5.2 | P5 | 同上 | G01 双轨资格映射：tile-only→display_only；COG ref→numeric_raster | 映射断言 |
| T5.3 | P5 | tests/data/test_prepare_accepts_remote_refs_v1.py | #1868 inspect 接受本方向 ref（收据四态不回归） | prepare 面测试 |
| T6.1 | P6 | frontend 组件测试 | SOURCE_TYPES 新协议可见 + 提交 payload 字段 | vitest |
| T7.1 | P7 | 并入 T4/T3 | 回执含 requests/bytes/cache_hit；采样对照 nodata/transform；ref 不灌 LLM（result_size_policy=ref_offload） | 断言 |
| T8.1 | P8 | tests/e2e 或 tests/unit/test_goal_e2e_scenarios_v1.py | 场景①②③ 全链（dispatch→工具→ref/MapSpec→证据） | E2E |
| T8.2 | P8 | 关键测试双跑 | 重复执行两次结果一致 | 两次全绿 |

回归面：ruff check 新文件、python -m compileall、既有 adapter/registry/mapspec 相关测试抽跑（test_data_fabric_adapters.py、test_ogc_adapters_753.py、mapspec 契约）、前端 typecheck。
