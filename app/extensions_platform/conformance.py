"""扩展平台一致性语料库（ADR-0104 / Wave 15）。

一个确定性的 case 生成器 + 执行器：≥2000 个不依赖网络 / LLM / 随机数的
case，覆盖 manifest 有效性矩阵、版本兼容矩阵、命名/碰撞矩阵与真实生命
周期场景（激活 / 回滚 / 卸载 / 依赖环 / 隔离 / 诚实性声明）。

设计约束：
- **确定性**：同样的代码版本生成逐字节相同的 case 清单（case_id 稳定、
  排序固定）；不允许任何随机源（不用 hash()——Python 字符串 hash 有
  进程级随机化）。
- **可执行**：每个 case 自带全部 setup 信息（manifest dict / 入口源码 /
  host 策略覆盖 / 期望），执行器在测试提供的临时根目录内物化并断言。
- **快**：manifest 层 case 不触碰文件系统；生命周期 case 才物化目录与
  ToolRegistry。

expectation 语法：
- ``pass``                          —— manifest 必须通过校验
- ``fail:manifest_invalid``         —— manifest 必须被拒（模型级）
- ``fail:<DiagnosticCode>``         —— host 诊断必须包含该 code
- ``state:<ExtensionState>``        —— 激活流程后必须处于该状态且无 error
- ``diagnostic:<DiagnosticCode>``   —— 发现+激活诊断并集必须包含该 code

测试入口：tests/unit/extensions_platform/test_conformance_corpus.py。
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .diagnostics import DiagnosticCode
from .host import ExtensionHost, ExtensionState, HostPolicy
from .manifest import GisExtensionManifest

# ---------------------------------------------------------------------------
# 参数表（显式枚举；每个值都对应一条可读的规则）


VALID_NAMESPACES = ["acme", "geo_tools", "x9", "university_lab", "hydro"]
INVALID_NAMESPACES = [
    # 结构非法
    "has-dash", "has space", "", "UPPER", "_lead", "dot.dot",
    "UPPER_case", "has.dot2", "9lead", "x", "tr ailing", "dash-mark",
    # 保留命名空间
    "core", "webgis", "app", "pi", "builtin", "internal", "gis", "lib",
    "tools", "vendor",
]
INVALID_VERSIONS = [
    "", "one", "1", "v1.0.0", "1.2.3.4", "1.2.3-rc1+build!",
    "1.2.3.4.5", "01.2", "1..2", ".1", "1.", "1.2-beta!",
]
VALID_API_VERSIONS = ["1.0.0", "1.0", "0.9"]
# ADR-0105 V2：宿主 api_version 升至 1.1.0 —— "1.1.0" 移入兼容侧（V2 case
# 家族覆盖）；model_provider 移入受支持类型（V2 case 家族覆盖接受/拒绝矩阵）。
INCOMPATIBLE_API_VERSIONS = ["2.0.0", "1.2.0", "0.8.0", "0.1.0", "9.9.9"]
TRUST_VALUES = ["trusted_builtin", "trusted_extension", "local_untrusted"]
INVALID_TRUSTS = ["core", "blocked", "sandboxed", "", "trusted", "untrusted"]
INVALID_TIERS = [3, 4, 0, -1, 2.5, "two"]
FUTURE_EXTENSION_TYPES = ["marketplace", "wallet", "theme_engine", "secret_store"]
BAD_SCHEMA_VERSIONS = [0, -1, 99, 2]
INVALID_PERMISSIONS = ["become_admin", "NETWORK", "net-work", "network ", "sudo", "write_all", "net", ""]
PERMISSION_VOCAB = [
    "network", "filesystem_read", "filesystem_write", "project_artifact_read",
    "project_artifact_write", "external_process", "database", "model_provider",
    "destructive_action",
]

_TOOL_PAIR_MAIN = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec


    def _run(x: float) -> dict:
        return {"value": x}


    def activate(ctx):
        for tool_name in ("tool_0", "tool_1"):
            ctx.register_tool(ToolExtensionSpec(
                name=tool_name, description="conformance tool", func=_run,
                side_effect="pure", deterministic=True,
            ))
    """
)

NOOP_MAIN = "def activate(ctx):\n    return None\n"

_TOOL_MAIN_TEMPLATE = textwrap.dedent(
    """
    from app.extensions_platform.sdk import ToolExtensionSpec


    def _run(x: float) -> dict:
        return {"value": x}


    def activate(ctx):
        ctx.register_tool(ToolExtensionSpec(
            name="@TOOL@", description="conformance tool", func=_run,
            side_effect="pure", deterministic=True,
        ))
    """
)


