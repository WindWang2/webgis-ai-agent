# 06 Optical Remote Sensing 审计

- 审计对象：worktree `/home/kevin/projects/webgis/webgis-ai-agent-science-v3`（分支 `feat/spatial-science-geoai-platform-v3`，HEAD 16d1c70）
- 审计范围：optical remote sensing 域（含与 SAR/共享底座的交界处；SAR 内部滤波/定标细节归 07 SAR 域，不展开）
- 审计方式：只读。运行了 `python3 -c` registry 枚举与关键 conformance 测试（55 passed），未修改 app/ tests/ docs/ scripts/ 任何文件
- 核心实现：
  - `app/lib/gis/algorithms/remote_sensing.py`（34 descriptors + 29 contracts）
  - `app/lib/geo_analysis/{spectral,tasseled_cap,raster_pca,rs_v3,glcm,raster_change,sar_temporal}.py`
  - `app/services/rs/{band_math,spectral_engine,stac_client}.py`（在线 STAC 路径）
  - `app/services/nature_resource_analyzer.py` + `app/lib/geo_analysis/raster_windowed.py`（本地 TIFF 窗口化路径）
  - 工具层：`app/tools/remote_sensing.py`（34 工具）、`app/tools/change_detection.py`、`app/tools/terrain_analysis.py`（compute_vegetation_index）、`app/tools/nature_resources.py`（analyze_vegetation_index）、`app/tools/advanced_spatial.py`（detect_raster_change / zonal_stats / raster_calculator）
- 测试状态：`test_spectral_science_vnext / test_tasseled_cap / test_raster_pca_v2 / test_rs_v3 / test_glcm_texture / test_change_detection_science` 合计 **55 passed**（本地实测），descriptor 引用的 conformance 测试路径均存在

---

## 1. Census 表

### 1.1 optical/光谱族 descriptors（registry 实枚举）

| id | 状态 | method_references | 前置条件 | seed | 契约 | 实现（已核实非 fake） |
|---|---|---|---|---|---|---|
| remote.ndvi | VALIDATED | rouse1974 | — | deterministic | — | `services/rs/band_math.INDEX_FORMULAS`（在线）+ `raster_windowed.windowed_band_index`（本地） |
| remote.spectral_index | VALIDATED | rouse1974,huete1988,gao1996,xu2006,zha_woodcock2003,key_benson2006,mcfeeters1996 | band_semantics_required | deterministic | spectral_index_analysis v2 | `geo_analysis/spectral.py`（INDEX_FAMILY 11 成员） |
| remote.tasseled_cap | VALIDATED | crist_cicone1984,baig2014,shi_xu2019 | band_semantics_required, raster_band_required:6 | deterministic | tasseled_cap_analysis v1 | `geo_analysis/tasseled_cap.py`（3 传感器系数注册表） |
| remote.pca | VALIDATED | （诚实留空） | raster_band_required:2, min_numeric_samples:8 | deterministic | raster_pca_analysis v1 | `geo_analysis/raster_pca.py`（SVD 全量） |
| remote.mnf | VALIDATED | green1988 | raster_band_required:2, min_numeric_samples:8 | deterministic | mnf_analysis v1 | `geo_analysis/rs_v3.mnf`（噪声白化 PCA + 逆变换） |
| remote.ica | VALIDATED | hyvarinen1999 | raster_band_required:2, min_numeric_samples:8 | fixed_seed | ica_analysis v1 | `rs_v3.ica`（sklearn FastICA，收敛披露） |
| remote.sam | VALIDATED | kruse1993 | raster_band_required:2, band_semantics_required | deterministic | sam_analysis v1 | `rs_v3.spectral_angle_mapper` |
| remote.sid | VALIDATED | chang2000 | raster_band_required:2, band_semantics_required | deterministic | sid_analysis v1 | `rs_v3.spectral_information_divergence`（对称形式） |
| remote.matched_filter | VALIDATED | boardman1995 | raster_band_required:2, band_semantics_required, min_numeric_samples:8 | deterministic | matched_filter_analysis v1 | `rs_v3.matched_filter`（pinv 白化投影） |
| remote.rx_anomaly | VALIDATED | reed1990 | raster_band_required:2, min_numeric_samples:8 | deterministic | rx_analysis v1 | `rs_v3.rx_anomaly` |
| remote.mad_change | VALIDATED | nielsen1998 | raster_band_required:2, min_numeric_samples:8 | deterministic | mad_change_analysis v1 | `rs_v3.mad_change`（SVD-CCA + IR-MAD ≤10 迭代） |
| remote.endmember_vca | EXPERIMENTAL | nascimento2005 | raster_band_required:2, min_numeric_samples:8 | fixed_seed | endmember_vca_analysis v1 | `rs_v3.extract_endmembers_vca`（简化确定性变体，披露） |
| remote.segmentation | VALIDATED | lloyd1982 | raster_band_required:1, min_numeric_samples:8 | fixed_seed | segmentation_analysis v1 | `rs_v3.segment_image`（k-means 基座，非 SLIC 披露） |
| remote.band_correlation | VALIDATED | — | raster_band_required:2, min_numeric_samples:8 | deterministic | band_correlation_analysis v1 | `rs_v3.band_correlation_table` |
| remote.temporal_features | VALIDATED | — | raster_band_required:2 | deterministic | temporal_features_analysis v1 | `rs_v3.temporal_features`（趋势+单周期谐波联合 LS） |
| remote.robust_normalize | VALIDATED | — | raster_band_required:1 | deterministic | robust_normalize_analysis v1 | `rs_v3.robust_normalize`（2-98 分位拉伸/匹配） |
| remote.cloud_qc | EXPERIMENTAL | — | raster_band_required:2, band_semantics_required | deterministic | cloud_qc_analysis v1 | `rs_v3.cloud_qc_basic`（亮度阈值 advisory） |

