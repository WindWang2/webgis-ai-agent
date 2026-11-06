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

Compatibility floor (ADR-0282): the bridge protocol implementation in this
repo targets Pi >= v1.5.0 (pi-schema argument validation, delta-only
``message_update``, ``disposition`` on prompt responses, two-phase extension
loading). ``PI_MIN_VENDOR_TAG`` is the floor this module reports against.
"""
from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
VENDOR_PI_DIR = REPO_ROOT / "vendor" / "pi"

# Bridge protocol floor. Bump ONLY with an explicit ADR + contract-test update
# (tests/integration/test_pi_vendor_contract.py pins the on-the-wire behavior).
PI_MIN_VENDOR_TAG = "v1.5.0"

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
        return (
            f"pi vendor: head={self.head_commit or '?'}"
            f"({self.head_commit_source}) describe={self.describe or '?'} "
            f"expected={self.expected_pin or '?'} match={self.matches_expected_pin} "
            f"entry={self.entry_kind or 'UNBUILT'} node={self.node_version or '?'} "
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
    except (OSError, subprocess.SubprocessError):
        pass
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


def pi_vendor_identity() -> PiVendorIdentity:
    """Collect the runtime identity (cheap, no network, no spawn)."""
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
            "%s — RPC entry NOT built; Pi bridge will fall back to ChatEngine "
            "(build with scripts/build-vendor-pi.sh)",
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