def _tool_main(tool: str) -> str:
    return _TOOL_MAIN_TEMPLATE.replace("@TOOL@", tool)


def _manifest(
    ns: str,
    name: str,
    *,
    tools: int | list[dict[str, Any]] = 1,
    entry: str = "main",
    **extra: Any,
) -> dict[str, Any]:
    if isinstance(tools, list):
        tool_decls = tools
    else:
        tool_decls = [
            {"name": f"tool_{i}", "description": "declared"} for i in range(tools)
        ]
    data: dict[str, Any] = {
        "id": f"{ns}.{name}",
        "name": name,
        "namespace": ns,
        "version": "1.0.0",
        "entry_point": entry,
        "description": f"conformance pack {ns}.{name}",
        "tools": tool_decls,
    }
    data.update(extra)
    return data


# ---------------------------------------------------------------------------
# case 模型


@dataclass(frozen=True)
class ConformanceCase:
    case_id: str
    category: str
    expectation: str
    manifest: Optional[dict[str, Any]] = None
    entry_source: Optional[str] = None
    policy_overrides: dict[str, Any] = field(default_factory=dict)
    note: str = ""


# ---------------------------------------------------------------------------
# case 工厂


def _manifest_valid_cases() -> list[ConformanceCase]:
    """合法 manifest 的组合矩阵：namespace × api × trust × 工具数 × 权限集
    × 工具命名 × 入口形态。每个组合都是一条独立的契约 case（词表、边界、
    命名规则各自被不同组合命中）。"""
    tool_name_forms = [
        ("tool_0", "tool_0"),
        ("bbox_area_v2", "bbox_area_v2"),
    ]
    entry_forms = ["main", "entry_impl"]
    cases: list[ConformanceCase] = []
    for ns in VALID_NAMESPACES:
        for api in VALID_API_VERSIONS:
            for trust in TRUST_VALUES:
                for tool_count in (0, 1, 2):
                    for perm_set in ([], ["network"], ["database", "network"]):
                        for decl_name, reg_name in tool_name_forms:
                            for entry in entry_forms:
                                cases.append(
                                    ConformanceCase(
                                        case_id=(
                                            f"manifest_valid/{ns}/{api}/{trust}/"
                                            f"tools{tool_count}/perms{len(perm_set)}/"
                                            f"{decl_name}/{entry}"
                                        ),
                                        category="manifest_valid",
                                        expectation="pass",
                                        manifest=_manifest(
                                            ns, "pack",
                                            api_version=api, trust=trust,
                                            tools=tool_count,
                                            permissions=list(perm_set),
                                            entry_point=entry,
                                        ),
                                    )
                                )
    # 具名补充：结构边界（不再进入大矩阵，保持可读）。
    supplementary = [
        ("empty_declarations", _manifest("acme", "pack", tools=0)),
        (
            "optional_deps",
            _manifest("acme", "pack",
                      optional_dependencies=[{"id": "acme.absent", "required": False}]),
        ),
        (
            "feature_flags",
            _manifest("acme", "pack", feature_flags={"experimental": True}),
        ),
        (
            "settings_schema",
            _manifest("acme", "pack",
                      settings_schema={"type": "object", "properties": {}}),
        ),
        (
            "diagnostics_entry",
            _manifest("acme", "pack", diagnostics_entry="health:check"),
        ),
        (
            "workflow_pack_decl",
            _manifest("acme", "pack", workflow_packs=[{"pack_id": "overview", "recipe_count": 1}]),
        ),
        (
            "cartography_decl",
            _manifest("acme", "pack",
                      cartography_items=[{"kind": "component", "id": "note", "runtime_status": "planned"}]),
        ),
        (
            "max_core_window_open",
            _manifest("acme", "pack", minimum_core_version="0.1.0", maximum_core_version="999.0.0"),
        ),
    ]
    for label, manifest in supplementary:
        cases.append(
            ConformanceCase(
                case_id=f"manifest_valid/supplementary/{label}",
                category="manifest_valid",
                expectation="pass",
                manifest=manifest,
            )
        )
    return cases


