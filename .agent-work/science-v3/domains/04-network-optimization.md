# 04 Network & Optimization 审计

审计范围：`app/lib/gis/algorithms/network.py`（域包 descriptor + PARAMETER_CONTRACTS）、实现层 `app/lib/geo_analysis/network.py` 与 `app/services/network/*.py`（13 模块，约 7.5k 行）、工具层 `app/tools/network_tools.py` / `advanced_spatial.py`（isochrone_network / service_area_simple）/ `chinese_maps/__init__.py`（isochrone_analysis 外部）、测试 `tests/unit/test_network_*` / `test_od_matrix_correctness` / `test_service_area_correctness` / `test_p_center` / `test_allocation_scaling` / `test_geo_analysis_isochrone_443` 等。

只读审计，未改动 app/ tests/ docs/ scripts/ 任何文件。

---

## 1. Census 表

域包 `ALGORITHMS` 实际含 **21 个 descriptor**（任务书写的"18 个"与当前文件不符，V2/V3 已 additive 扩到 21）；`PARAMETER_CONTRACTS` 8 份。

| # | descriptor id | scientific_status | 方法引用 | crs_class | approximate / fallback | 工具入口 | 关键规模语义 | conformance |
|---|---|---|---|---|---|---|---|---|
| 1 | network.isochrone | EXPERIMENTAL | —（外部） | GEOGRAPHIC_OK | deterministic=False；无 fallback | isochrone_analysis（高德 API） | 服务商配额 | 无（外部不可复现） |
| 2 | network.service_area.simple | EXPERIMENTAL | — | GEOGRAPHIC_OK | **approximate=True；fallback{isochrone: proxy}** | service_area_simple | 低 | descriptor 级（test_...vnext::test_service_area_simple_is_declared_proxy） |
| 3 | network.shortest_path | VALIDATED | dijkstra1959 | GEODESIC | fallback{closest_facility→…}（反向引用） | network_shortest_path | — | 5 条（A* 可采性/转向/六节点金标准） |
| 4 | network.closest_facility | VALIDATED | dijkstra1959 | GEODESIC | fallback{shortest_path: approximation} | network_closest_facility | — | 4 条 |
| 5 | network.od_matrix | VALIDATED | dijkstra1959 | GEODESIC | — | network_od_matrix, distance_matrix_cn | 工具层 10k 对硬闸 | 4 条 |
| 6 | flow.od_arc_build | VALIDATED | — | CRS_AGNOSTIC | — | od_flow_edges | — | 1 条 |
| 7 | network.service_area.multi | VALIDATED | dijkstra1959 | GEODESIC | — | network_service_area | breaks≤32（工具层） | 3 条（不桥接缝隙金标准） |
| 8 | network.isochrone.local | VALIDATED | —（缺 dijkstra1959，见 §7） | GEOGRAPHIC_OK | — | isochrone_network | — | 4 条（含断连图不跨分量） |
| 9 | network.accessibility | VALIDATED | luo_qi2009 | GEODESIC | — | network_accessibility | **无需求/设施量闸（风险 R4）** | 2 条 vnext + 6 条 v2（E2SFCA 手算金标准） |
| 10 | network.route_optimization | VALIDATED | — | GEODESIC | — | optimize_route | 2-opt ≤100 轮 | 4 条 |
| 11 | network.location_allocation | VALIDATED | teitz_bart1968, hakimi1964 | GEODESIC | — | location_allocation | 枚举闸 C(m,p)≤20000 | 7 条 |
| 12 | network.pmedian_exact | VALIDATED | **church_revelle1974（错引，F1）** | GEODESIC | fallback{location_allocation: approximation}；backend milp_highs | location_allocation(solver=exact_milp) | 需求×候选≤25000 且候选≤500（typed 拒绝） | 6 条（枚举等值/权重回归/确定性/闸） |
| 13 | network.pcenter_exact | VALIDATED | hakimi1964 | GEODESIC | 同上 | location_allocation(solver=exact_milp) | 同上 | 4 条 |
| 14 | network.optimize_route (VRP) | VALIDATED | — | GEODESIC | — | optimize_route | stops≤200（显式拒绝） | 2 条 |
| 15 | network.gravity_access | VALIDATED | hansen1959, zipf1946 | GEODESIC | — | network_gravity_access | 参数界 α∈[0,3], β∈[0.5,4] | 6 条（手算金标准 ±1e-9） |
| 16 | network.huff_interaction | VALIDATED | huff1964 | GEODESIC | — | network_huff_interaction | 同上 | 6 条 |
| 17 | network.centrality | VALIDATED | brandes2001 | GEODESIC | backend_variants: exact_brandes(≤2000)/sampled_brandes(>2000, k=500 seed=42) | network_centrality | 节点≤20000、边介数≤1500 边、输出≤5000 行 | 8 条 |
| 18 | network.eigenvector_centrality | VALIDATED | bonacich1972 | GEODESIC | —（幂迭代，负权 typed 拒绝） | network_centrality(metrics=eigenvector) | 节点≤20000 | 4 条（与 networkx rtol1e-6 一致） |
| 19 | network.route_external_api | EXPERIMENTAL | — | GEOGRAPHIC_OK | deterministic=False | plan_route | 外部依赖 | 无 |
| 20 | network.transit_route_external | EXPERIMENTAL | — | GEOGRAPHIC_OK | deterministic=False | search_transit_route | 外部依赖 | 无 |
| 21 | network.traffic_status_external | EXPERIMENTAL | — | GEOGRAPHIC_OK | deterministic=False | get_traffic_status | 实时不缓存（#702） | 无 |

