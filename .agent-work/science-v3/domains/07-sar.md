# 07 SAR 审计

审计对象：worktree `webgis-ai-agent-science-v3`（只读审计）。
审计日：2026-09-07。实现层 `app/lib/geo_analysis/{sar_calibration,sar_filter,sar_temporal,sar_v3}.py`；
descriptor 层 `app/lib/gis/algorithms/remote_sensing.py`；工具层 `app/tools/remote_sensing.py`；
测试 `tests/unit/lib/test_sar_{calibration_v2,filters_v2,v3}.py` + `tests/unit/test_temporal_science_vnext.py`。
实测：4 个测试文件 **48 passed**（23.55s，含注册表 validate/parity 门）。
注：`app/lib/gis/algorithms/temporal.py` 中 **无任何 SAR descriptor**（grep 零命中）——SAR 全部集中在 remote_sensing.py，任务描述中的 temporal.py 无审计对象。

## 1. Census 表

| id | name | status | tools | 实现位置 | 契约 | references | uncertainty | tolerance | variants | conformance | 测试覆盖 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| sar.temporal_stats | SAR 时序栈统计 | VALIDATED | sar_temporal_stats | sar_temporal.py `temporal_stack_statistics` | sar_temporal_stats_analysis **v2**（include_cv/percentiles additive） | 无（描述性统计，诚实留空） | pixels_all/partially/no_valid_slice 计数 | 手算精确 1e-12 | CV、≤5 分位数（默认关，v1 形状锁定） | 好：ddof=0 披露、T≤24/H·W≤16M 闸、CV 除零→NaN | 4（vnext hand/scale + v2 cv/percentiles/defaults） |
| sar.vh_ratio | SAR VV/VH 极化比 | VALIDATED | sar_vh_ratio | sar_temporal.py `vh_ratio`→raster_change.ratio_change | **无契约**（无 parameter_contract_ref） | 无 | vh=0→NaN；负值(dB)→UnsupportedMethod | 手算 1e-15 | 仅 linear ratio | **descriptor/工具描述陈旧**（见 FN-1） | vnext exact（仅线性路径） |
| sar.log_ratio_change | 双时相对数比值变化 | VALIDATED | detect_ratio_change（change_detection.py） | raster_change.py `ratio_change(method=log_ratio)` | ratio_change_analysis v1（与 remote.ratio_change 共享） | 无 | 零分母/非正 ratio→NaN；symmetry 披露 | 手算 1e-15 | ratio / log_ratio | 好：log(a)−log(b)==log(a/b)，对称性锁定，Inf 泄漏已 gate（MINOR-1） | test_log_ratio_symmetry_and_zeros 等 |
| sar.speckle_filter | 斑点滤波（Lee/RL/Frost/GMAP/Kuan） | VALIDATED | sar_speckle_filter | sar_filter.py `speckle_filter`（+gamma_map_filter/kuan_filter 门面） | v2（5 方法 + max_iterations） | lee1980, lee1981, lopes1990, frost1982, kuan1985, lee_jurkevich1994 | enl_source=explicit/estimated 披露；iterations_used | Lee 窗口手算精确；std 下降 | 5 滤波器 × 窗 {3,5,7} × ENL 显式/估计 | 好：nodata 感知、负值(dB)拒绝、常数场 DegenerateData、规模闸、边界披露 | 6 |
| sar.radiometric_calibration | 辐射定标 β⁰/σ⁰/γ⁰ | VALIDATED | sar_calibrate | sar_calibration.py `calibrate_sar` | v2（+incidence_lut 文档位） | oliver_quegan1998 | incidence_mode/lut_pixels；K 缺失→MissingRequiredField（绝不虚构） | 手算黄金值 1e-12（A=2,K=1→β⁰=4；θ=30°→σ⁰=2.0） | dn_amplitude/dn_intensity × sigma0/beta0/gamma0/all × to_db × 标量/逐像元入射角 | 好：负 DN→UnsupportedMethod、入射角 (0,90) 开区间 InvalidUnits、σ⁰ 逐像元定标 LUT 不解析（SAFE XML）诚实披露、热噪声/RTC 不隐式前置 | 5 |
| sar.glcm_texture | GLCM 纹理 | VALIDATED | sar_glcm_texture | glcm.py | v1 | haralick1973 | 零方差→NaN（correlation 不伪造） | 4×4 手算 GLCM 精确 | window/levels/directions/properties | 好（本审计抽样，未逐行） | 4（test_glcm_texture.py） |
| sar.temporal_composite | 时序栈合成 | VALIDATED | sar_temporal_composite | sar_temporal.py `temporal_composite` | v1 | oliver_quegan1998（median 鲁棒性） | pixels_no_valid_slice | nanmedian==参考 1e-12 | mean/median/percentile（percentile 必显式） | 好：栈深闸、MissingRequiredField | 2 |
| sar.thermal_noise_removal | 热噪声去除 | VALIDATED | sar_remove_thermal_noise | sar_calibration.py `remove_thermal_noise` | v1（noise_floor/noise_lut 互斥） | oliver_quegan1998（**引用拟合偏弱**，见 §7） | clamped_pixels/invalid_pixels/lut_pixels | 标量相减手算精确 | 标量噪声底 / 逐像元 LUT | 好：SAFE-XML 不解析披露、钳 0 正偏披露、dB 拒绝 | 2 |
| sar.log_scaling | 量纲换算 | VALIDATED | sar_log_scale | sar_calibration.py `sar_log_scale` | v1 | 无（纯代数，诚实留空） | floored_cells/nonpositive_to_nan | round-trip 1e-12；A=2→4→2 逐位 | 4 modes | 好：ε=1e-12 下限非静默、负值→NaN | 2 |
| sar.multitemporal_speckle | MT-Lee 多时相去斑 | VALIDATED | sar_multitemporal_speckle | sar_v3.py `multitemporal_speckle` | v1 | lee1980, oliver_quegan1998 | mean_temporal_weight、fallback_temporal/spatial、min_valid_slices；非 Quegan 谱域近似披露 | 重放逐位；常数栈恒等 | w=3/5/7；ENL 显式/估计 | 好：T≥3 InsufficientSamples、T≤24、负值拒绝、n_t<2 回退空域 | 2 |
| sar.coherence | 复数相干性 | **EXPERIMENTAL** | sar_coherence_estimate | sar_v3.py `coherence_estimate` | v1 | oliver_quegan1998 | clamped_pixels、valid_pairs、EXPERIMENTAL 状态 | a==b→γ=1（1e-9）；随机相位→γ<1 | re/im 双通道或 complex；w∈{3,5,7} | 好：强度-only 类型化拒绝（相位不可虚构）、γ 钳 [0,1]、无轨道元数据披露、不输出相位/解缠 | 2 |
| sar.rtc | 地形辐射校正 | VALIDATED | sar_radiometric_terrain_correction | sar_v3.py `radiometric_terrain_correction` | v1（cell_size/radar_range_azimuth 必需） | small2011, horn1981 | invalid_geometry_pixels、dem_invalid_pixels、convention | 平地恒等；斜坡 cos 比精确 | 标量/逐像元入射角 | 好：叠掩偶函数陷阱显式处理（facing∧α>θi 剔除）、cosθl≤0 阴影 nodata、米制 cell_size 披露 | 2 |
| sar.layover_shadow | 叠掩/阴影分类 | VALIDATED | sar_layover_shadow_mask | sar_v3.py `layover_shadow_mask` | v1 | small2011, horn1981 | fractions(4 类)、无 ray-casting 披露 | 楔形 DEM 三类精确 | {0,1,2,3} | 好：range-only 简化披露、DEM NaN→类 3、reflect 裙边保守披露 | 2 |
| sar.enl_map | 滑窗 ENL 图 | VALIDATED | sar_enl_map | sar_v3.py `enl_map` | v1 | oliver_quegan1998 | global_enl、degenerate_windows；低估偏差披露 | 4 视合成 ENL∈[3,5.5] | w∈{3,5,7}（默认 7） | 好：相对退化阈值（ε·mean²）防 1e16 伪 ENL、dB 拒绝、常数场 DegenerateData | 2 |