### 1.2 变化检测族

| id | 状态 | refs | 实现 |
|---|---|---|---|
| remote.change.raster | VALIDATED | — | `geo_analysis/raster_change.detect_raster_change`（文件路径、WarpedVRT 对齐、窗口化写入、O(window)） |
| remote.cva | VALIDATED | malila1980 | `raster_change.change_vector_analysis`（内存面，角色序契约） |
| remote.ratio_change / sar.log_ratio_change | VALIDATED | — | `raster_change.ratio_change`（ratio/log_ratio，含 `threshold_change` MAD/percentile 稳健阈值分类） |

### 1.3 SAR 标签但结构上通用的栈统计（光学可复用）

| id | 说明 |
|---|---|
| sar.temporal_stats | `sar_temporal.temporal_stack_statistics`：mean/std(ddof=0)/min/max/range + CV + 分位数；实现完全 generic（nan 感知时间维聚合），仅 descriptor 冠 SAR 名 |
| sar.temporal_composite | `temporal_composite`：mean/median/percentile 合成；同样 generic（median 对光学云污染合成亦是惯用法），仅 SAR 标签 |
| sar.glcm_texture | `glcm.glcm_texture`：纯 numpy GLCM（Haralick 8 属性，P+Pᵀ 对称，2-98 分位量化，逐行分块 bincount）——**光学纹理目前只能走 SAR 命名的 descriptor** |

### 1.4 域外注册但属光学路径的工具（registry 未挂 descriptor 的旁路）

| 工具 | 路径 | 波段语义 |
|---|---|---|
| compute_ndvi / compute_vegetation_index / detect_vegetation_change | `tools/remote_sensing.py` / `terrain_analysis.py` / `change_detection.py` → `services/rs/spectral_engine` | STAC 角色名 → common-name asset（显式，好） |
| analyze_vegetation_index | `tools/nature_resources.py` → `nature_resource_analyzer.calculate_index` | **波段位置自动猜测**（见 §3.1，域内最薄弱点） |
| detect_raster_change / raster_calculator / zonal_stats | `tools/advanced_spatial.py` | 波段索引（1-based）无语义角色 |

---

## 2. Fake-native / 声明不实 findings

总体结论：**未发现实质性 fake-native**。V2/V3 批次描述符与实现逐一对上，EXPERIMENTAL 标记（VCA/cloud_qc/coherence）与实现级近似披露（refined-lee MSE 代理、IR-MAD 简化重加权、VCA 简化变体、k-means 非 SLIC）诚实且被测试锁定。以下为残留的不实/过声明风险点（按严重度降序）：