参数契约（8）：network_shortest_path v2 / network_od_matrix v1 / network_service_area v1 / network_accessibility_analysis v1 / gravity_accessibility_analysis v1 / huff_interaction_analysis v1 / network_centrality_analysis v2 / location_allocation_analysis v1。契约只覆盖工具签名真实存在的参数（origin/destination 多态输入留在 schema 层，有逐条注释，不虚构）——parity 门校验通过（test_registry_validate_and_parity_clean）。

实现层真值核查：
- isochrone/service area **是真网络可达**：`service_area.multi` 有向图 Dijkstra + 逐 break 可达边分类 + 150 m 局部 UTM 缓冲包络（不桥接缝隙，test_isochrone_does_not_bridge_disconnected_gap）；`isochrone.local`（geo_analysis/network.py）无向 MultiGraph Dijkstra + 边几何缓冲。
- `service_area.simple` 是速度表×时间**直线缓冲**（advanced_spatial.py L2138-2146：`buffer(distance=speed*t)`）——proxy 身份在 descriptor 如实声明。
- OD/closest-facility/VRP 全部走同一累积式/前驱树 Dijkstra 核心（`_compute_od`），无逐对重寻路；不可达以 reachable=False+inf 显式返回，绝不静默缺行。
- location-allocation 三条路径真实存在且同源代价矩阵：C(m,p) 枚举（exact）→ Teitz-Bart/贪婪（heuristic）→ scipy.optimize.milp/HiGHS（exact_milp）。**pulp/ortools 确认缺失**（requirements/pyproject 无），但 **scipy≥1.12 内嵌 HiGHS**，MILP 精确主张有真实求解器背书，不是叫 exact 的启发式。

---

## 2. Fake-native / approximation 夸大 findings（严重度）

反目标「不得静默用欧氏替代路网」逐项核查：

