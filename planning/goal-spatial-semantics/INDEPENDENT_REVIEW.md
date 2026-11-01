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

## R2（复审，待记）

R1 清偿后由独立 Reviewer 复审：重点核验 P0/P1 修复的正确性与回归面。