| # | 级别 | finding | 证据 |
|---|---|---|---|
| F1 | **HIGH** | `raster_pca`/`mnf_transform` 等工具在规模超限时 correction_hint 写 **"或走栅格工件路径"**，但这些 V3 批次工具只接受内联 JSON 数组（`List[List[List[float]]]`），**不存在**文件/工件输入通道——提示指向一条不存在的路径 | `app/tools/remote_sensing.py:888`（raster_pca hint）vs 工具签名 `bands: List[List[List[float]]]`（:863） |
| F2 | **MEDIUM** | `_backend_selection_diagnostic` 在 remote_sensing.py 挂了 **22 处**，但 **没有任何 optical RS descriptor 声明 `backend_variants`**（仅 network/interpolation/statistics 声明）——诊断永远输出 "no_variants_declared: 默认工具路径"，是装饰性 evidence 而非决策层 | `app/tools/remote_sensing.py:87`；registry 枚举确认 RS 域 0 变体 |
| F3 | **MEDIUM** | `analyze_vegetation_index` 描述称"自动探测 RGBN/Sentinel-2 波段"，实现为**按波段数猜位置**（4 波段→band4=NIR；≥11 波段→S2 预设 band4=red/band8=NIR）。guess 结果虽然经 `detected_bands.source="guess-…"` 披露，但与中央 typed 层"绝不按位置猜测"的公开承诺直接矛盾（详见 §3.1） | `app/services/nature_resource_analyzer.py:52-70` |
| F4 | LOW | `sar.temporal_stats` / `sar.temporal_composite` / `sar.glcm_texture` 实现完全 generic，冠 SAR 名导致光学侧不可发现（可用性声明不实反向案例：能力存在但被标签藏住） | `geo_analysis/sar_temporal.py`（无 SAR 特有逻辑）、`glcm.py`（numpy only） |
| F5 | LOW | `remote.ndvi` descriptor `input_artifact_types=["raster_surface","terrain_surface"]`——NDVI 输入含 terrain_surface 语义不成立（复制粘贴痕迹） | `algorithms/remote_sensing.py:31` |

---

## 3. 契约缺口（band semantics 契约现状重点）

### 3.1 同仓库并存三套波段语义契约（核心结构缺口）

| 路径 | 契约 | 强度 |
|---|---|---|
| **Typed 层**（`geo_analysis/spectral.py`，remote.spectral_index/tasseled_cap/sam/sid/matched_filter/cloud_qc） | `band_map` 语义角色显式命名，缺角色 → `UnsupportedBandSemantics` 类型化拒绝；"本层不按波段位置猜测"写进模块 docstring 与 descriptor assumptions，测试锁定 | 最强 |
| **在线 STAC 层**（`services/rs/spectral_engine` + `band_math.INDEX_FORMULAS`） | 角色 → STAC common-name asset 显式映射（blue/green/red/nir/swir12），B02-B08 语义由 STAC 资产名承载 | 中强（依赖 STAC 元数据正确性） |
| **本地 TIFF 层**（`nature_resource_analyzer.auto_detect_bands` → `windowed_band_index`） | `band_map` 可显式传，但缺省走"Smart Guess"：count==4 → RGBN 位置猜；count≥11 → S2 预设位置（red=4,nir=8）；guess 来源仅在结果 payload 披露，**不阻塞** | 最弱——**位置猜波段的 BLOCKER 风险集中于此** |

具体风险：4 波段猜 RGBN 会误判 NIR−R−G 序（常见无人机/Planet 4-band 排布）；≥11 波段 S2 预设假定了文件内波段物理序（任意 11 波段栈如 Landsat 全序列/多时相栈同样命中预设）。guess 错误 → 指数数值系统性错误但仍有"合法"值域，`INDEX_VALID_RANGE` 只回传不校验，**不会触发任何告警**。

**建议**：guess 命中时（source 以 `guess-` 开头）默认升级为需要显式确认（strict 模式下类型化拒绝，宽容模式强制把 guess 事实写进 evidence warnings），并把 S2 预设改为按 **STAC/GDAL 元数据或波长** 解析而非波段序号。

### 3.2 NDWI 同名异式（跨路径语义冲突）

- 在线/本地路径（`band_math.INDEX_FORMULAS`、`raster_windowed.INDEX_BAND_ROLES`）：`ndwi = (green − nir)/(green + nir)` = **McFeeters 1996 开放水体**；
- Typed 层（`spectral.py INDEX_FAMILY`）：`ndwi = (nir − swir1)/(nir + swir1)` = **Gao 1996 植被水分**，`mndwi = (green − swir1)` = Xu 2006。

同名 `ndwi` 在两条路径给出不同公式、不同波段角色。`detect_vegetation_change(index_type="ndwi")` 走 McFeeters，`compute_spectral_index(index_id="ndwi")` 走 Gao——LLM/用户无法从 id 分辨。每条路径公式虽披露，但跨路径可比性断裂。建议拆分为 `ndwi_mcfeeters` / `ndwi_gao`（或 typed 层加 `ndwi_water` 别名并弃用裸 `ndwi`），并加一条跨路径 parity 测试锁定公式差异为**显式声明**。

### 3.3 其余契约缺口