| ID | Finding | 严重度 | 说明 |
|---|---|---|---|
| F1 | **pmedian_exact 引用 MCLP 文献**：`network.pmedian_exact.method_references=["church_revelle1974"]`——Church & ReVelle 1974 是最大覆盖（MCLP）论文，不是 p-median MILP（应为 ReVelle & Swain 1970 / Hakimi 1964）。错引已被 test_network_v3.py::test_exact_milp_tool_evidence_and_solver_disclosure L529 固化断言 | **High（学术正确性）** | 精确求解实现本身正确；错引会让 evidence 块对每个 p-median 精确解给出错误方法出处 |
| F2 | **luo_qi2009 引文出处错误**：method_references.py L179-183 记 "IJHG, 11(1), 68–84"；实际 Luo & Qi 2009 E2SFCA 发表于 *Health & Place* 15(4):1100–1107（已核）。且原始 2SFCA（Radke & Mu 2000；Luo & Wang 2003, IJHG 2(1):11）未登记，network.accessibility 的 2sfca 方法只挂 luo_qi2009 | **Medium** | 引文可验证但 venue/卷期错误；方法谱系缺源头引用 |
| F3 | **service_area_simple 工具描述误导**：工具 description 说"生成可达范围…❌ 不要用于：简单直线半径缓冲"，读起来像"本工具不是直线缓冲"；真相（速度×时间的欧氏缓冲圈）只写在 descriptor limitations。LLM 按工具描述择具时可能把它当路网可达用 | **Medium（工具层诚实性）** | descriptor 层诚实（approximate=True + fallback proxy + vnext 测试锁声明）；漏在 tool description 层 |
| F4 | **网络族 scale guard 不均衡**：OD 工具 10k 对闸、VRP 200 stops 闸、MILP 25000/500 闸、centrality 20000 节点闸都有；但 **location_allocation 启发式路径 / accessibility / gravity / huff / closest_facility 的工具面无需求×设施量闸**——OD 代价矩阵按 n×m 物化（accessibility 还按 dict-of-tuples），50k 需求×10k 候选请求会先 OOM 而非 typed 拒绝 | **Medium-High（可用性/诚实拒绝语义被绕过）** | 与"规模闸先拒绝不 OOM"的域内自设标准不一致 |
| F5 | **complexity 字段全空**：registry 支持 `AlgorithmDescriptor.complexity`，network 21 个 descriptor 0 处填写（只有 cpu/memory/io_cost 粗粒度） | Low | 声明现状缺口，非夸大 |
| F6 | **uncertainty_outputs 全域为空**：sampled betweenness（k=500）的排序噪声无置信区间（limitations 有文字披露但无 uncertainty 输出）；2SFCA/E2SFCA/gravity/Huff 无任何不确定性量化 | Medium | 见 §6 |
| F7 | **descriptor 假设宣称工具面不可达的能力**：network.shortest_path assumptions 宣称"转向惩罚经边状态搜索只在实际转向处计（#455）"——实现真实（routing.py `_turn_aware_shortest_path`），但 turn_penalty 只存在于 Impedance/TravelProfile 服务层对象，**工具签名与契约均未暴露**：经 network_shortest_path 工具调用时 turn_penalty 恒 0（engine.solve_shortest_path 从不构造带 penalty 的 Impedance）。descriptor 描述的是服务层能力，工具层消费者永远拿不到 | Medium | 假设与 tool_candidates 的实际可达面不符 |
| F8 | **max_coverage "exact" 枚举无近似保证披露**：贪婪 MCLP 有经典 (1−1/e) 近似保证但 summary/limitations 均未披露；同时 max_coverage 是三目标中唯一无 MILP 精确路径的（solver=exact_milp 抛 UnsupportedMethod——typed 诚实，但 HiGHS 在手，MCLP MILP 一行约束就能补） | Medium | 见 §8 R3 |
| F9 | **accessibility 工具描述承诺 polygon layer 但实现只支持点**：NetworkAccessibilityArgs.demand_layer 描述"Demand / population points or polygon layer"；engine._to_demand 对 Polygon geometry.coordinates（嵌套环数组）不做 geom_type 守卫，直接当 Point 坐标往下传 → 在 pyproj/捕捉层崩成 error dict（不会静默错值，但描述与行为不符；#814 只挡了"不可识别形状"，多边形恰好可识别） | Medium | 输入验证缺口 |
| F10 | accessibility 的 `capacity` 缺省 1.0：2SFCA R_j 退化为供需计数比（descriptor limitations 已声明）——不算夸大，但属于"缺数据时的隐式模型替换"，建议 summary 显式带 `capacity_source=defaulted` 标志 | Low | |

**结论：无假原生等时圈/路径**。欧氏缓冲只存在于 service_area.simple，且是全域唯一声明 proxy 的 fallback 终点；真路网族（shortest_path/od/service_area.multi/closest_facility）在断连时返回 inf/空路线 + 逐点名披露（unmatched_demand_ids / unreachable_facility_ids / unassigned_ids），从不以欧氏距离补值。

---

## 3. 契约缺口

