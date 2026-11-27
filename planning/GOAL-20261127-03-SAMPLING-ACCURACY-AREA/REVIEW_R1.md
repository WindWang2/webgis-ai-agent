# REVIEW_R1 — 独立科学审查：概率抽样调查与精度估计

- 审查人：R1（独立审查员，非实现作者）
- 仓库：`webgis-probability-sampling-map-accuracy-20261127`；分支 `zcode/goal-probability-sampling-map-accuracy-20261127`
- 审查范围：`git diff f34cb69dd..HEAD`。审查开始时 tip=`081ffa1fb`，审查过程中分支前进到 `eda3eb7bb`（新增 4 个 commit：docs/planning×3 + `tests/survey_sampling/test_reference_loop.py` + `frontend/lib/api/sampling-accuracy.ts` + i18n messages）。已核实 delta 中 **`app/` 后端零改动**（`git diff 081ffa1fb..HEAD --stat -- app/` 为空），本报告全部后端结论对当前 HEAD 成立。
- 方法：从 Olofsson et al. 2014（DOI 10.1016/j.rse.2014.02.015）定义独立重推全部方差公式；用自写 Monte Carlo（4000 次重复、3 层有限总体精确构建、不放回抽样）独立验证估计量的无偏性与 SE 公式；用边界输入直接驱动真实模块验证数值行为。**未采信实现者的任何测试**（实现者测试仅作交叉参照；本机无可用 pytest 环境，未运行其测试套件——数学正确性由独立 MC 保证）。

---

## 一、发现（按严重级别）

### P0（阻断）

**P0-1 分支不完整：已提交代码引用未提交的前端组件，fresh checkout 前端无法构建**
- 位置：`frontend/components/layout/context-panel.tsx:71`（HEAD 已提交）
- 问题：HEAD 提交了 `import { SamplingAccuracyPanel } from '@/components/sidebar/sampling-accuracy/sampling-accuracy-panel'`，但 `frontend/components/sidebar/sampling-accuracy/` 目录（panel + test）**未 track**（`git ls-files frontend/components/sidebar/sampling-accuracy` = 0，`git status` 显示 `??`）。任何人 clone/checkout 该分支都会在解析该 import 时构建失败。`frontend/lib/api/sampling-accuracy.ts`（507 行）与 messages 已提交，唯独面板组件本体漏 `git add`。
- 证据：`git grep -n "sampling-accuracy-panel" HEAD -- frontend` 命中；`git ls-files` 该目录为空。
- 建议：将 `frontend/components/sidebar/sampling-accuracy/` 提交入库；CI 增加"fresh clone 后前端 build"门禁防止此类漏提交。

### P1（严重）

**P1-1 store 路径注入：`frame_digest` / `estimate_id` 未做路径净化，可跨 owner scope 读取**
- 位置：`app/services/survey_sampling/store.py:97-98`（`get_frame_payload`）、`store.py:217-221`（`get_estimate`）、`store.py:212`（save_estimate 路径同构）
- 问题：`survey_id` 有净化（`_survey_dir`，store.py:113-118 拒绝 `/ \ .`），但 `frame_digest` 与 `estimate_id` 直接 `f"{digest}.json"` 拼路径。工具面入参完全无约束：
  - `app/tools/survey_sampling_tools.py:64`（`DesignPreviewArgs.frame_digest: str` 无长度/字符限制）、`:125`（`GetEstimateArgs.estimate_id: str`）；
  - `frame_digest = "../<另一owner>/frames/<digest>"` → `get_frame_payload` 读到**其他会话**的冻结框 payload（含源 URI、类别字典、面积）；`estimate_id = "../../<path>"` → 读取 owner 目录之外任意以 `.json` 结尾的文件。
- REST 面稍窄但同病：`app/schemas/survey_sampling_schema.py:27,40` 只限 `max_length=80`，无字符校验，短 hop（如 `../..`）仍可行；越界路径还会导致 `JSONDecodeError`/`IsADirectoryError` 未捕获 → 500（`_read_json` 只捕 FileNotFoundError）。
- 建议：对 digest/estimate_id 复用 `_survey_dir` 同款白名单字符校验（hex `[0-9a-f]{16,64}` 即可覆盖合法 id）；或统一走中央 path-sanitize helper。

