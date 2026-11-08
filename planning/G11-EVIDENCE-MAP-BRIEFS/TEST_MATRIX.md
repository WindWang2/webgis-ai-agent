# G11 TEST_MATRIX —— Oracle × 测试 × 证据

分层标记：pure unit / real domain integration / real Pi+stub LLM / browser+real backend。

| Oracle | 条件 | 测试（文件::用例） | 层 | 证据（命令/退出码） |
|---|---|---|---|---|
| BRF-O01 | 真实产物草稿；每个自动数值有合法 artifact/字段/单位/时间范围证据 | test_mapbrief_api.py::test_draft_from_real_artifacts；test_mapbrief_core.py::TestBriefCompiler | real domain integration | pytest 14 passed / 32 passed（退出码 0，2026-11-09） |
| BRF-O02 | 分母缺失/单位不一/stale 披露或阻断，不编造比例 | test_mapbrief_core.py::test_ratio_without_denominator_rejected / test_change_without_baseline_rejected / test_ratio_contradicting_recompute_is_unverified / test_denominator_zero_disclosed / test_unit_mismatch_is_warning / test_stale_artifact_is_warning | pure unit | 32 passed |
| BRF-O03 | 编辑/锁定/删除重生成保留；并发 revision 冲突显式 | test_mapbrief_api.py::test_edit_text_and_lock / test_revision_conflict_409 / test_regenerate_preserves_manual_and_locked；test_brief_tools.py::test_locked_block_edit_rejected | real domain integration | 14+5 passed |
| BRF-O04 | 点地图/指标定位冻结来源；换城市不改旧简报 | test_mapbrief_api.py::test_inspect_evidence_full_chain；mapspec_fingerprint pin（test_draft_from_real_artifacts 断言 map_view 块指纹）；j13 real 下载 HTML 含 pin 指纹 | real domain + browser real | 14 passed / j13 real 1 passed |
| BRF-O05 | 预检区分 draft/未验证/可交付；失败导出不产生 ready | test_mapbrief_api.py::test_preflight_verdicts / test_freeze_blocked_on_missing_map（blocked → 409，无 release 行）；manifest files status=ready 断言 | real domain integration | 14 passed |
| BRF-O06 | 发布不可变、pin 数据/图件/证据；幂等；旧版可读 | test_mapbrief_api.py::test_freeze_idempotent_and_download（already_released + manifest） / test_release_immutable_after_edit（编辑后旧版字节一致） | real domain integration | 14 passed |
| BRF-O07 | HTML 断网可读、manifest 对账、缺离线交互诚实说明 | test_mapbrief_api.py::test_freeze_idempotent_and_download（零外链正则断言）；test_mapbrief_security.py::test_zero_external_links；j13 real 读取下载文件断言零外链 + brief-bundle 在场；brief_export.py 查看器缺图 warn 分支 | real domain + browser real | 14+13 passed / j13 real 1 passed |
| BRF-O08 | token/签名/owner secrets 不入交付；XSS/CSV/穿越负例失败封闭 | test_mapbrief_security.py 全文件（13 passed：XSS/早闭合/SVG/CSV/深度/NaN/REDACT/URL scrub/交付物无 token）；test_release_html_has_no_tokens 抓到 release_meta 泄漏后修复为白名单投影 | pure unit | 13 passed |
| BRF-O09 | private 默认、显式分享、过期/撤销、跨 owner 真权限测试 | test_mapbrief_api.py::test_share_lifecycle（撤销后 404）/ test_cross_owner_404 / test_unknown_session_404；j13 real 外部者 401-404 断言 | real domain + browser real | 14 passed / j13 real 1 passed |
| BRF-O10 | 浏览器真实后端：草稿→编辑→证据→预检→冻结→下载；Pi 同链 | e2e/j13-brief-delivery.journey.ts mock（1 passed）+ real（1 passed）；Pi 链：test_brief_tools.py::test_dispatch_via_pi_http_callback_adapter（agent_pi_bridge.dispatch_tool → ToolDispatchService → 真实领域计算） | browser+real backend + real Pi adapter | 见 j13 输出 1 passed×2；pytest 5 passed |
| BRF-O11 | 数据变化只标记受影响自动块；原 release 不变；manual 不被覆盖 | test_mapbrief_api.py::test_stale_marked_not_mutated / test_release_immutable_after_edit / test_regenerate_preserves_manual_and_locked；j13 real 面板 stale 徽标路径 | real domain + browser | 14 passed / j13 |
| BRF-O12 | 长文本/中文/多章节/缺资产/取消/GC 有回归、资源有界、不新建渲染器/证据库 | 模型边界（MAX_BRIEF_BLOCKS/FACTS、深度门、NaN、ID 词表）test_mapbrief_core 11 项；预算（512KB spec / 8MB HTML）test_mapbrief_security::test_oversized_bundle_rejected；既有渲染/事实库全部复用（OWNERSHIP 只读清单） | pure unit | 32+13 passed |

## MUST 指令覆盖

| 指令 | 验证 |
|---|---|
| 1 整理成简报 | j13 real（UI 草稿）+ test_brief_tools 全旅程 + test_mapbrief_api draft |
| 2 比例怎么来的 | test_mapbrief_api::test_inspect_evidence_full_chain；j13 mock 证据侧栏断言 field_path/权威值 |
| 3 保留结论只刷新指标 | test_regenerate_preserves_manual_and_locked；j13 real 编辑+锁定 rev2 |
| 4 先检查再导出私有 HTML | j13 real 预检→冻结→下载（零外链）+ 不公开原始点（默认私有 + 脱敏 + 白名单投影） |

## 跟进事项（复审遗留，不阻塞）

- N3（P3）：信任传播仅一层 —— 依赖为"因依赖降级的 warning"时二级派生
  可幸存 verified（复审探针 2c B 实证）。到达面受限（无 facts 编辑入口，
  仅内部构造可触发）；修复方向：降级打标 + 迭代至不动点。
- N3'（P3）：map_view 块静态图当前共用全图 SVG（compile_mapspec_to_svg
  不接收块镜头参数）；BRF-O04 的版本 pin 已达成，逐块镜头渲染待后续。
- N5（P3）：_locate_value 同值多字段可能错位绑定行（值核验不受影响，
  来源归因可能指到同值异行）。

## 预存红（master 债，非本分支引入）

- `lib/map-commands/catalogue-contract.test.ts`：webgis_requirement_compile
  幽灵命令（G02 db5303b17 引入，前端目录未登记；本分支未触碰两侧文件）
- `lib/data-plane/data-plane.perf.test.ts`：100k 上界（本机负载敏感）
- `tests/test_alembic_metadata.py::test_alembic_upgrade_head_survives_percent_encoded_url`：
  pytest tmp % 路径 sqlite 环境问题（移除本迁移后仍失败，已验证与 0104 无关）