1. **turn restriction 契约缺失**：现支持仅"转向惩罚"（#455，bearings>25° 计费，且只在 shortest_path 节点级搜索）；无"禁止转向"（banned movement / turn restriction）语义；OD 族树明确不带惩罚（跨工具语义有注释与测试，诚实）。缺：`turn_restrictions` 参数契约 + 边状态模型扩展。
2. **multimodal graph 契约缺失**：TravelProfile 单模式；transit 仅外部 API；isochrone.local 的 transit 417 m/min 是在无向道路图上开车的速度换算，不是公交网。无 walk+ride 本地图契约、无 mode 切换边（connector）模型。
3. **参数契约未覆盖启发式质量**：location_allocation_analysis 契约只有 objective/solver；无 `heuristic_budget`（Teitz-Bart 轮数）、无多起点重启参数——启发式质量旋钮不可契约化。
4. **network.isochrone.local 缺 method_references**（唯一的 VALIDATED Dijkstra 族 descriptor 无 dijkstra1959，见 §7）。
5. **accessibility 缺 demand-side 汇总契约**：多边形需求（面域加权/质心降载）无契约位；decay_zones 1-10 上限有，zones=1 退化为 2SFCA 的等价性有测试（test_e2sfca_single_band_matches_2sfca）但契约描述未说明。
6. **backend_variants 覆盖不全**（见 §5/§9）：只有 centrality 与两个 MILP descriptor 声明；od_matrix / service_area / location_allocation(启发式) / gravity / huff 都是单后端无声明。
7. **turn penalty 工具面缺口**（F7）：Impedance.turn_penalty_s 服务层存在、工具/契约不存在。
8. **flow.od_arc_build 无 method_references/limitations 之外的数值语义**（线宽是渲染量，已声明，可接受）。

---

## 4. 数值/算法风险图

| ID | 位置 | 风险 | 触发条件 | 现有缓解 |
|---|---|---|---|---|
| R1 | od_matrix._single_source_costs / routing 权重函数 | 负权破坏 Dijkstra | 用户边数据 speed≤0 / length≤0 | graph_builder 建边时 speed≤0→默认速度、weight_func 钳 `max(0.0001,…)`——**负权实际不可达**；钳制值 0.0001 使 0 长退化边近乎免费，断连图返回 inf 不补值 |
| R2 | routing._split_edge_at_fraction L171 | 分割点不在任何段 1e-9 邻域内时 sub2 = 两个相同点 → 退化 LineString 边（长度 0） | 浮点噪声极端几何 | 后续 weight 钳 0.0001 兜底；影响极小 |
| R3 | allocation._od_cost_matrix / accessibility time_matrix / interaction cost matrix | **n×m 代价矩阵物化内存**（Python float list / dict，每对 ≥ 50B）：50k×10k ≈ GB 级 | 大需求×大候选集、无工具面闸（F4） | 无（MILP 路径有闸、启发式路径无） |
| R4 | service_area.multi 逐 break 边分类 + 缓冲并集 | O(B×E) LineString 引用 + unary_union 内存 | breaks≤32、大图 | breaks 32 闸；150 m 缓冲在局部 UTM；#1063 单趟分类已把 B×E 降为单趟 |
| R5 | centrality sampled betweenness | 采样介数排序噪声无 CI | n>2000 | betweenness_mode/sample_k/seed=42 披露 + limitations 声明；无 CI（§6） |
| R6 | graph_builder._process_intersections | 100k 线网络的 STRtree 候选交集仍是重 GEOS 扫描 | 巨型路网首建（缓存未热） | STRtree 候选剪枝 + 线程锁缓存 + 指纹 memo；无 typed 拒绝 |
| R7 | routing._turn_aware_shortest_path | 状态空间 O(E)，每个状态算 bearing（atan2） | 长路径+大图+penalty | 仅 travel_time_s 阻抗激活；goal 早退；无闸但罕见爆炸 |
| R8 | engine._snap_evidence | 每端点一次 snap（STRtree nearest）| 32 端点上限 | 有界 32 + 截断披露 |
| R9 | 启发式 LA 的 1e9 不可达惩罚 | 部分不可达时目标被 1e9·w 支配，比较尺度失真 | 断连需求占比高 | 不可达点名披露（unassigned_ids）；MILP 路径直接剔除不引入 1e9（更干净）；语义差异已注释 |
| R10 | p-median MILP 代价经 network_od_matrix 的 round(2) | MILP 目标基于 0.01 s 舍入代价 | — | 与枚举/启发式同源同舍入（一致可比）；gravity/huff 走原始 `_compute_od`（不舍入）——差异有意且有注释 |
| R11 | allocation 启发式 evaluate() O(n·p) × p×(m−p) × ≤10 轮 | 大实例延迟（非内存） | n=50k, m=10k | 无闸；属 F4/R3 同根 |