### P2（中等）

**P2-1 估计输入缺一致性校验：n_resp > N_h 时负 fpc → 未捕获 ValueError（500）或被静默钳为 0**
- 位置：`app/lib/survey_sampling/estimation.py:259-264`（`_v_of`）、`estimation.py:223-251`（`StratifiedCounts.__post_init__` 校验 ΣW=1 与名称集合相等，但**不校验 n_resp[h] ≤ N[h]、N[h] ≥ 1**）
- 问题（已实测复现）：构造 `counts=[[20,10],[0,0]]`、`N=[10,10]` → fpc=1−30/10=−3 → UA 路径 `math.sqrt(var_i)`（estimation.py:419-423）抛 `ValueError: expected a nonnegative input`，逃逸出 `stratified_estimate`；service 层只捕 `EstimationNotEvaluable`（service.py:643-644），工具/REST 层只捕 `SurveySamplingError` → 500/UNEXPECTED。OA 路径的 `_sum_se`（estimation.py:511-518）对负分量求和后 `max(total,0)` 钳位——若 UA 未先行崩溃，会静默输出假 SE=0。
- 服务正常路径 n_resp ≤ N 由设计合同（n_h+backup ≤ N_h，design.py:139-144）保证；但 store 文件是用户可改的 JSON（samples.json 加行即可越界），且 `stratified_estimate` 是库级公共入口。
- 建议：`__post_init__` 增加 `np.all(n_responded <= N)` 与 `np.all(N >= 1)` 校验（DegenerateData typed 拒绝）；`_v_of` 对 fpc<0 显式抛错。

**P2-2 版本号竞态 + CAS 实际是 opt-in：文档声称"expected=None 仅首次创建允许"，实现却对 None 无条件放行**
- 位置：`app/services/survey_sampling/service.py:439-446`（`_next_version` 在 store 锁外读全量记录算 max+1）；`store.py:173-200`（`bump_revision`：`expected_revision is not None` 才比对）；`store.py:178` docstring 与实现矛盾
- 问题：两个并发 `record_reference`（同 sample_id）都算出相同 version → records.jsonl 出现重复版本号；`effective_labels`（service.py:548-558）用严格 `>` 保留**先写入**的同版本记录——后到的更正被静默遮蔽。同时客户端省略 `expected_revision`（工具/REST 默认 None）即完全绕过 CAS，乐观锁形同虚设。
- 建议：把 `_next_version` 移进 `append_records` 同一把锁（或让 append+version+bump 成单临界区）；`bump_revision` 对 `expected=None` 区分"首次创建"与"调用方省略"（例如首建后 state.json 必存在，None 且 revision>0 → 拒绝）。

**P2-3 分配可超小层总体：preview 无警告、create_survey 抛未映射的 InsufficientSamples → 500**
- 位置：`app/lib/survey_sampling/design.py:226-240`（`n_total` 只对总量封顶，逐层分配不按 N_h 封顶、不告警）；`service.py:244-262`（`SurveyDesign(...)` 构造在校验 `n_h+backup > N_h` 时抛 `InsufficientSamples`，service 层无捕获）
- 问题（已实测复现）：strata N=(10, 100000)、`n_total=10000`、equal 策略 → preview 返回 `{rare: 5000, big: 5000}` 无 warning；`create_survey` 中 `SurveyDesign.__post_init__`（design.py:139-144）抛 `InsufficientSamples`（ValueError 族，非 `SurveySamplingError`）。工具 `survey_create_design` 只捕 `SurveySamplingError`（tools:275-276），REST 只捕 `SurveySamplingError`（routes:119-120）→ 用户预算请求以 500/UNEXPECTED 收场而非 typed 422。
- 建议：`preview_sample_size` 逐层 `min(n_h, N_units)` 封顶并把溢出预算重分配/披露；service 把 `InsufficientSamples` 映射为 `INVALID_DESIGN` 422。