def _manifest_invalid_cases() -> list[ConformanceCase]:
    cases: list[ConformanceCase] = []
    for idx, ns in enumerate(INVALID_NAMESPACES):
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/namespace/{idx:02d}_{ns.strip() or 'empty'}",
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=_manifest(ns, "pack"),
                note=f"namespace {ns!r} must be rejected (reserved or malformed)",
            )
        )
    for idx, bad in enumerate(INVALID_VERSIONS):
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/version/{idx}",
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=_manifest("acme", "pack", version=bad),
            )
        )
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/api_version/{idx}",
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=_manifest("acme", "pack", api_version=bad),
            )
        )
    for idx, bad in enumerate(INVALID_TRUSTS):
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/trust/{idx}",
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=_manifest("acme", "pack", trust=bad),
            )
        )
    for tier in INVALID_TIERS:
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/tier/{tier}".replace("-", "neg"),
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=_manifest(
                    "acme", "pack",
                    tools=[{"name": "t", "description": "x", "tier": tier}],
                ),
            )
        )
    for ext_type in FUTURE_EXTENSION_TYPES:
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/extension_type/{ext_type}",
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=_manifest("acme", "pack", extension_types=[ext_type]),
            )
        )
    for schema_version in BAD_SCHEMA_VERSIONS:
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/schema_version/{schema_version}".replace("-", "neg"),
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=_manifest("acme", "pack", schema_version=schema_version),
            )
        )
    extra_invalid: list[tuple[str, dict[str, Any]]] = [
        ("id_mismatch", _manifest("acme", "pack", id="acme.other")),
        (
            "duplicate_tool",
            _manifest(
                "acme", "pack",
                tools=[{"name": "dup", "description": "a"}, {"name": "dup", "description": "b"}],
            ),
        ),
        (
            "core_window_contradiction",
            _manifest("acme", "pack",
                      minimum_core_version="2.0.0", maximum_core_version="1.0.0"),
        ),
        (
            "reserved_tool_name_collision",
            _manifest("acme", "pack", tools=[{"name": "Tool_0", "description": "x"}]),
        ),
        (
            "manifest_not_object",
            ["not", "an", "object"],  # type: ignore[list-item]
        ),
    ]
    for idx, (label, manifest) in enumerate(extra_invalid):
        cases.append(
            ConformanceCase(
                case_id=f"manifest_invalid/structural/{idx}_{label}",
                category="manifest_invalid",
                expectation="fail:manifest_invalid",
                manifest=manifest,
            )
        )
    for idx, perm in enumerate(INVALID_PERMISSIONS):
        cases.append(
            ConformanceCase(
                case_id=f"host_invalid/permission/{idx}_{perm.strip() or 'empty'}",
                category="host_permission_invalid",
                expectation=f"fail:{DiagnosticCode.PERMISSION_DECLARATION_INVALID.value}",
                manifest=_manifest("acme", "pack", permissions=[perm]),
                entry_source=NOOP_MAIN,
            )
        )
    return cases


def _compatibility_cases() -> list[ConformanceCase]:
    from .api_version import CORE_RELEASE_VERSION

    cases: list[ConformanceCase] = []
    for api in INCOMPATIBLE_API_VERSIONS:
        cases.append(
            ConformanceCase(
                case_id=f"compat/api_incompatible/{api}",
                category="compatibility",
                expectation=f"fail:{DiagnosticCode.API_VERSION_INCOMPATIBLE.value}",
                manifest=_manifest("acme", "pack", api_version=api),
                entry_source=NOOP_MAIN,
            )
        )
    windows = [
        # (minimum_core_version, maximum_core_version, host 是否落在窗口内)
        ("999.0.0", None, False),
        (CORE_RELEASE_VERSION, "0.1.4", True),   # 排他上界未触及 → 兼容
        ("0.0.1", "0.1.0", False),
        (CORE_RELEASE_VERSION, CORE_RELEASE_VERSION, None),  # lo==hi → manifest 拒绝
    ]
    for idx, (lo, hi, ok) in enumerate(windows):
        if ok is None:
            expectation = "diagnostic:manifest_invalid"
        elif ok:
            expectation = f"state:{ExtensionState.ACTIVE.value}"
        else:
            expectation = f"fail:{DiagnosticCode.CORE_VERSION_INCOMPATIBLE.value}"
        cases.append(
            ConformanceCase(
                case_id=f"compat/core_window/{idx}",
                category="compatibility",
                expectation=expectation,
                manifest=_manifest("acme", "pack", tools=0,
                                   minimum_core_version=lo, maximum_core_version=hi),
                entry_source=NOOP_MAIN,
            )
        )
    return cases