1. **反射率值域校验只有一半**：`BAND_ROLES` 为光学角色声明了 `valid_range=(0,1)`，但 `compute_spectral_index` 仅对**输出**做 `out_of_range_fraction` 报告，**输入**从不对照 BAND_ROLES 值域校验；本地路径 `INDEX_VALID_RANGE` 只回传 payload、无 out-of-range 统计；在线路径仅 EVI 有 `_maybe_dn_to_reflectance` 启发式（max>1.5 即除 10000——亮云 at-satellite 反射率 >1.5 时会被错误归一化）。
2. **EVI 零分母语义两路径不一致**：在线 band_math 用 `where=den>0`（负分母 → NaN），typed `_safe_div` 用 `where=den!=0`（负分母参与运算 → 大负值，仅靠 out_of_range 披露）。
3. **传感器系数引用缺失**：tasseled cap 无 `kauth1976`（MSS 正典）与 Landsat-7 ETM+（Huang et al. 2002）；`landsat8_oli` 对 L9 的适用性未声明；MSAVI(qi1994)/NDMI(wilson_sader2002)/GNDVI(gitelson1996) 出处留空（**已诚实披露**，但词表可补全）。
4. **band-order 型算法（PCA/MNF/ICA/SAM/SID/MF/RX/MAD）**：接受 list/ndarray 时角色退化为 `band_i` 序号名（插入序披露）——比位置猜测好（有披露），但没有"必须声明角色"的硬闸；SAM 的 band_semantics_required 前置在 profile 缺 `bandSemantics` 事实时 **deferred（PASS）**，实际执行层并不强制（端元对齐靠长度校验）。
5. **raster_change / detect_vegetation_change 无波段角色契约**：`band=1` 默认索引参与差值，语义由调用方负责（descriptor assumptions 已披露"不构成语义分类"）。
6. **质量掩膜契约缺失**（详见 §6/§8）：无 S2 SCL / Landsat QA_PIXEL 位解码角色，cloud_qc 无云影检测。

---

## 4. 数值风险图（除零、归一化、NaN 传播）

| 算法 | 除零/归一化防护 | NaN 传播 | 残余风险 |
|---|---|---|---|
| spectral INDEX_FAMILY | `_safe_div` 零分母→NaN；EVI/EVI2 全零波段→NaN（#537）；MSAVI 判别式<0→NaN | 输入 NaN/Inf + nodata 掩膜→输出 NaN；统计 nan-aware | 负分母不拒（与在线路径 den>0 不一致）；out_of_range 只报告不钳制（by-design，好）；**输入无值域校验** |
| band_math（在线） | `where=(den>0)`；EVI DN 自检测（阈值 1.5/10000） | 零分母→NaN，不稀释统计（audit B-F09 语义） | DN 自检测为启发式：max>1.5 的合法亮反射率被误除 |
| tasseled_cap | 线性组合无除法 | 任一角色无效→三轴全 NaN（无角色级稀释）✓ | contribution 用 nanmean——全 NaN 场景返回 NaN 占比（无崩溃） |
| raster_pca | 标准化 std≤1e-15→DegenerateData；公共掩膜整行剔除 | 分量 NaN 回填✓ | common_fraction<0.5 仅警告不拒（披露）✓ |
| mnf | 噪声协方差奇异双闸（相对+数据尺度兜底）→DegenerateData；ρ/SNR=λ−1 | 同上 | 常量波段必拒（诚实）；差分噪声估计在边缘/错位栈上有偏（披露） |
| ica | 收敛 ConvergenceWarning 捕获→converged=false 披露 | — | 分量序/符号不唯一（披露） |
| sam | 零范数像元/端元→NaN；cos 钳 [−1,1] | argmin 仅在有限角度上取✓ | arccos 近 1 条件数（披露 1e-7） |
| sid | 非正分量/非正和→NaN + nonpositive_fraction | — | 要求正值输入（披露） |
| matched_filter | 零方差波段剔除披露；pinv；denom≈0→DegenerateData | — | 单高斯背景假设（披露） |
| rx | 岭正则尺度不变；常量场→δ≡0 披露 | — | 阈值 mean+kσ 启发式（非假设检验，披露） |
| mad_change | ρ 钳 ≤1−1e-12 防 0/0；权重下限 1e-4；共线→DegenerateData | — | **χ² 自由度取 2k，与 Nielsen/Canty 正典 χ²_k 不一致**（见下）；IR-MAD 无 no-change 概率优化（披露） |
| glcm | 量化 2-98 分位；零方差窗口 correlation→NaN；无有效对窗口全 NaN | 块内 NaN 感知均值✓ | 边界窗口对数变少（计数诚实）；entropy 自然对数（披露，与 base-2 惯例不同） |
| temporal_features | T≥4 且设计满秩才做谐波 | first/last 无效→NaN；无有效切片→NaN✓ | **无日期输入**：t 归一 [0,1] 假设等间隔采样，不规则时相使谐波频率失真（未披露） |
| robust_normalize | 分位区间 0→DegenerateData；NaN-aware 分位 | 越界钳端点（披露） | percentile_match 假设两景同序波段（按索引匹配，角色不校验） |
| cloud_qc | 阈值分位 nan-aware；零分母 NDVI 不进条件（保守） | isfinite 门✓ | **全 NaN 场景**：threshold=NaN → 空掩膜 + suspect_fraction=0，静默"无云"而非 NoValidObservations（minor） |
| raster_change（文件） | normalized_difference 零分母→nodata；inf→无效 | valid=双有效交集✓ | 阈值分类 nodata=255 uint8 语义清晰✓ |
| threshold_change | MAD=0 退化路径显式披露（阈值取最小超中位值） | NaN 不参与估计不判变化✓ | 逐像元独立性假设（披露） |