断连图行为全部核查为诚实路径：shortest_path → total_cost=inf 空路线；od → reachable=False 行；closest_facility → unmatched_demand_ids；service_area → unreachable_facility_ids（engine 层点名）；LA → unassigned_ids；gravity → unreachable_pair_count（全不可达且无 cutoff 抛 DisconnectedNetwork）；huff → unassigned_demand_ids；centrality eigenvector → 主导分量披露；isochrone.local → 断连分量不跨域（测试锁定）。

---

## 5. 规模/后端缺口

- **OD / service area 无 scalable backend**：单机 networkx Dijkstra，无收缩层级（CH）/OSRM/Valhalla/GoalHopper 类外部后端变体；10k 对 OD 闸对 LLM 场景够用，城市级矩阵（1e6+ 对）只能拆批。建议 descriptor 级声明 backend_variants（local_networkx 唯一变体 + 预留 external_matrix_api 变体 typed unavailable）。
- **启发式 LA 无规模闸**（F4/R3/R11）：与 MILP 的 25000/500 闸不对称。
- **centrality 20000 节点闸**合理（Brandes O(nm)）；edge_betweenness 1500 边"超出诚实拒绝，不假采样"是域内诚实性的样板。
- **accessibility/gravity/huff 无点数闸**：OD 树本身 O(V+E)×origin 可行，但成对物化无闸。
- **isochrone.local 全图载入 GeoJSON 构建 MultiGraph**：无 feature 数闸（to_utm_gdf + 逐边 STRtree）；20k+ 边已有性能修补（#443/#991），无拒绝语义。
- **MILP（scipy.milp/HiGHS）**：需求×候选≤25000 且候选≤500 是保守而诚实的闸（HiGHS 本可更大，但 LLM 交互预算内合理）；超闸抛 ResourceScaleMismatch 指向启发式——**typed 拒绝机制工作正常且有测试**。

---

## 6. 不确定性缺口

1. 全域 `uncertainty_outputs=[]`（21/21）。
2. sampled betweenness：k=500 固定种子的排序噪声无置信区间（可给 per-node bootstrap/Wilson 型区间或至少 top-k 稳定性标志）。
3. 2SFCA/E2SFCA：无容量/需求估计误差传播；capacity 缺省 1.0 的敏感性无披露字段。
4. gravity/Huff：β 无标定（无观测流量拟合），score 无量纲相对量——已有诚实声明（"不做随机效用离散选择估计/参数标定"），但**没有输出任何不确定性度量**（如 bootstrap 分数区间）。
5. isochrone 多边形：150 m 缓冲是形态近似，无"边界置信带"输出（可作为 uncertainty_outputs 词表的候选键）。
6. MILP：mip_gap / dual_bound 已透传 solve_stats（好的先例）——但 status=1（iteration/time limit）时 optimality="iteration_or_time_limit" 仍会带 objective_value，消费方可能当最优用；建议此时 summary 追加显式 warning。

---

## 7. 参考文献验证

| ref_id | 引文（registry） | 核验 | 结论 |
|---|---|---|---|
| dijkstra1959 | Numerische Mathematik 1:269–271 | 正确 | ✓ |
| teitz_bart1968 | Operations Research 16(5):955–961 | 正确 | ✓ |
| hakimi1964 | Operations Research 12(3):450–459 | 正确（p-center/p-median on graphs） | ✓ |
| church_revelle1974 | Papers of the RSA 32(1):101–118 | 引文本身正确，但**被挂到 pmedian_exact（F1）**；它是 MCLP 出处 | ✗ 挂靠错误 |
| huff1964 | Journal of Marketing 28(3):34–38 | 正确；Huff 1963（Land Economics，概率分析前身）可选并列——模型规范引用 1964 是学界主流 | ✓（可补 1963） |
| luo_qi2009 | "IJHG, 11(1), 68–84" | **错**：应为 *Health & Place* 15(4):1100–1107（已核） | ✗ venue/卷期错误 |
| hansen1959 | JAIP 25(2):73–76 | 正确 | ✓ |
| zipf1946 | American Sociological Review 11(6):677–686 | 正确 | ✓ |
| brandes2001 | J. Mathematical Sociology 25(2):163–177 | 正确 | ✓ |
| bonacich1972 | J. Mathematical Sociology 2(1):113–120 | 正确 | ✓ |
| （缺）luo_wang2003 / radke_mu2000 | 未登记 | 2SFCA 原始出处（Luo & Wang 2003, IJHG 2(1):11）未挂 network.accessibility | 缺口 |
| （缺）revelle_swain1970 | 未登记 | p-median MILP 标准引用 | 缺口（与 F1 一并修） |
| （缺）dijkstra1959 on isochrone.local | — | descriptor #8 无 method_references | 缺口 |