**P2-4 import_records 的合同校验异常未包装 → 未 typed 的 500**
- 位置：`app/services/survey_sampling/service.py:527-545`（`_import_row` 调 `rec.model_validate_strict()` 无 try/except）
- 问题：导入行 `status=undetermined` 且带 `reference_class` 时，`model_validate_strict`（contracts.py:149-164）抛裸 `ValueError`，逃出 `import_records`；对照 `record_reference` 同一校验有包装（service.py:390-394 → RECORD_INVALID）。同类输入一个入口是 typed 422、另一个是 500。
- 建议：`_import_row` 捕 ValueError → `RECORD_INVALID`（field="rows"），并带行号定位。

**P2-5 盲标可被 sample_id 确定性逆向击穿：uuid5(digest|stratum|unit_index) 可枚举**
- 位置：`app/lib/survey_sampling/selection.py:69-72`（`make_sample_id`）；`service.py:787-808`（盲标视图剥掉 samples 的 stratum_id/unit_index，但 `doc` 仍含完整 design payload：digest、strata 名、allocation、weights）
- 问题：解释员拿到盲视图（sample_id + 坐标）与 design doc（含 digest 与全部层名）后，可对每层枚举 `uuid5(ns, f"{digest}|{stratum}|{unit_index}")`（uuid5=SHA1，每候选微秒级；典型 N_h 秒级完成）精确匹配 sample_id → **恢复每个样本的预测层**，盲标防护对有心者失效。设计上 uuid5 保证幂等/跨端稳定，与盲标目标互相冲突。
- 建议：盲标视图下的 sample_id 改为每调查随机盐（如 uuid4 存于 samples.json、与 records 用映射表关联），或 records.jsonl 按盐化 id 存储；至少在 limitations 披露"盲标依赖流程纪律，非密码学保证"。

**P2-6 工具面无运行时上下文时 fail-open 到共享空 scope**
- 位置：`app/tools/survey_sampling_tools.py:32-33`（`_sid()` 把 `_resolve_session_id` 的 fail-closed None 兜底为 `""`）
- 问题：`upload_tools._resolve_session_id`（:17-39）在无 verified context 时返回 None（#2156 修复，fail closed），但 `_sid` 把它转成 `""` → `owner_scope={"session_id": ""}`，`store.owner_dir`（store.py:42-51）只查 key 维数不查值非空 → 所有无上下文调用共享 `session_id-/` 目录，互相可读写。正常 registry 链路总是绑定 context，但这是把已修复的 fail-closed 又改回 fail-open。
- 建议：`_sid` 为 None 时直接抛 typed UNAUTHORIZED（fail closed），`owner_dir` 同时拒绝空 value。

### P3（轻微）

**P3-1 不可估原因字符串与真实诱因不符**
- 位置：`app/lib/survey_sampling/estimation.py:394-403`（`any_zero` 把"非响应率超限/分配跳层"也归入 `_NOT_EVAL_NO_RESPONSE`（"stratum has zero responded samples"）；`estimation.py:447-449` PA 在零响应层情形给 `_NOT_EVAL_NO_VARIANCE`（"some strata n_h<2"））。status 正确（not_evaluated），仅机器可读 reason 误导排障。建议按诱因分发 reason。

**P3-2 limitations 静默截断**
- 位置：`estimation.py:169`（`list(self.limitations)[:32]`）。MAX_STRATA=64 时零响应层提示也只取前 8（各 limitations 条目内 `[:8]`）。截断未披露，可能丢失披露项。建议截断时附 "…and N more"。

**P3-3 preview 分配无逐层封顶/无告警**（与 P2-3 同根）
- 位置：`design.py:226` 只封总量；equal 余数按层序、proportional/neyman 均可给小层分配 > N_units。preview 是"无副作用预览"，用户据其确认预算——应显示不可行并给替代。