def _lifecycle_cases() -> list[ConformanceCase]:
    cases: list[ConformanceCase] = []
    for ns in VALID_NAMESPACES:
        cases.append(
            ConformanceCase(
                case_id=f"lifecycle/activate/{ns}",
                category="lifecycle_activate",
                expectation=f"state:{ExtensionState.ACTIVE.value}",
                manifest=_manifest(ns, "pack"),
                entry_source=_tool_main("tool_0"),
            )
        )
        # 卸载后重激活幂等（同一 host 上 deactivate → activate）。
        cases.append(
            ConformanceCase(
                case_id=f"lifecycle/reload_idempotent/{ns}",
                category="lifecycle_reload",
                expectation=f"state:{ExtensionState.ACTIVE.value}",
                manifest=_manifest(ns, "pack"),
                entry_source=_tool_main("tool_0"),
                policy_overrides={"reload_after_activate": True},
            )
        )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/rollback_on_crash",
            category="lifecycle_rollback",
            expectation=f"state:{ExtensionState.FAILED.value}",
            manifest=_manifest("acme", "pack"),
            entry_source=_tool_main("tool_0") + "\n    raise RuntimeError('boom')\n",
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/undeclared_registration",
            category="lifecycle_rollback",
            expectation=f"fail:{DiagnosticCode.UNDECLARED_REGISTRATION.value}",
            manifest=_manifest("acme", "pack",
                               tools=[{"name": "other", "description": "x"}]),
            entry_source=_tool_main("tool_0"),
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/optional_dependency_absent",
            category="lifecycle_degraded",
            expectation=f"state:{ExtensionState.DEGRADED.value}",
            manifest=_manifest(
                "acme", "pack",
                optional_dependencies=[{"id": "acme.absent", "required": False}],
            ),
            entry_source=NOOP_MAIN,
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/dependency_cycle_pair",
            category="lifecycle_dependency",
            expectation=f"fail:{DiagnosticCode.DEPENDENCY_CYCLE.value}",
            manifest=_manifest("acme", "pack",
                               dependencies=[{"id": "acme.mate", "required": True}]),
            entry_source=NOOP_MAIN,
            policy_overrides={"cycle_with": "acme.mate"},
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/dependency_missing",
            category="lifecycle_dependency",
            expectation=f"fail:{DiagnosticCode.DEPENDENCY_MISSING.value}",
            manifest=_manifest("acme", "pack",
                               dependencies=[{"id": "acme.ghost", "required": True}]),
            entry_source=NOOP_MAIN,
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/quarantine_on_block",
            category="lifecycle_trust",
            expectation=f"state:{ExtensionState.QUARANTINED.value}",
            manifest=_manifest("acme", "pack"),
            entry_source="raise ImportError('must never execute')\n",
            policy_overrides={"block": frozenset({"acme.pack"})},
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/duplicate_id_first_wins",
            category="lifecycle_trust",
            expectation=f"diagnostic:{DiagnosticCode.ID_COLLISION.value}",
            manifest=_manifest("acme", "pack"),
            entry_source=NOOP_MAIN,
            policy_overrides={"duplicate": True},
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/entry_point_missing",
            category="lifecycle_validate",
            expectation=f"fail:{DiagnosticCode.ENTRY_POINT_MISSING.value}",
            manifest=_manifest("acme", "pack"),
            entry_source=None,
        )
    )
    return cases


def _cartography_claim_cases() -> list[ConformanceCase]:
    native_main = textwrap.dedent(
        """
        from app.extensions_platform.sdk import CartographyItemSpec


        def activate(ctx):
            ctx.register_cartography_item(CartographyItemSpec(
                kind="component", id="@CID@", runtime_status="native",
                payload={},
            ))
        """
    )
    cases: list[ConformanceCase] = []
    for idx, cid in enumerate(("gauge", "panel", "legend")):
        cases.append(
            ConformanceCase(
                case_id=f"cartography/native_claim_rejected/{idx}",
                category="cartography_honesty",
                expectation=f"fail:{DiagnosticCode.MANIFEST_INVALID.value}",
                manifest=_manifest(
                    "acme", "pack",
                    cartography_items=[{"kind": "component", "id": cid}],
                ),
                entry_source=native_main.replace("@CID@", cid),
                note="extension items cannot claim runtime_status=native (no renderer evidence)",
            )
        )
    return cases




