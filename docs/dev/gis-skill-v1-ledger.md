# GIS Skill Library V1 — Ledger（执行台账）

- 分支：`harness/gis-skill-procedure-library-v1` @ origin/master `580b33e9`
- 约束执行：独立 worktree `../webgis-wt-gis-skills-v1`；并发 subagents ≤2；
  本地测试（串行/-n 2 上限）；不等线上 CI；不自动 merge。

## 里程碑

| 阶段 | 内容 | 状态 |
|------|------|------|
| M0 | fetch + PR 对账（#1270/#1273/#1274/#1275）+ worktree | ✅ |
| M0 | Phase 0 勘察（2 个 Explore agent 并行）+ recon/decisions 文档 + ADR-0182 | ✅ |
| M1 | S1-S3 契约/IR/语义/桥接（contract/procedure_ir/semantics/situation/bridges） | ✅ |
| M2 | S4-S5 resolver/composition（deterministic-first + 资格硬门槛 + 有界组合） | ✅ |
| M3 | S6-S8 core 技能库 YAML：**37 skills**（矢量/统计/栅格地形/遥感/制图设计/数据准备/时序/网络）+ 4 compositions | ✅ |
| M4 | S15-S19 packs/loader(fail-loud)/validation/catalog(渐进披露)/retrieval(BM25-lite) | ✅ |
| M5 | S20-S22 工具面（gis_skill_search/detail/replay_check，tier-2 只读）+ evidence recorder + registry_validation 接线 | ✅ |
| M6 | S24-S25 benchmark（**162 用例**语料 + 确定性评测器）+ replay | ✅ |
| M7 | 测试全绿（本地，资源受控） | ✅（见下） |
| M8 | 四轴独立 review + P0/P1 修复 | ✅ |
| M9 | commit + PR（不 merge） | ✅（PR 见描述） |

## 决策日志

- ADR 编号取 **0182**（0180 被 #1275 与 #1274 两分支各自占用、0181 被
  capability-graph 分支占用，避开未合并编号冲突）。
- Skill 判定为必要但仅作薄层：空白点=过程 IR/三类语义/作业阶段组合/
  渐进披露目录/重放（详见 decisions.md D0）。
- 技能数据形态=版本化 YAML + pydantic 契约（extra=forbid 基类
  `_base.SkillAssetModel`）+ fail-loud loader。
- core pack 含 37 技能：goal §13/§14 清单全覆盖（7 vector + 4 raster/terrain
  + 4 RS）并补齐本体已有而清单未列的核心作业（自相关/插值/OD/MCDA 选址），
  外加 10 个制图设计 + 5 个数据准备 + 2 个时序 + 2 个网络技能。
- resolver 资格语义：`SelectionFacts.data_roles` 视为会话角色**穷举**
  （situation 投影），必需角色须为子集；unknown ≠ 不满足红线保持。

## 测试与资源记录

- 技能套件：`tests/unit/gis_harness/test_skill_{contract,procedure_ir,resolver,
  library,replay_benchmark,tools}_v1.py` —— **94 passed**（串行，~7s，零 LLM）。
- benchmark：162/162 passed；top1 87.1%、top3 100%、wrong-skill 0；
  分类通过率 clear 132/132、ambiguous 8/8、insufficient 8/8、
  unsupported 10/10、multi 4/4。
- 回归：`validate_gis_library()` 0 issues（含 skill_library 块）；
  `init_tools` 全量 330 工具注册无冲突；tests/unit/gis_harness 全量回归
  结果见 PR 描述。
- 资源控制：全程串行 pytest、无 xdist、--no-cov；benchmark 评测纯确定性
  （零 LLM/零网络，<1s）。

## 独立四轴 Review 结果（2 个评审 agent，只读）

| 轴 | 结论 | 要点 |
|----|------|------|
| Architecture | PASS | 引用不复制（153 capability 全对账）、零 LLM、无第二 loop、replay 只投影、loader 模式一致 |
| Semantic | PASS | 分母声明矛盾/terrain 水文占位/data-prep 本体错配 3×P1（已修，见下） |
| Reliability | FAIL→修复 | 1×P0（replay skip_policy 可绕过）+3×P1（已修）+P2（unknown 几何/CAPABILITY 映射） |
| Performance | PASS | resolve 1.08ms@222 技能、评测 279ms/162 例；P2 双重解析与目录预算（已修） |

### 评审修复清单（P0/P1 全修，P2 择要修）

- **P0 replay**：required+skip_policy=never 步骤被声明跳过 → 判 missing
  （原 skipped_declared 可让 plan_facts 绕过验收门）；锁定测试
  `test_never_step_skip_is_violation`。
- **P1 分母声明**：validate_vocabulary 补反向校验（纯需分母度量集 ×
  flag=false = 违规）；混合度量集由分支级步骤证据承担（ADR §2.3 更新）。
- **P1 terrain 水文**：补 terrain_hydrology(advanced) 能力引用与水文衍生
  步骤，使 watershed/stream_network 本体声明有支撑。
- **P1 data-prep 本体错配**：清除复制粘贴式 ontology_tasks（RS 异常检测等），
  选择回归关键词面。
- **P1 deprecated 依赖**：validation 实装（存活技能的 alternative_skill
  目标与组合成员不得指向弃用技能，fatal），删除死代码。
- **P1 situation.data_roles 词表**：validate_contract 补对账。
- **P1 S14 资产完备性**：37 技能全部补齐三核心触发器（missing_input/
  unsupported_geometry/insufficient_data）fallback 声明；validation fatal。
- **P2**：resolver 几何 "unknown" 过滤；CAPABILITY_MISSING→provider_unavailable
  fallback 映射；replay 工具不再混入进程级 recorder 证据（跨会话互染）；
  catalog 预算 8KB→16KB；registry_validation 复用单例（省一半启动 YAML
  解析）；MAX_COMPOSITION_DEPTH 措辞（V1 扁平 depth=1）。
- **P3**：overlay when_to_use 排版、choropleth when_not_to_use 方向、
  "3857"/"4326" 死模式恢复、task_types 词表校验、SKILL_DOMAINS docstring。

### 评审后状态

- 测试：**103 passed**（94 + 9 个评审修复锁定测试），串行 ~19s。
- benchmark：162/162，top1 87.1% / top3 100% / wrong-skill 0。
- ruff clean；validate_gis_library 0 issues；init_tools 330 工具无冲突。
- 全量回归：tests/unit/gis_harness 1398 passed / 1 failed —— 该失败
  （test_component_lifecycle[trio]）在干净 master 上同样失败（文件内
  顺序敏感的既有问题，与本任务无关，已用 stash 对照验证）。

## 修复记录（实现期发现）

- resolver：capability 硬门槛原先在事实门槛全过时被提前 return 跳过 →
  改为独立检查（`test_capability_hard_gate` 锁定）。
- resolver：重复 id 检测对不可哈希 pydantic 模型用集合差集 → TypeError；
  改为 id 集合检测。
- replay：covered 分支 `detail` 未初始化（UnboundLocalError）→ 修复。
- loader：`compositions.yaml` 顶层 `compositions:` 键未解包 → 修复。
- 语义：`area_share` 属需分母度量（补入 DENOMINATOR_REQUIRED_MEASURES）；
  GEOGRAPHIC_UNITS 补 `line`/`polygon` 展示单元。
