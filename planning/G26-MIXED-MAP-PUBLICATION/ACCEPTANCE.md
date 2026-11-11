# G26 ACCEPTANCE — 验收矩阵 A01–A28 映射

标注：✅ 已验证（测试/证据）｜🟡 部分（有证据但覆盖不完整）｜⛔ 外部阻断（如实记录）
测试基线：本分支 HEAD；Python .venv 3.13.17 + WeasyPrint 70 + rsvg-convert（宿主）。

| # | 验收项 | 状态 | 证据（测试/位置） |
|---|---|---|---|
| A01 | 矢量+单波段栅格完整 PNG | ✅ | `test_mixed_map_publication.py::TestMixedPng::test_png_composition_pixel_probe`（渐变角像元 + 矢量圆点像素核验）；服务级 `test_png_real_rasterization` |
| A02 | 同图 SVG 嵌入栅格保留矢量文本 | ✅ | `TestMixedSvgCompilation::test_svg_embeds_raster_and_keeps_vector_text`（data URI + `<text>` + 标注 + 标题） |
| A03 | 同图 PDF 图像在场 + 文字可提取 | ✅ | `TestMixedPdf::test_pdf_image_objects_and_extractable_text`（图像对象 + 中文标题/标注提取）；服务级 PDF E2E |
| A04 | 多个不同 extent 栅格按坐标对齐 | ✅ | `test_two_rasters_align_by_coordinates`（A-only 暗 / B-only 亮像素分区） |
| A05 | 不同 CRS 正确重投影或明确拒绝 | ✅ | `test_publication_window.py::test_3857_source_aligns_through_transform`（3857↔4326 一致性）；不可重投影 CRS → typed INVALID_CRS（SUPPORT_MATRIX §3） |
| A06 | 部分/完全无交集不读错边缘 | ✅ | `test_disjoint_view_returns_transparent_not_edge_pixels`；`test_frames_disjoint_extent_disclosed_per_frame`；半区断言 `test_left_half_view_only_reads_left_half` |
| A07 | nodata 透明 | ✅ | `test_nodata_pixels_transparent`；合成级 `TestNodataAndAlpha::test_nodata_transparent_in_composed_png` |
| A08 | 全 nodata/常值说明正确 | ✅ | `test_g26_legend_and_fonts.py::test_constant_note_flows_to_footnote`（A08 图例脚注）+ 窗口 `all_nodata` notes + `raster_window_degraded` 披露 |
| A09 | RGB 通道顺序与 live 一致 | ✅ | `test_rgb_all_channels_valid_rule`（R/G/B 冻结 stretch 同 spec）；图例 R←band4/G←band3/B←band2 顺序断言 |
| A10 | alpha 与图层顺序一致 | ✅ | `test_raster_between_vector_layers_keeps_order`（SVG 层序）+ PDF 分层 `<img>`（E0.2 序保持） |
| A11 | 冻结 revision 导出中不变 | ✅ | CAS：`test_expected_revision_cas` + `test_stat_key_mismatch_rejects` + `test_spec_tampering_rejected`（style_id 篡改 → STALE_STYLE） |
| A12 | 离线打开不依赖 session token | ✅ | `test_svg_full_map_self_contained`（无 session 依赖断言）+ data URI 自包含（E0.3） |
| A13 | 中文标题/标注无系统性缺字 | ✅ | PDF 文本提取（A03 同测试含「混合地图出版链」「西部站」）；CJK 内嵌修复（`test_font_data_uri_fetcher_returns_response_object`）；缺字扫描脚本见 TEST_EVIDENCE E5 |
| A14 | legend 分级/单位来自同一表达 | ✅ | `test_unit_row_present`（单位：m）+ derive 自冻结 legend_spec（G20 单源）；G20 服务测试既有 |
| A15 | 共享 legend 不混合计数与率 | ✅ | G15 既有 shared_expression 同量纲契约（`test_frame_product_publication.py` shared breaks 测试）不变；G26 不触碰 |
| A16 | 高 DPI 有真实分辨率披露 | ✅ | manifest `renderer.effective_dpi` + 窗口 `truncated` 披露（`test_max_side_truncation_disclosed`；resolution_capped note） |
| A17 | 四 frame 资源不串 id/clip | ✅ | `test_four_frames_each_extent_aligned_unique_ids`（4 帧唯一 bounds + 每页恰一绘制图像 + 帧命名空间既有 fp{i}-） |
| A18 | 每 frame extent 和 raster 正确 | ✅ | 同上（NW/SE 帧覆盖南北分区断言）+ `test_frames_disjoint_extent_disclosed_per_frame` |
| A19 | 未支持底图/层不能标完整成功（含编译期窗口失败） | ✅ | `test_missing_binding_discloses_not_silent` + `test_external_basemap_blocking_vs_partial`（EXPORT_BLOCKED_INCOMPLETE）+ **编译后复检**（`TestPostCompileRecheck`：窗口失败不得报 complete，review P1-2）+ PDF 披露不误报（`test_pdf_without_bindings_still_discloses_omission`） |
| A20 | 显式 partial 有完整遗漏清单 | ✅ | manifest `omitted_layers`（layer_id+reason）断言 |
| A21 | PDF 任意网络/file 读取被拒 | ✅ | `test_external_and_non_png_schemes_denied`（http/https/file/svg+xml 全拒）+ 既有 `test_url_fetch_denied` |
| A22 | 大像素/内存超预算 typed 失败 | ✅ | 窗口 2048 边 + 22M 像元解码预算（truncated 披露）；嵌入 24MB fetcher 拒绝；页预算 typed PageCompileFailure；PNG rasterizer 缺席 503 typed |
| A23 | 取消不留半文件 | ✅ | `_persist_export_file` 原子写（tempfile+os.replace 既有）；G26 不新建写盘路径 |
| A24 | 产物权限隔离 | ✅ | `_set_export_owner` + 下载路由 owner 校验/HMAC（既有，`test_persist_writes_artifact_manifest_owner` 断言 owner 流程）；跨租户 403 路由守卫 |
| A25 | 旧 vector-only 与 G20 专用 snapshot 兼容 | ✅ | 50 publication 回归 + `TestRasterOnlyToolCompat::test_g20_snapshot_path_unchanged` + `test_pdf_without_bindings_still_discloses_omission` |
| A26 | 真实 Pi 调用现有 export 工具获得可核验产物 | ✅ | `test_mixed_export_route_and_tool.py::TestMixedExportTool`（ToolRegistry 真实 dispatch + refs-only 回执）；Pi 子进程级验证记录于 TEST_EVIDENCE E4 |
| A27 | 真实浏览器查看/下载同一产物 | 🟡 | 签名下载 URL + HUD exports 列表接线（前端混合路由测试）；真实浏览器 E2E 未跑（无浏览器会话授权环境，见 TEST_EVIDENCE E4 阻断说明） |
| A28 | 导出结构/像素探针可复跑 | ✅ | 全部断言为可复跑 pytest/vitest（命令见 TEST_EVIDENCE） |

## 说明

- A27 🟡：下载 URL/导出列表/HUD 消息的前端链路有测试（mock API 层）；真实浏览器
  端到端在本机无授权浏览器会话下未执行 —— 按「明确外部阻断」记录，不冒充完成。
  产物本身可经签名 URL 直接打开（服务级断言）。
- A15：G26 未改变 G15 共享图例契约（共享 breaks 同源 stamped legend_spec）。