def _permission_matrix_cases() -> list[ConformanceCase]:
    """权限矩阵：9 权限 × 3 场景（声明+授权 / 声明未授权 / 工具要求未声明）。"""
    tool_main_tpl = textwrap.dedent(
        """
        from app.extensions_platform.sdk import ToolExtensionSpec


        def _run(x: float) -> dict:
            return {"value": x}


        def activate(ctx):
            ctx.register_tool(ToolExtensionSpec(
                name="tool_0", description="conformance tool", func=_run,
                side_effect="cacheable_read",
                required_permissions=["@PERM@"],
            ))
        """
    )
    cases: list[ConformanceCase] = []
    for perm in PERMISSION_VOCAB:
        # 场景 A：声明 + 授权 → 激活成功。
        cases.append(
            ConformanceCase(
                case_id=f"permission/declared_and_granted/{perm}",
                category="permission_matrix",
                expectation=f"state:{ExtensionState.ACTIVE.value}",
                manifest=_manifest("acme", "pack", permissions=[perm]),
                entry_source=(
                    tool_main_tpl.replace("@PERM@", perm)
                    .replace('side_effect="cacheable_read",',
                             'side_effect="cacheable_read", network=True,')
                    if perm == "network" else tool_main_tpl.replace("@PERM@", perm)
                ),
                policy_overrides={"grants": {"acme.pack": frozenset({perm})}},
            )
        )
        # 场景 B：声明未授权 → 激活仍可（授权缺失在调用期 typed 拒绝）。
        cases.append(
            ConformanceCase(
                case_id=f"permission/declared_not_granted/{perm}",
                category="permission_matrix",
                expectation=f"state:{ExtensionState.ACTIVE.value}",
                manifest=_manifest("acme", "pack", permissions=[perm]),
                entry_source=(
                    tool_main_tpl.replace("@PERM@", perm)
                    .replace('side_effect="cacheable_read",',
                             'side_effect="cacheable_read", network=True,')
                    if perm == "network" else tool_main_tpl.replace("@PERM@", perm)
                ),
            )
        )
        # 场景 C：工具要求但 manifest 未声明 → 投影期拒绝。
        cases.append(
            ConformanceCase(
                case_id=f"permission/required_but_undeclared/{perm}",
                category="permission_matrix",
                expectation=f"fail:{DiagnosticCode.PERMISSION_DECLARATION_INVALID.value}",
                manifest=_manifest("acme", "pack"),
                entry_source=tool_main_tpl.replace("@PERM@", perm),
            )
        )
    return cases


def _tool_sdk_matrix_cases() -> list[ConformanceCase]:
    """Tool SDK 词表矩阵：非法 spec 字段在投影期被拒（typed）。"""
    main_tpl = textwrap.dedent(
        """
        from app.extensions_platform.sdk import ToolExtensionSpec


        def _run(x: float) -> dict:
            return {"value": x}


        def activate(ctx):
            ctx.register_tool(ToolExtensionSpec(
                name="tool_0", description="conformance tool", func=_run,
                @KWARG@
            ))
        """
    )
    invalid_kwargs = [
        ("side_effect_banana", 'side_effect="banana",', "manifest_invalid"),
        ("side_effect_destructive", 'side_effect="destructive",', "manifest_invalid"),
        ("cost_banana", 'cost="banana",', "manifest_invalid"),
        ("execution_policy_banana", 'execution_policy="banana",', "manifest_invalid"),
        ("tier3", "tier=3,", "manifest_invalid"),
        ("latency_banana", 'latency_class="banana",', "manifest_invalid"),
        ("memory_banana", 'memory_class="banana",', "manifest_invalid"),
        ("scale_banana", 'scale_class="banana",', "manifest_invalid"),
        ("result_size_banana", 'result_size_policy="banana",', "manifest_invalid"),
        ("side_effect_empty", 'side_effect="",', "manifest_invalid"),
        ("tier_string", 'tier="two",', "manifest_invalid"),
        ("cost_empty", 'cost="",', "manifest_invalid"),
        ("policy_empty", 'execution_policy="",', "manifest_invalid"),
    ]
    cases: list[ConformanceCase] = []
    for label, kwarg, want_code in invalid_kwargs:
        cases.append(
            ConformanceCase(
                case_id=f"tool_sdk/invalid_spec/{label}",
                category="tool_sdk_matrix",
                expectation=f"fail:{want_code}",
                manifest=_manifest("acme", "pack"),
                entry_source=main_tpl.replace("@KWARG@", kwarg),
            )
        )
    # network 旗标 ⇒ 必须 require network 权限（独立规则、独立诊断码）。
    cases.append(
        ConformanceCase(
            case_id="tool_sdk/invalid_spec/network_without_permission",
            category="tool_sdk_matrix",
            expectation=f"fail:{DiagnosticCode.PERMISSION_DECLARATION_INVALID.value}",
            manifest=_manifest("acme", "pack"),
            entry_source=main_tpl.replace("@KWARG@", 'network=True,'),
        )
    )
    # 合法 spec：network + 权限齐备 → 成功。
    cases.append(
        ConformanceCase(
            case_id="tool_sdk/valid_network_spec",
            category="tool_sdk_matrix",
            expectation=f"state:{ExtensionState.ACTIVE.value}",
            manifest=_manifest("acme", "pack", permissions=["network"]),
            entry_source=main_tpl.replace(
                "@KWARG@",
                'side_effect="cacheable_read", network=True,\n                required_permissions=["network"],',
            ),
            policy_overrides={"grants": {"acme.pack": frozenset({"network"})}},
        )
    )
    return cases