相关非专属：`remote.ratio_change`（明示适用 SAR 强度）；`remote.sid` 明示拒绝 SAR dB 输入（诚实）。

**关键区分核对**：intensity/amplitude（calibrate input_domain + log_scale 4 modes，严格）；linear/dB（6 处负值守卫拒绝 dB——但**按符号启发式**，全正 dB 场会漏检，见 §4-R6）；calibrated/uncalibrated（K 必需、绝不虚构；时序/合成工具明示无隐式定标/滤波前置）；single-date/time-series（MT-Lee 结构性要求 T≥3；时序工具 T 维显式）。总体严格。

## 2. Fake-native findings

| # | 严重度 | 发现 |
|---|---|---|
| FN-1 | **MEDIUM** | **sar.vh_ratio 宣称的 dB 路径不存在**。descriptor 假设（remote_sensing.py:217）与工具描述（tools/remote_sensing.py:421）仍写「dB 域为 dB 差（VV−VH）；单位语义由输入决定」；但实现自 C1 修复后**只支持线性功率比值**，dB（负值）输入直接抛 UnsupportedMethod（sar_temporal.py:337-357 docstring 明言旧声明是错的）。宣称行为=不存在 → fake-native 残留（文档未随 C1 同步）。无测试锁定 dB 路径（因不存在）。 |
| FN-2 | **MEDIUM** | **acquisition_comparability / SARAcquisitionMeta 是 lib 死代码**：入射角差>5°/升降轨混搭/极化混搭的可比性守卫已实现且有测试（test_temporal_science_vnext.py:353-381），但**没有任何生产工具接收获取元数据或调用该守卫**（grep 全仓：仅测试引用）。sar.temporal_stats / composite 的 limitation 也未提入射角混栈风险——「时序可比性」宣称与运行现实脱节。 |
| FN-3 | LOW | SAR 工具规模错误的 correction_hint「走栅格工件路径」**无对应实现**：全部 13 个 SAR 工具只收内联 JSON 数组（`_TOOL_ARRAY_MAX_VALUES=4_000_000`），而 lib 层允许 16M 像元——提示指向一条不存在的路径；且 4M（工具）vs 16M（lib）双闸不一致未披露。 |
| FN-4 | LOW | coherence 的 reflect 边界使窗口计数与边缘 γ 偏高——代码注释承认（sar_v3.py:435-437）但**未进 disclosure/meta 文本**，证据块读者看不到该偏差。 |
| FN-5 | LOW | sar.log_ratio_change 假设写「输入须为正（线性强度或 dB）」；实现 gate 的是 ratio>0——**同号负值对（纯 dB 场）实际可通过**。声明比实现更严（保守方向），非虚报，但契约≠实现。 |