**MAD χ² 自由度**：实现/meta 披露 `chi2_dof = 2·n_bands`；Nielsen (1998) / Canty IR-MAD 惯例为 **χ²_k**（k=变分量数，标准化变分量方差 2(1−ρ) → 每分量 1 自由度）。当前实现不输出 no-change 概率，危害限于下游用 2k 查表误读；但 descriptor 把 2k 写成"约定"，属于**参考保真度偏差**。建议改 k 或给出推导说明。

---

## 5. 规模/后端缺口（chunk/window、内存上界）

| 层 | 上界 | 记忆模型 |
|---|---|---|
| 工具通道（内联 JSON） | `_TOOL_ARRAY_MAX_VALUES = 4,000,000` 值（~32 MB float64） | whole-array，先拒绝 |
| rs_v3 批次 / raster_pca（lib） | `RS_SCALE_LIMIT_CELLS = 16,777,216`（n_bands·H·W） | **whole-array，无分块**；超限 `ResourceScaleMismatch`（诚实拒绝，不假装可扩展）——注意 lib 上界(16M)大于工具通道上界(4M)，实际可达规模由工具层钉死 |
| spectral.py / tasseled_cap.py（lib） | **lib 层无规模守卫**（仅工具层 4M 兜底）；直接调用 lib 无界 | whole-array |
| glcm | `GLCM_OPS_LIMIT = 64M` pair-ops 估算 + `_CHUNK_HIST_CELLS=4M` bincount 分块 | **唯一真正分块的 numpy 实现**（峰值内存钉在常数预算）✓ |
| sar_temporal | T≤24 且 H·W≤4096² | whole-array 拒绝 |
| 文件路径（raster_change / windowed_band_index / raster_calculator） | `raster_guard`: 250M 像元 / 1 GiB 输出 / 10⁴ 放大比 | **O(window)**：窗口由内存预算推导、原子写+overview✓ |

缺口：
1. **V3 批次全部无文件/工件输入通道**（PCA/MNF/ICA/SAM/SID/MF/RX/MAD/VCA/segmentation/temporal_features/robust_normalize/cloud_qc 只吃内联数组）→ 16M 像元的真实影像进不来，"走栅格工件路径"提示不实（§2 F1）。tasseled_cap/spectral_index 的窗口化文件路径亦缺（仅 ndvi 族有 `windowed_band_index`）。
2. **backend_selection 对 RS 域是空转**（F2）：无 streaming/incremental 变体声明；`ScaleProfile.raster_cells` 通路存在但 RS 工具全部以 `feature_count`=像元数喂入（可用但语义混用）。
3. PCA/MNF 大栈唯一可行扩展路径是随机化/增量 SVD（sklearn `IncrementalPCA`/`randomized_svd`）+ 两遍协方差，当前未实现（descriptor 诚实声明"无流式实现"）。

---

## 6. 不确定性缺口

1. **RS 域 0 个 descriptor 声明 `uncertainty_outputs`**（对比 temporal.trend 声明了 statistical_significance）。cloud_qc 的 suspect_fraction、RX 的 anomaly_fraction、threshold_change 的 changed_fraction 都是单点比例，无置信区间/bootstrap。
2. RX 阈值 mean+kσ 是启发式（已披露），无 p-value/CFAR 语义（Reed-Yu 正典有 CFAR 推导）——可作为增强而非缺口否定。
3. IR-MAD 缺 no-change 概率输出（正典产物的核心量）——与 χ² dof 问题（§4）同源，是最值得补的不确定性量。
4. cloud_qc 无逐像元置信度（非概率产品已强披露）；无云影/气溶胶不确定性来源（sensitivity 分析缺失）。
5. temporal_features 谐波无残差方差/R² 输出——幅值/相位无可信度；不规则采样未建模。
6. VCA EXPERIMENTAL 已带人工核验要求✓，但无丰度（abundance）输出，端元恢复质量无量化指标（重建 RMSE 缺失）。

---

