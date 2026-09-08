"""扩展平台开发者 CLI（ADR-0104 / Wave 13）。

``python -m app.extensions_platform <command>``：只读诊断 + 脚手架。

任务契约（硬性约束）：
- 仅用 argparse，不引入任何 CLI 框架；
- **永不激活扩展**——所有命令只到 discover / validate_extension 为止；
  host 以全新 ``ToolRegistry()`` 实例构造（不需要 live registry），进程
  退出即丢弃，registry 零污染；
- ``app.core.config`` 与 ``app.tools.registry``（重依赖，约 1s 导入）全部
  在函数内 lazy import——``--help`` 不支付设置加载成本；
- 默认输出人读表格文本；``--json`` 时 stdout 为**纯 JSON**（trust 边界
  提示等横幅一律成为 JSON 字段），错误走 stderr，保证机器可解析。

信任边界表述必须诚实（trust.py 契约）：扩展 in-process 加载，是
trusted-code boundary 而非沙箱——CLI 输出不得使用 "sandbox" 宣传语。

命令一览::

    list      发现并列出 id/version/state/trust/declared types/entry + failures
    inspect   单个扩展完整 manifest（JSON pretty）+ 指纹 + 依赖边 + 诊断
    validate  host.validate_extension 兼容性判定（exit 0 / 1）
    doctor    设置摘要 + 每扩展状态 + 常见问题提示（纯只读，永不激活）
    scaffold  生成可立即通过 validate 的起步扩展包（manifest/main/health/test）
    catalog   按命名空间分组的声明目录（默认 markdown，--json 机器可读）
    package   对扩展包做内容签名（写 signature.json；绝不输出密钥材料）
    verify    按受信发布者验签（exit 0 = verified/missing，1 = 其它裁决）
    sbom      打印扩展包的确定性 SBOM（文件清单/imports/依赖/secret 扫描）

main(argv) 返回退出码：0 成功（list/doctor/catalog 的「发现问题」不算
失败），1 校验失败 / 目标不存在 / 设置解析失败，2 用法错误（scaffold
参数非法 / 目标已存在）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from .diagnostics import ExtensionDiagnostic, ExtensionPlatformError

# 诚实边界声明（trust.py：「任何 untrusted-code sandbox 的表述都是虚假
# 宣传」）——list / catalog / doctor 的人读输出与 JSON 字段共用本行。
TRUST_BOUNDARY_NOTICE = (
    "trust boundary: extensions load in-process (trusted-code boundary, "
    "NOT sandboxed); manifest permissions gate SDK channels only"
)

# doctor 常见问题提示（按稳定诊断码索引；诊断只描述事实，恢复建议在这里给）。
_PROBLEM_HINTS: dict[str, str] = {
    "manifest_schema_unsupported": (
        "manifest schema_version is newer than the host supports; "
        "upgrade the host (or downgrade the extension manifest)"
    ),
    "manifest_invalid": (
        "manifest.json is invalid; fix the fields listed in the diagnostic "
        "(schema is extra=forbid — unknown fields are rejected)"
    ),
    "manifest_parse_failed": (
        "check the pack layout: <root>/<ext>/manifest.json must exist, be "
        "valid JSON and stay under the 256 KiB limit"
    ),
    "core_version_incompatible": (
        "core release falls outside the extension's "
        "[minimum_core_version, maximum_core_version) window; adjust the manifest"
    ),
    "api_version_incompatible": (
        "extension api_version major must equal the host api_version; "
        "re-target the extension (see `inspect` for the host value)"
    ),
    "dependency_missing": (
        "required dependency was not discovered; add its pack to the roots "
        "(EXTENSIONS_DIRS / --root)"
    ),
    "dependency_cycle": (
        "required-dependency cycle across manifests; break the cycle"
    ),
    "trust_blocked": (
        "id hits operator policy: EXTENSIONS_BLOCK (blocked) or missing from "
        "EXTENSIONS_ALLOW / EXTENSIONS_BUILTIN_IDS (untrusted by default)"
    ),
    "permission_declaration_invalid": (
        "manifest declares a permission outside the fixed vocabulary "
        "(see app/extensions_platform/permissions.py)"
    ),
    "permission_not_granted": (
        "permission not listed in EXTENSION_PERMISSION_GRANTS for this "
        "extension id; declaration != authorization, grants are operator-managed"
    ),
    "entry_point_missing": (
        "check the file layout: entry_point must resolve to <pack>/<entry>.py "
        "(or <pack>/<entry>/__init__.py) and define activate(ctx)"
    ),
    "id_collision": (
        "the same extension id was discovered under multiple roots; "
        "remove the stale duplicate copy"
    ),
    "discovery_limit_exceeded": (
        "too many extension directories in the roots (hard limit 64); "
        "split into more specific roots"
    ),
}


# ── lazy 设置 / host 构造 ────────────────────────────────────────────────
def _settings_summary() -> dict[str, str]:
    """lazy 读取 EXTENSIONS_* 设置块原文（doctor / 默认 roots 用）。"""
    from app.core.config import settings

    return {
        "EXTENSIONS_ENABLED": str(bool(settings.EXTENSIONS_ENABLED)),
        "EXTENSIONS_DIRS": settings.EXTENSIONS_DIRS,
        "EXTENSIONS_ALLOW": settings.EXTENSIONS_ALLOW,
        "EXTENSIONS_BLOCK": settings.EXTENSIONS_BLOCK,
        "EXTENSIONS_BUILTIN_IDS": settings.EXTENSIONS_BUILTIN_IDS,
        "EXTENSION_PERMISSION_GRANTS": settings.EXTENSION_PERMISSION_GRANTS,
        "EXTENSION_FEATURE_FLAGS": settings.EXTENSION_FEATURE_FLAGS,
        "EXTENSION_SETTINGS_JSON": settings.EXTENSION_SETTINGS_JSON,
    }


def _build_host(root_overrides: list[str]) -> tuple[Any, list[str]]:
    """构造只读 host：全新 ToolRegistry + settings 派生 HostPolicy。

    root_overrides 非空时整体替换 policy.roots（``--root`` 可重复）。绝不
    调用 activate——扩展在 CLI 进程里只被发现与静态校验。设置解析失败时
    抛 ExtensionPlatformError / ValueError（fail closed，绝不半份配置继续）。
    """
    import logging
    import warnings

    from app.tools.registry import ToolRegistry  # 重依赖（约 1s）→ lazy

    from .host import ExtensionHost
    from .settings_bridge import host_policy_from_settings

    # Round-1 审计 C-2：app.core.config 的 import 期副作用（JWT/LLM key
    # 告警与 logger 行）会污染 CLI 输出（--json 消费方尤其受害）。在
    # 导入与策略构建窗口内静音；CLI 自身的诊断照常输出。
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        _quiet = logging.getLogger("app")
        prev_level = _quiet.level
        _quiet.setLevel(logging.ERROR)
        try:
            policy = host_policy_from_settings()
        finally:
            _quiet.setLevel(prev_level)
    roots = (
        [str(p) for p in root_overrides]
        if root_overrides
        else [str(p) for p in policy.roots]
    )
    if root_overrides:
        policy = replace(policy, roots=tuple(Path(p) for p in root_overrides))
    host = ExtensionHost(tool_registry=ToolRegistry(), policy=policy)
    host.discover()
    return host, roots


def _scan_failures(roots: list[str]) -> tuple[list[Any], list[ExtensionDiagnostic]]:
    """发现失败的目录（无/坏 manifest）+ 顶层发现诊断。

    host.discover() 会把失败折叠进返回诊断（丢路径分组），CLI 需要
    failures 的结构化呈现，故对同一批根目录再做一次有界扫描（目录数
    有硬上界，成本可忽略）。
    """
    from .discovery import discover_extensions

    result = discover_extensions([Path(p) for p in roots])
    return list(result.failures), list(result.diagnostics)


# ── 呈现辅助 ─────────────────────────────────────────────────────────────
def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def _format_diagnostic(diag: ExtensionDiagnostic) -> str:
    where = f" [{diag.extension_id}]" if diag.extension_id else ""
    return f"{diag.severity.value.upper():7s} {diag.code.value}{where}: {diag.message}"


def _dedupe_diagnostics(diagnostics: list[ExtensionDiagnostic]) -> list[ExtensionDiagnostic]:
    # host.validate_extension 会把上一轮检查诊断带到下一轮再追加（重复
    # 调用语义如此）；CLI 在 discover 之后再次调用它，呈现前按全字段去重。
    seen: set[str] = set()
    out: list[ExtensionDiagnostic] = []
    for diag in diagnostics:
        key = json.dumps(diag.to_dict(), sort_keys=True, default=str)
        if key not in seen:
            seen.add(key)
            out.append(diag)
    return out


def _format_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = [
        "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)),
        "  ".join("-" * w for w in widths),
    ]
    for row in rows:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)))
    return "\n".join(lines)


def _hint_for(code: str, message: str) -> str:
    # manifest 的 schema_version 超前在解析期归一为 manifest_invalid，
    # 依消息特征补挂「升级宿主」提示（任务点名的常见问题）。
    if "schema_version" in message and "newer" in message:
        return _PROBLEM_HINTS["manifest_schema_unsupported"]
    return _PROBLEM_HINTS.get(code, "")


def _declared_items(manifest: Any) -> dict[str, list[dict[str, Any]]]:
    """manifest 声明节 → 目录条目（投影后的命名空间化 id + 元信息）。"""
    return {
        "tools": [
            {
                "name": manifest.namespaced_tool_name(t.name),
                "description": t.description,
                "tier": t.tier,
                "side_effect": t.side_effect,
            }
            for t in manifest.tools
        ],
        "algorithms": [
            {
                "id": manifest.namespaced_algorithm_id(a.id),
                "scientific_status": a.scientific_status,
            }
            for a in manifest.algorithms
        ],
        "data_providers": [
            {
                "source_type": manifest.namespaced_source_type(p.source_type),
                "supports_query": p.supports_query,
            }
            for p in manifest.data_providers
        ],
        "cartography_items": [
            {"kind": c.kind, "id": c.id, "runtime_status": c.runtime_status}
            for c in manifest.cartography_items
        ],
        "workflow_packs": [
            {
                "pack_id": f"{manifest.namespace}_{w.pack_id}",
                "recipe_count": w.recipe_count,
            }
            for w in manifest.workflow_packs
        ],
    }


# ── list ─────────────────────────────────────────────────────────────────
def _cmd_list(args: argparse.Namespace) -> int:
    host, roots = _build_host(args.root)
    failures, top_diagnostics = _scan_failures(roots)
    report = host.status_report()
    if args.json:
        _print_json(
            {
                "host_api_version": report["host_api_version"],
                "roots": roots,
                "trust_boundary": TRUST_BOUNDARY_NOTICE,
                "extensions": report["extensions"],
                "failures": [
                    {
                        "path": str(f.path),
                        "diagnostics": [d.to_dict() for d in f.diagnostics],
                    }
                    for f in failures
                ],
                "discovery_diagnostics": [d.to_dict() for d in top_diagnostics],
            }
        )
        return 0

    lines = [
        f"extension roots: {', '.join(roots) if roots else '(none configured — set EXTENSIONS_DIRS or pass --root)'}",
        f"host api_version: {report['host_api_version']}",
        f"notice: {TRUST_BOUNDARY_NOTICE}",
        "",
    ]
    extensions = report["extensions"]
    if extensions:
        rows = [
            [
                ext["id"],
                ext["version"],
                ext["state"],
                ext["trust"],
                ",".join(ext["declared_types"]) or "-",
                ext["entry_point"] or "-",
            ]
            for ext in extensions
        ]
        lines.append(
            _format_table(
                ["ID", "VERSION", "STATE", "TRUST", "TYPES", "ENTRY"], rows
            )
        )
    else:
        lines.append("no extensions discovered")
    for diag in top_diagnostics:
        lines.append(f"discovery: {_format_diagnostic(diag)}")
    if failures:
        lines.append("")
        lines.append(f"failures ({len(failures)}):")
        for failure in failures:
            lines.append(f"  {failure.path}")
            for diag in failure.diagnostics:
                lines.append(f"    {_format_diagnostic(diag)}")
    print("\n".join(lines))
    return 0


# ── inspect ──────────────────────────────────────────────────────────────
def _cmd_inspect(args: argparse.Namespace) -> int:
    from .api_version import CORE_API_VERSION

    host, roots = _build_host(args.root)
    record = host.get_record(args.extension_id)
    if record is None:
        print(
            f"error: extension {args.extension_id!r} was not discovered "
            f"(roots: {roots or ['(none)']}); it may have failed discovery — run `list`",
            file=sys.stderr,
        )
        return 1
    payload: dict[str, Any] = {
        "id": record.extension_id,
        "path": str(record.path),
        "state": record.state.value,
        "trust": record.trust.value,
        "fingerprint": record.fingerprint,
        "host_api_version": CORE_API_VERSION,
        "manifest": record.manifest.model_dump(),
        "dependencies": {
            "required": [
                {"id": dep.id, "feature_flag": dep.feature_flag}
                for dep in record.manifest.dependencies
            ],
            "optional": [
                {"id": dep.id, "feature_flag": dep.feature_flag}
                for dep in record.manifest.optional_dependencies
            ],
        },
        "diagnostics": [d.to_dict() for d in record.diagnostics],
    }
    if args.json:
        _print_json(payload)
        return 0

    # 人读模式：完整 manifest 以 JSON pretty 先行（作者核对声明的正体）。
    print(json.dumps(payload["manifest"], indent=2, ensure_ascii=False))
    print()
    print(f"path: {payload['path']}")
    print(f"state: {payload['state']}  trust: {payload['trust']}")
    print(f"fingerprint: {payload['fingerprint']}")
    print("dependencies:")
    edges = payload["dependencies"]
    if not edges["required"] and not edges["optional"]:
        print("  (none)")
    for edge in edges["required"]:
        flag = f" (feature_flag: {edge['feature_flag']})" if edge["feature_flag"] else ""
        print(f"  required: {edge['id']}{flag}")
    for edge in edges["optional"]:
        flag = f" (feature_flag: {edge['feature_flag']})" if edge["feature_flag"] else ""
        print(f"  optional: {edge['id']}{flag}")
    print("diagnostics:")
    if payload["diagnostics"]:
        for diag in payload["diagnostics"]:
            print(
                f"  {diag['severity'].upper():7s} {diag['code']}: {diag['message']}"
            )
    else:
        print("  (none)")
    return 0


# ── validate ─────────────────────────────────────────────────────────────
def _cmd_validate(args: argparse.Namespace) -> int:
    from .host import ExtensionState

    host, roots = _build_host(args.root)
    record = host.get_record(args.extension_id)
    diagnostics = _dedupe_diagnostics(host.validate_extension(args.extension_id))
    # 未知 id（含 manifest 解析失败、未被发现）也产出 typed 诊断 → exit 1。
    compatible = (
        record is not None and record.state is ExtensionState.COMPATIBLE
    )
    if args.json:
        _print_json(
            {
                "extension_id": args.extension_id,
                "compatible": compatible,
                "state": record.state.value if record else None,
                "roots": roots,
                "diagnostics": [d.to_dict() for d in diagnostics],
            }
        )
        return 0 if compatible else 1
    if diagnostics:
        for diag in diagnostics:
            print(_format_diagnostic(diag))
    else:
        print("no diagnostics")
    verdict = (
        f"{args.extension_id}: compatible (state={record.state.value})"
        if compatible
        else f"{args.extension_id}: NOT compatible"
    )
    print(verdict)
    if not compatible and record is None:
        print(
            "hint: the id was not discovered — a broken manifest shows up as "
            "a discovery failure; run `list` for the parse diagnostics",
            file=sys.stderr,
        )
    return 0 if compatible else 1


# ── doctor ───────────────────────────────────────────────────────────────
def _cmd_doctor(args: argparse.Namespace) -> int:
    from .permissions import parse_grants_config

    problems: list[str] = []
    hints: list[str] = []
    settings_raw: dict[str, str] = {}
    grants_parsed: dict[str, list[str]] | None = None
    extensions: list[dict[str, Any]] = []
    roots: list[str] = []
    host_api_version: str | None = None

    # 1) 设置摘要（lazy；settings 加载失败本身即 doctor 的发现之一）。
    try:
        settings_raw = _settings_summary()
    except Exception as exc:  # noqa: BLE001 - doctor 报告环境问题而非崩溃
        problems.append(f"cannot load app.core.config settings: {exc}")

    # 2) 授权表解析（任务点名走 parse_grants_config；fail closed）。
    if settings_raw:
        try:
            grants_parsed = {
                ext_id: sorted(perms)
                for ext_id, perms in parse_grants_config(
                    settings_raw["EXTENSION_PERMISSION_GRANTS"]
                ).items()
            }
        except ExtensionPlatformError as exc:
            problems.append(
                f"EXTENSION_PERMISSION_GRANTS invalid: {exc.diagnostic.message}"
            )
        except KeyError:  # pragma: no cover - settings_raw 非空必然含该键
            problems.append("settings summary is incomplete")

    # 3) 发现 + 静态校验（策略构建失败 → 问题行，绝不激活）。
    if settings_raw:
        try:
            host, roots = _build_host(args.root)
            report = host.status_report()
            extensions = report["extensions"]
            host_api_version = report["host_api_version"]
        except (ExtensionPlatformError, ValueError) as exc:
            problems.append(f"host policy build failed (fail closed): {exc}")

    # 4) 常见问题提示。
    if settings_raw and settings_raw["EXTENSIONS_ENABLED"] == "False":
        hints.append(
            "EXTENSIONS_ENABLED=False: the runtime lifespan will not activate "
            "extensions; this CLI never activates anything either"
        )
    if not roots:
        hints.append(
            "no extension roots configured; set EXTENSIONS_DIRS or pass --root PATH"
        )
    for ext in extensions:
        for diag in ext["diagnostics"]:
            if diag["severity"] == "info":
                continue
            hint = _hint_for(diag["code"], diag["message"])
            hints.append(
                f"[{ext['id']}] {diag['code']}: {hint}" if hint else f"[{ext['id']}] {diag['code']}"
            )

    if args.json:
        _print_json(
            {
                "host_api_version": host_api_version,
                "settings": settings_raw,
                "grants_parsed": grants_parsed,
                "roots": roots,
                "extensions": extensions,
                "problems": problems,
                "hints": hints,
                "trust_boundary": TRUST_BOUNDARY_NOTICE,
                "read_only": True,
            }
        )
        return 0

    lines = ["== doctor (read-only; never activates extensions) =="]
    for key, value in settings_raw.items():
        lines.append(f"{key}: {value}" if value else f"{key}: ''")
    if grants_parsed is not None:
        rendered = (
            ", ".join(f"{eid}: {perms}" for eid, perms in sorted(grants_parsed.items()))
            or "(no grants)"
        )
        lines.append(f"grants parsed: {rendered}")
    lines.append(f"roots used: {', '.join(roots) if roots else '(none)'}")
    if host_api_version:
        lines.append(f"host api_version: {host_api_version}")
    lines.append("")
    lines.append(f"discovered extensions: {len(extensions)}")
    for ext in extensions:
        lines.append(
            f"  {ext['id']} {ext['version']} state={ext['state']} trust={ext['trust']}"
        )
        for diag in ext["diagnostics"]:
            lines.append(
                f"    {diag['severity'].upper():7s} {diag['code']}: {diag['message']}"
            )
    if problems:
        lines.append("")
        lines.append(f"problems ({len(problems)}):")
        lines.extend(f"  - {p}" for p in problems)
    if hints:
        lines.append("")
        lines.append("hints:")
        lines.extend(f"  - {h}" for h in hints)
    print("\n".join(lines))
    return 0


# ── scaffold ─────────────────────────────────────────────────────────────
# 模板用 __TOKEN__ 占位 + str.replace（模板体内有大量 dict 花括号，
# str.format 转义成本高于可读性收益）。
_MAIN_TEMPLATE = '''"""__EXT_ID__ — 脚手架示例扩展（python -m app.extensions_platform scaffold 生成）。