未发现其他 descriptor 宣称 vs 实现的重大虚报：EXPERIMENTAL 边界（coherence、无 SAFE XML、非 Quegan、无 ray-casting、refined-lee 为近似）均实现-披露一致，是本仓诚实性最好的域之一。

## 3. 契约缺口

1. `sar.vh_ratio` 无 parameter_contract_ref（唯二无契约的 SAR descriptor 之一；另一为 sar.log_ratio_change，后者借 shared ratio_change_analysis v1）。
2. **nodata 语义分裂**：SAR 各模块 nodata=标量哨兵；`ratio_change`/`vh_ratio` 的 nodata 是**布尔掩膜**（`Optional[np.ndarray]`，`valid &= ~nodata`）——同名参数两种语义，工具层未暴露（未爆雷但易误用）。
3. 无获取元数据通道（时间戳/极化/入射角/轨道）→ FN-2；时序工具时间轴仅按数组序约定。
4. incidence_lut/noise_lut 在契约中 type="string"（「文档位」）——无 array 类型词表，形状/区间校验下放到工具签名+实现层；已声明但属 schema 级缺口。
5. 定标输出 dB/linear 仅靠 meta.to_db 区分，产物命名无 dB 后缀约定（跨工具链易混淆量纲）。
6. speckle/composite 等「无定标隐式前置」只靠披露——无输入 provenance 校验（定标前后不可在运行时区分），现状可接受但应在 descriptor assumptions 保持明示（已做到）。
7. 检测 detect_ratio_change 工具名不暴露 SAR 语义，sar.log_ratio_change 可发现性弱（tag/description 未标 SAR）。

