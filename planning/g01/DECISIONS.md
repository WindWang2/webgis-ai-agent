# G01 DECISIONS

| # | 决策 | 理由 | 备选与否决原因 |
|---|---|---|---|
| D1 | 角色绑定放 `app/services/dataset_semantics/role_binding.py`（新文件，主责区） | ADR-0215 的服务包即语义权威面；避免碰 workflow_schema（#1758 相邻区外但为 C3 稳定契约，不宜动） | 改 resolve_data_roles 本体：会动 conformance corpus 依赖的稳定契约，风险大收益小 |
| D2 | 词表复用 DATA_ROLES（15 值），不新造角色 | 单一事实源；方法级 requires_roles 已依赖该词表 | 新语义角色词表：违反 SSOT |
| D3 | 绑定状态词表：bound / not_found / ambiguous / stale / partial（+not_authorized 经读取失败码） | 提示要求的五类区分；与 qualify 五态（eligible/…）显式不同层——绑定层管「哪个数据集」，资格层管「数据行不行」，不混用 | 复用 QUALIFICATION_STATES：语义混淆 |
| D4 | 确定性排序：refs 按 ref_id 字典序排序后再绑 | dict 迭代序跨后端不稳定（内存 vs Redis）；「首个命中」即当前歧义根源 | 声明序：ref 无声明序字段 |
| D5 | ambiguous 输出候选清单（≤4）+不自动选 | 「两个同名数据集」场景必须结构化澄清而非取首个 | 按字母序取首个：伪造确定性 |
| D6 | W2 接线全部 additive 可选参数（缺席=现状不变） | 向后兼容；#1758 未触碰这些文件但仍保持零破坏 | 改签名必填：破坏 4 个生产调用方 |
| D7 | 恢复接线放 project_runtime resume 路径（rebind 后置钩子），复用 build_descriptor_from_ref_descriptor 零扫描补铸 + evaluate_reuse | resume_anchor.py:368-388 已验证同一模式；不新造迁移机制 | 在 rebind 内改 semantics_preserved 语义：动冻结校验面风险高 |
| D8 | preflight 只读 descriptor/bbox/crs/temporal/units（metadata-only），不触数据 | 禁止每 turn 扫大表；深度检查走既有后台任务 | 即时扫描：违反资源纪律 |
| D9 | 测试目录 tests/unit/dataset_semantics/（新建子目录） | 主责测试独立成区（提示要求）；引用既有 conftest | — |
| D10 | 不跑全量后端套件（#1753 ~80% 挂起）；只跑目标目录 + ruff | 提示明确禁止无超时全量 | — |
| D11 | planner finalize / chat plan_orchestrator / workflow_runtime 不注入 role evidence | planner 无 session 锚（_evaluate_workflow_contract 签名无 session_id，穿线需动 3+ 调用方）；语义证据生产已达 semantic_tools 真实入口（advisory 编译工具，含 data_role_bindings/data_preflight 审计面）；主链注入留作后续 additive 缝 | 大手术穿线：超出「小范围生产桥只碰实证必要的缝」边界 |
| D12 | 时间判据月精度 + not_applicable 细分 | 复审 P1/P2：年粒度假 pass、静态数据误伤；open-ended 不外推端点 | 整年区间硬比：假结论 |
