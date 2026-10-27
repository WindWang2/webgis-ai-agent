# INDEPENDENT_REVIEW — g3dv2-a-geo-data-supply

## 第一轮（评审者：未参与实现的 general-purpose subagent；逐文件源码求证 + 调用链追踪 + 本机复现运行，非复述实现方结论）

结论：**需修复后可合** —— 1×P1 + 6×P2 + 若干 P3；安全面（鉴权次序/token 语义/路径校验/429 泄漏/日志脱敏）与科学判定面未发现 P0。

### 发现与修复对照（全部 P1/P2 已修，P3 部分修复部分记账）

| 级别 | 发现 | 修复 commit |
|---|---|---|
| P1 | openapi 快照在 response_model 落盘后陈旧（再生成先于装饰器提交），契约红线门 HEAD 即红；EVIDENCE"12 passed"时序失实 | 快照重刷 + EVIDENCE 修正（本轮修复 commit） |
| P2 | make_safe_session 限流包装在 session.request，redirect 逐跳走 send 不经 request，docstring 断言错误 | 改 send 层包装（每跳计数）+ docstring 修正 + 测试改类级 stub（防实例级替换杀掉包装的回归锚） |
| P2 | 摘要/上传车道发布 tiles_url_template/tilejson_url 缺 ?session_id=（必需参数），照抄即 422/404 | 两处发布 URL 内嵌 session_id（匿名附 &token=）+ 4 个新断言测试 |
| P2 | 表单声明单位静默压倒可解析 band tag 的冲突（3.28 倍高程差可入 analysis_grade） | VERTICAL_UNIT_CONFLICT reason（display_only 级）+ 正反测试 |
| P2 | reason code 词表文档漏 SOURCE_FILE_MISSING/NON_FINITE_PIXELS，无词表锁定测试 | docstring 重写（17 码分级）+ `_ALL_REASON_CODES` frozenset + 3 个锁定测试（集合==/docstring 逐码/冲突码） |
| P2 | `_derive_terrain_header` 缺北向上检查（旋转网格经 TileJSON 端点成第二个无治理入口）+ 错误码语义错位 | 复用 `_north_up` + 按因分码（CRS/单波段/旋转/不可读）+ 2 个测试 |
| P2 | 手册 §2.2 声称 egress 白名单感知（函数实际只看 deployment profile） | 删除失实承诺，如实描述判定输入与替代方案 |
| P3 | ?token= 无 max_length | 已修（max_length=128，三处） |
| P3 | receipt 损坏降级无 typed reason | 已修（RECEIPT_UNREADABLE） |
| P3 | TerrainQualification 3 值 vs 上游 4 值（unknown）错位 | 已修（receipt 严格 3 值 + 投影面 ProjectedTerrainQualification 4 值分轨） |
| P3 | GAP_MATRIX G9 文件名错 | 已修 |
| P3 | 快照再生成捆绑 master 陈旧漂移（storymap/pmtiles 行） | EVIDENCE 归因修正 |
| P3 | 手册未披露 rpm>4096 钳制 | 已修（§3 注明） |
| P3 | 限流器键驱逐非 LRU / 幂等 first-wins / dedup 短路与车道次序 / 反子午线 bounds 互操作 / "anon" 死代码 | 记账 RISKS/LOOP_STATE，不在本 PR 扩面 |

### 评审者已核查无问题清单（要点）

鉴权先于缓存（三新端点+terrain 路由，行号级）；?token= 与 SEC-08 先例逐字同构；429 不泄漏键本体；日志无 token；validate_data_path 覆盖两入口；北向上 Affine 判定正确；单位换算无数值陷阱（哨兵掩蔽→换算→统一 nodata→原子发布）；GSD/maxzoom 推导合理；资格判定逐码核对无 display_only→analysis_grade 泄漏；early-return receipt 无下游崩溃；滑动窗口实现正确；多 worker 披露诚实且单进程部署实测（Dockerfile 无 --workers）；58 例本机复现通过、无 mock 掩盖核心路径；既有红灯确非本 PR 引入；禁区零触碰（changed-files 全集核对）。

## 修复验证（round 1 fixes）

- 新测试 58 → **66 passed**（+8：单位冲突正反/词表锁定×3/URL 鉴权×4/TileJSON 分码×2，其中部分并入既有文件）。
- ruff：本 track 全部文件 0 error（app/ 全仓仍余 master 既有的 scene_quality.py E741）。
- openapi + 字段快照：重刷后 `tests/quality/test_api_compatibility.py + tests/unit/api_contract/test_field_contract.py` **13 passed**（含字节一致闸）。
- 受影响回归 66 passed（synthetic_terrain/mapspec_e2e/raster_tile_service/pmtiles/deep_review/upload_v4）。
- cartography 发布门复跑：见 EVIDENCE.md 最终记录。

## 第二轮复核（修复后由原评审者上下文继续，或新评审者抽核修复面）

见下方追加记录。

## 第二轮复核（新评审者，未参与实现与第一轮；逐项实跑验证）

- a–g 全部**已修复**（证据行号：security.py:474-485 send 层；layer.py:1087-1097 URL 鉴权；terrain_ingest.py:310-322+450 冲突码；:59-71 词表；layer.py:988-991 北向上+分码；手册 §2.2 如实化）；HEAD 实跑 api_compatibility **12 passed**。
- 修复本身无新 P1/P2：effective_token 与守卫同语义；send 包装与 SSRF adapter 互不遮蔽（requests 2.34.2 源码级核对，redirect 逐跳双防线）；词表分轨两处用对；owner_token 链完整。
- 测试抽核：TestNonIngestedRefErrorCodes 真实 rasterio fixture + 真实 HTTP，非 mock 断言。
- P3 残留（记账不阻塞）：客户端自定 session_id 含花括号可使其自身 tiles_url_template 不可 .format()（自损无跨权；既有 tilejson 端点同模式）。
- **最终结论：可合。**