**P3-4 area_estimate 不验证 total_area 有限性；MetricValue.as_dict 不过 _jsonable**
- 位置：`estimation.py:479-493`、`estimation.py:96-111`。`total_area` 为 NaN/负时（store 文件手改才可达；frame 冻结值正常）会产出 NaN 的 estimate/se 且以裸 float 进 JSON（Python json 默认输出 `NaN` 非法 JSON）。建议 as_dict 复用 `_jsonable` 语义 + service 校验 `total_area > 0 and isfinite`。

**P3-5 store 锁表无界增长 / 锁键不含 owner**
- 位置：`store.py:77-85`。`_LOCKS` 按 survey_id 只增不减（长进程内存缓慢泄漏）；键不含 owner_scope——不同 owner 同名 survey_id 共用一把锁（仅竞争，无正确性问题）。建议用弱引用/定期清理；键加 owner 前缀。

**P3-6 MAX_SURVEYS_PER_SCOPE 声明未执行**
- 位置：`contracts.py:74`。全仓库无 enforcement 点（grep 仅定义处）。单 scope 可无限建调查。建议 `create_survey` 计数阻断。

**P3-7 frame 排除计数口径瑕疵**
- 位置：`frame.py:243-249`。`nd is None` 时 nodata 桶恒 0，真实 nodata 像元全计入 `outside_dictionary`；类别值 == nodata 值时同一像元既入框又计入 nodata 排除（重复计数）。仅影响披露口径，不影响帧/估计数学。

**P3-8 q̂=0/1 处 plug-in 方差塌缩为 0**
- 位置：`_v_of`（estimation.py:259-264）数学性质：层内全对/全错 ⇒ SE=0、CI 退化为点（实测 PA_a se=0.0、CI=[0.6,0.6]）。Olofsson 公式本身如此，非实现 bug，但与 `normal_approx_ok` 同级的边界披露（如 "variance estimate zero due to boundary proportion"）会更诚实。

**P3-9 选样复现性绑定 numpy Generator 流**
- 位置：`selection.py:88-90`（`default_rng(seed).permutation`）。numpy 对 Generator 流不承诺跨大版本冻结（NEP 19 政策），design digest 固定了 seed 但未记录 numpy 版本 → 多年后重抽可能不同序列。建议 payload 记录 numpy 版本或改用流冻结的 `RandomState`/显式记录算法版本。

**P3-10 工具域关键词 "survey" 过宽**
- 位置：`app/services/tool_catalog.py:44-55`。单独 "survey" 一词会激活调查工具面（按该文件"宁严勿宽"政策只是 token 代价），可接受；提示可选 "survey_sampling"/"抽样调查" 级别词。

**P3-11 GET /surveys 与 survey_detail 非 blind 视图泄露层名/分配强度**
- 位置：`routes/survey_sampling.py:245-251`、`service.py:812-819`、`service.py:787-808`。清单返回 design 文档（strata 名、allocation、weights、seed）。层名即图例（解释员本来就可见），不含样本级预测——**单独不构成盲标泄露**；但与 P2-5 叠加后 design doc 成为逆向样本层的字典。严重度按 P2-5 计，此处仅记录清单面本身：建议盲标协议下把"每层样本量/权重"延后到 unblind 后披露。

**P3-12 survey_progress 向解释员暴露每层已标注计数**
- 位置：`service.py:609-618`。进度面板按层名聚合（per_stratum 键=预测类名）。不含样本级映射，弱推断面；展示用途可接受，记录在案。

---

## 二、数学正确性验证（独立推导 vs 实现，逐项）

先声明记号：N 个等面积单元，层 h（=地图类 h）有 N_h 单元，W_h=A_h/A、ΣW_h=1；层内 SRS 不放回抽 n_h，n_hj = 层 h 中判读为参考类 j 的响应数，n_h=Σ_j n_hj。

