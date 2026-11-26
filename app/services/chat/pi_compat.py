"""Pi vendor compatibility identity (goal-pi-native-host-vnext / ADR-0282).

Answers one question honestly at runtime: **which Pi is this backend actually
running, and can it run at all?** Before this module nothing compared the
submodule checkout against expectations, ``PI_SKIP_VERSION_CHECK=1`` disabled
the vendor's own check, and an unbuilt ``dist/`` silently degraded every chat
turn to ChatEngine while the UI badge kept claiming "pi".

Scope / discipline:
    - Read-only introspection. No process spawn (that stays in
      ``pi_rpc_client`` — its ``probe_pi_entry_version`` feeds the capability
      snapshot's strongest version evidence), no tool dispatch, no MapSpec
      state.
    - Identity is best-effort (a production image may ship without ``.git``):
      ``pin_source`` says where each fact came from so the report can never
      overstate certainty — ``git`` submodule facts, ``bundle``/``dist``
      filesystem facts, build-manifest facts, or ``unknown``.
    - Fail-open vs fail-closed is the CALLER's policy: the bridge keeps its
      existing fail-closed spawn behavior (FileNotFoundError → ChatEngine);
      this module only makes the state observable (logs, /health, pi_doctor).

Compatibility floor (ADR-0282 → ADR-0288 → ADR-0301): the bridge protocol
implementation in this repo targets Pi >= v1.5.1 (pi-schema argument
validation, delta-only ``message_update``, ``disposition`` on prompt
responses, two-phase extension loading; v1.5.1 adds the $ref-aware
tool_search corpus, nested-call ``parentToolCallId`` events and the
clear_queue/ordered-shutdown RPC semantics this repo now consumes).
``PI_MIN_VENDOR_TAG`` is the floor this module reports against. The *pin*
(v1.6.1, ADR-0301) is what ships and is verified; the *floor* stays v1.5.1 so
an explicit dev override with an older checkout remains legible instead of
silently unsupported.

Capability snapshot (ADR-0301 D2): consumers ask ``caps.has(...)`` about
**features**, never compare versions themselves. The static
feature→min-version table below is the single mapping point between upstream
release notes and host behavior.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from app.services.chat.pi_rpc_client import PI_VENDOR_DIR_ENV, pi_entry_version_output, probe_pi_entry_version

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
VENDOR_PI_DIR = REPO_ROOT / "vendor" / "pi"

# Bridge protocol floor. Bump ONLY with an explicit ADR + contract-test update
# (tests/integration/test_pi_vendor_contract.py pins the on-the-wire behavior).
# v1.5.1（ADR-0288）：native tool discovery（tool_search/codemode 内置扩展）、
# nested parentToolCallId 事件、clear_queue/有序停机。
PI_MIN_VENDOR_TAG = "v1.5.1"

# ── 运行时版本握手（WP1）：探针 + 能力表 ──────────────────────────────────
#
# 事实模型：构建产物（rpc-entry.js）是唯一承载 wire 行为的东西 —— describe
# 说的是「checkout 处于哪个 tag」，`node rpc-entry.js --version` 说的是「这份
# 产物实际构建自哪个版本」。两者会真实漂移（改了 submodule 忘了 rebuild），
# 所以 pi_doctor 的 vendor 域做三方对账（runtime probe vs describe vs
# superproject gitlink）。
#
# 与 spawn 接缝的关系：RPC ``get_state`` 无版本字段（v1.5.x/v1.6.0 实测），
# host spawn 时设 PI_SKIP_VERSION_CHECK=1 跳过 vendor 自检 —— 版本握手的
# 唯一通道就是这条 spawn `--version` 探针。

#: `node <entry> --version` 探针预算。cold start（冷 node 进程 + 大 bundle
#: 的 require 链）实测 <2s；10s 是诊断路径的宽上限，不是 turn 路径预算。
_RUNTIME_PROBE_TIMEOUT_S = 10.0

_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")


def parse_vendor_version(text: str | None) -> tuple[int, int, int] | None:
    """从任意 vendor 版本表述里取前三个 semver 数字。

    接受探针输出（``1.6.0``）、describe（``v1.6.0`` / ``v1.6.0-3-gabc``）。
    解析不出 → ``None``（调用方按未知版本处理，绝不猜）。
    """
    if not text:
        return None
    m = _SEMVER_RE.search(text)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def vendor_capabilities(version: str | None) -> dict[str, bool]:
    """版本字符串 → 能力位。纯函数，单一定义点。

    ``None``（探测失败/未探测）→ 全 False 并以键名直说：诊断出口必须把
    「未知」呈现为「未知」，不得暗示「已确认具备」。
    """
    snapshot = capabilities_for_version(version)
    return {name: snapshot.has(cap) for name, cap in _DIAGNOSTIC_CAPABILITIES.items()}


def probe_vendor_runtime_version(entry_path: Path) -> str | None:
    """Spawn ``node <entry> --version`` 并解析构建产物自报的版本。

    同步 subprocess（调用方负责 offload / 或接受诊断路径的一次性成本），
    语义与 ``pi_rpc_client`` 的 spawn 对齐（同为 PATH 上的 ``node``）。
    失败（文件缺失/node 缺失/超时/输出无 semver）→ ``None``，绝不抛。
    """
    output = pi_entry_version_output(Path(entry_path), timeout=_RUNTIME_PROBE_TIMEOUT_S)
    match = _SEMVER_RE.search(output or "")
    return match.group(0) if match else None


# Superproject-recorded submodule pin (gitlink). Read lazily; ``None`` when the
# deployment ships without git metadata (e.g. production image) — identity then
# degrades to filesystem facts only.
_GITLINK_TIMEOUT_S = 3.0

#: build-vendor-pi.sh 成功后写入的构建清单（ADR-0301 D3）。位于子模块工作树内，
#: 超项目索引天然不可见——它是本机构建事实，仓库事实是 gitlink SHA。
BUILD_MANIFEST_NAME = ".build-manifest.json"

#: submodule 的正确 git 树形态。120000（symlink）是 #2169 引入的回归形态，
#: 必须显式 FAIL 而不是静默 unknown（ADR-0301 D2）。
GITLINK_MODE = "160000"


class PiCapability(str, Enum):
    """封闭能力词表。新增成员必须同步 ``_CAPABILITY_MIN_VERSION`` 与契约测试。"""

    # ── v1.5.1 floor 已含（ADR-0282/0288 已消费）──
    NATIVE_TOOL_DISCOVERY = "native_tool_discovery"      # tool_search 内置扩展
    CODEMODE_SANDBOX = "codemode_sandbox"                # QuickJS 沙箱 + executeTool
    NESTED_TOOL_EVENTS = "nested_tool_events"            # parentToolCallId 事件
    ORDERED_SHUTDOWN = "ordered_shutdown"                # EOF/首信号有序停机
    PROMPT_DISPOSITION = "prompt_disposition"            # prompt 响应 disposition
    COMPACTION_EVENTS = "compaction_events"              # compaction_start/end
    AUTO_RETRY_EVENTS = "auto_retry_events"              # auto_retry_start/end
    # ── v1.6.0（ADR-0301 D5）──
    EXTENSION_ERROR_EVENTS = "extension_error_events"    # extension_error RPC 事件
    ABORT_BATCH_TERMINAL = "abort_batch_terminal"        # Esc 中止批量工具的终态结果
    RPC_SESSION_LIFECYCLE_FIX = "rpc_session_lifecycle_fix"  # session_start 单跑 + entry_appended 转发
    UNREPORTED_DIAGNOSTICS = "unreported_diagnostics"    # takeUnreportedDiagnostics()
    # ── v1.6.1（ADR-0301 D5）──
    PROVIDER_FAILURE_EVENTS = "provider_failure_events"  # after_provider_response 覆盖 4xx/5xx 与重试
    DECISION_MODEL_CLASSIFY = "decision_model_classify"  # codemode models.classify + responseId
    RPC_LINE_SCAN_FIX = "rpc_line_scan_fix"              # 超大行 O(n²) 扫描修复


#: feature → 引入版本。**唯一映射点**；上游 changelog 是事实来源。
_CAPABILITY_MIN_VERSION: dict[PiCapability, str] = {
    PiCapability.NATIVE_TOOL_DISCOVERY: "v1.5.1",
    PiCapability.CODEMODE_SANDBOX: "v1.5.1",
    PiCapability.NESTED_TOOL_EVENTS: "v1.5.1",
    PiCapability.ORDERED_SHUTDOWN: "v1.5.1",
    PiCapability.PROMPT_DISPOSITION: "v1.5.1",
    PiCapability.COMPACTION_EVENTS: "v1.5.0",
    PiCapability.AUTO_RETRY_EVENTS: "v1.5.0",
    PiCapability.EXTENSION_ERROR_EVENTS: "v1.6.0",
    PiCapability.ABORT_BATCH_TERMINAL: "v1.6.0",
    PiCapability.RPC_SESSION_LIFECYCLE_FIX: "v1.6.0",
    PiCapability.UNREPORTED_DIAGNOSTICS: "v1.6.0",
    PiCapability.PROVIDER_FAILURE_EVENTS: "v1.6.1",
    PiCapability.DECISION_MODEL_CLASSIFY: "v1.6.1",
    PiCapability.RPC_LINE_SCAN_FIX: "v1.6.1",
}


# Stable field names consumed by pi_doctor's JSON report.
_DIAGNOSTIC_CAPABILITIES = {
    "clear_queue_ordered_shutdown": PiCapability.ORDERED_SHUTDOWN,
    "prompt_disposition": PiCapability.PROMPT_DISPOSITION,
    "nested_parent_tool_call_id": PiCapability.NESTED_TOOL_EVENTS,
    "abort_batch_tool_results": PiCapability.ABORT_BATCH_TERMINAL,
}
PI_VENDOR_CAPABILITIES = {
    name: _CAPABILITY_MIN_VERSION[cap] for name, cap in _DIAGNOSTIC_CAPABILITIES.items()
}


def _version_tuple(version: str) -> tuple[int, ...] | None:
    """数字元组（忽略 prerelease 段）。prerelease 排序语义在
    ``capabilities_for_version`` 里处理（R1-P3-3：``1.6.1-rc.1`` 是
    ``1.6.1`` 的**低**版本，不得等值授能）。"""
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", version)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def _has_prerelease(version: str) -> bool:
    """``1.2.3-rc.1``/``1.2.3+build`` → True（``1.2.3`` → False）。"""
    return bool(re.search(r"^v?\d+\.\d+\.\d+[-+]", version))


@dataclass(frozen=True)
class PiCapabilitySnapshot:
    """Typed, feature-keyed protocol capability verdict.

    ``evidence`` never claims more than its source: ``entry_probe``（真实
    rpc-entry 自报版本）> ``describe``（git tag 推断）> ``floor_table``
    （仅 floor 保证）> ``unknown``。unsupported 不代表拒绝服务——消费方按
    feature 决定降级（如无 codemode 时显式降级普通工具调用）。
    """

    observed_version: str | None
    version_source: str  # "entry_probe" | "describe" | "unknown"
    _supported: frozenset[PiCapability]
    evidence: dict[str, str] = field(default_factory=dict)

    def has(self, cap: PiCapability) -> bool:
        return cap in self._supported

    def supported_names(self) -> list[str]:
        return sorted(c.value for c in self._supported)

    def unsupported_names(self) -> list[str]:
        return sorted(
            c.value for c in _CAPABILITY_MIN_VERSION if c not in self._supported
        )

    def summary_line(self) -> str:
        return (
            f"pi capabilities: version={self.observed_version or '?'}"
            f"({self.version_source}) supported={len(self._supported)}"
            f"/{len(_CAPABILITY_MIN_VERSION)}"
            f" unsupported=[{','.join(self.unsupported_names())}]"
        )


def capabilities_for_version(
    observed_version: str | None,
    version_source: str = "unknown",
    *,
    floor_tag: str = PI_MIN_VENDOR_TAG,
) -> PiCapabilitySnapshot:
    """版本事实 → 能力快照。纯函数、无 IO；单一 feature 判定点。

    ``observed_version is None`` 时只有「floor 以下即不支持」语义不成立——
    一律判 unsupported（不谎报），消费方看到 ``version_source="unknown"``
    应自行决定降级。
    """
    got = _version_tuple(observed_version) if observed_version else None
    # semver：同基础版本的 prerelease 严格低于正式版（1.6.1-rc.1 < 1.6.1）——
    # got 带 prerelease 时只有基础版本**严格大于**需求版本才授能。
    got_is_prerelease = bool(observed_version) and _has_prerelease(observed_version)
    supported: set[PiCapability] = set()
    evidence: dict[str, str] = {}
    for cap, min_version in _CAPABILITY_MIN_VERSION.items():
        need = _version_tuple(min_version)
        ok = got is not None and need is not None and got >= need
        if ok and got_is_prerelease and got == need:
            ok = False
        if ok:
            supported.add(cap)
            evidence[cap.value] = f"entry_probe>={min_version}" if version_source == "entry_probe" else f"{version_source}>={min_version}"
        else:
            evidence[cap.value] = "unknown" if got is None else f"observed<{min_version}"
    return PiCapabilitySnapshot(
        observed_version=observed_version,
        version_source=version_source,
        _supported=frozenset(supported),
        evidence=evidence,
    )


def pi_capability_snapshot(
    entry_path: str | None = None,
    *,
    observed_version: str | None = None,
    version_source: str = "",
) -> PiCapabilitySnapshot:
    """身份事实 + 带外版本探针 → 能力快照（探针是唯一的 spawn，pi_rpc_client 所有）。

    ``observed_version`` 允许调用方注入已探明的版本事实（如
    ``PiVendorIdentity.observed_version``，避免 doctor 对同一入口 spawn 两次）。
    """
    if observed_version is not None:
        return capabilities_for_version(observed_version, version_source or "entry_probe")
    version, source = probe_pi_entry_version(
        Path(entry_path) if entry_path else None
    )
    if version is None:
        # 带外探针失败 → 退回 git describe（tag 推断，证据弱一档）。
        # R1-P1-2：探针失败正是"未构建/无 node"降级场景，_describe 需要
        # vendor 目录参数（env override 在场时必须与事实来源一致）。
        describe = _describe(_vendor_root()[0])
        if describe:
            tag = re.match(r"^v(\d+\.\d+\.\d+)", describe)
            if tag:
                return capabilities_for_version(tag.group(0), "describe")
        return capabilities_for_version(None, "unknown")
    return capabilities_for_version(version, source)


@dataclass(frozen=True)
class PiVendorIdentity:
    """Observed facts about the vendored Pi runtime. Every field carries its
    provenance; nothing here is a guess dressed as a measurement."""

    head_commit: str | None
    head_commit_source: str  # "git" | "unknown"
    describe: str | None  # e.g. "v1.5.0" or "v1.5.0-3-gabcdef0"
    expected_pin: str | None  # gitlink recorded by the superproject
    expected_pin_source: str  # "git" | "unknown"
    entry_path: str | None  # resolved rpc-entry (bundle preferred)
    entry_kind: str | None  # "bundle" | "dist" | None
    node_version: str | None
    # ── ADR-0301 增量 ──
    vendor_dir: str | None = None  # resolved vendor root
    vendor_dir_source: str = "default"  # "default" | "env_override"
    tree_mode: str | None = None  # git tree entry mode: "160000"/"120000"/...
    tree_mode_source: str = "unknown"  # "git" | "unknown"
    observed_version: str | None = None  # entry --version probe
    observed_version_source: str = "unknown"  # "entry_probe" | "unknown"
    entry_sha256_16: str | None = None  # first 16 hex of the entry file digest
    manifest_state: str = "unknown"  # "absent"|"consistent"|"manifest_only"|"pin_mismatch"|"entry_drift"|"unparsed"
    manifest_pin: str | None = None  # pin recorded by .build-manifest.json
    # Optional diagnostic view of the entry's self-reported version.
    runtime_version: str | None = None
    runtime_version_source: str = "unavailable"  # "runtime_probe" | "unavailable"

    @property
    def entry_built(self) -> bool:
        return self.entry_kind is not None

    @property
    def matches_expected_pin(self) -> bool | None:
        """True/False when both facts are known; None when unverifiable."""
        if self.head_commit is None or self.expected_pin is None:
            return None
        return self.head_commit == self.expected_pin

    @property
    def vendor_tree_not_gitlink(self) -> bool | None:
        """True when the superproject's tree entry for vendor/pi is not a
        gitlink (e.g. the #2169 symlink regression). None when git facts are
        unavailable (production image without .git)."""
        if self.tree_mode is None:
            return None
        return self.tree_mode != GITLINK_MODE

    @property
    def meets_floor(self) -> bool | None:
        """Tag/describe-based floor check; None when no tag info is available."""
        if not self.describe:
            return None
        m = re.match(r"^v(\d+)\.(\d+)\.(\d+)", self.describe)
        if not m:
            return None
        got = tuple(int(x) for x in m.groups())
        floor = tuple(int(x) for x in PI_MIN_VENDOR_TAG.lstrip("v").split("."))
        return got >= floor

    def summary_line(self) -> str:
        runtime = self.runtime_version or "?"
        return (
            f"pi vendor: head={self.head_commit or '?'}"
            f"({self.head_commit_source}) describe={self.describe or '?'} "
            f"expected={self.expected_pin or '?'} match={self.matches_expected_pin} "
            f"entry={self.entry_kind or 'UNBUILT'} node={self.node_version or '?'} "
            f"runtime={runtime}({self.runtime_version_source}) "
            f"floor={PI_MIN_VENDOR_TAG} meets_floor={self.meets_floor} "
            f"tree_mode={self.tree_mode or '?'} version={self.observed_version or '?'}"
            f"({self.observed_version_source}) manifest={self.manifest_state} "
            f"dir_source={self.vendor_dir_source}"
        )


def _run_git(args: list[str], cwd: Path) -> str | None:
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=_GITLINK_TIMEOUT_S,
        )
        if res.returncode != 0:
            return None
        return res.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _expected_pin() -> tuple[str | None, str, str | None]:
    """Submodule commit the superproject records, plus the raw tree-entry mode
    (works even when the working tree drifted — reads the gitlink, not the
    checkout). Mode is reported even when the entry is not a gitlink so the
    #2169-style symlink regression is *diagnosable*, not silently unknown."""
    out = _run_git(["ls-tree", "HEAD", "vendor/pi"], cwd=REPO_ROOT)
    if not out:
        return None, "unknown", None
    parts = out.split()
    # "<mode> <type> <commit>\tvendor/pi"
    # type 必须是 commit：#2169 回归里 120000 symlink 的 blob SHA 同样是 40
    # hex，只查形状会把 blob 误报为 pin——必须连 type 一起钉住。
    mode = parts[0] if parts else None
    if len(parts) >= 3 and parts[1] == "commit" and re.fullmatch(r"[0-9a-f]{40}", parts[2]):
        return parts[2], "git", mode
    return None, "unknown", mode


def _vendor_root() -> tuple[Path, str]:
    """Resolved vendor root + provenance (default path vs PI_VENDOR_DIR)."""
    override = os.environ.get(PI_VENDOR_DIR_ENV, "").strip()
    if override:
        candidate = Path(override).expanduser().resolve()
        if (candidate / "package.json").is_file():
            return candidate, "env_override"
    return VENDOR_PI_DIR, "default"


def _head_commit(vendor_dir: Path) -> tuple[str | None, str]:
    out = _run_git(["rev-parse", "HEAD"], cwd=vendor_dir)
    if not out:
        return None, "unknown"
    return out, "git"


def _describe(vendor_dir: Path) -> str | None:
    out = _run_git(["describe", "--tags", "--long", "HEAD"], cwd=vendor_dir)
    if out is None:
        # Not fatal: describe needs tags; fall back to plain describe.
        out = _run_git(["describe", "--tags", "HEAD"], cwd=vendor_dir)
    return out


def _node_version() -> str | None:
    try:
        res = subprocess.run(
            ["node", "--version"], capture_output=True, text=True, timeout=_GITLINK_TIMEOUT_S
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Pi Node version probe unavailable (%s)", type(exc).__name__[:64])
    return None


def _entry_facts(vendor_dir: Path) -> tuple[str | None, str | None]:
    """(resolved entry path, kind) — bundle preferred over dist."""
    base = vendor_dir / "packages" / "coding-agent" / "dist"
    bundle = base / "bundle" / "rpc-entry.js"
    dist = base / "rpc-entry.js"
    if bundle.is_file():
        return str(bundle), "bundle"
    if dist.is_file():
        return str(dist), "dist"
    return None, None


def _entry_digest(entry_path: str | None) -> str | None:
    if not entry_path:
        return None
    try:
        digest = hashlib.sha256()
        with open(entry_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()[:16]
    except OSError:
        return None


def _manifest_facts(
    vendor_dir: Path, entry_path: str | None, entry_digest: str | None
) -> tuple[str, str | None]:
    """Cross-check the build manifest against reality.

    Returns ``(state, manifest_pin)`` with the bounded vocabulary consumed by
    pi_doctor: ``absent``（未构建或旧构建——build 脚本 v1.6.1 起才写清单）、
    ``consistent``（git HEAD 与清单 pin 对上）、``manifest_only``（无 git 环境
    ——清单是唯一 pin 证据，不冒充已对账）、``pin_mismatch``（清单记录的 pin ≠
    当前子模块 checkout）、``entry_drift``（入口文件指纹与清单不符——上游
    esbuild 产物非字节级可复现，正常重建也会换 chunk hash；也可能是入口被
    替换，对账即可观测）、``unparsed``（清单存在但内容不可读）。
    """
    path = vendor_dir / BUILD_MANIFEST_NAME
    if not path.is_file():
        return "absent", None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unparsed", None
    if not isinstance(data, dict):
        return "unparsed", None
    pin = data.get("pin") if isinstance(data.get("pin"), str) else None
    digest = data.get("entry_sha256_16")
    if pin is None:
        return "unparsed", None
    # Pin 对账基准：优先子模块 checkout HEAD（git 事实）。镜像内通常无 .git
    # （node:slim 无 git、COPY 不含超项目 .git）——此时清单是唯一 pin 证据，
    # 状态如实标 manifest_only（不冒充已对账）。
    head, _src = _head_commit(vendor_dir)
    if head is not None and head != pin:
        return "pin_mismatch", pin
    if entry_path and isinstance(digest, str) and entry_digest is not None:
        if digest != entry_digest:
            return "entry_drift", pin
    if head is None:
        return "manifest_only", pin
    return "consistent", pin


def pi_runtime_entry() -> tuple[bool | None, str | None]:
    """Stat-only vendor entry. ``(entry_built, entry_kind)``; no git/node spawn.

    ``entry_built is None`` only when the filesystem probe itself fails.
    """
    try:
        vendor_dir, _src = _vendor_root()
        _path, kind = _entry_facts(vendor_dir)
    except OSError:
        return None, None
    return kind is not None, kind


def pi_vendor_identity() -> PiVendorIdentity:
    """Collect the runtime identity (cheap, no network; spawns only
    ``node --version`` and the version probe on the resolved entry)."""
    vendor_dir, vendor_src = _vendor_root()
    head, head_src = _head_commit(vendor_dir)
    pin, pin_src, tree_mode = _expected_pin()
    entry_path, entry_kind = _entry_facts(vendor_dir)
    entry_digest = _entry_digest(entry_path)
    manifest_state, manifest_pin = _manifest_facts(vendor_dir, entry_path, entry_digest)
    observed_version, observed_src = probe_pi_entry_version(
        Path(entry_path) if entry_path else None
    )
    return PiVendorIdentity(
        head_commit=head,
        head_commit_source=head_src,
        describe=_describe(vendor_dir),
        expected_pin=pin,
        expected_pin_source=pin_src,
        entry_path=entry_path,
        entry_kind=entry_kind,
        node_version=_node_version(),
        vendor_dir=str(vendor_dir),
        vendor_dir_source=vendor_src,
        tree_mode=tree_mode,
        tree_mode_source="git" if tree_mode is not None else "unknown",
        observed_version=observed_version,
        observed_version_source=observed_src,
        entry_sha256_16=entry_digest,
        manifest_state=manifest_state,
        manifest_pin=manifest_pin,
    )


def attach_runtime_version(identity: PiVendorIdentity) -> PiVendorIdentity:
    """对已构建 entry 跑一次 ``--version`` 探针，回填 identity 的版本字段。

    只在 ``entry_built`` 时 probe；未构建/探针失败 → ``unavailable``（字段
    保持诚实：没有测量就没有数字）。返回新 dataclass（frozen，原对象不动）。
    """
    if not identity.entry_built or not identity.entry_path:
        return identity
    version = probe_vendor_runtime_version(Path(identity.entry_path))
    if version is None:
        return identity
    return replace(
        identity,
        runtime_version=version,
        runtime_version_source="runtime_probe",
    )


def log_pi_vendor_identity() -> "PiVendorIdentity | None":
    """One startup log line + WARN when the state deserves operator eyes.

    WARN (fail-open observability — the spawn itself stays fail-closed in the
    bridge): unbuilt entry, non-gitlink tree shape (#2169 regression class),
    pin mismatch (working tree drifted off the recorded gitlink), build
    manifest disagreement, or floor violation. Never raises.
    """
    try:
        ident = pi_vendor_identity()
    except Exception:  # noqa: BLE001 — observability must never break startup
        logger.warning("pi vendor identity probe failed", exc_info=True)
        return None
    line = ident.summary_line()
    if not ident.entry_built:
        logger.warning(
            "%s — RPC entry NOT built; the Pi-only chat path will fail closed "
            "(503) until vendor/pi is built with scripts/build-vendor-pi.sh",
            line,
        )
    elif ident.vendor_tree_not_gitlink:
        logger.warning(
            "%s — vendor/pi tree entry is mode %s, not a gitlink (%s): fresh "
            "clones cannot build; restore the submodule (git rm --cached + "
            "update-index --cacheinfo 160000,<pin>,vendor/pi)",
            line, ident.tree_mode, "symlink regression class, ADR-0301",
        )
    elif ident.matches_expected_pin is False:
        logger.warning(
            "%s — vendor/pi working tree drifted off the recorded pin; "
            "protocol compatibility is only guaranteed for the recorded tag",
            line,
        )
    elif ident.meets_floor is False:
        # R1-P3-7：floor 违例比 manifest/entry 观测项更严重——先于它们发 WARN，
        # 不被 elif 链屏蔽。
        logger.warning(
            "%s — below the bridge protocol floor; upgrade the submodule pin",
            line,
        )
    elif ident.manifest_state in ("pin_mismatch", "unparsed"):
        logger.warning(
            "%s — build manifest disagreement (%s; manifest pin=%s); "
            "rebuild with scripts/build-vendor-pi.sh",
            line, ident.manifest_state, ident.manifest_pin or "?",
        )
    elif ident.manifest_state == "entry_drift":
        logger.warning(
            "%s — entry digest differs from build manifest (entry_drift): "
            "upstream esbuild output is not byte-reproducible, so a plain "
            "rebuild also explains this; rebuild refreshes the manifest",
            line,
        )
    else:
        logger.info(line)
    return ident


# ── 运行时三态披露（PI-06 / ADR-0286 D3）───────────────────────────────────

#: 唯一词表：native（真 Pi RPC 承载 turn）/ unavailable（桥不可用，fail-closed）。
#: 不再有 fallback（ChatEngine 已不是对话回退路径）。
RUNTIME_MODES = ("native", "unavailable")

#: 有界原因码（安全摘要 —— 无内部路径/密钥/异常文本）。
RUNTIME_MODE_REASONS = (
    "",                    # native：无原因
    "bridge_not_initialized",  # 桥对象未创建（import/spawn 失败或测试未拉起）
    "vendor_entry_unbuilt",    # vendor dist 未构建
    "pi_process_down",     # 桥在场但子进程不活（crash/OOM/坏扩展）
    "probe_failed",        # 事实探测自身失败（/health 专用；真值未知）
)


def classify_runtime_mode(
    *,
    enabled: bool,
    bridge_present: bool,
    alive: bool,
    entry_built: bool | None,
) -> tuple[str, str]:
    """宿主事实 → ``(mode, reason)``。纯函数，单一定义点。

    被 /health（``agent_runtime_mode`` 字段）、Pi 桥 ``task_start``
    （``runtime_mode`` 键）、``scripts/pi_doctor``（汇总行/JSON 字段）共用
    —— 三态词表与原因码不因出口而漂移。不可判定的 ``entry_built``（None）
    按已构建处理（fail-open on undeterminable vendor facts —— 进程活性是
    更直接的事实；真正的 fail-closed 在 turn 终态未知词表→FAILED）。
    """
    if not enabled or not bridge_present:
        # enabled 保留形参以兼容旧调用方；产品已无关闭开关，False 与未初始化同义。
        return "unavailable", "bridge_not_initialized"
    if not alive:
        if entry_built is False:
            return "unavailable", "vendor_entry_unbuilt"
        return "unavailable", "pi_process_down"
    return "native", ""
