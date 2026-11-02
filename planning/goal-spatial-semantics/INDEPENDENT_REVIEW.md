# INDEPENDENT_REVIEW — GOAL 01

## R1（2026-11-01，独立 Reviewer agent，未参与实现）

方式：对 `git diff 6c5e71c2c...HEAD` 全量 adversarial 评审（8 维度），
运行 74 项 pytest + vitest + 8 组 repro 脚本验证假设。结论：架构方向与
核心数学正确（3857 闭式反算/极区阈值/换算/轴序均验证通过），发现
**P0×1 + P1×6 + P2×8**。

| # | 级别 | finding | 清偿（commit 见 git log） |
|---|---|---|---|
| P0 | advanced_spatial field 分母伪造"个/万人"口径（与数值差 10⁴ 倍）+ 分子显示硬编码"个" | field 未请求 per_万人 → density_unit=""；per_10k_persons 请求 → ×1e4 确定性换算 + 链；分子按 numerator_field 区分（Σ/个）—— test_r1_fixes.py 3 项锁定 |
| P1-1 | unknown/assumed CRS × 度量敏感操作实际放行（整数网格被当 WGS84 度缓冲），与词表承诺矛盾 | validate 收敛新增：metric_sensitive + SSC_UNKNOWN_CRS/SSC_CRS_ASSUMED → requires_clarification；空 FC 预期同步（测试更新） |
| P1-2 | raw_span>180 无条件环绕归一：近全球 bbox（±180）被判 ≤6° 单带 UTM | 跨度/中心三级证据链：①采样占据弧（最大空隙法，≤200 采样）②AM 证据门控环绕 ③原始跨度；contract 新增 lon_span_occupied/lon_center_hint（additive optional，round-trip 兼容锁定） |
| P1-3 | spatial_aggregate 只对 zones 过门，points 层逃逸（投影样点层执行期才报误导性错误） | points 层 preflight_tool_input（与 spatial_join 双层对称）—— dispatch 端到端测试 |
| P1-4 | requested_density_denominator 词表外值静默吞掉（fail-open，全角 ² 拼错无痕迹） | 词表外 → SSC_DENOMINATOR_KIND_MISMATCH + blocked + clarify_density_unit 修复建议（词表明细在建议内） |
| P1-5 | 工具参数域（count/field/area）与词表错位：per_10k_persons 经 field 载体永远被拒 | validate 人口族接受 field/population 双载体；测试改用工具真实参数域 |
| P1-6 | crs member {"properties":{"code":N}} 形态被丢，合法声明数据误 blocked | 复用 geo_processor.extract_declared_crs 单一真相（删本地重实现）—— code 形态测试 |
| P2-1 | ADR "28 个理由码"实为 26，6 个死码；TEST_MATRIX S14/S15 承诺未实现 | 删 6 死码（词表=20），文档数字对齐；S14/S15 在矩阵标注为"本 PR 未实现（KDE/栅格侧 not_evaluated 披露）" |
| P2-2 | area_unit 无词表校验器 | 补 _v_area_unit（与 distance_unit 同款）+ 测试 |
| P2-3 | buffer_analysis Redis 缓存（ttl=86400）命中绕过 preflight（部署后 ≤24h 窗口） | 接受并披露（PR body 风险节）：缓存键=(tool_name,args) 无版本盐可 bump；自愈型（TTL 过期后新语义生效）；本地/无 Redis 环境无此窗口 |
| P2-4 | SSC_UNKNOWN_CRS 双 field 重复出现 | _add 去重按 code（field 只是补充信息）+ 测试 |
| P2-5 | data_bbox 是前缀采样口径，>2000 要素可被误判 disjoint → blocked | evidence.scan_truncated 标记；截断时 disjoint → unknown（不凭部分证据下 blocking 结论）+ 测试 |
| P2-6 | geocompute _input_fc 读 node.parameters["crs"]（自由 dict，会发明数据没有的声明） | 只信 inputs 载荷 metadata.crs，删 parameters 通道 |
| P2-7 | NaN 坐标逃过包络检查（nan 比较恒 False） | _coordinate_structure 非有限坐标显式标记 → out_of_envelope + 测试 |
| P2-8 | cartography 用真实声明 crs 后，URN 形态（urn:ogc:def:crs:EPSG::4326）跳出 measurement DEGREE_LIKE_METRIC 白名单 | classify_crs 判 geographic → 白名单代表值 4326 参检（投影原样不参检） |