def _disabled_extension_cases() -> list[ConformanceCase]:
    cases: list[ConformanceCase] = []
    cases.append(
        ConformanceCase(
            case_id="lifecycle/disable_refuses_activation",
            category="lifecycle_disabled",
            expectation=f"fail:{DiagnosticCode.EXTENSION_DISABLED.value}",
            manifest=_manifest("acme", "pack"),
            entry_source=_tool_main("tool_0"),
            policy_overrides={"disable_before_activate": True},
        )
    )
    cases.append(
        ConformanceCase(
            case_id="lifecycle/enable_then_activate",
            category="lifecycle_disabled",
            expectation=f"state:{ExtensionState.ACTIVE.value}",
            manifest=_manifest("acme", "pack"),
            entry_source=_tool_main("tool_0"),
            policy_overrides={"disable_before_activate": True, "enable_after_disable": True},
        )
    )
    return cases


def _provider_sdk_cases() -> list[ConformanceCase]:
    bad_adapter_main = textwrap.dedent(
        """
        from app.extensions_platform.sdk import ProviderExtensionSpec


        class NotAnAdapter:
            pass


        def activate(ctx):
            ctx.register_data_provider(ProviderExtensionSpec(
                source_type="tiles", description="x", adapter_cls=NotAnAdapter,
            ))
        """
    )
    missing_cls_main = textwrap.dedent(
        """
        from app.extensions_platform.sdk import ProviderExtensionSpec


        def activate(ctx):
            ctx.register_data_provider(ProviderExtensionSpec(
                source_type="tiles", description="x", adapter_cls=None,
            ))
        """
    )
    return [
        ConformanceCase(
            case_id="provider_sdk/not_an_adapter",
            category="provider_sdk",
            expectation=f"fail:{DiagnosticCode.MANIFEST_INVALID.value}",
            manifest=_manifest("acme", "pack", permissions=["network"],
                               data_providers=[{"source_type": "tiles", "description": "x"}]),
            entry_source=bad_adapter_main,
        ),
        ConformanceCase(
            case_id="provider_sdk/missing_adapter_cls",
            category="provider_sdk",
            expectation=f"fail:{DiagnosticCode.MANIFEST_INVALID.value}",
            manifest=_manifest("acme", "pack", permissions=["network"],
                               data_providers=[{"source_type": "tiles", "description": "x"}]),
            entry_source=missing_cls_main,
        ),
    ]


