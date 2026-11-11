# G19-TYPED-FIELD-COMPUTE — PLAN

目标：在独立 worktree 交付「有类型、单位、时区、作用范围和版本锚的字段计算工作台」，贯通安全表达式 → 预览 → 完整派生 → 持久成果 → UI/Pi 后续制图；原件不变，与 G12–G18/G20–G21 独立；review→修复→复审后提交 PR，不等线上 CI。

## 架构决定（基于 BASELINE / DEDUP_MATRIX）

- **新域独立**：`app/services/data_preparation/field_compute/`（`contracts / functions / parser / interpreter / units / geometry / scope / snapshot / receipt / service`）。不重写 preparation、Data Fabric、MapSpec。
- **表达式 = 数据不是代码**：闭合 Pydantic discriminated-union AST，只有 `lit / field / unary / binary / call / case` 六类节点；函数名与算子是 closed 词表；无 `eval`/`exec`/`Function`/SQL 拼接/属性反射/文件/网络/系统时钟/随机数。
- **单一解释器**：`preview` 与 `execute` 共用 `interpreter.evaluate_rows()`；差别仅在输入行数与是否登记。
- **单位诚实**：字段单位默认 `unknown`；只有 `field_units` 显式 user-declared 才参与换算/科学资格。`CANONICAL_UNITS` 提供 dimension + `scale_to_reference`；派生量保留 numerator/denominator 来源与 `unit_status`。
- **几何走既有 CRS 门控**：`classify_crs` 判 `geographic`（`pyproj.Geod` 大地测量面积/长度）/ `projected_local_metric`（shapely 平面）/ 其余（3857/unknown）阻断。
- **一致快照**：执行/预览开始时把 ref 载荷一次性 `deepcopy` 冻结（预算内），所有批次只读该快照；发布前**再核一次**当前 store 内容哈希，变化即 `SNAPSHOT_CHANGED` 中止且不发布。不采分页样本冒充全表。
- **Windows 基线缺陷隔离**：descriptor/provenance/artifact 登记 additive，失败如实披露；ready 只取决于「输出 ref 可读 + 结果对账 + receipt 落盘」。
- **不占 ADR 编号**：契约在本目录 `INTERFACES.md` + 模块 docstring（ADR 目录已有 0286/0287 重号）。

## 增量轮次（每轮：读链 → 单一可验收增量 → 实现 → 真实验证 → 更新 LEDGER → commit）

| 轮 | WP | 可独立验收增量 | 主要产物 |
|---|---|---|---|
| 1 | WP01 | 基线/排重/接口账本 + 既有 preparation 小旅程真实结果 | 本目录 8 个文件初版（已完成基线测试 24/9） |
| 2 | WP02 | 闭合 IR、解析器、函数集版本、依赖字段、复杂度、稳定指纹；未知节点/超预算/保留字段/循环依赖 fail-closed | `contracts.py`, `functions.py`, `parser.py`, `tests/data/test_field_compute_ir_v1.py` |
| 3 | WP03 | 数值/比较/条件/null/error 解释；分支惰性；错误策略；批大小不变性 | `interpreter.py`, `tests/data/test_field_compute_interpreter_v1.py` |
| 4 | WP04/WP05 | 文本/代码/日期时区；单位投影换算；CRS 门控面积/长度 | `units.py`, `geometry.py`, 扩展测试 |
| 5 | WP06/WP07 | InputAnchor/冻结快照/scope 可靠性/预览摘要/generation 失效 | `scope.py`, `snapshot.py`, `receipt.py`, 服务层 preview |
| 6 | WP08 | 有界批执行 + CPU offload + 取消 + 原子可见登记 + 幂等重试 + 持久化披露 | `service.py` 执行主链 + `tests/data/test_field_compute_service_v1.py` |
| 7 | WP09/WP10 | API 路由、Pi `@tool`（同一服务）、前端独立面板 + i18n + 入图 | `app/api/routes/field_compute.py`, `app/schemas/field_compute_schema.py`, `app/tools/field_compute_tools.py`, `frontend/...` |
| 8 | WP11 | 21 场景回归矩阵 + 合成夹具 + 规模有界基准 + TEST_MATRIX 回填 | 测试集、`TEST_MATRIX.md` |
| 9 | WP12 | 独立 reviewer（未写核心实现）→ 修复 → 复审 → fetch 最新 master 校正 → 定向 gate → push + PR | `REVIEW.md`, `PR_HANDOFF.md`, `PR_BODY.md` |