---

## 8. V3 建议清单

格式：方法引用 / 现状差距 / 无 solver 时 heuristic vs exact 策略 / 可行性 / 测试策略 / 优先级。

**R0. 修 church_revelle1974 错引（F1）**
- 引用：p-median MILP → ReVelle & Swain (1970) *Integer programming formulation...*（Geographical Analysis 2(1)）+ 保留 hakimi1964；church_revelle1974 留给 MCLP。
- 差距：network.pmedian_exact method_references + test_network_v3 L529 断言同步改。
- 策略：不涉及 solver。可行性：纯声明+测试改动。测试：registry validate + evidence 断言更新。**P0**。

**R1. 修 luo_qi2009 引文 + 补 luo_wang2003/radke_mu2000（F2）**
- 差距：method_references.py 一条 venue 修正 + 两条新增；network.accessibility method_references 追加。
- 测试：descriptor 引用存在性校验（validate 已有）+ 引文文本断言。**P0**。

**R2. MCLP（max_coverage）精确 MILP**
- 引用：church_revelle1974（本来就该归它）。
- 差距：三目标中唯一无 exact_milp 路径；启发式贪婪无 (1−1/e) 保证披露。
- 策略：HiGHS 在手（scipy.milp 已用于 p-median/p-center），MCLP MILP 只需 `max Σ w_i z_i; z_i ≤ Σ_{j: c_ij≤T} y_j; Σy=p`，规模闸复用 `_milp_scale_guard`；无 solver 场景（假设未来 HiGHS 被剥离）→ BackendVariant `milp_highs` 声明 + typed SolverUnavailable，贪婪启发式保持 heuristic 披露并补 (1−1/e) 保证文字。
- 可行性：高（<150 行 + 复用 _exact_milp_result）。测试：枚举等值金标准、覆盖权重回归、规模闸 typed、确定性（克隆 TestPMedianExactMILP 模式）。**P0**。

**R3. 启发式路径统一规模闸（F4/R3/R11）**
- 差距：LA 启发式 / accessibility / gravity / huff / closest_facility 工具面无 n×m 闸。
- 策略：工具层（同 MAX_OD_MATRIX_PAIRS 先例）或服务层 `_od_matrix_scale_guard`，超闸抛 ResourceScaleMismatch 指向"拆批/子网"correction_hint——不静默回退。exact/heuristic 无关，属统一资源包络。
- 测试：monkeypatch 闸值 + typed 断言（复用 test_exact_milp_scale_guard_refusal_is_typed 模式）。**P0**。

**R4. capacitated facility location（CFLP / 容量约束 p-median）**
- 引用：Hakimi (1964)（容量版常用 dela_paz1968? 主流引：CFLP → Balinski 1965 / Wolfe & Gaul1959? 推荐 Geoffrion & McBride 1978 或直接沿用 ReVelle & Swain 1970 加约束的引用惯例）。
- 差距：capacity 字段存在（Facility.capacity）但选址目标完全不用它——2SFCA 用容量、选址不用，语义不对称。
- 策略：MILP 加 `Σ_i w_i x_ij ≤ K_j·y_j`；无 solver →启发式（贪婪+容量检查）声明 heuristic；绝不叫 exact。
- 可行性：中（模型+闸+披露）。测试：容量绑定金标准（小实例手算）、超容不可行时的 typed/披露行为。**P1**。

**R5. location-allocation 增强**
- 差距：Teitz-Bart 单起点（前 p 个候选）；p-center 启发式 ≤10 轮 + 1.1× 界（测试锁定）；无多起点重启、无启发式质量旋钮。
- 策略：契约加 `heuristic_restarts`（确定性起点集：top-p by degree/weight，seed 固定）；exact 语义不变（MILP/枚举仍是 exact）。
- 测试：确定性（重启集固定）+ 相对单起点质量不退化。**P1**。