## 4. 数值风险图

| # | 位置 | 风险 | 现状 |
|---|---|---|---|
| R1 | sar_log_scale db_to_linear | dB>~3082 → 10^(dB/10) 溢出 inf；输出**不计数、不披露**（errstate over=ignore，valid 掩膜按输入计算） | 低概率，建议 inf 计数披露（V3-R6） |
| R2 | calibrate_sar 负 DN | 单个负值拒绝整景（严格）。真实 GRD 未经热噪声去除的弱信号负值会整景失败 | 有意为之+hint 正确（先 remove_thermal_noise/log_scale）；无自动链路 |
| R3 | _window_stats | var=Σx²/n−mean² 灾难性消去→极小负值；已钳 0；n=0→NaN | 已处理 |
| R4 | Frost 边界 | zero-pad 偏移+有效集归一 vs Lee/Kuan 的 reflect——同窗口不同边界语义 | meta.boundary 已披露 |
| R5 | gamma_map Newton | 发散/越界冻结上一迭代（确定性）；闭式正根恰为二次方程精确解（代数验证 §7）；moved 以 |mean|+ε 归一；上限 50 硬校验 | 已处理 |
| R6 | **dB 检测为符号启发式** | 全正 dB 场（如强亮城市场景 σ⁰_dB>0）可穿透 6 处负值守卫被当线性强度处理（ENL/CV/滤波全错但静默） | 未披露；建议文档明示「负值检测≠量纲证明」或加可选 domain 参数 |
| R7 | enl_map 退化窗口 | uniform_filter 运行和残差 ~1e-16·mean² 曾产 1e16 伪 ENL | 已修：相对阈值 ε·mean²（好实践） |
| R8 | coherence | denom=0→NaN；γ 钳 [0,1]+超 1 计数；reflect 边界计数偏高 | 除 FN-4 外已处理 |
| R9 | ratio/log_ratio | 零分母→NaN；非正 ratio gate 防 Inf 泄漏（MINOR-1） | 已处理 |
| R10 | RTC cosθl | cos 公式对 (α−θi) 为偶函数→叠掩伪装成合法小角；已用 facing∧α>θi 显式剔除（罕见的正确细节）；arccos 前 clip ±1 | 已处理 |
| R11 | MT-Lee | σ²_temporal 含真实地物变化→权重保守偏空域（披露）；双方差 0→w=1 恒等 | 已披露 |

log 域/除零/NaN 纪律整体优秀：所有除法经 where-gate，errstate 显式，计数化披露。

## 5. 规模/后端缺口

- **工具面只有内联 JSON 路径**（≤4M 值）；lib 层 16M 像元闸在工具面不可达（FN-3）。无瓦片/工件/流式路径；4096² 场景下 refined_lee（≈13 个 float64 全平面）≈1.5-2GB 峰值，仅 memory_cost=high 声明，无峰值估算披露。
- 全部实现纯 numpy/scipy 单机；preferred_execution_policy（THREAD/INLINE）为进程内并发标记，无分布式/GPU 后端映射。对 T=24×16M 的 MT-Lee（O(T·HW·w²)）实际不可行的规模未被后端层拦截，只靠契约上限。
- 窗口族硬编码 {3,5,7}：大窗口（11/13）经典配置不支持（契约明示，诚实）；无多视（multilook）预处理算法。

