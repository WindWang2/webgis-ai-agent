"""生成物依赖图（Quality V2 W12）——source→generated 指纹账本。

背景：注册表变更后忘记再生成产物，只能等字节闸红（合并后手工
"产物再生成" 纪律，c114d021/a2d8a8 模式）。本模块把每个生成物的
**输入指纹**记账：输入 = 生成脚本自身 + 其语义源文件（registry 种子 /
quality 编译器 / chaos 注册表…）。``check_generated_staleness.py``
重算指纹并与 ``docs/quality/generated-artifacts.json`` 比对 → 在合并
**之前**列出 stale 清单（merge-ref 前置检查，替代事后纪律）。

约束：
- 确定性：指纹 = sha256(canonical([path, sha256(content)])*)，无时间戳；
- 只读投影：绝不改写被生成的产物（再生成仍由各自 gen 脚本负责）；
- 有界：目录输入展开有文件数硬上限。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

#: 生成物账本自身（也是本图的派生物之一）
ARTIFACT_GRAPH_PATH = "docs/quality/generated-artifacts.json"

REPO_ROOT = Path(__file__).resolve().parents[3]

_MAX_DIR_FILES = 400


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _hash_paths(entries: List[Tuple[str, Path]]) -> str:
    payload = []
    for rel, path in sorted(entries):
        payload.append([rel, _file_hash(path)])
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True,
                   separators=(",", ":")).encode("utf-8")).hexdigest()


def _expand(entry: str) -> List[Tuple[str, Path]]:
    """单条输入声明 → [(rel_path, path)]；目录递归展开（有界）。"""
    path = REPO_ROOT / entry
    if path.is_dir():
        files = sorted(p for p in path.rglob("*.py") if p.is_file())
        if len(files) > _MAX_DIR_FILES:
            # R2 review：静默截断 = 新增文件不计入指纹（staleness 盲区）
            # —— 超限必须显式失败，逼迫把目录拆细或上调上限（需 ADR）。
            raise ValueError(
                f"artifact_graph 输入目录 {entry} 含 {len(files)} 个 .py，"
                f"超过有界上限 {_MAX_DIR_FILES}；请拆分声明或显式上调上限")
        return [(f.relative_to(REPO_ROOT).as_posix(), f) for f in files]
    return [(entry, path)]


@dataclass(frozen=True)
class GeneratedEntry:
    artifact: str                      # repo 相对路径（生成物本体）
    generator: str                     # 生成脚本（repo 相对）
    inputs: Tuple[str, ...]            # 语义输入（文件或目录，repo 相对）
    # Quality V3（Epic 10 W5，additive）：作用域与生成器版本。
    # scope=global：全分支共享，rebase/merge 后必须重验再生成；
    # scope=branch-local：单分支工作产物，不参与跨分支冲突。
    scope: str = "global"
    generator_version: int = 1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "generator": self.generator,
            "inputs": list(self.inputs),
            "scope": self.scope,
            "generator_version": self.generator_version,
        }

    def input_fingerprint(self) -> str:
        entries: List[Tuple[str, Path]] = [(self.generator,
                                            REPO_ROOT / self.generator)]
        for item in self.inputs:
            entries.extend(_expand(item))
        return _hash_paths(entries)


#: 依赖声明（单一事实源：新增生成物必须在此登记，否则闸红）
DECLARED: Tuple[GeneratedEntry, ...] = (
    GeneratedEntry(
        artifact="docs/quality/QUALITY_MANIFEST.md",
        generator="scripts/gen_quality_manifest.py",
        inputs=(
            "app/lib/quality/manifest.py",
            "app/lib/quality/discovery.py",
            "app/lib/quality/behavioral.py",
            "app/lib/gis/algorithms",
            "app/lib/gis/capabilities",
            "app/tools",
        ),
    ),
    GeneratedEntry(
        artifact="docs/quality/quality-manifest.json",
        generator="scripts/gen_quality_manifest.py",
        inputs=(
            "app/lib/quality/manifest.py",
            "app/lib/quality/discovery.py",
            "app/lib/quality/behavioral.py",
            "app/lib/gis/algorithms",
            "app/lib/gis/capabilities",
            "app/tools",
        ),
    ),
    GeneratedEntry(
        artifact="docs/quality/QUALITY_REPORT.md",
        generator="scripts/gen_quality_report.py",
        inputs=(
            "app/lib/quality/manifest.py",
            "docs/quality/quality-manifest.json",
            "docs/quality/contract-drift-report.json",
        ),
    ),
    GeneratedEntry(
        artifact="docs/quality/quality-report.json",
        generator="scripts/gen_quality_report.py",
        inputs=(
            "app/lib/quality/manifest.py",
            "docs/quality/quality-manifest.json",
            "docs/quality/contract-drift-report.json",
        ),
    ),
    GeneratedEntry(
        artifact="docs/quality/CONTRACT_DRIFT_REPORT.md",
        generator="scripts/gen_drift_report.py",
        inputs=("app/lib/quality/drift.py", "app/api/routes"),
    ),
    GeneratedEntry(
        artifact="docs/quality/contract-drift-report.json",
        generator="scripts/gen_drift_report.py",
        inputs=("app/lib/quality/drift.py", "app/api/routes"),
    ),
    GeneratedEntry(
        artifact="docs/quality/certifications/SECURITY_CONTROLS.md",
        generator="scripts/gen_security_manifest.py",
        inputs=("app/lib/quality/security_manifest.py",),
    ),
    GeneratedEntry(
        artifact="docs/quality/certifications/CANCELLATION_COVERAGE.md",
        generator="scripts/gen_resource_certification.py",
        inputs=("app/lib/cancellation.py", "app/lib/geo_analysis"),
    ),
    GeneratedEntry(
        artifact="docs/quality/certifications/RESOURCE_SAFETY.md",
        generator="scripts/gen_resource_certification.py",
        inputs=("app/lib/cancellation.py", "app/lib/geo_analysis"),
    ),
    GeneratedEntry(
        artifact="docs/quality/certifications/CHAOS_FAULT_REGISTRY.md",
        generator="scripts/gen_chaos_registry.py",
        inputs=("tests/fixtures/chaos.py",),
    ),
    GeneratedEntry(
        artifact="docs/quality/certifications/DETERMINISM.md",
        generator="scripts/gen_determinism_certification.py",
        inputs=("app/lib/gis/algorithms",),
    ),
    GeneratedEntry(
        artifact="tests/quality/snapshots/realtime-contract.json",
        generator="app/lib/quality/api_compat.py",
        inputs=(
            "app/services/ws_service.py",
            "app/api/routes/ws.py",
            "app/utils/sse.py",
            "app/lib/quality/api_compat.py",
        ),
    ),
    # Quality V3（Epic 10 / ownership parity）：science 声明面投影此前在
    # artifact_graph 之外自管，导致 ownership 规则无法与之对齐（生成物
    # 权威必须是单一账本）。登记后由同一 staleness 闸保护。
    GeneratedEntry(
        artifact="docs/science/BENCHMARK_MANIFEST.md",
        generator="scripts/gen_science_benchmark_manifest.py",
        inputs=("app/lib/gis/algorithm_registry.py",),
    ),
)


def content_fingerprint(artifact: str) -> Optional[str]:
    """生成物本体 sha256（确定性；产物不存在 = None，未生成是合法状态）。"""
    path = REPO_ROOT / artifact
    if not path.is_file():
        return None
    return _file_hash(path)


def build_graph_state() -> Dict[str, Any]:
    """{artifact: {generator, inputs, scope, generator_version,
    input_fingerprint, content_fingerprint}}（确定性）。

    Quality V3 W5：content_fingerprint 使再生成 diff 可归因——
    输入指纹变 = 正常再生成；输入不变而内容变 = 手改（regenerate-dont-edit
    被违反）或生成器非确定（由 DETERMINISM 认证另行覆盖）。
    """
    out: Dict[str, Any] = {}
    for entry in DECLARED:
        out[entry.artifact] = {
            **entry.as_dict(),
            "input_fingerprint": entry.input_fingerprint(),
            "content_fingerprint": content_fingerprint(entry.artifact),
        }
    return out


def find_hand_edits(recorded: Dict[str, Any]) -> List[str]:
    """手改检测（Quality V3 W5）：输入指纹未变而生成物内容已变的产物。

    与 find_stale 正交：stale = 输入变（须再生成）；hand-edit = 输入没变
    但产物被直接编辑（regenerate-dont-edit 违反，改动会随下次再生成静默
    丢失，必须显式 waiver 或改为修改生成器输入）。
    """
    current = build_graph_state()
    edited: List[str] = []
    for artifact, state in sorted(current.items()):
        rec = recorded.get(artifact)
        if not rec:
            continue
        inputs_match = rec.get("input_fingerprint") == state["input_fingerprint"]
        content_changed = (
            state.get("content_fingerprint") is not None
            and rec.get("content_fingerprint") is not None
            and rec.get("content_fingerprint") != state["content_fingerprint"]
        )
        if inputs_match and content_changed:
            edited.append(artifact)
    return edited


def find_stale(recorded: Dict[str, Any]) -> List[str]:
    """指纹不符 / 账本缺失登记的生成物列表（升序）。"""
    current = build_graph_state()
    stale: List[str] = []
    for artifact, state in sorted(current.items()):
        rec = recorded.get(artifact)
        if not rec or rec.get("input_fingerprint") != \
                state["input_fingerprint"]:
            stale.append(artifact)
    # 已删生成物仍在账本 → 也算 stale（提示清理账本）
    for artifact in sorted(set(recorded) - set(current)):
        stale.append(artifact)
    return stale


def load_recorded() -> Dict[str, Any]:
    path = REPO_ROOT / ARTIFACT_GRAPH_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("artifacts", {})
