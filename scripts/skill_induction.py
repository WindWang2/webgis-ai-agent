"""离线批量 Skill 归纳 CLI（ADR-0191；引擎的运维入口）。

扫描 ReplayTrace 录制目录（``<root>/<session>/<turn>.json``，与
``app/lib/harness/replay/recorder.py`` 的落盘布局一致），逐条跑归纳
引擎；``--cluster-by-session`` 时按 session 聚簇走 ``induce_many``
（同构轨迹跨轨迹证据互证）。induced 资产写入 ``--out-dir``，汇总
报告写入 ``--report``（JSON）。

用法::

    python scripts/skill_induction.py \
        --recordings-dir data/replay-recordings \
        --out-dir data/induced-skills \
        --report data/induction-report.json

只读语料、只写 --out-dir/--report 两处；不触网络、不触 DB、不修改
core 技能库。exit 0 = 正常跑完（个别轨迹被拒绝不算失败，见报告）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from app.lib.harness.replay.schema import ReplayTrace
from app.services.gis_skills.induction import (
    DEFAULT_SATISFACTION_THRESHOLD,
    InducedSkillStore,
    SkillInductionEngine,
)


def default_recordings_dir() -> Path:
    """与 recorder 同向的缺省语料目录（env 覆盖 > data/replay-recordings）。"""
    override = os.environ.get("HARNESS_REPLAY_DIR")
    if override:
        return Path(override)
    return Path("data") / "replay-recordings"


def iter_recorded_traces(root: Path) -> Iterator[Tuple[Path,
                                                        Optional[ReplayTrace]]]:
    """产出 (源路径, ReplayTrace|None)；形态非法的文件以 None 让位。"""
    if not root.is_dir():
        return
    for path in sorted(root.glob("*/*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            yield path, ReplayTrace.from_dict(doc)
        except (OSError, ValueError, TypeError):
            yield path, None


def run(records_dir: Path, out_dir: Path,
        report_path: Optional[Path] = None, *,
        satisfaction_threshold: float = DEFAULT_SATISFACTION_THRESHOLD,
        cluster_by_session: bool = False, dry_run: bool = False,
        domain: str = "general") -> Dict[str, Any]:
    """批处理主流程（返回汇总 dict；不抛异常，坏轨迹如实入报告）。"""
    engine = SkillInductionEngine(
        store=None if dry_run else InducedSkillStore(out_dir),
        satisfaction_threshold=satisfaction_threshold,
        domain=domain,
    )
    by_session: Dict[str, List[Tuple[Path, ReplayTrace]]] = {}
    invalid: List[Dict[str, str]] = []
    for path, trace in iter_recorded_traces(records_dir):
        if trace is None:
            invalid.append({"source": str(path), "reason": "invalid_trace"})
            continue
        by_session.setdefault(trace.session_id, []).append((path, trace))

    outcomes: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    for session_id, entries in sorted(by_session.items()):
        if cluster_by_session and len(entries) >= 2:
            try:
                outcome = engine.induce_many([t for _, t in entries])
            except Exception as exc:  # noqa: BLE001 - 单簇失败不中止批处理
                errors.append({"source": str([str(p) for p, _ in entries]),
                               "reason": repr(exc)[:200]})
                continue
            outcomes.append({"source": [str(p) for p, _ in entries],
                             **outcome.report})
            continue
        for path, trace in entries:
            try:
                outcome = engine.induce(trace)
            except Exception as exc:  # noqa: BLE001 - 单轨迹失败不中止批处理
                errors.append({"source": str(path), "reason": repr(exc)[:200]})
                continue
            outcomes.append({"source": str(path), **outcome.report})

    induced = sum(1 for o in outcomes if o.get("status") == "induced")
    rejected = sum(1 for o in outcomes if o.get("status") == "rejected")
    summary = {
        "engine": "gis-skills.induction-v1",
        "recordings_dir": str(records_dir),
        "out_dir": str(out_dir) if not dry_run else "",
        "dry_run": dry_run,
        "cluster_by_session": cluster_by_session,
        "satisfaction_threshold": satisfaction_threshold,
        "total": len(outcomes),
        "induced": induced,
        "rejected": rejected,
        "invalid": len(invalid),
        "errors": len(errors),
        "outcomes": outcomes,
        "invalid_records": invalid,
        "error_records": errors,
    }
    if report_path is not None:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ReplayTrace → GIS Skill 离线批量自合成（ADR-0191）")
    parser.add_argument("--recordings-dir", type=Path,
                        default=default_recordings_dir(),
                        help="ReplayTrace 录制目录（缺省 env HARNESS_REPLAY_DIR "
                             "或 data/replay-recordings）")
    parser.add_argument("--out-dir", type=Path,
                        default=Path("data") / "induced-skills",
                        help="induced 资产输出目录")
    parser.add_argument("--report", type=Path, default=None,
                        help="汇总报告输出路径（JSON）")
    parser.add_argument("--min-satisfaction", type=float,
                        default=DEFAULT_SATISFACTION_THRESHOLD,
                        help="D1 满意度门槛（缺省 0.95）")
    parser.add_argument("--cluster-by-session", action="store_true",
                        help="按 session 聚簇合并归纳（同构轨迹互证）")
    parser.add_argument("--domain", default="general",
                        help="induced 技能域（缺省 general）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只评估不入库（store 不落盘）")
    args = parser.parse_args(argv)

    summary = run(args.recordings_dir, args.out_dir, args.report,
                  satisfaction_threshold=args.min_satisfaction,
                  cluster_by_session=args.cluster_by_session,
                  dry_run=args.dry_run, domain=args.domain)
    print(json.dumps({k: summary[k] for k in
                      ("total", "induced", "rejected", "invalid",
                       "dry_run", "cluster_by_session")},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