## 7. 参考文献验证（传感器系数引用）

对 `method_references.py` 实文核对（全部条目存在、书目信息与公开文献一致）：

| 引用 id | 核对结论 |
|---|---|
| rouse1974（NASA SP-351, 309–317） | ✓ 正确 |
| huete1988（SAVI, RSE 25(3)） | ✓；**但 EVI 系数项正典出处为 Liu & Huete 1995 / Huete et al. 1997**——代码注释已披露"引 SAVI/EVI 谱系"，属披露的词表折衷，建议补 huete1997 条目 |
| gao1996（RSE 58(3):257–266） | ✓（typed ndwi=植被水分语义一致） |
| mcfeeters1996（IJRS 17(7)） | ✓（但见 §3.2 路径冲突） |
| xu2006（IJRS 27(14):3025–3033） | ✓ |
| zha_woodcock2003（IJRS 24(3):583–594） | ✓ |
| key_benson2006（FIREMON RMRS-GTR-164） | ✓ |
| malila1980（LARS Symposia） | ✓ |
| crist_cicone1984（IEEE TGRS GE-22(3)） | ✓；系数 [0.3037,0.2793,0.4743,0.5585,0.5082,0.1863 / −0.2848,…,−0.1800 / 0.1509,…,−0.4572] 与发表值逐位一致 ✓ |
| baig2014（RSE 140:111–119） | ✓；OLI at-satellite 三行系数与发表值一致 ✓；reflectance_domain=at_satellite 披露正确 |
| shi_xu2019（IEEE GRSL 16(1):111–115） | ✓；S2 三行系数（0.3327,0.3637,0.5621,0.5728,0.3813,0.2423 / …）与 Shi & Xu 2019 发表值一致 ✓ |
| green1988 / hyvarinen1999 / kruse1993 / chang2000（IEEE Trans. IT 46(5):1927–1932，确为 IT 而非 TGRS） / boardman1995 / reed1990（页码 1760–1770，通行引文为 1760–1772，极小出入） / nielsen1998 / nascimento2005 / lloyd1982 / haralick1973 | ✓ 全部属实 |
| **缺失条目** | kauth1976（Kauth & Thomas, MSS 缨帽正典——tasseled_cap 家族源头未引）；huang2002（Landsat 7 ETM+ 缨帽）；qi1994（MSAVI）；wilson_sader2002（NDMI）；gitelson1996（GNDVI）——代码内已诚实留空，词表补全即可 |

---

## 8. V3 建议清单

格式：**方法引用｜现状差距｜可行性（依赖均已在库：numpy/rasterio/scipy/sklearn/numba；scikit-image 缺失）｜测试策略｜优先级**

1. **MNF 增强**（remote.mnf 已 native）
   - 引用：Green et al. 1988（已核）。
   - 差距：噪声估计仅 local_diff；whole-array 16M 上限；无多方向/多窗口噪声选项。
   - 可行：高——加 `noise_estimation=("local_diff","diff_2nd")`（二阶差分替代）与噪声可分离选项；大栈走 randomized SVD（sklearn utils 可用）。
   - 测试：rank-1 退化、逆重建 ≤1e-9（沿用现有 4 case）+ 新估计量 golden。
   - 优先级：P2。
2. **PCA 增强**（remote.pca）
   - 差距：无 streaming/增量；载荷符号 LAPACK 依赖；无文件通道。
   - 可行：高——`sklearn.decomposition.IncrementalPCA` 或两遍协方差+eigh；**确定性符号约定**（最大|载荷|分量为正）消除跨构建翻转；加工件输入。
   - 测试：现有 4 case + 符号约定锁定 + 分块 vs 全量一致性（1e-10）。
   - 优先级：**P1**。
3. **Tasseled cap 扩展**（remote.tasseled_cap）
   - 差距：缺 landsat7_etm+（Huang 2002）、landsat_mss（Kauth-Thomas 1976，同时补引用正典 kauth1976）；L9 适用性未声明；无 surface-reflectance 系数行变体声明。
   - 可行：高——注册表加两行系数 + 引用条目即可，模式已就绪。
   - 测试：手算 golden 全传感器（沿用 test_tasseled_cap 模式）。
   - 优先级：**P1**（低成本高价值）。
4. **Indices catalog 扩展**（remote.spectral_index）
   - 差距：11 指数缺红边族（NDRE=red_edge/nir）、CIre/CIgreen、MTCI、AWEI、UI、NDTI、BSI。
   - 可行：高——INDEX_FAMILY 纯数据扩展；`red_edge` 角色已存在（BAND_ROLES 有定义、无消费者）。
   - 测试：手算 golden + 缺角色类型化错误（沿用 test_spectral_science_vnext）。
   - 优先级：**P1**；同时补 §3.3-1 的**输入反射率值域校验**（BAND_ROLES.valid_range 消费者）与 EVI 零分母语义统一（P1 随行）。
