# LOOP_LEDGER · G13-CAPABILITY-EXECUTION

## 轮次记录

### L1（2026-11-10）
- SHA base `816705218`；完成 BASELINE/DEDUP/PLAN；核对全部开放 PR/issue 与
  G06 代码。关键事实：G06 五模块零生产调用点；两原语 `binding_unbacked`；
  `artifact://` 合成 URI 零消费者；`compile_availability_snapshot` 无缓存。
- M1/WP06：两条 AlgorithmDescriptor + service_area capability +stats_table、
  cutoff 必填（契约对齐清 contract_divergence）、计数 253→255、生成物重生成。
  commit `94be1d3e1`。
- M2/M3（WP03+WP04）：缓存模块 + selector 6.8 阶段 + pi_native_surface 抑制
  消费（re-pin skip/disclosure/receipt drop）。commit `0f7ed8ae0`。
  修复：selection_trace 被整体重建覆盖的 KeyError；分类器 suppress 策略下
  才做 hallucinated 判定（keep 策略不误杀新工具）。
- M4-M6（WP02/05/07/08/09/10）：资格摘要 + 执行前复核 + artifact v2 封套/
  seam 铸 ref/内联大表剥离 + MAX_SCHEMES/不可达披露 + 发现面披露。
  commit `99e5cd3ab`。
  修复：seam 只认 v2 契约封套（v1 伪 URI 不铸真 ref）——由故障反验用例逼出。
- WP11：`test_g13_capability_execution.py` 26 用例（C01/C03/C05-C08/C12/
  C13/C15/C16 + 反验 + WP09/10）；全套回归绿；verify 6/6；catalog --check 通过。

## 关键决定（含取舍理由）

1. **不加 fallback_algorithms**：均值/单方案服务区不是科学等价替代（C12 语义；
   registry 校验要求 fallback 必须声明科学等价性，不诚实就不声明）。
2. **unknown_policy 双策略**：独立 rerank 保持 G06「缺席=抑制」；生产 selector
   「缺席=保留零贡献」——快照可能落后于刚注册的工具，dispatch/registry 闸权威。
3. **复核只认阳性结构事实**：declared_only/temporarily_unavailable/版本漂移可拒；
   权限不做（路由/owner 门禁所有，不建第二权限系统）；provider 不探测
   （缺事实=not_evaluated）。复核故障放行（闸不成为新故障面）。
4. **artifact v2 而非修补 v1**：合成 URI 是写-only 承诺；v2 由 seam 铸真
   ref:analysis-*，工具直跑时 stored=false 是诚实现状。
5. **cutoff 必填**：静默 2.0 默认可把「15 分钟」算成 2 米（伪成功）；
   与参数契约 required=True 对齐（science face 契约对拍逼出）。
6. **seam 剥内联 zones/边表**：完整表经 ref 可读，模型上下文只留有界投影
   + data_ref（与 uncertainty/profile 先例同纪律）。

## 失败与修复记录

- selection_trace KeyError（trace dict 被步骤 7 整体重建）→ 本地暂存后合并。
- C03 测试先用注入 store（与全局 session_data_manager 真源分叉，ref 解析失败）
  → 改用生产全局单例 + 唯一 session id。
- fake-URI 反验失败暴露 seam 对 v1 形状也铸 ref → 收紧到 v2 契约。

## 下一步

- WP12：独立 review → 修复 → 复审 → PR。
