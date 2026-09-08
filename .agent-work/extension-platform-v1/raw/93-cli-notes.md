# 93 — Developer CLI / diagnostics（Wave 13）实施笔记

日期：2026-09-08 · 分支：feat/gis-extension-platform-v1（worktree extension-platform-v1）

## 交付物（仅三个新文件，未改动任何既有文件）

- `app/extensions_platform/cli.py` — 全部命令逻辑，`main(argv) -> int`
- `app/extensions_platform/__main__.py` — `python -m app.extensions_platform` 薄入口
- `tests/unit/extensions_platform/test_cli.py` — 18 个用例，直接驱动 `main(argv)`

## 命令面

```
list --root PATH(可重复) --json     # id/version/state/trust/types/entry 表 + failures 节 + 信任边界声明
inspect <id> [--json]               # manifest JSON pretty + 指纹 + 依赖边 + 诊断
validate <id> [--json]              # host.validate_extension；compatible → 0，否则 1
doctor [--json]                     # EXTENSIONS_* 设置摘要 + parse_grants_config + 每扩展诊断 + 提示；恒 exit 0
scaffold <ns> <name> --dir OUT      # manifest/main.py/health.py/test_<name>.py；落盘前先过 manifest_from_dict
catalog [--json]                    # markdown（默认）/ JSON，按 namespace 分组 + 声明条目（投影名）
```

## 关键设计决策

1. **永不激活**：host 用全新 `ToolRegistry()` + `host_policy_from_settings()` 构造，所有命令止步于 `discover()` / `validate_extension()`（任务契约；`--root` 非空时用 `dataclasses.replace` 整体替换 policy.roots，allow/block/grants 仍来自设置）。
2. **lazy import**：`app.core.config`、`app.tools.registry`（重，约 1s）、`host/discovery/settings_bridge` 全部函数内导入；`--help` 实测 0.37s（剩余成本来自包 `__init__` 导出 host→pydantic，属已提交的平台设计，不在本次可改范围）。测试用「快照 sys.modules 差集」钉住该契约（不能断言 `not in sys.modules`，全量套件下可能已被加载）。
3. **--json 纯 JSON**：横幅（trust boundary notice）进 JSON 字段而非 stdout 行；错误走 stderr。已验证 `app.core.config` 的启动告警（JWT/LLM key）全走 stderr，不污染 stdout。
4. **validate 去重**：`host.validate_extension` 每次调用会把上轮检查诊断带上再追加（重复调用语义如此），而 `discover()` 已隐式跑过一轮——CLI 二次调用后按诊断全字段去重再呈现；兼容判定以 `record.state is COMPATIBLE` 为准。
5. **failures 结构化**：`host.discover()` 把 DiscoveryFailure 折叠进诊断（丢路径分组），list 需要结构化 failures，故对同批根目录用 `discover_extensions` 再做一次有界扫描（≤64 目录，成本可忽略）。
6. **scaffold 的 health.py 桥**：宿主按扁平模块加载扩展（目录不在 sys.path），`import health` 会撞 sys.modules 通用名缓存；main.py 用 importlib 以 `__name__` 前缀（含指纹化模块名）加载 health.py，`diagnostics_entry: "check_health"` 指回 main.py 转发。已用真实 host 激活闭环验证：activate 零诊断 → active，`host.health()` 返回 healthy。
7. **scaffold fail closed**：namespace/name 复用 `manifest.py` 的 `_TOKEN_RE/_NAME_RE/RESERVED_NAMESPACES`（同包私有名，保证与平台校验同源）；manifest 落盘前先过 `manifest_from_dict`；目标目录存在即拒绝（exit 2，不覆盖）；用法错误 exit 2，校验失败/未知 id/设置解析失败 exit 1，list/doctor/catalog 发现问题不算失败（exit 0）。
8. **argparse 细节**：`--root/--json` 挂在子命令（共享 parent parser），规避主/子解析器同名 dest 默认值互相覆盖的经典坑；`add_subparsers(required=True)`。

## doctor 提示映射（`_PROBLEM_HINTS`，按稳定诊断码）

`entry_point_missing`→查文件布局；`permission_not_granted`→EXTENSION_PERMISSION_GRANTS；`trust_blocked`→ALLOW/BLOCK/BUILTIN_IDS；`dependency_missing`→根目录缺依赖包；schema_version 超前在解析期归一为 `manifest_invalid`，按消息特征（"schema_version"+"newer"）补挂「升级宿主」提示。另有两条环境提示：EXTENSIONS_ENABLED=False、roots 未配置。

## 验证结果

- `python -m pytest tests/unit/extensions_platform/ -q --no-cov` → **2129 passed**（含本 wave 新增 18；任务基线为 59，差额来自并行 wave 在同一 worktree 新落地的 test_sdk_projections / test_conformance_corpus / test_example_pack / test_ogc_stac_hardening）
- 我改动前基线：59 passed；本 wave 的 4 个基线测试文件合跑：77 passed
- `ruff check app/extensions_platform tests/unit/extensions_platform` → 现存 5 个错误全部位于并行 wave 的文件（test_host_lifecycle.py 1 个为改动前既有；test_sdk_projections.py 4 个为并行 agent 新引入）；**本 wave 三个文件 0 错误**
- 真实 CLI 冒烟：scaffold→validate（exit 0）→inspect --json（指纹 64 hex）→list human/--json（failures 节含坏包路径）→catalog markdown→doctor（exit 0，含设置摘要）→scaffold 产物 stub pytest 2 passed→真实 host 激活闭环 healthy

## 已知边界 / 备注

- `--json` 模式下 list/doctor 的诊断仍含平台英文消息（诊断词表即英文，与平台一致）；CLI 注释/docstring 中文、用户可见文本英文，与仓库惯例一致。
- 并行 wave 正在同一 worktree 工作（`app/core/config.py`、`test_host_lifecycle.py` 等有他人改动）；本 wave 未触碰任何既有文件，合并时无冲突面。
- registry 冒烟脚本中的临时目录已清理；未 commit（按任务要求）。
