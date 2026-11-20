# G19-TYPED-FIELD-COMPUTE — DEDUP_MATRIX

口径：`present` = 主干已有且本任务只做兼容回归；`partial` = 已有基础但缺本任务必需语义；`missing` = 主干无，本任务新增且独占；`owned-by-other` = 并行任务/开放 PR 的领地，不接管；`blocked` = 外部条件不具备，如实披露。证据为当前 `origin/master @8167052` 源码调用链或本次真实测试。

| # | 能力 | 状态 | 源码 / 测试证据 | 本任务动作 |
|---|---|---|---|---|
| 1 | 准备步 closed-union、未知类型拒收 | present | `app/services/data_preparation/plan.py:138-165`（`PreparationStep` 6 型 union + `parse_step` fail-closed） | 不改既有步集；新增计算列走**独立域**，不把任意 dict 塞进 `PreparationStep` |
| 2 | 原件不可变 / 输出新 ref | present | `executor.py:162` `copy.deepcopy` + `:501` `store(prefix="prep")`；`tests/data/test_preparation_plan_v1.py::test_input_payload_immutable` 通过 | G19 输出 `prefix="fc"` 新 ref，沿用同一 new-ref 语义 |
| 3 | 内容/descriptor 双锚、STALE_INPUT | present | `executor.py:471-490`（`_content_hash` + descriptor fingerprint）；`test_content_anchor_stale` 通过 | 扩展为 G19 `InputAnchor`（+ schema 指纹/scope/冻结快照），提交前重核 |
| 4 | 幂等收据 + 并发锁 | present | `executor.py:407-431`（`find_receipt` / `_exec_lock`）；`test_concurrent_execute_single_receipt` 通过 | G19 独立 receipt ring 与 per-fingerprint 锁，不复用 `prep` 的 hash 命名空间 |
| 5 | provenance / artifact 登记 | present（Windows 降级） | `executor.py:574-606` `record_transformation` + `register_artifact`；`app/services/artifact_registry.py:535` | additive 复用；失败如实披露，不作为 ready 的必要条件 |
| 6 | 字段角色提案 / 单位语义 | present | `field_proposal.py::propose_field_roles`、`app/lib/gis/measurement.py:101-119` `CANONICAL_UNITS`、`:440` `derive_field_semantics` | 复用 `CANONICAL_UNITS` 与 `scale_to_reference`；**字段名只作提示**，科学资格须 user-declared 单位 |
| 7 | CRS 分类与可信度量投影 | present | `app/lib/gis/crs_safety.py:114-161` `classify_crs`（含 3857 失真闸门）、`:164` `recommend_metric_crs` | G19 几何函数只经 `classify_crs` 门控 + `pyproj.Geod`，不复制临时经纬度公式 |
| 8 | MapSpec 样式表达式 | present（**不可复用**） | `app/services/mapspec_expr.py:22-34` 词表外 fail-open 回落 | G19 独立 fail-closed IR；绝不借用 fail-open 写数据 |
| 9 | 属性/空间/聚合连接 | present | `app/tools/data_fabric_tools.py:855-1154` | 只读消费其结果 ref，不重写 join |
| 10 | Data Fabric 查询模型 | present（select 仅字段名） | `app/services/data_fabric/query/models.py:117-131` `QuerySpecV2.select: Optional[List[str]]` | 不假定 select 支持任意表达式；计算列是本域 IR |
| 11 | 要素编辑 set_fields / undo journal | owned-by-other (G14 / #1873) | `app/services/feature_edit/contracts.py`；PR #1873 merged | G19 **不写原件、不碰 undo journal**；替换图层引用走既有 MapSpec mutation/CAS |
| 12 | staged 上传 / 容器清单 / 导入计划 | owned-by-other (G17) | 同机 `goal-g17-import-workbench-*` worktree；本批分工表 | 只读已有 refs（普通 GeoJSON/上传夹具），不碰上传协议 |
| 13 | ref 驱动数据导出 / 下载 / 保真报告 | owned-by-other (G18) | 同机 `goal-g18-dataset-delivery-*` worktree | 不等其下载器；用 `session_data_manager.get` + 现有读取接口验证回读 |
| 14 | Pi pin / RPC / extension / 原生工具暴露 | owned-by-other (G12 / #1973 / #2036) | `.gitmodules`、`PI_RUNTIME_CONTRACT.md`、PR #2036 Pi-only | 不改 runtime；按现有 `@tool`/ToolRegistry 注册新工具 |
| 15 | capability availability / qualification | owned-by-other (G13 / #2016) | PR #2016 merged | 不改资格政策；工具走既有 dispatch 门控 |
| 16 | 多画幅 / frame / PublicationIR | owned-by-other (G15) | 分工表 | 不碰 |
| 17 | 要素级标注覆写 / label schema | owned-by-other (G16) | 分工表 | 不碰 |
| 18 | 栅格表达状态 / tiles / 像元 | owned-by-other (G20) | 同机 `goal-g20-raster-authoring-*` worktree | 不碰 |
| 19 | OD 语义 / 方向流图 | owned-by-other (G21) | 分工表 | 不碰 |
| 20 | 项目数据刷新 / 自动刷新 | owned-by-other (#2022 G10) | PR #2022 open | 不做自动刷新；只做提交前版本重核 |
| 21 | 证据化地图简报 | owned-by-other (#2021 G11) | PR #2021 open | 不碰 |
| 22 | 全局 UI 重设计 | owned-by-other (#2032) | PR #2032 open，改主题/布局/侧栏骨架 | 仅新增功能专属子组件 + 一处局部挂载；不改主题/全局布局/面板宽度 |
| 23 | G09 参数化批量分析 | present/不纳入 | 历史任务，无在途 PR | 不做批参数实验/计算编排 |
| 24 | **闭合类型化表达式 IR + 稳定指纹** | **missing** | 主干无；`mapspec_expr` 是 fail-open 且无版本/指纹 | WP02 新增 `field_compute/contracts.py` + `parser.py` |
| 25 | **数值/条件/null 确定解释器（预览=全量同一核心）** | **missing** | 主干无 | WP03 新增 `interpreter.py` |
| 26 | **受限文本/代码/日期时区函数 + 前导零与大整数精度** | **missing** | 主干无 | WP04 |
| 27 | **单位投影/换算/派生量证据 + CRS 门控面积长度** | **partial**（词表与 CRS 分类已有，派生量传播无） | `measurement.py` / `crs_safety.py` | WP05 新增 `units.py` + `geometry.py`（只消费既有模块） |
| 28 | **作用范围 all_rows/可靠 selection/predicate + 冻结一致快照** | **missing** | 主干 selection 无全量可靠性语义（#2004 截断族、`#2066` 静默截断为反例） | WP06 `scope.py` + `snapshot.py` |
| 29 | **表达式预览摘要 / before-after / generation 失效** | **partial**（`dry_run` 只有质量 delta，无行级新值） | `executor.py:319-345` | WP07 新域 preview（不改旧 dry_run 契约） |
| 30 | **有界批执行 / 取消 / 原子可见登记 / 项目持久化披露** | **partial**（executor 有锁与 additive 登记，无 CPU offload/阶段可见性） | `executor.py:444-616` | WP08 新域 `service.py` + `receipt.py` |
| 31 | **属性计算 UI（独立子组件 + i18n + 入图）** | **missing** | `prepare-flow.tsx` 无计算列面板 | WP09 |
| 32 | **Pi 字段计算工具 + 对话闭环（同一服务）** | **missing** | `data_preparation_tools.py` 只有 `prepare_dataset` | WP10 `field_compute_tools.py` |
| 33 | **合成夹具 / 21 场景回归 / 独立 review** | **missing** | 本任务 | WP11–12 |

## 结论

本任务 = 24–33 共 10 项 `missing` + 27/29/30 三项 `partial` 的增量；1–10 的既有能力全部转为兼容回归（不重复实现），11–23 明确让出所有权。`DEDUP_MATRIX` 不因篇幅重复实现已 present 的项。