附带修复（R1 复现过程中暴露）：GCJ-02/BD-09 声明帧此前 crs_class=unknown
（classify_crs 不识偏移帧）→ 误入新收敛规则；修正为偏移地理帧归
geographic（归一 WGS84 后即标准地理数据）。

清偿后验证：89/89 测试绿（新增 test_r1_fixes.py 15 项）；回归
83 passed（buffer/crs_1110/passthrough/audit/cartography×2/geocompute/
geometry_ops）；evidence 脚本三场景 EXIT=0；ruff 全绿。

## R2（2026-11-01，另一独立 Reviewer agent，复审）

方式：核验 R1 逐条清偿（全部通过：根因修复/职责分层/数学主体正确）+ 全量
diff 复读 + 真实基线 worktree 对比。**新发现 R2-1（P0）+ R2-2（P1）+
R2-3/R2-4/R2-5（P2）**，并纠正了主 Agent 的一处验证方法错误：

| # | 级别 | finding | 清偿 |
|---|---|---|---|
| R2-1 | P0 | capability "spatial_semantics" 未注册 → runtime_manifest fatal（strict 启动即挂）：api_contract 13 ERROR + gis_harness 2 个新失败；且主 Agent 此前用 `git stash` 做"干净基线"验证在已提交状态下无效 → PR body 红灯归因虚假 | data_access.py 按 layer_browse 先例注册 capability（含描述/purpose_template/version）；复验 api_contract 57 passed 0 ERROR、gis_harness 失败集与**真实基线 worktree**（/tmp 临时建、用后即删）完全一致（4 个既有红灯）；PR body 归因改写 |
| R2-2 | P1 | _occupied_lon_span_center 退化形态（全采样同经度×西经）弧心未归一：-160.5 → 200.5 → 选 60 带而非 4 带（P1-2 修复引入的新回归） | `(lons[0]+180)%360-180` 归一 + test_p2_degenerate_same_lon_west_normalized（断言 EPSG:32604） |
| R2-3 | P2 | near_global 测试用 raw_span=350 形态，旧算法也能通过（未锁定真缺陷 bbox 触 ±180 → span→0/1 单带） | 补 test_p1_true_antimeridian_edge_bbox_goes_geodesic（[-180..179] 71 点 → geodesic） |
| R2-4 | P2 | per_10k_persons ×1e4 换算不看分母字段语义：households 分母被标"个/万人"；链文案硬编码 per-person | 链改用库层真实 rate_unit（numerator_per_<field>）；口径串分母显示按字段语义（人口字段→"人"，其余按字段名如实呈现） |
| R2-5 | P2 | R1 P2-3 承诺的 Redis 缓存窗口 PR body 披露缺失 | PR body 风险节补齐（≤24h 窗口/自愈/无 Redis 环境无窗口） |

方法教训（记入账本）：**已提交状态下的"干净基线"验证必须用临时 worktree，
`git stash` 只回退未提交改动** —— 此前 M5/M8 两轮"基线复现"结论部分无效，
已全部用真实 worktree 重核（gis_harness 4 失败集一致；前端 typecheck 2 错误
文件为 #1875 遗留且非本 PR 改动面，结论仍成立）。

R2 后终态：91 项 spatial_semantics 测试绿；api_contract 57 passed 0 ERROR
（1 失败=基线既有）；gis_harness 2822 passed + 4 失败（=基线同集）；
evidence EXIT=0；ruff 全绿。