**R6. service area / OD scalable backend 变体声明**
- 差距：单后端（networkx）无 backend_variants，也无外部后端。
- 策略：声明 `local_networkx` 变体（现状）；预留 `external_matrix_api`（如 OSRM/table 或高德矩阵）为 **typed unavailable**（未配置即 SolverUnavailable，不静默换语义——与 isochrone 外部工具"互不 fallback"原则一致）。本地语义（haversine 边权）与服务商标距不可混用，variants 文档必须写明互斥。
- 测试：变体选择器单测 + 未配置 typed 拒绝。**P1**。

**R7. accessibility inequality（公平性诊断）**
- 差距：2SFCA/E2SFCA 输出逐点 score，但无分布不平等度量。
- 引用：Gini/Theil on accessibility → 常引 Witten et al. 或直接统计引用；建议标 "descriptive inequality diagnostics (Gini 1912 / Theil 1967)"，明确是描述统计非新方法。
- 策略：score 分布的 Gini/Theil/Lorenz 曲线点 + summary 诊断；无 solver 问题。
- 测试：手工金标准分布。**P1**。

**R8. gravity/Huff 扩展**
- 差距：β 无标定、无 competing-destination 修正。
- 引用：Huff 1963（前身）+ Fotheringham (1983) competing destinations（若做）；标定引 Cesario (1975) 或直接 `scipy.optimize.curve_fit`（已有依赖）。
- 策略：可选 `calibrate_beta(observed_flows=...)` 参数（opt-in，无观测流即 typed MissingRequiredField）；Huff 加 Wilson 置信区间（候选集份额二项近似）作 uncertainty 输出。
- 测试：合成流 recover β 金标准。**P2**。

**R9. E2SFCA 变体**
- 差距：只有等分带高斯 E2SFCA。
- 引用：3SFCA / V2SFCA（Luo & Whippo 2012, *Health & Place* 18(4):789–795，变距捕获）；Gaussian 2SFCA 连续衰减。
- 策略：method 枚举 additive（`e2sfca_gaussian_continuous`/`v2sfca`），契约 version+1；带权函数已参数化，改动集中在 `_w_of`。
- 测试：zones→∞ 与连续高斯的收敛性质 + 单带退化=2SFCA。**P2**。

**R10. network robustness 模块**
- 差距：无。edge_betweenness（≤1500 边精确）已给出关键边信号但无扰动模拟。
- 引用：Albert & Barabási (2002) 综述 / Sullivan et al. (2010) 交通韧性；或 percolation → Schneider et al. (2011)。
- 策略：`network_robustness` 新 descriptor：k 个边/节点移除情景下 WCC 尺寸曲线与效率损失（确定性情景集：介数 top-k、随机固定 seed）；typed 规模闸沿用 centrality 闸。
- 测试：星形/链形手算曲线。**P2**。

**R11. multimodal graph contract**
- 差距：单模式 DiGraph；transit 纯外部。
- 策略：先立**契约**而非实现：`network.multimodal` descriptor（EXPERIMENTAL）声明 walk+ride 三段式（access/egress walk + transit leg 外部或 GTFS 本地）+ typed unavailable（无 GTFS 数据时 SolverUnavailable/DataUnavailable）；禁止在无公交网时用道路图速度表冒充 transit。
- 测试：契约/parity + typed 拒绝。**P2**。

**R12. turn restrictions contract**
- 差距：turn penalty 服务层真实现但工具面不可达（F7）；无禁止转向。
- 策略：短期把 `turn_penalty_s` 提升为 network_shortest_path 契约参数（version 2→3，仅 travel_time_s 阻抗生效——与 build_weight_func 注释一致）；禁止转向做成边状态图的黑名单转移（`_turn_aware_shortest_path` 天然支持：转移时查 (prev_edge, next_edge) 黑名单）。
- 测试：L 形/十字路口单转向金标准（克隆 test_network_issue455）。**P1**（penalty 暴露）/ **P2**（banned turns）。

**R13. 声明面补齐（低成本批量）**
- `complexity` 字段填充（Dijkstra O((V+E)logV)、Brandes O(nm)、MILP NP-hard 指数最坏等）；`uncertainty_outputs` 至少给 centrality(sampled) 与 isochrone(buffer_width) 挂词表键；isochrone.local 补 dijkstra1959；service_area_simple 工具 description 改写为"速度×时间的直线缓冲圈（非路网可达；路网等时圈用 network_service_area / isochrone_network）"；accessibility demand_layer 描述去掉 polygon 或补 Polygon→representative_point 守卫。**P0-P1 打包**。