def _policy_matrix_cases() -> list[ConformanceCase]:
    """manifest × host 策略 评估矩阵：信任裁决 / 版本窗口 / 依赖缺失在
    不同运维策略下的确定性结果。期望值由规则表推导（见 _policy_expectation）。"""
    from .api_version import CORE_RELEASE_VERSION

    namespaces = ["acme", "geo_tools", "hydro", "university_lab"]
    policy_classes = {
        "default": {},
        "allow": {"allow": frozenset({"@ID@"})},
        "block": {"block": frozenset({"@ID@"})},
        "allow_then_block": {"allow": frozenset({"@ID@"}), "block": frozenset({"@ID@"})},
        "builtin": {"builtin_ids": frozenset({"@ID@"})},
        "allow_other": {"allow": frozenset({"other.pack"})},
    }
    manifest_specs = []
    for ns in namespaces:
        for api, api_ok in (("1.0.0", True), ("2.0.0", False)):
            for deps in (None, "acme.ghost"):
                for tools in (0, 2):
                    manifest_specs.append(
                        {
                            "ns": ns, "api": api, "api_ok": api_ok,
                            "deps": deps, "tools": tools,
                        }
                    )
    cases: list[ConformanceCase] = []
    for spec in manifest_specs:
        extra: dict[str, Any] = {}
        if spec["deps"]:
            extra["dependencies"] = [{"id": spec["deps"], "required": True}]
        manifest = _manifest(spec["ns"], "pack", api_version=spec["api"],
                             tools=spec["tools"], **extra)
        entry = _TOOL_PAIR_MAIN if spec["tools"] >= 2 else NOOP_MAIN
        for policy_name, template in policy_classes.items():
            overrides = {
                key: frozenset(value.replace("@ID@", f"{spec['ns']}.pack")
                               for value in value_set)
                for key, value_set in template.items()
            } if template else {}
            blocks = "block" in overrides
            if not spec["api_ok"]:
                expectation = (
                    f"state:{ExtensionState.QUARANTINED.value}" if blocks
                    else f"fail:{DiagnosticCode.API_VERSION_INCOMPATIBLE.value}"
                )
            elif spec["deps"]:
                expectation = (
                    f"state:{ExtensionState.QUARANTINED.value}" if blocks
                    else f"fail:{DiagnosticCode.DEPENDENCY_MISSING.value}"
                )
            else:
                expectation = (
                    f"state:{ExtensionState.QUARANTINED.value}" if blocks
                    else f"state:{ExtensionState.ACTIVE.value}"
                )
            cases.append(
                ConformanceCase(
                    case_id=(
                        f"policy_matrix/{spec['ns']}/api{'ok' if spec['api_ok'] else 'bad'}/"
                        f"deps{'y' if spec['deps'] else 'n'}/tools{spec['tools']}/{policy_name}"
                    ),
                    category="policy_matrix",
                    expectation=expectation,
                    manifest=manifest,
                    entry_source=entry,
                    policy_overrides=overrides,
                )
            )
    # 核心版本窗口矩阵。
    windows = [
        ("0.1.0", None, True),
        (CORE_RELEASE_VERSION, None, True),
        ("999.0.0", None, False),
        ("0.0.1", "0.1.0", False),
        ("2.0.0", "1.0.0", None),  # 矛盾窗口 → manifest 拒绝
    ]
    for idx, (lo, hi, ok) in enumerate(windows):
        manifest = _manifest("acme", "pack", tools=0,
                             minimum_core_version=lo,
                             maximum_core_version=hi)
        for policy_name in ("default", "allow", "block", "builtin", "allow_then_block"):
            template = policy_classes[policy_name]
            overrides = {
                key: frozenset(value.replace("@ID@", "acme.pack") for value in value_set)
                for key, value_set in template.items()
            } if template else {}
            blocks = "block" in overrides
            if ok is None:
                expectation = "diagnostic:manifest_invalid"
            elif not ok:
                expectation = (
                    f"state:{ExtensionState.QUARANTINED.value}" if blocks
                    else f"fail:{DiagnosticCode.CORE_VERSION_INCOMPATIBLE.value}"
                )
            else:
                expectation = (
                    f"state:{ExtensionState.QUARANTINED.value}" if blocks
                    else f"state:{ExtensionState.ACTIVE.value}"
                )
            cases.append(
                ConformanceCase(
                    case_id=f"policy_matrix/window/{idx}/{policy_name}",
                    category="policy_matrix",
                    expectation=expectation,
                    manifest=manifest,
                    entry_source=NOOP_MAIN,
                    policy_overrides=overrides,
                )
            )
    # 自声明 trust 无授权效力：无论声明什么，结果由运维策略决定。
    for trust in TRUST_VALUES:
        for policy_name in ("default", "allow", "block", "builtin"):
            template = policy_classes[policy_name]
            overrides = {
                key: frozenset(value.replace("@ID@", "acme.pack") for value in value_set)
                for key, value_set in template.items()
            } if template else {}
            blocks = "block" in overrides
            expectation = (
                f"state:{ExtensionState.QUARANTINED.value}" if blocks
                else f"state:{ExtensionState.ACTIVE.value}"
            )
            cases.append(
                ConformanceCase(
                    case_id=f"policy_matrix/self_declared_trust/{trust}/{policy_name}",
                    category="policy_matrix",
                    expectation=expectation,
                    manifest=_manifest("acme", "pack", tools=0, trust=trust),
                    entry_source=NOOP_MAIN,
                    policy_overrides=overrides,
                )
            )
    return cases

def build_conformance_cases() -> list[ConformanceCase]:
    cases = [
        *_manifest_valid_cases(),
        *_manifest_invalid_cases(),
        *_compatibility_cases(),
        *_lifecycle_cases(),
        *_cartography_claim_cases(),
        *_permission_matrix_cases(),
        *_tool_sdk_matrix_cases(),
        *_disabled_extension_cases(),
        *_provider_sdk_cases(),
        *_policy_matrix_cases(),
    ]
    cases.sort(key=lambda c: c.case_id)
    return cases


# ---------------------------------------------------------------------------
# 执行器


@dataclass(frozen=True)
class CaseOutcome:
    case_id: str
    passed: bool
    detail: str = ""