**1a. 加权误差矩阵 p̂_hj = W_h·n_hj/n_h —— 正确**
- 实现：`estimation.py:366-369`，`p_hat = W[:,None] * (counts/safe_denom)`，空层行置 NaN（序列化为 null，不是假 0）。
- 手推：行=地图类=层，与 Olofsson Eq.(1) 一致；行和 Σ_j p̂_hj = W_h（已知常数，实现用于 oracle 断言）；全矩阵 Σ=1（当所有层有响应）。列序=行序（service.py:714-720 强制 strata==refs 集合相等，`StratifiedCounts.__post_init__` estimation.py:243-251 二次阻断字典漂移）。
- 结论：✅ 与定义逐位一致。

**1b. OA = Σ W_h q_h，V(OA)=Σ W_h²(1−n_h/N_h)·q_h(1−q_h)/(n_h−1) —— 正确**
- 实现：`estimation.py:399-411`；`_v_of`（:259-264）= (1−n/N)p(1−p)/(n−1)，逐层乘 W_h² 后求和。
- 手推：Olofsson Eq.(4) V(Ô)=Σ_h W_h² Ŝ²_h/n_h，Ŝ²_h=n_h/(n_h−1)·q_h(1−q_h) ⇒ 化简恰为 W_h²(1−n_h/N_h)q(1−q)/(n_h−1)。(n−1) 与 (n/(n−1))/n 两种写法代数等价，实现采用了化简形。
- MC 验证：4000 次重复、三层（W=.6/.3/.1，N=6万/3万/1万，n=200/150/120）：OA 偏差 −7.9e−5≈0；解析 SE / 经验 SD = **1.013**（差异在 MC 误差 1.1% 内）；95% 覆盖率 0.953。
- 结论：✅。

**1c. UA_i = q_i，V(UA_i)=(1−n_i/N_i)·q_i(1−q_i)/(n_i−1)，无 W² 因子 —— 正确**
- 实现：`estimation.py:413-423`，SE=√v_i，无权重乘子。
- 手推：strata==地图类 ⇒ 行和 N_i 已知（UA 分母无抽样误差），Olofsson Eq.(5) 特化即层内单比例方差。
- MC：三层 SE 比值 0.999/0.989/0.984。漏乘/多乘均不存在。
- 结论：✅。

**1d. PA_j = W_j q_j / p̂_·j 的 δ 方法方差 —— 正确（含 Cov(A,B)=V(A) 的独立验证）**
- 实现：`estimation.py:425-477`：`var_pa = ((1−f)²V(A) + f²Σ_{h≠s}W_h²v_h)/B²`，A=W_s q_s、B=p̂_·j、f=A/B。
- 手推：f=A/B，B=A+Σ_{h≠s}W_h p_hj；层间样本独立 ⇒ Cov(A, 其余项)=0 ⇒ Cov(A,B)=W_s²v_s=V(A)。δ 展开：V(f)=(1/B²)[V(A)+f²V(B)−2fCov(A,B)]，代入 V(B)=V(A)+Σ_{h≠s}W_h²v_h ⇒ =（1/B²)[(1−2f+f²)V(A)+f²Σ_{h≠s}W_h²v_h]=（1/B²)[(1−f)²V(A)+f²Σ_{h≠s}W_h²v_h]。**与实现逐项一致**（分子层方差已含于 V(A)，循环正确跳过 h==s_row，estimation.py:461-469）。
- MC：PA 三类 SE 比值 0.994/1.003/0.990；覆盖率 0.938–0.946（δ 线性化在 f 远离 0.5 时轻微反保守，属方法固有、非实现错误）。
- 边界处理：B≤0（无任何响应单元判为 j）→ evaluable=false（estimation.py:452-454）；分子层零响应 → zero_row；✅。
- 结论：✅。

**1e. 面积 = 比例 × total_area，SE/CI 线性缩放 —— 正确**
- 实现：`estimation.py:479-493`：estimate/se/ci/clipped 全部 ×total_area，CI 界改为 [0, total_area]（clipped 重新按 0/1 裁剪后缩放，与原区间裁剪逻辑一致）。
- 手推：Â_j=A_total·p̂_·j，V(Â)=A_total²·V(p̂)，线性量随机缩放严格成立（无 δ 近似）。oracle 用例 5800/4200 与比例×10000 一致。
- 遗留：total_area 无有限性校验（见 P3-4）。
- 结论：✅（缩放数学正确）。