5. **SAM/SID 补强**（已 native）
   - 差距：无 SID-SAM 联合判据（SID(1−cos SAM) 惯用组合）、无分类置信度、无端元从 VCA 输出直连的工具链（VCA→SAM 需手工搬运数组）。
   - 可行：高（纯 numpy）。
   - 测试：hand golden（现有 2 case 基础上加组合判据）。
   - 优先级：P2。
6. **Linear spectral unmixing（缺口：完全没有丰度反演）**
   - 现状：有 VCA（端元提取）+ matched_filter（单目标丰度式得分），**无 FCLS/GCLS 多端元约束解混**、无 OLS/NNLS 丰度图。
   - 引用：Heinz & Chang 2001（FCLS, IEEE TGRS 39(3)）；NNLS 经 `scipy.optimize.nnls` 或 `scipy.optimize.lsq_linear`。
   - 可行：**高**（小 k 批量 NNLS 向量化）；与 VCA/MF 形成完整 unmixing 链。
   - 测试：纯像元合成丰度恢复（sum-to-one 残差 ≤1e-6）、非负约束生效、退化端元类型化拒绝。
   - 优先级：**P1**（光学域最大功能性空白之一）。
7. **Texture family（光学命名 + 扩展）**
   - 差距：GLCM 只在 `sar.glcm_texture` 名下；无 GLCV/变差函数纹理、无 wavelet、无窗口/levels 奇偶扩展。
   - 可行：高——descriptor 增 `remote.glcm_texture`（同一实现、光学 band_semantics 前置）零实现成本；变差函数纹理可用 numba 窗口化。
   - 测试：alias parity 测试（与 sar.glcm_texture 逐位一致）。
   - 优先级：**P1**（alias）／P3（新纹理族）。
8. **Local entropy（缺口：未实现）**
   - 现状：GLCM entropy 是共生矩阵熵，**不是**局部直方图 Shannon 熵（经典 local entropy / König 变换）。
   - 可行：高——`sliding_window_view` + bincount 直方图 + −Σp·log p（分块同 glcm 模式）；补 log2/log e 披露约定。
   - 测试：已知直方图窗口手算精确；全常量窗口→NaN。
   - 优先级：P2。
9. **Multiband temporal statistics（光学别名）**
   - 现状：`sar.temporal_stats` 实现已 generic（T≤24、4096²、CV/分位），光学侧不可发现；多波段（多维谱×时间）联合统计缺。
   - 可行：高——descriptor `remote.temporal_stats` alias + parity 测试；契约 v3 加 per-band 参数。
   - 优先级：**P1**。
10. **Phenology descriptors（缺口）**
    - 现状：remote.temporal_features 仅单周期谐波+趋势，无 SOS/EOS/LOS/peak/mid-season（物候期）。
    - 引用：Jonsson & Eklundh 2002（TIMESAT 双 logistic, JGR 107）；Fisher et al. 2006（threshold-based phenology）。
    - 可行：中——双 logistic 逐像元拟合（n_t≥12 才有意义）；先行版可做分位阈值法（幅度 20%/80% 交叉）纯 numpy。
    - 测试：构造 logistic 时序 golden（SOS/EOS 误差 ≤1 切片）；NaN 序列退化。
    - 优先级：P2（阈值法先行）／P3（双 logistic）。
11. **Robust temporal composite（光学）**
    - 现状：sar.temporal_composite（mean/median/percentile）generic 但 SAR 名；无 **medoid** 合成（保持光谱一致性的正典光学合成）、无 quality-weighted 合成。
    - 引用：Flood 2013（medoid compositing, RTSTARS）。
    - 可行：高——medoid = argmin_i Σ_j d(b_i, b_j)（波段欧氏），numpy 可向量化；接 cloud_qc 掩膜做权重。
    - 测试：3 切片手工 medoid；NaN 感知；与 median 合成的光谱保真对比断言。
    - 优先级：**P1**。
12. **Temporal anomaly（缺口：栅格栈无）**
    - 现状：RX 是空间异常；栅格时序 z-score 相对 climatology（同期均值/方差偏离）无实现；temporal 域 CUSUM 只吃 stats_table 点序列。
    - 可行：高——给定完整栈按 cycle 聚合 mean/std → 逐像元 z；nan 感知。
    - 测试：注入异常切片 δ>3 检出；常量场退化披露。
    - 优先级：P2。
