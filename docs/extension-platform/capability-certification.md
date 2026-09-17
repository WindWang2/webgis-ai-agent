# Pack Capability Certification v2（ADR-0199）

面向扩展作者的分级能力认证指南：六阶段语义、manifest `certification` /
`skills` 声明节、报告持久化与激活 gate 的运维手册。

术语与既有文档对齐：pack = 扩展（`GisExtensionManifest`）；capability =
pack 声明的单个 tool / algorithm / skill。本文不重复
[packaging.md](packaging.md) 与 [security-boundary.md](security-boundary.md)
的既有内容。

## 六阶段管线

`python -m app.extensions_platform certify <ext-id> --staged`（或
`run_pack_certification(host, ext_id)`）按固定顺序执行：

| 阶段 | 证明什么 | 失败示例 |
| --- | --- | --- |
| `supply_chain` | 签名 / SBOM secret / 包布局 / 资源预算 / 协议面 | 签名被吊销；包内有 symlink |
| `schema` | 声明面可认证：tool `side_effect` 已分类（`unclassified` 不可认证）；算法 `scientific_status` 合法；skill 契约通过 `SkillContract` 校验且引用可解析 | 工具没写 side_effect；skill 引用不存在的 capability |
| `implementation` | 真实激活后每个声明条目都在权威 registry 里 | 声明了工具但 `activate()` 没注册（**未实现的 capability 无法认证**） |
| `tests` | pack 自带证据可重放：算法 smoke cases（`run_authoring_checks`）；VALIDATED/PRODUCTION 必须有 `conformance_tests` + `uncertainty_outputs` | smoke 期望值对不上 |
| `runtime_probe` | manifest 探针经**真实注册的**（权限包裹后）可调用对象执行：结果断言 + 确定性重放（两次调用 canonical-JSON 摘要相等）+ latency / result-size 类别核验 | 两次调用结果不同（replay mismatch）；声明 `latency_class=fast` 实测超 10× 预算 |
| `lifecycle` | 干净停用 + 无孤儿投影 | 停用后 `extdemo_rect_area` 仍挂在 ToolRegistry（dangling callable） |

`certified = 无 fail`（warning 如实呈现不阻断）。报告确定性：固定检查顺序、
无时间戳、无原始墙钟读数——同一 pack 连续两次认证产出逐字节相同的报告。

## manifest `certification` 节（api ≥ 1.3.0）

```json
"certification": {
  "tools": {
    "rect_area": {
      "args": {"xmin": 0, "ymin": 0, "xmax": 3, "ymax": 2},
      "replay": true,
      "expect_key": "area",
      "expect_value": 6.0,
      "tolerance": 1e-9
    }
  },
  "algorithms": {
    "rect_area_algo": { "args": {"xmin": 0, "ymin": 0, "xmax": 3, "ymax": 2},
                        "expect_key": "area", "expect_value": 6.0 }
  }
}
```

规则（解析期 fail closed）：

- 键必须是本 manifest **已声明**的能力（tools → `tools[].name`，
  algorithms → `algorithms[].id`）；跨节孤儿引用直接拒绝；
- `args` 必须是严格 JSON（NaN/Infinity 拒绝）且 ≤ 16 KiB；
- `replay` 缺省 true：探针连跑两次，canonical-JSON sha256 必须相等；
- 算法探针经其 descriptor 的第一个 `tool_candidates` 工具执行。

latency / result-size 核验用的是工具 spec 上的声明类别（`latency_class` /
`result_size_policy`，词表 = 核心 descriptor）。判决是**数量级**核验：
`fast`/`medium`/`slow` 预算 1s/10s/120s × 10 倍容差；`inline_small` ≤ 16 KiB、
`bounded`/`ref_offload` ≤ 1 MiB、`unknown` 不断言。报告只记判决不记耗时。

## manifest `skills` 节（api ≥ 1.3.0）

```json
"skills": [
  { "skill_id": "area_skill",
    "contract": { "name": "Area Skill", "domain": "general",
                  "procedure": {"steps": [{"step_id": "s1", "title": "t", "kind": "analyze"}]} } }
]
```

- `contract` 是 gis_harness `SkillContract` 形状的 payload；宿主强制
  `id = <ns>.<skill_id>`、`pack = <ns>` 后做结构校验 + 引用存在性
  （capability/recipe/artifact/ontology 引用必须可解析）；
- ≤ 32 KiB，严格 JSON，`skill_id` 节内唯一；
- **边界（诚实声明）**：pack 技能在本版只到「认证 + catalog 投影」
  （catalog 中 `governance_tier: "candidate"`）。进入 SkillLibrary 运行时
  overlay 需要 SKILL_PACKS 词表与 SkillPolicy trusted 面的治理决定
  （#1327 热面），本 ADR 刻意不接线。

## 报告持久化与信任模型

```bash
python -m app.extensions_platform certify <ext-id> --staged --save            # 未签名
python -m app.extensions_platform certify <ext-id> --staged --save --sign-key <keyfile>
```

- 写入包内 `.certification.json`；该文件**不入包指纹**（与
  `signature.json` 同理），stale 判定 = 重算包指纹与报告绑定指纹比对，
  与报告文件自身的字节/EOL 无关（autocrlf 安全，见 commit `017d1d41`）；
- **evidence 模式**（`EXTENSIONS_CERTIFICATION_TRUST=evidence`，默认）：
  接受未签名报告，但每次接受都产出 warning——不防篡改，只用于本地开发；
- **strict 模式**（`...=strict`）：报告必须带运维认证密钥的 HMAC
  （canonical JSON 域分隔前缀 `webgis-extension-certification-v1`）；
  未签名 / 错钥 / 篡改 / 未配密钥一律 fail closed。

## 激活 gate（运维手册）

```bash
EXTENSIONS_REQUIRE_CERTIFIED=true          # 开 gate（默认 false = 关）
EXTENSIONS_CERTIFICATION_TRUST=strict      # 生产建议
EXTENSIONS_CERTIFICATION_KEY=/etc/webgis/cert.key
```

- gate 在 LOADING **之前**检查：无报告（`certification_required`）、指纹
  漂移（`certification_stale`）、报告无效/验签失败
  （`certification_invalid`）→ 拒绝激活，状态 FAILED（可修复后重试）；
- `EXTENSIONS_BUILTIN_IDS` 名单内的包豁免（随仓库发行、CI 用同一条管线
  认证）；
- 认证管线自身用 `override_gate` 激活——证据生产者不递归消费 gate；
- **kill switch**：`EXTENSIONS_REQUIRE_CERTIFIED=false`（默认），行为与
  master 逐字节一致。

## 升级 / 卸载

`lifecycle` 阶段的孤儿检查即 Oracle：停用后，本包命名空间化的任何条目
（tool / algorithm / provider 投影名）残留在权威 registry 即 fail。
升级 = 换版本目录后重新认证（指纹变化 → 旧报告自动 stale → 重新走管线）。

## 与 conformance corpus 的关系

`conformance.py` 的 2000+ 确定性 case 仍覆盖平台自身回归；V4 增量在
`tests/unit/extensions_platform/test_capability_certification.py`（管线）、
`test_certification_gate.py`（gate/信任模型）、
`test_certification_manifest_v4.py`（声明面 fail-closed 矩阵）、
`test_pack_catalog_v4.py`（catalog）。样例包：
`extensions/examples/extdemo-certified-pack/`。