声明与注册一一对应：manifest.json 声明工具 __TOOL_NAME__，activate(ctx)
注册同名 spec（多注册 / 少注册都是 fail-closed 的 error 诊断）。健康检查
入口 check_health 转发同目录 health.py——扩展由宿主按扁平模块加载，扩展
目录不在 sys.path，不能 `import health`（会撞 sys.modules 通用名缓存），
故用 importlib 以 __name__ 前缀的指纹化模块名加载，多扩展间零碰撞。
"""

import importlib.util
from pathlib import Path
from typing import Any

from app.extensions_platform.sdk import ToolExtensionSpec


def _load_health():
    """加载同目录 health.py（模块名带 __name__ 前缀 → 每个扩展实例唯一）。"""
    spec = importlib.util.spec_from_file_location(
        f"{__name__}_health", Path(__file__).with_name("health.py")
    )
    assert spec is not None and spec.loader is not None  # scaffold 自带，必然可加载
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def reverse_text(text: str) -> dict:
    """反转输入文本（纯函数示例：无 IO、无副作用、确定性）。"""
    return {"reversed": text[::-1], "length": len(text)}


def activate(ctx: Any) -> None:
    """激活入口：宿主注入 ExtensionContext；只注册 manifest 声明过的工具。"""
    ctx.register_tool(
        ToolExtensionSpec(
            name="__TOOL_NAME__",
            description="Reverse the input text (scaffold sample tool).",
            func=reverse_text,
            side_effect="pure",
            deterministic=True,
            param_descriptions={"text": "text to reverse"},
        )
    )


def check_health() -> dict:
    """健康检查入口（manifest.diagnostics_entry = "check_health"）。"""
    return _load_health().check_health()
'''

_HEALTH_TEMPLATE = '''"""__EXT_ID__ 健康检查（main.check_health 转发到这里）。