## 首批函数集（`FUNCTION_SET_VERSION = "g19-fc-1.0"`）

| 族 | 成员 | 输入→输出 | null 行为 | 单位规则 | 预算 |
|---|---|---|---|---|---|
| 算子 | `+ - * / %`, `== != < <= > >=`, `and or not` | number/boolean → number/boolean | 任一 null → null（`and/or` 按三值逻辑） | 加减同维否则编译期拒；乘除生成派生量 | 每行 ≤256 op |
| 数值 | `abs, round, floor, ceil, min, max, sqrt` | number → number | null→null；sqrt 负数→error | 保持输入 unit；`min/max` 两侧须同维 | 节点 ≤64、深度 ≤12 |
| 条件 | `if(cond,then,else)`, `coalesce(a,b)`, `is_null(x)` | boolean/任意 → 分支类型 | 未选分支不求值（不制造除零） | 两分支 unit 不一致 → 编译期拒 | 分支 ≤8 |
| 文本 | `trim, upper, lower, concat, substring, str_length` | string → string/number | null→null（concat 的 null 视作空串需显式 `coalesce`） | 无单位；结果 ≤512 字符 | 输入串 ≤2048 字符 |
| 代码 | `pad_left(s,width,ch)`, `to_string(x)`, `to_number(s)`, `to_integer(s)` | string/number | 空串 `to_number` → error（`EMPTY_STRING_NOT_ZERO`）；`00123` 保前导零 | `to_string` 丢失 unit 并披露 | bigint >2^53 → 文本化 + 披露 |
| 日期 | `parse_datetime(s, format, tz)`, `datetime_part(dt, part, tz)`, `date_diff_days(a,b)` | string → datetime/number | 含糊格式/无 tz → 编译期拒；无效日期 → 行级 error | tz 必须 IANA 或固定 UTC 偏移 | 不读系统时钟 |
| 单位 | `convert_unit(x, from, to)` | number → number | null→null | 同 dimension 且 `scale_to_reference` 已知；percent↔fraction ×100 | closed 单位词表 |
| 几何 | `area_km2()`, `area_m2()`, `length_km()`, `length_m()` | 行几何 → number | 无/非法几何 → error | 固定输出 km²/m²/km/m；参与版本锚 | `classify_crs` 门控 |

非目标：UDF、正则、窗口函数、SQL 方言、任意脚本、join 引擎、自动刷新、批参数实验、多画幅、标签编辑、全局 UI 重设计。

## 资源与验证纪律

- 每轮只跑相关测试；重型阶段单进程、`OMP/OPENBLAS/MKL=1`；不改 `HOME`/系统位置；不并行 3 万级全量 pytest。
- 既不等待也不禁用线上 CI；`git diff --check`、ruff（改动文件）、pytest 定向、前端 typecheck（若前端依赖可用）为本地 gate。
- 每轮 commit 一次，阶段性有意义；推送用正常提交，不强推；不自动 merge。
- 独立 reviewer 在功能验收通过后启动（释放实现槽位，同时存活 subagent ≤3）。

## 终止条件

专属完成条件（WP01–WP12 全交付 + 21 场景真实通过）与公共终止条件（独立 review 无阻塞、已推送并创建 PR）同时满足；外部权限/额度/服务阻塞时保存可恢复检查点并如实披露。