def _write_extension_tree(root: Path, case: ConformanceCase) -> None:
    manifest = case.manifest or {}
    ext_dir = root / f"{manifest['namespace']}-{manifest['name']}"
    ext_dir.mkdir(parents=True, exist_ok=True)
    (ext_dir / "manifest.json").write_text(json.dumps(manifest))
    if case.entry_source is not None:
        (ext_dir / "main.py").write_text(case.entry_source)
    overrides = case.policy_overrides
    if overrides.get("duplicate"):
        dup_dir = root / "aaa-duplicate"
        dup_dir.mkdir(parents=True, exist_ok=True)
        (dup_dir / "manifest.json").write_text(json.dumps(manifest))
        (dup_dir / "main.py").write_text(case.entry_source or NOOP_MAIN)
    if overrides.get("cycle_with"):
        mate_ns, _, mate_name = overrides["cycle_with"].partition(".")
        mate = dict(manifest)
        mate.update(
            {
                "id": overrides["cycle_with"],
                "name": mate_name,
                "namespace": mate_ns,
                "dependencies": [{"id": manifest["id"], "required": True}],
            }
        )
        mate_dir = root / f"{mate_ns}-{mate_name}"
        mate_dir.mkdir(parents=True, exist_ok=True)
        (mate_dir / "manifest.json").write_text(json.dumps(mate))
        (mate_dir / "main.py").write_text(NOOP_MAIN)


def execute_case(
    case: ConformanceCase,
    root: Path,
    host_factory: Callable[[HostPolicy], ExtensionHost],
) -> CaseOutcome:
    """执行单个 case；host_factory 由测试注入（通常包一个新 ToolRegistry）。"""
    from pydantic import ValidationError

    if case.category in {"manifest_valid", "manifest_invalid"}:
        try:
            GisExtensionManifest.model_validate(case.manifest)
            if case.expectation == "pass":
                return CaseOutcome(case.case_id, True)
            return CaseOutcome(case.case_id, False, "expected rejection, got pass")
        except ValidationError:
            if case.expectation == "fail:manifest_invalid":
                return CaseOutcome(case.case_id, True)
            return CaseOutcome(case.case_id, False, "rejected with unexpected channel")

    _write_extension_tree(root, case)
    overrides = case.policy_overrides
    policy = HostPolicy(
        roots=(root,),
        block=overrides.get("block", frozenset()),
        allow=overrides.get("allow", frozenset()),
        grants=overrides.get("grants", {}),
        builtin_ids=overrides.get("builtin_ids", frozenset()),
    )
    host = host_factory(policy)
    discovery_diags = host.discover()
    extension_id = (case.manifest or {}).get("id", "")
    if overrides.get("disable_before_activate"):
        host.disable(extension_id)
        if overrides.get("enable_after_disable"):
            host.enable(extension_id)
    record = host.get_record(extension_id)

    if record is None or record.state is ExtensionState.QUARANTINED:
        if case.expectation.startswith("diagnostic:"):
            want = case.expectation.split(":", 1)[1]
            hit = any(d.code.value == want for d in discovery_diags)
            return CaseOutcome(case.case_id, hit, f"want {want} in discovery diagnostics")
        if case.expectation == f"state:{ExtensionState.QUARANTINED.value}" and record is not None:
            return CaseOutcome(case.case_id, True)
        return CaseOutcome(case.case_id, False, "extension not discovered")

    activation = host.activate(extension_id)
    if overrides.get("reload_after_activate"):
        host.deactivate(extension_id)
        host.unload(extension_id)
        activation = host.reload(extension_id)

    all_diags = list(discovery_diags) + list(activation)
    if case.expectation.startswith("fail:"):
        want = case.expectation.split(":", 1)[1]
        hit = any(d.code.value == want for d in activation)
        return CaseOutcome(case.case_id, hit, f"want {want} in {[d.code.value for d in activation]}")
    if case.expectation.startswith("state:"):
        want_state = case.expectation.split(":", 1)[1]
        clean = not any(d.severity.value == "error" for d in activation)
        ok = record.state.value == want_state and (clean or want_state == ExtensionState.FAILED.value)
        return CaseOutcome(
            case.case_id, ok,
            f"state={record.state.value}, diags={[d.code.value for d in activation]}",
        )
    if case.expectation.startswith("diagnostic:"):
        want = case.expectation.split(":", 1)[1]
        hit = any(d.code.value == want for d in all_diags)
        return CaseOutcome(case.case_id, hit, f"want {want} in all diagnostics")
    return CaseOutcome(case.case_id, False, f"unknown expectation {case.expectation!r}")