13. **Change vector 扩展**
    - 现状：CVA 幅度+固定角色序前两分量角；无多时相 CVA、无与 MAD 变分量的 MAF 后处理（Nielsen 1998 论文含 MAF，当前 descriptor 摘要未做）。
    - 可行：中——MAF 需空间协方差估计（可做）；多时相 CVA 是逐期两两 CVA 的薄封装。
    - 测试：MAF 方差序断言 + 现有 CVA hand case。
    - 优先级：P3。
14. **Robust change thresholds（集成缺口）**
    - 现状：`threshold_change`（MAD-z / percentile）已实现且质量好，**但没有 descriptor/工具面暴露**——只作为 raster_change 内部函数；CVA/MAD 幅度栅格走不到它（工具层断层）。
    - 可行：**高**——加 descriptor `remote.threshold_change` + 工具壳即可。
    - 测试：现有行为锁定 + 工具层 evidence 块测试。
    - 优先级：**P1**（纯暴露工作，零算法风险）。
15. **Quality mask contracts（缺口：云/影/QA 波段无契约）**
    - 现状：cloud_qc EXPERIMENTAL 亮度阈值；无 S2 L2A **SCL** 位表、无 Landsat **QA_PIXEL** 位解码、无云影检测。
    - 引用：Zhu & Woodcock 2012（Fmask/云影）；s2cloudless（Sentinel Hub）。
    - 可行：中——SCL/QA 解码是纯位表工作（rasterio 读 QA 波段 + 查表 → quality 角色掩膜），可先落地"QA 波段摄入契约"（BAND_ROLES 加 `scl`/`qa_pixel` 角色 + 解码器）；Fmask 级检测依赖热红外，标记 planned 诚实披露。
    - 测试：构造 SCL/QA 位样张 golden 解码；位完整性断言。
    - 优先级：**P1**（QA 解码）／P3（云影）。

---

## 9. 现有算法增强建议（非新增算法）

1. **修复 F1 提示不实**：raster_pca/MNF 等工具 correction_hint 改为"降采样后内联重试（本工具暂无工件输入通道）"或补文件通道。
2. **修复 F3 波段猜测**：`auto_detect_bands` guess 命中 → evidence warnings 强制 + strict 开关（默认拒绝 guess，S2 预设改按波长/元数据解析）。
3. **MAD χ² dof 2k→k**（§4），或 descriptor 里给出与 Nielsen 对照的推导；若不动实现，至少把 "约定" 措辞改为 "实现选择（与 Nielsen χ²_k 不同）"。
4. **NDWI 拆名**（§3.2）：typed 层加 `ndwi_gao`/`ndwi_water`，band_math 保持 McFeeters 但 description 注明两者不可互换；加跨路径 parity 文档测试。
5. **PCA 符号确定性**：固定符号约定（如首载荷最大绝对值分量为正）→ 跨构建可复现；当前"依 LAPACK 约定"披露对跨运行比较是实际负担。
6. **EVI DN 自动检测显式化**：在线路径 `scale_factors` 显式参数优先，自动检测降为 fallback 并写进 evidence（当前静默除 10000）。
7. **cloud_qc 全 NaN 场景**：返回 `NoValidObservations` 而非空掩膜（静默"无云"）。
8. **temporal_features 时间轴**：接受可选 `dates` 序列（年积日/ISO），不规则采样下 t 用真实时间；无 dates 时披露"等间隔假设"。
9. **tasseled_cap L9 声明**：`landsat8_oli` 说明适用 L8/L9 OLI/OLI-2（惯用约定，写进 disclosure）。
10. **backend_selection 落地或撤下**：为 remote.pca/mnf/glcm 声明真实 `backend_variants`（如 inline vs artifact 窗口化），或移除 22 处装饰性诊断挂载（§2 F2）。
11. **remote.ndvi input_artifact_types** 去掉 `terrain_surface`（§2 F5）。
12. **UQ 起步**：为 threshold_change/cloud_qc 加 `uncertainty_outputs` 声明 + bootstrap 比例置信区间（成本最低的两个入口）。

### 审计总结

光学域是本仓库科学化程度最高的域之一：typed 波段语义层 + 诚实披露 + conformance 锁定构成完整闭环，55 项核心测试全绿，tasseled cap 系数与逐条参考文献经核对均与发表值/书目一致。三个结构性短板：(1) 本地 TIFF 路径的"位置猜波段"与 typed 层承诺冲突（BLOCKER 级风险集中点）；(2) ndwi 同名异式与 EVI 零分母语义的跨路径不一致；(3) V3 批次无文件通道 + backend_selection 空转，使 16M 上限成为纸面数字。功能空白最大的是 linear unmixing（FCLS/NNLS）与 quality mask（QA/SCL 位契约）、medoid 合成——三者均为低成本高价值 P1。