**1f. 有限总体修正 n_h=N_h 时方差归零 —— 正确（已实测）**
- 实现：`_v_of` fpc=1−n/N；实测 counts 恰为全查（n_resp=[100,50]=N）时 OA/UA/area 的 SE **全部精确 =0.0**、CI 退化为点估计。
- 结论：✅。

**1g. z=1.96 正态近似与越界披露 —— 正确且诚实**
- 实现：`Z_95=1.959963984540054`（estimation.py:55）；`_ci`（:267-279）原始区间永远给出，越界时附 `ci_clipped` 裁剪副本，**不静默截断**；`normal_approx_ok = n_resp ≥ 30`（:495）按层披露（建议阈值非硬拒绝，docstring 与行为一致）；n_h=1 层单独 limitation（:325-330）。
- 小 n 行为：n_h<2 → SE=None → evaluable=false（点估计保留）；全部层 <2 → `EstimationNotEvaluable`（:370-381，实测 Case5 抛出）。不会用 t 分布遮掩，也不会静默给假区间。
- 结论：✅。

**1h. 替补顶替机制 —— 机制合理，等概率性以 MAR 为条件且已披露**
- 实现：主样本与替补同为 `permutation(N_h)` 前缀（selection.py:101-132），层内同 π=n_h/N_h；`_assemble_counts`（service.py:702-779）按 draw_rank 顺序用"下一有标签替补"逐位顶替非响应主样本，未用上的替补不计入（无重复计数）；`substitution_rate=非响应/计划`、`shortfall_rate=(计划−响应)/计划`，任一超设计声明上限 → status=not_evaluated 拒绝总体推断（estimation.py:345-364）；未超限但有顶替 → partial + "assumes missing-at-random within strata — disclosed, not verified"。
- 手推评估：主样本∪替补 = 层内 SRS(n_h+backup)；分析集 = 有标签主样本 + 按序顶替的有标签替补。**只有当非响应在层内随机（MAR）时**，响应集才是层内可交换子集，把 n_resp 当 SRS(n_resp) 处理才是设计一致的——该假设不可验证，实现以 limitations 显式披露而非宣称，符合 Olofsson §5 对响应处理的建议口径。上限拒绝逻辑（>max 拒绝、==max 放行为 partial 披露）与合同语义一致。
- 结论：✅（PASS，MAR 披露到位； disputed 记录会占用主样本位并触发顶替、原因入 excluded_reasons——行为自洽）。

---

## 三、数值边界实测（真实模块直接驱动）

| 输入 | 行为 | 判定 |
|---|---|---|
| 全查 n_resp=N | SE 全 0、CI 退化点 | ✅ |
| 层 n_h=0 | 矩阵行 NaN→null；OA/PA/area 全部 evaluable=false；status=not_evaluated | ✅ 不产假 0 |
| 层 n_h=1 | OA evaluable=false（点保留）、status=partial + limitation | ✅ |
| 所有层 n_h<2 | 抛 EstimationNotEvaluable（typed 422） | ✅ |
| 零列（无人判为 j） | area=0（SE=0，见 P3-8）；PA_j evaluable=false reason=zero_column | ✅（P3-8 记录） |
| ΣW≠1 / W 含 NaN | DegenerateData 拒绝 | ✅ |
| strata 与 ref 字典集合不等 | DegenerateData（类字典漂移阻断） | ✅ |
| n_resp>N（fpc<0） | **UA 路径裸 ValueError → 500**；OA 路径钳位风险 | ❌ P2-1 |
| total_area 异常 | 未校验（NaN 产非法 JSON） | ⚠ P3-4 |

结论：正常服务路径不会输出假 0 / 假 CI / 伪成功；假 SE 风险仅存在于"绕过设计的输入"（P2-1 的根因是缺一致性校验而非公式错误）。

---

## 四、通过项清单（检查过、无问题）