## 6. 不确定性缺口（ENL 等）

- ENL 矩估计无不确定度：ENL=mean²/var 只有 enl_source 披露，无样本方差/CI（gamma 下 N·ENL 的卡方型区间可闭式给出）。滤波器 k 对 ENL 的敏感性未传播到输出不确定度。
- enl_map 的 valid_pairs 已输出，但 coherence **未提供逐窗置信区间**（Fisher z + 有效对数即可），EXPERIMENTAL 定位下尤其该给。
- 时序 CV/分位数：无各像元有效样本数下限守卫（n_t=1 时 std=0/CV=0 合法但误导——counts 进 meta 了，未挡）。
- RTC/layover：无 DEM 误差/坡度量化传播；local_incidence_deg 对无效几何像元也输出（>90° 值可用于诊断，算特性非缺陷）。
- 钳 0（热噪声）、ε 下限（dB）、矩估计（ENL）三类正偏/低估偏差均已计数披露——诚实性到位，缺的是**量级**（如 clamped_fraction 对中位数影响的界）。

## 7. 参考文献验证

method_references.py（app/lib/gis/method_references.py）逐条核对：

- **lee1980**：Lee, J.-S. (1980), IEEE TPAMI PAMI-2(2):165–168 ✓（局部统计 MMSE，k=var/(var+m²/ENL) 公式与原文一致）
- **lee1981**：Refined Filtering…, CGIP 15(4):380–389 ✓；实现为 7 子窗 MSE 代理，descriptor 明示「非 Lopes 完整 MAP 变体」——归因+近似披露正确
- **lopes1990**：Lopes/Touzi/Nezry, IEEE TGRS 28(6):992–1000 ✓
- **frost1982**：Frost/Stiles/Shanmugan/Holtzman, IEEE TPAMI 4(2):157–166 ✓；k=D·(CV/CVF)²、城市块距离权重实现与文献惯用式一致
- **kuan1985**：Kuan/Sawchuk/Strand/Chavel, IEEE TPAMI 7(2) ✓；k=(1−Cu²/Cv²)/(1+Cu²) 闭式与文献一致，测试手算锁定
- **oliver_quegan1998**：Understanding SAR Images, Artech House ✓；gamma_map MAP 方程 α·R²/m̄+(N−α+1)R−Nx=0 经独立求导（Gamma 形状 N 视似然 × Gamma(α, α/μ) 先验）**代数验证正确**，闭式正根 R₀=[(α−N−1)m̄+√((α−N−1)²m̄²+4αN·m̄·x)]/(2α) 正确
- **lee_jurkevich1994**：Remote Sensing Reviews 8(4):313–340 ✓（综述定位恰当）
- **small2011**：Flattening Gamma, IEEE TGRS 49(8):3081–3093 ✓；γ_flat=σ⁰·cosθi/cosθl 与原文一致；cosθl=cosθi·cosα+sinθi·sinα·cos(β−β_r) 几何推导核对正确（面坡 α=θi → θl=0）；Horn 3×3/北朝上/顺时针方位角约定自洽并有测试
- **horn1981**：Proc. IEEE 69(1):14–47 ✓
- **haralick1973**：IEEE TSMC SMC-3(6):610–621 ✓（完整无截断）

轻微引用拟合问题：**sar.thermal_noise_removal 引 oliver_quegan1998**——热噪声 LUT/denoising 是 ESA Sentinel-1 IPF 产品语义，O&Q 1998 并不覆盖；建议补 ESA IPF/去噪文献。`reed1990`（RX，非 SAR 域）作者写作 "Reed & Xiaoli 1990"（合同描述内），规范应为 Reed & Yu——顺带记录。