---

## 9. LP/MIP 可选 backend 契约建议（typed unavailable 机制现状与缺口）

**现状**：
- **不存在独立 LP/MIP 依赖**：pulp/ortools/cvxpy 均不在 requirements（grep 证实）。MILP 由 `scipy.optimize.milp`（scipy≥1.12，内嵌 HiGHS 分支定界）承担（allocation.py L17、requirements L44 `scipy>=1.12.0`）。因此"MILP exact"主张不依赖可选依赖，**不存在静默降级风险**。
- **typed unavailable 机制现由三类 ScientificError 承担**（scientific_errors.py，全部 ValueError 子类、带 scientific_code + correction_hint）：
  - `ResourceScaleMismatch`（规模闸：MILP 25000/500、centrality 20000 节点/1500 边）——先拒绝后分配，有测试；
  - `UnsupportedMethod`（方法-数据不匹配：max_coverage 无 MILP 式、负边权拒绝 eigenvector、未知 metrics/solver 枚举）；
  - `DisconnectedNetwork`（结构事实：OD 全不可达且无 cutoff、gravity 全对不可达）。
  - `DegenerateData`（空图）。拒绝→启发式路径从不静默发生：MILP 超闸抛错并把 `solver='heuristic'` 写进 correction_hint，由调用方显式选择。

**缺口**：
1. **没有 `SolverUnavailable` 错误类型**：当前 MILP 后端是 scipy 内嵌的所以不需要；一旦引入任何*可选*后端（GUROBI/CPLEX、OSRM 外部矩阵、GTFS 本地公交网），会以裸 ImportError/Generic error dict 冒出，绕过 scientific_code 通道。建议：`class SolverUnavailable(ScientificError): scientific_code="SOLVER_UNAVAILABLE"`，并给 `AlgorithmDescriptor.backend_variants[]` 增加 `availability: "bundled" | "optional"` 语义（或在 BackendVariant 增加字段），optional 变体未配置时必须 typed 拒绝而非回退到另一语义后端。
2. **backend_variants 与 heuristic 描述符脱节**：location_allocation（启发式）descriptor 无 variants，`select_backend` 对它只能输出"默认工具路径"诊断（network_tools._backend_diagnostic 注释自认）。建议给 heuristic 路径也声明 `heuristic_enumeration`（exact≤20k 组合）与 `teitz_bart`/`greedy` 两个真实切换的变体——代码里真在切，声明面上看不见。
3. **MILP 状态=1（time/iteration limit）时的"最优"措辞**：optimality 已诚实叫 `iteration_or_time_limit`，但 objective_value 仍在主字段；建议契约规定该状态下 summary 必须追加 warning 且 evidence 的 optimality diagnostic value≠optimal。
4. **未来可选 exact 后端（如 GUROBI）的 parity 义务**：若新增，应复用"枚举金标准等值（±1e-6）+ 两次运行逐位一致"的既有 numerical_tolerance 范式（network.pmedian_exact 已立此模板）。

**建议契约文本（草案）**：
```
SolverUnavailable(ScientificError): scientific_code="SOLVER_UNAVAILABLE"
  detail: "exact_milp 求解后端 {backend} 未安装/未配置"
  correction_hint: "install {backend} or use solver='heuristic' (disclosed as heuristic, no optimality guarantee)"
规则：descriptor 声明 exact 的算法，其 exact 变体必须 either bundled（scipy/HiGHS）
  or optional-with-typed-refusal；exact 声明绝不允许降级到启发式执行。
```

---

### 审计员总评

该域是全部科学域中诚实性工程最成熟的一个：proxy/approximate 分级、typed 规模闸、不可达点名披露、exact/heuristic/MILP 三路同源代价矩阵、金标准+差分+确定性+闸测试齐备，fake-native 面为零。主要欠账集中在：(a) 一处 MCLP→p-median 的方法错引（已固化进测试）；(b) 一处 E2SFCA 引文 venue 错误与 2SFCA 源头缺引；(c) 启发式路径与可达性族工具面缺统一规模闸；(d) max_coverage 无 exact 路径（补齐成本极低）；(e) 转向惩罚服务层/工具层能力脱节。全部可在不新增依赖的前提下于 V3 内收敛。
