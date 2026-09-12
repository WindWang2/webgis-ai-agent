"""Intent corpus evaluation harness (AC-01 / ADR-0150).

对 ``tests/cartography/fixtures/intent_corpus.jsonl`` 的 300 条双语语料跑
意图解析并评分。基线（重构前）与回归（重构后）共用同一评分逻辑，保证
「命中率 ≥ 基线 + 8pt」的对比口径一致。

指标定义（与 docs/dev/ac-01-intent-recon.md 锚点一致）：

- ``task_hit_rate``：仅明确条目（``clarify=false``），解析 task == 期望 task。
- ``clarify_hit_rate``：仅模糊条目（``clarify=true``），触发澄清视为命中。
- ``overall_score``：两者按各自条目数加权合并（300 条全量口径，门禁用）。
- ``scope_hit_rate`` / ``subject_hit_rate``：期望给出该槽位时的次级诊断。
- 置信度分布直方图与 fallback 触发占比（静默降级代理指标）。

CLI::

    python tests/cartography/corpus_harness.py \
        [--corpus PATH] [--report PATH] [--by-id CSV]

pytest 中作为库导入（``from corpus_harness import evaluate_corpus``）。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_CORPUS = REPO_ROOT / "tests/cartography/fixtures/intent_corpus.jsonl"

CONFIDENCE_BINS = (0.0, 0.3, 0.5, 0.65, 0.7, 0.85, 1.0)


def load_corpus(path: Path = DEFAULT_CORPUS) -> list[dict]:
    items: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


@dataclass
class ItemResult:
    item_id: str
    lang: str
    family: str
    variant: str
    query: str
    clarify_expected: bool
    clarify_triggered: bool
    task_hit: bool
    expected_task: str | None
    actual_task: str | None
    scope_hit: bool | None = None
    subject_hit: bool | None = None
    confidence: float = 0.0
    fallback_used: bool = False
    degraded_reason: str = ""


@dataclass
class CorpusReport:
    total: int = 0
    definite: int = 0
    ambiguous: int = 0
    task_hits: int = 0
    clarify_hits: int = 0
    scope_evaluated: int = 0
    scope_hits: int = 0
    subject_evaluated: int = 0
    subject_hits: int = 0
    fallback_count: int = 0
    confidence_values: list = field(default_factory=list)
    per_lang: dict = field(default_factory=dict)
    misses: list = field(default_factory=list)

    @property
    def task_hit_rate(self) -> float:
        return self.task_hits / self.definite if self.definite else 0.0

    @property
    def clarify_hit_rate(self) -> float:
        return self.clarify_hits / self.ambiguous if self.ambiguous else 0.0

    @property
    def overall_score(self) -> float:
        if not self.total:
            return 0.0
        return (self.task_hits + self.clarify_hits) / self.total

    @property
    def fallback_rate(self) -> float:
        return self.fallback_count / self.definite if self.definite else 0.0

    def to_dict(self) -> dict:
        def _bin_key(lo: float, hi: float) -> str:
            return f"{lo:g}-{hi:g}"

        hist = {
            _bin_key(lo, hi): 0
            for lo, hi in zip(CONFIDENCE_BINS[:-1], CONFIDENCE_BINS[1:])
        }
        for value in self.confidence_values:
            for lo, hi in zip(CONFIDENCE_BINS[:-1], CONFIDENCE_BINS[1:]):
                if lo <= value < hi or (hi == 1.0 and value >= 1.0):
                    hist[_bin_key(lo, hi)] += 1
                    break
        return {
            "total": self.total,
            "definite": self.definite,
            "ambiguous": self.ambiguous,
            "task_hit_rate": round(self.task_hit_rate, 4),
            "clarify_hit_rate": round(self.clarify_hit_rate, 4),
            "overall_score": round(self.overall_score, 4),
            "scope_hit_rate": round(
                self.scope_hits / self.scope_evaluated, 4)
            if self.scope_evaluated else None,
            "subject_hit_rate": round(
                self.subject_hits / self.subject_evaluated, 4)
            if self.subject_evaluated else None,
            "fallback_rate": round(self.fallback_rate, 4),
            "confidence_mean": round(statistics.fmean(self.confidence_values), 4)
            if self.confidence_values else None,
            "confidence_histogram": hist,
            "per_lang": self.per_lang,
            "misses": self.misses[:40],
        }


def _scope_matches(expected: str, resolved_scope: dict) -> bool:
    name = (resolved_scope or {}).get("name") or ""
    a, b = expected.strip().lower(), name.strip().lower()
    return bool(a) and bool(b) and (a in b or b in a)


def _subject_matches(expected: str, resolved_subject: dict) -> bool:
    category = (resolved_subject or {}).get("category") or ""
    a, b = expected.strip().lower(), category.strip().lower()
    return bool(a) and bool(b) and (a in b or b in a)


def evaluate_corpus(
    items: list[dict],
    resolver=None,
    clarifier=None,
) -> CorpusReport:
    """跑语料评分。

    ``resolver`` 缺省用 ``resolve_map_request_intent``；``clarifier`` 缺省
    为 None（基线行为：从不触发澄清）。返回 :class:`CorpusReport`。
    """
    if resolver is None:
        from app.services.gis_harness.intent import (
            resolve_map_request_intent as resolver,  # type: ignore[no-redef]
        )

    report = CorpusReport(total=len(items))
    for item in items:
        expect = item.get("expect") or {}
        intent = resolver(item["query"])
        dumped = intent.model_dump() if hasattr(intent, "model_dump") else dict(
            intent)
        matched = dumped.get("matched_rules") or []
        fallback_used = bool(matched) and matched[0] == "fallback_distribution_default"
        clarify_triggered = bool(clarifier(item["query"], dumped)) if clarifier else False

        expected_task = expect.get("task")
        if item.get("clarify"):
            report.ambiguous += 1
            task_hit = False
            clarify_hit = clarify_triggered
        else:
            report.definite += 1
            task_hit = expected_task is not None and dumped.get("task") == expected_task
            clarify_hit = False

        scope_hit: bool | None = None
        expected_scope = expect.get("scope")
        if expected_scope:
            scope_hit = _scope_matches(expected_scope, dumped.get("scope") or {})
        subject_hit: bool | None = None
        expected_subject = expect.get("subject_category")
        if expected_subject:
            subject_hit = _subject_matches(
                expected_subject, dumped.get("subject") or {})

        if task_hit:
            report.task_hits += 1
        if clarify_hit:
            report.clarify_hits += 1
        if scope_hit is not None:
            report.scope_evaluated += 1
            if scope_hit:
                report.scope_hits += 1
        if subject_hit is not None:
            report.subject_evaluated += 1
            if subject_hit:
                report.subject_hits += 1
        if fallback_used and not item.get("clarify"):
            report.fallback_count += 1
        report.confidence_values.append(float(dumped.get("confidence") or 0.0))

        if not (task_hit or clarify_hit):
            report.misses.append({
                "id": item["id"], "lang": item["lang"],
                "family": item["family"], "variant": item["variant"],
                "query": item["query"],
                "expected": expected_task, "actual": dumped.get("task"),
                "clarify_triggered": clarify_triggered,
            })

        result = ItemResult(
            item_id=item["id"], lang=item["lang"], family=item["family"],
            variant=item["variant"], query=item["query"],
            clarify_expected=bool(item.get("clarify")),
            clarify_triggered=clarify_triggered, task_hit=task_hit,
            expected_task=expected_task, actual_task=dumped.get("task"),
            scope_hit=scope_hit, subject_hit=subject_hit,
            confidence=float(dumped.get("confidence") or 0.0),
            fallback_used=fallback_used,
            degraded_reason=str(dumped.get("degraded_reason") or ""),
        )
        lang_bucket = report.per_lang.setdefault(result.lang, {
            "total": 0, "definite": 0, "ambiguous": 0, "task_hits": 0,
            "clarify_hits": 0,
        })
        lang_bucket["total"] += 1
        if result.clarify_expected:
            lang_bucket["ambiguous"] += 1
            lang_bucket["clarify_hits"] += int(clarify_hit)
        else:
            lang_bucket["definite"] += 1
            lang_bucket["task_hits"] += int(task_hit)
        for bucket in report.per_lang.values():
            bucket.setdefault("task_hit_rate", 0.0)
            bucket.setdefault("overall_score", 0.0)
        for bucket in report.per_lang.values():
            denom = bucket["total"]
            bucket["task_hit_rate"] = round(
                bucket["task_hits"] / bucket["definite"], 4) if bucket["definite"] else 0.0
            bucket["overall_score"] = round(
                (bucket["task_hits"] + bucket["clarify_hits"]) / denom, 4) if denom else 0.0
        _ = result  # 保持单条结果可供 --by-id 展开

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--report", type=Path, default=None,
                        help="JSON 报告输出路径（缺省仅打印 stdout）")
    args = parser.parse_args()

    items = load_corpus(args.corpus)
    report = evaluate_corpus(items)
    payload = report.to_dict()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text, encoding="utf-8")
        print(f"report -> {args.report}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