## 8. V3 建议清单

| # | 方法引用 | 现状差距 | 可行性 | 测试策略 | 优先级 |
|---|---|---|---|---|---|
| R-1 | —（契约修复） | FN-1：删除/改写 sar.vh_ratio descriptor 假设与工具描述中的「dB 域为 dB 差」，改为「仅线性功率；dB 请用 log-ratio」 | 极低（两处文案+1 断言） | 现有 vnext exact 测试加「dB 输入抛 UnsupportedMethod」锁定新契约 | **P0** |
| R-2 | Small 2011 式可比性（入射角>5°/混轨不可比） | FN-2：把 acquisition_comparability 接入 sar_temporal_stats/composite 工具（可选 acquisition 元数据参数→warnings 进证据块），或在 descriptor 声明其未接线 | 中（工具签名+可选参数） | 混轨/大入射角差 fixture → 证据块 warnings 断言 | **P0** |
| R-3 | Oliver&Quegan §4（ENL 估计方差） | ENL 估计无 CI | 中：均匀场景 var(ENL̂)≈ENL²·(2/n+…)；直接进 meta | 4 视合成 ENL 落入自报 CI 的频率测试（固定种子） | P1 |
| R-4 | O&Q coherence 统计 / Fisher z | coherence 无逐窗置信区间 | 低-中：z=atanh(γ)、SE≈1/√(n_pairs−3)，valid_pairs 已有 | γ CI 覆盖真值频率；n_pairs 小→宽 CI 断言 | P1 |
| R-5 | —（规模契约） | FN-3：工具 4M vs lib 16M 双闸 + 虚构「栅格工件路径」hint | 低：统一提示或实现瓦片工件路径；至少把 hint 改为真实选项 | 规模闸双界测试；hint 文案断言 | P1 |
| R-6 | ESA S1 IPF denoising 文档 | thermal_noise_removal 引用拟合弱 + R6 dB 启发式漏检 | 低：补引用条目；可选 input_domain 参数显式声明量纲 | 全正 dB 场 + 显式 domain=dB → 拒绝测试 | P1 |
| R-7 | db_to_linear 溢出 | inf 输出不计数 | 极低 | 400 dB 输入 → overflow_cells 计数断言 | P2 |
| R-8 | Quegan & Yu 1991（谱域多时相滤波） | MT-Lee 为强度栈近似（已披露）；真 Quegan 需复数 SLC 相干分解 | 中-高（依赖 SLC 数据面）；维持现状+在 descriptor 建议路线 | 若实现：复数栈 + 与 MT-Lee 对比 ENL 提升 | P2 |
| R-9 | 双极化 RVI（Keys 2012 类）/ compact-pol H-α | 极化特征仅 VV/VH 比值 | 中：RVI=4σVH/(σVV+σVH) 纯代数 | 手算 golden + dB 拒绝（与 vh_ratio 同守卫） | P2 |
| R-10 | ray-casting cast shadow / 多视预处理 | layover_shadow 无视线遮蔽（已披露）；无 multilook 算法 | 中：DEM 剖线遮蔽 O(HW·range) 或近似 | 楔形+高墙 DEM 遮蔽 golden | P2 |
| R-11 | FN-4 | coherence 边界偏差进 disclosure 文本 | 极低 | disclosure 字符串断言 | P2 |

**总评**：SAR 域是该仓科学诚实性最好的域之一——13 个工具全部注册且 parity 门零 issue、48 测试全绿、EXPERIMENTAL/近似/不解析边界全部显式披露、Gamma-MAP 方程经独立求导验证正确。主要问题是两处文档-实现脱节（vh_ratio dB 宣称、comparability 死代码）与工具面规模路径（4M/16M 双闸 + 不存在的工件路径 hint），均为 P0/P1 级低风险修复。
