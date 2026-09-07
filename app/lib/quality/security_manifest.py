"""Security Control → Test Manifest（ADR-0104 Wave 15，audit §6 平台配方）。

把安全控制映射到（a）实现锚点（file + 必须存在的代码字符串）和（b）回归测试
节点（pytest node id）。``validate_security_manifest()`` 是门禁：

- 每个 实现锚点 的文件必须存在且包含锚点字符串（守卫被搬走/改名 → 红）；
- 每个测试节点的文件必须存在且定义该节点（测试被删/改名 → 红）；
- 每个控制必须 **要么** 有测试（status="tested"），**要么** 显式声明缺口
  （status="known-gap" + 非空 gap_note）——沉默缺失即红。

认证表由 ``scripts/gen_security_manifest.py`` 派生到
``docs/quality/certifications/SECURITY_CONTROLS.md``；行为红线
``tests/quality/test_security_regression.py`` 锁字节一致与诚实性。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

from app.lib.quality.discovery import repo_root

#: 控制状态：tested = 有真实回归测试；known-gap = 无测试但显式披露（不许伪造）。
STATUS_TESTED = "tested"
STATUS_KNOWN_GAP = "known-gap"

_VALID_STATUSES = (STATUS_TESTED, STATUS_KNOWN_GAP)


@dataclass(frozen=True)
class ImplAnchor:
    """实现锚点：相对 repo 根的文件 + 必须存在于文件内的代码字符串。"""

    file: str
    anchor: str


@dataclass(frozen=True)
class SecurityControl:
    """一条安全控制及其证据映射。"""

    control_id: str
    area: str
    description: str
    impl: Tuple[ImplAnchor, ...] = ()
    test_nodes: Tuple[str, ...] = ()
    status: str = STATUS_TESTED
    gap_note: str = ""


# ── 控制→实现→测试映射（audit 06-security-regression-map.md §2/§4）────────

SECURITY_CONTROLS: Tuple[SecurityControl, ...] = (
    SecurityControl(
        control_id="SEC-03",
        area="tenant isolation（data-fabric sources / catalog）",
        description=(
            "数据源/目录项按 org_id+owner_id 门控；越权统一 404（不泄漏存在性）。"
        ),
        impl=(
            ImplAnchor(
                file="app/api/routes/data_fabric.py",
                anchor="def _require_tenant_owned",
            ),
        ),
        test_nodes=(
            "tests/unit/test_data_fabric_security.py::test_authorize_catalog_item_blocks_cross_tenant",
            "tests/integration/test_cross_tenant_isolation.py::test_cross_tenant_session_detail_404",
        ),
    ),
    SecurityControl(
        control_id="SEC-04",
        area="SSRF egress gate（pre-flight validate_url + per-hop adapter）",
        description=(
            "出站 URL 白名单 scheme / 私网·元数据 IP 拒绝；HTTP(S) 每一跳（含重定向）"
            "重新过门（ADR-0050 §5 P0）。"
        ),
        impl=(
            ImplAnchor(
                file="app/services/data_fabric/security.py",
                anchor="def validate_url",
            ),
        ),
        test_nodes=(
            "tests/unit/test_data_fabric_security.py::test_ssrf_adapter_blocks_metadata_ip_on_send",
            "tests/test_connection_manager_ssrf.py::TestConnectionManagerHostOnlySSRF"
            "::test_rejects_private_loopback_metadata_host_without_url",
        ),
    ),
    SecurityControl(
        control_id="SEC-07",
        area="stored connection profile shape（allow_private 钉扎）",
        description=(
            "create 路由无条件 allow_private=False；落库 connection_profile 顶层只能"
            "承载 False，probe/tile 读取端缺省 False —— 存储型 SSRF 旁路（audit gap #2）"
            "被存储形态回归测试钉死。"
        ),
        impl=(
            ImplAnchor(
                file="app/api/routes/data_fabric.py",
                anchor="allow_private=False,",
            ),
            ImplAnchor(
                file="app/services/data_fabric/manager.py",
                anchor="stored_profile = conn_profile.model_dump()",
            ),
        ),
        test_nodes=(
            "tests/quality/test_security_regression.py"
            "::test_create_route_forces_allow_private_false_regardless_of_payload",
            "tests/quality/test_security_regression.py"
            "::test_create_data_source_stores_allow_private_false_round_trip",
            "tests/quality/test_security_regression.py"
            "::test_probe_route_reader_defaults_to_false_when_key_missing",
        ),
    ),
    SecurityControl(
        control_id="SEC-08",
        area="session ownership guard（owner_token / legacy fail-closed）",
        description=(
            "verify_session_owner：匿名会话需 X-Session-Token 匹配；legacy NULL/NULL"
            "行 fail-closed（#1109）；越权统一 404。"
        ),
        impl=(
            ImplAnchor(
                file="app/core/auth.py",
                anchor="async def verify_session_owner",
            ),
        ),
        test_nodes=(
            "tests/test_sec08_session_owner_token.py::test_new_anon_session_404_with_wrong_token",
            "tests/test_sec08_session_owner_token.py::test_legacy_null_null_session_fail_closed",
        ),
    ),
    SecurityControl(
        control_id="SEC-RT-01",
        area="path traversal（report download validator + 路由调用点）",
        description=(
            "report.py:_validate_file_path realpath 前缀门；测试直接 import 真实"
            "helper 并端到端走 download 路由（原测试文件是伪覆盖 —— 复写 helper "
            "副本，audit gap #1），含 planted symlink 逃逸向量。"
        ),
        impl=(
            ImplAnchor(
                file="app/api/routes/report.py",
                anchor="def _validate_file_path",
            ),
            ImplAnchor(
                file="app/api/routes/report.py",
                anchor="if not _validate_file_path(report.file_path, REPORT_DIR):",
            ),
        ),
        test_nodes=(
            "tests/test_api_path_traversal.py"
            "::test_rejects_planted_symlink_escaping_root",
            "tests/test_api_path_traversal.py"
            "::test_download_rejects_symlink_escape_planted_in_report_dir",
        ),
    ),
    SecurityControl(
        control_id="SEC-RT-02",
        area="query injection net（AST→SQL/CQL2/ArcGIS/FES 编译器 + 标识符卫生）",
        description=(
            "编译器把值全部送入绑定参数（pyformat %s / 单引号翻倍 / XML 实体转义），"
            "标识符走白名单正则；PostGIS 适配器标识符卫生 + app 内 f-string SQL "
            "执行静态扫描（audit gap #8：此前零直接测试）。"
        ),
        impl=(
            ImplAnchor(
                file="app/services/data_fabric/query/compilers.py",
                anchor="def quote_ident",
            ),
            ImplAnchor(
                file="app/services/data_fabric/adapters/postgis_adapter.py",
                anchor="def _sanitize_identifier",
            ),
        ),
        test_nodes=(
            "tests/quality/test_security_regression.py"
            "::test_predicate_values_stay_bound_parameters",
            "tests/quality/test_security_regression.py"
            "::test_adapter_identifier_rejects_injection",
            "tests/quality/test_security_regression.py::test_no_fstring_sql_execution_in_app",
        ),
    ),
    SecurityControl(
        control_id="SEC-RT-03",
        area="error oracle（is_production 门控 traceback/error_detail 出口）",
        description=(
            "exception.py:include_details = not settings.is_production() —— 生产分支"
            "返回泛化消息、零内部信息；dev 分支 traceback 经 sanitize_traceback 剥离"
            "仓库路径（audit gap #4：生产分支此前无 e2e）。"
        ),
        impl=(
            ImplAnchor(
                file="app/core/exception.py",
                anchor="include_details = not settings.is_production()",
            ),
        ),
        test_nodes=(
            "tests/quality/test_security_regression.py"
            "::test_production_response_is_generic_no_traceback",
            "tests/quality/test_security_regression.py"
            "::test_dev_response_pins_designed_detail_behavior",
            "tests/test_error_sanitization_v2.py::test_chat_exception_hides_internal_details",
        ),
    ),
    SecurityControl(
        control_id="SEC-RT-04",
        area="sensitive-key redaction parity（2 denylist + trace hints + 1 allowlist）",
        description=(
            "共享关键凭据基线（15 键，含 auth / api-key 近形键）必须被 jobs 落库"
            "denylist、data-fabric profile 出口 denylist、runtime trace hints 覆盖，"
            "且绝不进入 geocompute trace allowlist（audit gap #7：三个词表无 parity）。"
        ),
        impl=(
            ImplAnchor(
                file="app/services/jobs/redaction.py",
                anchor="SENSITIVE_KEY_PARTS: tuple[str, ...] = (",
            ),
            ImplAnchor(
                file="app/services/data_fabric/security.py",
                anchor="def sanitize_profile_dict",
            ),
            ImplAnchor(
                file="app/lib/runtime/trace.py",
                anchor="_SENSITIVE_KEY_HINTS = (",
            ),
            ImplAnchor(
                file="app/services/geocompute/tracing.py",
                anchor="allowed = {",
            ),
        ),
        test_nodes=(
            "tests/quality/test_security_regression.py"
            "::test_jobs_redaction_denylist_covers_baseline_behaviorally",
            "tests/quality/test_security_regression.py"
            "::test_geocompute_trace_allowlist_never_admits_baseline_keys",
            "tests/jobs/test_job_redaction_progress.py::test_sensitive_keys_are_redacted",
        ),
    ),
    SecurityControl(
        control_id="SEC-KG-01",
        area="artifact ownership（KNOWN-GAP）",
        description=(
            "产物注册表按 session_id 键控；无 owner/身份概念 —— 隔离依赖调用方"
            "先过会话守卫的隐形调用顺序。"
        ),
        impl=(
            ImplAnchor(
                file="app/services/artifact_registry.py",
                anchor="async def get_artifact",
            ),
        ),
        test_nodes=(
            "tests/quality/test_security_regression.py"
            "::test_artifact_session_scope_isolates_foreign_sessions",
        ),
        status=STATUS_KNOWN_GAP,
        gap_note=(
            "artifact_registry.get_artifact/list_artifacts 没有 owner 参数：隔离"
            "完全依赖调用方以『已过 verify_session_owner 的 session_id』调用，"
            "新增 /artifacts/{id} 类便捷路由或跳过会话守卫的 ref 解析会直接打开"
            "跨会话产物读取（audit 风险 #6）。已落地的回归只钉住最近的真实边界"
            "（session 作用域互不可见/不可改、同名 ref 不串写）。修复方向：给"
            "注册表加 owner 列并在 get/list/mark 校验，或静态契约扫描强制全部"
            "调用点先过会话守卫。"
        ),
    ),
    # R1 review 残差披露（MINOR-1/MINOR-2，后续硬化项，不在本分支扩大战线）：
# - MINOR-1：LockLostError 防护路径在 artifact_registry 各写边界未启用
#   （fail_on_lost 默认 False）——chat/session_plan 写边界已消费 .lost。
#   后续：共享写段落默认 fail_on_lost=True 或注册表写前轮询。
# - MINOR-2：report.py 下载存在 swap-after-check TOCTOU 残差（复用路径
#   再打开）；硬化方向 = dirfd 相对打开 + O_NOFOLLOW + fstat 比对。
    SecurityControl(
        control_id="SEC-KG-02",
        area="templates / knowledge delete authZ（KNOWN-GAP）",
        description=(
            "DELETE /templates/{id} 与 DELETE /knowledge/document/{id} 仅有 authN"
            "（AST 扫描覆盖），无属主矩阵回归（audit 风险 #5，#1109 同类）。"
        ),
        impl=(
            ImplAnchor(
                file="app/api/routes/templates.py",
                anchor="async def delete_template(",
            ),
            ImplAnchor(
                file="app/api/routes/knowledge.py",
                anchor="async def delete_document(",
            ),
        ),
        test_nodes=(),
        status=STATUS_KNOWN_GAP,
        gap_note=(
            "两个 delete 路由的 authZ 无显式回归：若实现不带 owner 校验（或未来"
            "重构丢失），同形于 #1109 的可枚举 IDOR 删除不会被抓到。修复方向：按"
            "tests/test_upload_ownership_matrix_1109.py 的 7 案矩阵补 route 级"
            "owner/token 矩阵后再翻 status=tested。"
        ),
    ),
)


# ── 门禁校验 ─────────────────────────────────────────────────────────────

_NODE_DEF_RE_CACHE: dict = {}


def _node_defined(text: str, node_name: str) -> bool:
    """节点存在性：``file::Class::test_x`` 要求 def 出现在该 class 体内
    （R1 review MINOR-3：纯文件级 containment 会让未收集类的同名 def 误过）；
    ``file::test_x`` 维持文件级判定。"""
    pat = _NODE_DEF_RE_CACHE.get(node_name)
    if pat is None:
        pat = re.compile(r"^\s*(?:async\s+)?def\s+" + re.escape(node_name) + r"\s*\(",
                         re.MULTILINE)
        _NODE_DEF_RE_CACHE[node_name] = pat
    if "::" not in node_name:
        return bool(pat.search(text))
    cls, func = node_name.split("::", 1)
    # 类体边界封顶：目标 def 必须出现在 class {cls} 声明之后、且先于
    # 下一个 column-0 的 class/def（DOTALL 懒惰量词 + 边界锚，防跨类误配）。
    for m in re.finditer(r"^class\s+" + re.escape(cls) + r"\b[^:]*:", text, re.M):
        body_start = m.end()
        next_top = re.search(r"^(?:class\s|def\s|async\s+def\s)", text[body_start:], re.M)
        body_end = body_start + (next_top.start() if next_top else len(text) - body_start)
        body = text[body_start:body_end]
        if re.search(r"^\s+(?:async\s+)?def\s+" + re.escape(func) + r"\s*\(", body, re.M):
            return True
    return False


def validate_security_manifest(
    controls: Optional[Sequence[SecurityControl]] = None,
) -> List[str]:
    """返回问题列表（空 = 通过）。规则见模块 docstring。"""
    issues: List[str] = []
    controls = SECURITY_CONTROLS if controls is None else controls
    root = repo_root()
    seen = set()

    for control in controls:
        cid = control.control_id
        prefix = f"[{cid}]"
        if cid in seen:
            issues.append(f"{prefix} duplicate control id")
        seen.add(cid)
        if not control.area.strip():
            issues.append(f"{prefix} missing area")
        if control.status not in _VALID_STATUSES:
            issues.append(f"{prefix} invalid status {control.status!r}")
            continue

        if not control.impl:
            issues.append(f"{prefix} no implementation anchors declared")

        for anchor in control.impl:
            path = root / anchor.file
            if not path.exists():
                issues.append(f"{prefix} implementation file missing: {anchor.file}")
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                issues.append(f"{prefix} unreadable {anchor.file}: {exc}")
                continue
            if anchor.anchor not in text:
                issues.append(
                    f"{prefix} anchor {anchor.anchor!r} not found in {anchor.file}"
                    "（实现被搬走/改名 → 更新锚点或补测试）"
                )

        if control.status == STATUS_TESTED and not control.test_nodes:
            issues.append(
                f"{prefix} status=tested requires at least one test node"
                "（无测试的控制必须翻 known-gap 并写 gap_note）"
            )
        if control.status == STATUS_KNOWN_GAP and len(control.gap_note.strip()) < 20:
            issues.append(f"{prefix} known-gap requires an explicit gap_note")

        for node in control.test_nodes:
            if "::" not in node:
                issues.append(f"{prefix} malformed test node {node!r}")
                continue
            rel, _, rest = node.partition("::")
            path = root / rel
            if not path.exists():
                issues.append(f"{prefix} test file missing: {rel}")
                continue
            text = path.read_text(encoding="utf-8")
            # R2 review MAJOR-2：传完整 rest（含类限定），类感知分支才可达；
            # 类体边界由 _node_defined 的"下一个 class/column-0 def"封顶。
            if not _node_defined(text, rest):
                issues.append(f"{prefix} test node not found in {rel}: {rest}")

    return issues


# ── 认证表渲染 ───────────────────────────────────────────────────────────


def _render_impl(anchors: Sequence[ImplAnchor]) -> str:
    if not anchors:
        return "—"
    return "<br>".join(f"`{a.file}`（锚点 `{a.anchor}`）" for a in anchors)


def _render_nodes(nodes: Sequence[str]) -> str:
    if not nodes:
        return "—"
    return "<br>".join(f"`{n}`" for n in nodes)


def render_security_manifest_md() -> str:
    issues = validate_security_manifest()
    lines: List[str] = []
    lines.append("# Security Controls Certification（自动生成）")
    lines.append("")
    lines.append("> 由 `python scripts/gen_security_manifest.py` 派生，请勿手改。")
    lines.append("> 契约：`app/lib/quality/security_manifest.py`；行为红线：")
    lines.append("> `tests/quality/test_security_regression.py`。")
    lines.append("")
    lines.append("## 控制 → 实现 → 回归测试")
    lines.append("")
    lines.append("| control | area | status | 实现锚点 | 回归测试 |")
    lines.append("|---|---|---|---|---|")
    for c in SECURITY_CONTROLS:
        status = "TESTED" if c.status == STATUS_TESTED else "KNOWN-GAP"
        lines.append(
            f"| {c.control_id} | {c.area} | {status} | "
            f"{_render_impl(c.impl)} | {_render_nodes(c.test_nodes)} |"
        )
    lines.append("")
    lines.append("## 显式缺口（KNOWN-GAP：manifest 门允许『有测试』或『显式缺口』，沉默缺失即红）")
    lines.append("")
    for c in SECURITY_CONTROLS:
        if c.status != STATUS_KNOWN_GAP:
            continue
        lines.append(f"- **{c.control_id}**（{c.area}）：{c.gap_note}")
    if not any(c.status == STATUS_KNOWN_GAP for c in SECURITY_CONTROLS):
        lines.append("（无）")
    lines.append("")
    lines.append(
        "> 审计出处：`.agent-work/quality-v1/06-security-regression-map.md`"
        "（file:line 证据 + 15 风险排名）。"
    )
    lines.append("")
    payload = json.dumps(
        [
            {
                "control_id": c.control_id,
                "area": c.area,
                "status": c.status,
                "impl": [[a.file, a.anchor] for a in c.impl],
                "test_nodes": list(c.test_nodes),
                "gap_note": c.gap_note,
            }
            for c in SECURITY_CONTROLS
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    lines.append(f"- 内容指纹：`{fingerprint[:16]}…`")
    lines.append(f"- 门禁校验：`validate_security_manifest()` → {len(issues)} issue(s)")
    lines.append("")
    return "\n".join(lines)