数学与统计
1. 加权矩阵/OA/UA/PA/面积五组公式与 Olofsson 2014 定义一致（独立 MC 通过，见第二节）。
2. Cov(A,B)=V(A) 的 A⊂B 完全共线推导正确落入实现。
3. fpc、(n−1) 分母、W² 权逐项正确；UA 无 W 因子。
4. 全查归零、n_h=0/1 降级、EstimationNotEvaluable 全部按 docstring 契约行为。
5. Neyman/比例/等额分配与大盘尼余数（总量恰好 n、平列按层序稳定）正确；Eq.13 反推 n=(ΣW_hS_h/S)² 正确。
6. 层独立 seed（sha256(design_seed|stratum)）消除同种子跨层排列相关。
7. 帧等面积核查（5×5 测地探针、spread>1% typed 拒绝）防止"像元数当面积"；类别值 dtype 可表示性校验防 uint8 回绕错层；空类拒建框。
8. 源漂移：estimate 前 `RasterReader.metadata().fingerprint` 复核（service.py:669-699）；create 时重建帧 digest 比对（service.py:343-351）；16hex 前缀比较语义正确。

工程与权限
9. 共享文件改动全部**纯增量**：`app/main.py`（1 import + 1 include_router）、`app/tools/__init__.py`（+1 注册行）、`tool_catalog.py`（+1 域关键词）、`algorithms/__init__.py`、`capabilities/__init__.py`、`method_references.py`（+olofsson2014 条目）——无删除、无既有语义改动，回归风险低。
10. `survey_id` 路径净化正确（拒 `/ \ .`）；survey_id 服务端生成（uuid4 hex16）。
11. REST 全端点挂 `require_owned_session`；工具面 session 经 `_resolve_session_id` 校验（除了 P2-6 的空串兜底）。
12. `record_reference` 响应不含 stratum/预测类（盲标响应面干净）；参考类必须在冻结字典内（CLASS_DICTIONARY_DRIFT 阻断）；records append-only、争议不覆盖（disputed 落库 + adjudicate 恢复链路自洽）。
13. store 写全走 tmp+`os.replace` 原子替换；records.jsonl 进程内锁；帧 payload content-addressed 幂等（digest 先于自身嵌入，稳定）。
14. 估计 payload 完整携带 provenance（design/frame digest、max_nonresponse_rate、seed、estimator_version、Olofsson 引用）——可追溯性好。
15. `EstimationNotEvaluable`/`InsufficientSamples`/`DegenerateData` 在 estimate 路径的映射正确（ESTIMATION_NOT_EVALUABLE 422）。
16. 预算：MAX_SAMPLES_PER_SURVEY、MAX_RECORD_BATCH、MAX_STRATA、n_h+backup≤N_h 均有 typed 阻断（MAX_SURVEYS_PER_SCOPE 除外，P3-6）。
17. 前端共享文件（nav-rail/context-panel/hud-types/workbenchSlice/messages）均为 append-only tab 注册，无既有语义破坏（组件本体漏提交除外，P0-1）。

---

## 五、修复优先级建议

1. **立即**：P0-1 补提交 sampling-accuracy 组件（阻断合入）。
2. **合入前**：P1-1 digest/estimate_id 路径白名单；P2-1 StratifiedCounts 一致性校验；P2-4 import 校验包装。
3. **尽快跟进**：P2-2 版本号+CAS 语义收紧；P2-3 preview 逐层封顶 + InsufficientSamples 映射；P2-5 盲标 sample_id 盐化；P2-6 空 scope fail-closed。
4. **择机**：全部 P3。

## 六、审查独立性声明

本报告全部公式由审查者从 Olofsson et al. 2014 定义独立重推，Monte Carlo（seed=20261128，4000 次重复）为审查者自写代码，边界探测为审查者自写驱动脚本；未采信实现者测试的任何断言（其 `test_estimation_oracle.py` 手算值与审查者独立推导一致，仅作旁证）。审查期间分支前进 4 个 commit，已核实 delta 不触及本报告所依据的后端代码。除本报告文件外未修改任何代码/测试。
