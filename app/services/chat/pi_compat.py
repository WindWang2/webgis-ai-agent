"""Pi vendor compatibility identity (goal-pi-native-host-vnext / ADR-0282).

Answers one question honestly at runtime: **which Pi is this backend actually
running, and can it run at all?** Before this module nothing compared the
submodule checkout against expectations, ``PI_SKIP_VERSION_CHECK=1`` disabled
the vendor's own check, and an unbuilt ``dist/`` silently degraded every chat
turn to ChatEngine while the UI badge kept claiming "pi".

Scope / discipline:
    - Read-only introspection. No process spawn (that stays in
      ``pi_rpc_client``), no tool dispatch, no MapSpec state.
    - Identity is best-effort (a production image may ship without ``.git``):
      ``pin_source`` says where each fact came from so the report can never
      overstate certainty — ``git`` submodule facts, ``bundle``/``dist``
      filesystem facts, or ``unknown``.
    - Fail-open vs fail-closed is the CALLER's policy: the bridge keeps its
      existing fail-closed spawn behavior (FileNotFoundError → ChatEngine);
      this module only makes the state observable (logs, /health, pi_doctor).

Compatibility floor (ADR-0282 → ADR-0288): the bridge protocol implementation
in this repo targets Pi >= v1.5.1 (pi-schema argument validation, delta-only
``message_update``, ``disposition`` on prompt responses, two-phase extension
loading; v1.5.1 adds the $ref-aware tool_search corpus, nested-call
``parentToolCallId`` events and the clear_queue/ordered-shutdown RPC semantics
this repo now consumes). ``PI_MIN_VENDOR_TAG`` is the floor this module
reports against.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

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

#: 探针结果缓存，键 ``(entry_path, mtime_ns)``。命中与失败都缓存：版本是
#: 构建产物的函数，产物变化必然表现为 mtime 变化 → 自动失效；探针失败的
#: ``None`` 一并缓存（避免每次诊断都白付 10s 超时），代价是「node 缺失」类
#: 环境性瞬态失败在下一次产物变化前不会被重试 —— 对诊断披露可接受。
_RUNTIME_VERSION_CACHE: dict[tuple[str, int], str | None] = {}

#: Host 侧「版本 → 能力」推断表（capability → 引入该能力的最低 vendor tag）。
#: 这是**推断**不是探测：RPC 协议没有 capability 协商握手，本表供诊断/日志/
#: 未来 gate 语义用，不构成 wire 层事实。升 vendor pin 时按上游 changelog
#: 补行（上游 issue 号见各行注释）。
PI_VENDOR_CAPABILITIES: dict[str, str] = {
    # ADR-0288 / 上游 v1.5.1：clear_queue + 有序停机（Esc 语义）
    "clear_queue_ordered_shutdown": "v1.5.1",
    # v1.5.1：prompt 响应携带 disposition（turn 终态归属）
    "prompt_disposition": "v1.5.1",
    # v1.5.1：嵌套调用的 tool_execution_* 事件带 parentToolCallId
    "nested_parent_tool_call_id": "v1.5.1",
    # 上游 #9986：abort 时批量中未执行的工具也拿到 "Operation aborted"
    # error tool result（batch 终态完整）
    "abort_batch_tool_results": "v1.6.0",
}

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
    got = parse_vendor_version(version)
    out: dict[str, bool] = {}
    for capability, min_tag in PI_VENDOR_CAPABILITIES.items():
        floor = parse_vendor_version(min_tag)
        out[capability] = got is not None and floor is not None and got >= floor
    return out


def probe_vendor_runtime_version(entry_path: Path) -> str | None:
    """Spawn ``node <entry> --version`` 并解析构建产物自报的版本。

    同步 subprocess（调用方负责 offload / 或接受诊断路径的一次性成本），
    语义与 ``pi_rpc_client`` 的 spawn 对齐（同为 PATH 上的 ``node``）。
    失败（文件缺失/node 缺失/超时/输出无 semver）→ ``None``，绝不抛。
    """
    entry = Path(entry_path)
    try:
        stat = entry.stat()
    except OSError:
        return None
    cache_key = (str(entry), stat.st_mtime_ns)
    if cache_key in _RUNTIME_VERSION_CACHE:
        return _RUNTIME_VERSION_CACHE[cache_key]
    version: str | None = None
    try:
        res = subprocess.run(
            ["node", str(entry), "--version"],
            capture_output=True,
            text=True,
            timeout=_RUNTIME_PROBE_TIMEOUT_S,
        )
        if res.returncode == 0:
            m = _SEMVER_RE.search(res.stdout or "")
            if m:
                version = m.group(0)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(
            "pi runtime version probe unavailable (%s)", type(exc).__name__[:64]
        )
    _RUNTIME_VERSION_CACHE[cache_key] = version
    return version

# Superproject-recorded submodule pin (gitlink). Read lazily; ``None`` when the
# deployment ships without git metadata (e.g. production image) — identity then
# degrades to filesystem facts only.
_GITLINK_TIMEOUT_S = 3.0


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
    # WP1 版本握手：构建产物自报版本（`node entry --version`）。默认不填 ——
    # pi_vendor_identity() 是 cheap 路径（模块纪律：no process spawn），探针
    # 要 spawn 一次 node（10s 上限），由诊断出口（pi_doctor vendor 域）经
    # attach_runtime_version() 显式 opt-in。
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
            f"floor={PI_MIN_VENDOR_TAG} meets_floor={self.meets_floor}"
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


def _expected_pin() -> tuple[str | None, str]:
    """Submodule commit the superproject records (works even when the working
    tree drifted — reads the gitlink, not the checkout)."""
    out = _run_git(["ls-tree", "HEAD", "vendor/pi"], cwd=REPO_ROOT)
    if not out:
        return None, "unknown"
    parts = out.split()
    # "<mode> <type> <commit>\tvendor/pi"
    if len(parts) >= 3 and re.fullmatch(r"[0-9a-f]{40}", parts[2]):
        return parts[2], "git"
    return None, "unknown"


def _head_commit() -> tuple[str | None, str]:
    out = _run_git(["rev-parse", "HEAD"], cwd=VENDOR_PI_DIR)
    if not out:
        return None, "unknown"
    return out, "git"


def _describe() -> str | None:
    out = _run_git(["describe", "--tags", "--long", "HEAD"], cwd=VENDOR_PI_DIR)
    if out is None:
        # Not fatal: describe needs tags; fall back to plain describe.
        out = _run_git(["describe", "--tags", "HEAD"], cwd=VENDOR_PI_DIR)
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


def _entry_facts() -> tuple[str | None, str | None]:
    """(resolved entry path, kind) — bundle preferred over dist."""
    base = VENDOR_PI_DIR / "packages" / "coding-agent" / "dist"
    bundle = base / "bundle" / "rpc-entry.js"
    dist = base / "rpc-entry.js"
    if bundle.is_file():
        return str(bundle), "bundle"
    if dist.is_file():
        return str(dist), "dist"
    return None, None


def pi_runtime_entry() -> tuple[bool | None, str | None]:
    """Stat-only vendor entry. ``(entry_built, entry_kind)``; no git/node spawn.

    ``entry_built is None`` only when the filesystem probe itself fails.
    """
    try:
        _path, kind = _entry_facts()
    except OSError:
        return None, None
    return kind is not None, kind


def pi_vendor_identity() -> PiVendorIdentity:
    """Collect the runtime identity (cheap, no network, no spawn).

    ``runtime_version`` 保持 ``None``/``unavailable`` —— 探针要 spawn node，
    不属于 cheap 路径；需要产物自报版本的出口（pi_doctor vendor 域）显式走
    :func:`attach_runtime_version`。
    """
    head, head_src = _head_commit()
    pin, pin_src = _expected_pin()
    entry_path, entry_kind = _entry_facts()
    return PiVendorIdentity(
        head_commit=head,
        head_commit_source=head_src,
        describe=_describe(),
        expected_pin=pin,
        expected_pin_source=pin_src,
        entry_path=entry_path,
        entry_kind=entry_kind,
        node_version=_node_version(),
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
    bridge): unbuilt entry, pin mismatch (working tree drifted off the recorded
    gitlink), or floor violation. Never raises.
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
    elif ident.matches_expected_pin is False:
        logger.warning(
            "%s — vendor/pi working tree drifted off the recorded pin; "
            "protocol compatibility is only guaranteed for the recorded tag",
            line,
        )
    elif ident.meets_floor is False:
        logger.warning(
            "%s — below the bridge protocol floor; upgrade the submodule pin",
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