保持纯静态自检：不碰网络 / 文件系统 / 宿主状态。返回
{"status": healthy|degraded|unhealthy, "messages": [...]}——unhealthy 会
在激活后被宿主回滚（HEALTH_UNHEALTHY typed 诊断）。
"""


def check_health() -> dict:
    """脚手架默认恒 healthy；作者应替换为真实自检。"""
    return {"status": "healthy", "messages": []}
'''

_TEST_TEMPLATE = '''"""__EXT_ID__ 脚手架自检（需宿主可导入（在仓库根运行，或 PYTHONPATH=<repo>））。"""

import importlib.util
import json
from pathlib import Path

EXT_DIR = Path(__file__).resolve().parent


def _load_main():
    spec = importlib.util.spec_from_file_location(
        "__MODULE__", EXT_DIR / "main.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_declares_sample_tool():
    manifest = json.loads((EXT_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["id"] == "__EXT_ID__"
    assert manifest["entry_point"] == "main"
    assert manifest["tools"] == [
        {
            "name": "__TOOL_NAME__",
            "description": "Reverse the input text (scaffold sample tool).",
            "tier": 1,
            "side_effect": "pure",
        }
    ]


def test_activate_registers_declared_tool():
    module = _load_main()
    registered = []

    class _Ctx:
        @staticmethod
        def register_tool(spec):
            registered.append(spec)

    module.activate(_Ctx())
    assert [s.name for s in registered] == ["__TOOL_NAME__"]
    assert registered[0].validate() == []
'''


def _scaffold_manifest(extension_id: str, namespace: str, name: str, tool: str) -> dict:
    """起步 manifest：默认即合法（schema v1 / api 1.0.0 / 无权限 / 单工具）。"""
    return {
        "schema_version": 1,
        "id": extension_id,
        "name": name,
        "namespace": namespace,
        "version": "0.1.0",
        "api_version": "1.0.0",
        "title": f"{extension_id} (scaffolded starter)",
        "description": (
            f"Starter extension scaffolded by `python -m app.extensions_platform "
            f"scaffold {namespace} {name}`; declares one pure sample tool."
        ),
        "vendor": "",
        "extension_types": ["tools"],
        "permissions": [],
        "tools": [
            {
                "name": tool,
                "description": "Reverse the input text (scaffold sample tool).",
                "tier": 1,
                "side_effect": "pure",
            }
        ],
        "entry_point": "main",
        "diagnostics_entry": "check_health",
    }


def _cmd_scaffold(args: argparse.Namespace) -> int:
    # 词表与 manifest.py 同源复用（同包内私有正则），保证 CLI 预检与
    # 平台校验不出现两套标准。
    from .sdk.identifier import NAME_RE as _SHORT_NAME_RE
    from .manifest import RESERVED_NAMESPACES, _TOKEN_RE, manifest_from_dict

    namespace, name = args.namespace, args.name
    if not _TOKEN_RE.match(namespace):
        print(
            f"error: namespace {namespace!r} must match {_TOKEN_RE.pattern}",
            file=sys.stderr,
        )
        return 2
    if namespace in RESERVED_NAMESPACES:
        print(f"error: namespace {namespace!r} is reserved", file=sys.stderr)
        return 2
    # Round-2 审计 MINOR-2：名字用与 manifest 相同的短名规则（单字符合法）。
    if not _SHORT_NAME_RE.match(name):
        print(
            f"error: name {name!r} must match {_SHORT_NAME_RE.pattern}", file=sys.stderr
        )
        return 2

    extension_id = f"{namespace}.{name}"
    tool_name = f"{name}_reverse"
    out_dir = Path(args.out_dir)
    pack_dir = out_dir / f"{namespace}-{name}"
    if pack_dir.exists():
        print(f"error: target {pack_dir} already exists (refusing to overwrite)", file=sys.stderr)
        return 2

    manifest = _scaffold_manifest(extension_id, namespace, name, tool_name)
    # fail closed：落盘前先过平台正式解析器，脚手架绝不写出非法包。
    parsed, err = manifest_from_dict(manifest)
    if parsed is None:
        print(f"error: scaffold produced an invalid manifest: {err}", file=sys.stderr)
        return 1

    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (pack_dir / "main.py").write_text(
        _MAIN_TEMPLATE.replace("__EXT_ID__", extension_id).replace(
            "__TOOL_NAME__", tool_name
        ),
        encoding="utf-8",
    )
    (pack_dir / "health.py").write_text(
        _HEALTH_TEMPLATE.replace("__EXT_ID__", extension_id), encoding="utf-8"
    )
    (pack_dir / f"test_{name}.py").write_text(
        _TEST_TEMPLATE.replace("__EXT_ID__", extension_id)
        .replace("__TOOL_NAME__", tool_name)
        .replace("__MODULE__", f"{extension_id}_main_under_test".replace(".", "_")),
        encoding="utf-8",
    )
    print(f"scaffolded extension pack: {pack_dir}")
    for filename in (
        "manifest.json",
        "main.py",
        "health.py",
        f"test_{name}.py",
    ):
        print(f"  {filename}")
    print("next:")
    print(
        f"  python -m app.extensions_platform validate {extension_id} "
        f"--root {out_dir}"
    )
    print(
        f"  python -m app.extensions_platform inspect {extension_id} --root {out_dir}"
    )
    return 0


# ── catalog ──────────────────────────────────────────────────────────────
def _cmd_catalog(args: argparse.Namespace) -> int:
    host, roots = _build_host(args.root)
    report = host.status_report()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ext in report["extensions"]:
        record = host.get_record(ext["id"])
        assert record is not None  # status_report 与 records 同源
        entry = {
            "id": ext["id"],
            "version": ext["version"],
            "state": ext["state"],
            "trust": ext["trust"],
            "description": record.manifest.description,
            "declared": _declared_items(record.manifest),
        }
        grouped.setdefault(record.manifest.namespace, []).append(entry)
    namespaces = [ns for ns in sorted(grouped)]

    if args.json:
        _print_json(
            {
                "host_api_version": report["host_api_version"],
                "roots": roots,
                "trust_boundary": TRUST_BOUNDARY_NOTICE,
                "namespaces": [
                    {"namespace": ns, "extensions": grouped[ns]} for ns in namespaces
                ],
            }
        )
        return 0

    total = sum(len(items) for items in grouped.values())
    lines = [
        "# GIS Extension Catalog",
        "",
        "- generated by: `python -m app.extensions_platform catalog`",
        f"- host api_version: {report['host_api_version']}",
        f"- roots: {', '.join(roots) if roots else '(none)'}",
        f"- extensions: {total}",
        f"- {TRUST_BOUNDARY_NOTICE}",
    ]
    if not namespaces:
        lines += ["", "_no extensions discovered_"]
    for ns in namespaces:
        lines += ["", f"## namespace `{ns}`", ""]
        for entry in grouped[ns]:
            lines.append(
                f"### `{entry['id']}` v{entry['version']} — "
                f"{entry['state']} / {entry['trust']}"
            )
            lines.append("")
            if entry["description"]:
                lines.append(entry["description"])
                lines.append("")
            declared = entry["declared"]
            if not any(declared.values()):
                lines.append("_(no declared items)_")
                continue
            for section, items in declared.items():
                if not items:
                    continue
                title = section.replace("_", " ").title()
                lines.append(f"**{title}**")
                lines.append("")
                if section == "tools":
                    lines += [
                        f"- `{item['name']}` — {item['description']} "
                        f"(tier {item['tier']}, {item['side_effect']})"
                        for item in items
                    ]
                elif section == "algorithms":
                    lines += [
                        f"- `{item['id']}` ({item['scientific_status']})"
                        for item in items
                    ]
                elif section == "data_providers":
                    lines += [
                        f"- `{item['source_type']}` "
                        f"(query: {'yes' if item['supports_query'] else 'no'})"
                        for item in items
                    ]
                elif section == "cartography_items":
                    lines += [
                        f"- {item['kind']} `{item['id']}` ({item['runtime_status']})"
                        for item in items
                    ]
                else:  # workflow_packs
                    lines += [
                        f"- `{item['pack_id']}` ({item['recipe_count']} recipes)"
                        for item in items
                    ]
                lines.append("")
    print("\n".join(lines).rstrip() + "\n")
    return 0


# ── package / verify（Wave 6 签名）───────────────────────────────────────
def _cmd_package(args: argparse.Namespace) -> int:
    from .signing import SIGNATURE_FILENAME, sign_pack

    pack_dir = Path(args.pack_dir)
    summary = sign_pack(pack_dir, args.key_id, Path(args.key_file))
    payload = {
        "pack": str(pack_dir),
        "key_id": summary["key_id"],
        "fingerprint": summary["fingerprint"],
        "signature_file": str(pack_dir / SIGNATURE_FILENAME),
    }
    if args.json:
        _print_json(payload)
        return 0
    print(f"pack: {payload['pack']}")
    print(f"key_id: {payload['key_id']}")
    print(f"fingerprint: {payload['fingerprint']}")
    print(f"signature_file: {payload['signature_file']}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    from .settings_bridge import parse_trusted_publishers
    from .signing import (
        STATUS_MISSING,
        STATUS_SIGNED_VERIFIED,
        verify_pack_signature,
    )

    # --publisher 可重复或逗号分隔；统一拼成原始串走 settings_bridge 的
    # 同一解析器（fail closed：坏条目抛 typed 异常 → main 归一 exit 1）。
    publishers = parse_trusted_publishers(",".join(args.publisher))
    pack_dir = Path(args.pack_dir)
    status = verify_pack_signature(pack_dir, publishers)
    payload = {
        "pack": str(pack_dir),
        "status": status.status,
        "publisher": status.publisher,
        "detail": status.detail,
    }
    if args.json:
        _print_json(payload)
    else:
        print(f"pack: {payload['pack']}")
        print(f"status: {payload['status']}")
        print(f"publisher: {payload['publisher'] or '-'}")
        if status.detail:
            print(f"detail: {status.detail}")
    # 退出码契约：verified / missing 算通过（missing 由宿主策略告警），
    # invalid / tampered / signed_untrusted 算失败。
    return 0 if status.status in (STATUS_SIGNED_VERIFIED, STATUS_MISSING) else 1


# ── 参数解析 ─────────────────────────────────────────────────────────────
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.extensions_platform",
        description=(
            "GIS 扩展平台开发者 CLI：只读诊断 + 脚手架。"
            "任何命令都不会激活扩展（activate 只发生在宿主 lifespan）。"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --root / --json 挂在每个子命令上（避免与主解析器同名 dest 的默认值
    # 相互覆盖这一 argparse 经典坑）。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--root",
        action="append",
        default=[],
        metavar="PATH",
        help="扩展根目录（可重复；缺省读 EXTENSIONS_DIRS）",
    )
    common.add_argument(
        "--json",
        action="store_true",
        help="stdout 输出纯 JSON（机器可读）",
    )

    p_list = sub.add_parser(
        "list", parents=[common], help="列出发现的扩展与发现失败项"
    )
    p_list.set_defaults(handler=_cmd_list)

    p_inspect = sub.add_parser(
        "inspect", parents=[common], help="单个扩展的完整 manifest + 指纹 + 依赖 + 诊断"
    )
    p_inspect.add_argument("extension_id", help="扩展 id（<namespace>.<name>）")
    p_inspect.set_defaults(handler=_cmd_inspect)

    p_validate = sub.add_parser(
        "validate", parents=[common], help="校验扩展兼容性（exit 0 兼容 / 1 不兼容）"
    )
    p_validate.add_argument("extension_id", help="扩展 id（<namespace>.<name>）")
    p_validate.set_defaults(handler=_cmd_validate)

    p_doctor = sub.add_parser(
        "doctor", parents=[common], help="设置摘要 + 每扩展状态 + 常见问题提示（纯只读）"
    )
    p_doctor.set_defaults(handler=_cmd_doctor)

    p_scaffold = sub.add_parser(
        "scaffold", help="生成可通过 validate 的起步扩展包（manifest/main/health/test）"
    )
    p_scaffold.add_argument("namespace", help="命名空间（小写 snake 片段）")
    p_scaffold.add_argument("name", help="扩展短名（namespace 内唯一）")
    p_scaffold.add_argument(
        "--dir",
        dest="out_dir",
        required=True,
        metavar="OUT_DIR",
        help="输出根目录（包写到 OUT_DIR/<namespace>-<name>/）",
    )
    p_scaffold.set_defaults(handler=_cmd_scaffold)

    p_catalog = sub.add_parser(
        "catalog", parents=[common], help="按命名空间分组的声明目录（默认 markdown）"
    )
    p_catalog.set_defaults(handler=_cmd_catalog)

    # package / verify 直接操作包目录，无需发现根（--root 不适用）；
    # --json 单独挂载。
    p_package = sub.add_parser(
        "package", help="对扩展包做内容签名（写 signature.json；不输出密钥材料）"
    )
    p_package.add_argument("pack_dir", help="扩展包目录（含 manifest.json）")
    p_package.add_argument(
        "--key-id", required=True, metavar="ID", help="发布者 key_id（小写标识符）"
    )
    p_package.add_argument(
        "--key-file", required=True, metavar="PATH", help="HMAC 密钥文件（文件内容即密钥字节）"
    )
    p_package.add_argument("--json", action="store_true", help="stdout 输出纯 JSON")
    p_package.set_defaults(handler=_cmd_package)

    p_verify = sub.add_parser(
        "verify", help="按受信发布者验签（exit 0 = verified/missing，1 = 其它裁决）"
    )
    p_verify.add_argument("pack_dir", help="扩展包目录")
    p_verify.add_argument(
        "--publisher",
        action="append",
        default=[],
        metavar="KEY_ID:PATH",
        help="受信发布者 key_id:密钥文件路径（可重复或逗号分隔多个）",
    )
    p_verify.add_argument("--json", action="store_true", help="stdout 输出纯 JSON")
    p_verify.set_defaults(handler=_cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI 入口（测试直接驱动本函数；返回退出码）。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except (ExtensionPlatformError, ValueError) as exc:
        # 设置/授权解析 fail closed：typed 诊断或 ValueError 归一为 CLI 错误。
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
