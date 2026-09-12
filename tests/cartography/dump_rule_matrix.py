"""Dump the intent rule matrix (rule × corpus coverage × conflicts) to CSV.

P0 勘察工具（AC-01 / ADR-0150）：把 ``app/services/gis_harness/intent.py``
的确定性规则表对基线语料逐条跑命中，产出：

- 每条规则的语料命中数与示例命中（rule → 命中样本 → 覆盖 task）；
- 规则间冲突（同一 query 命中多条规则时，与首条共现的规则集合）；
- 未被任何规则覆盖、落入 fallback 的语料样本。

用法（worktree 根目录）::

    python tests/cartography/dump_rule_matrix.py \
        --corpus tests/cartography/fixtures/intent_corpus.jsonl \
        --out docs/dev/ac-01-rule-matrix.csv

只读勘察工具：不修改任何业务状态。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from app.services.gis_harness import intent_semantic  # noqa: E402


def _load_corpus(path: Path) -> list[dict]:
    items: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus", type=Path,
        default=REPO_ROOT / "tests/cartography/fixtures/intent_corpus.jsonl")
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "docs/dev/ac-01-rule-matrix.csv")
    args = parser.parse_args()

    corpus = _load_corpus(args.corpus)
    # AC-01 重构后规则表迁至 intent_semantic.TASK_RULES（TaskRule dataclass）。
    # 注意：docs/dev/ac-01-rule-matrix.csv 是**基线快照**（重构前规则对语料
    # 的命中，见 ac-01-intent-recon.md §1）；本脚本对当前规则重生成会覆盖
    # 它——再生成时请换输出路径。
    rules = intent_semantic.TASK_RULES

    # 每个 query 命中的全部规则（不只首条），用于冲突分析。
    hits_per_rule: dict[str, list[str]] = defaultdict(list)
    hits_per_rule_task: dict[str, set] = defaultdict(set)
    co_occurrence: dict[str, Counter] = defaultdict(Counter)
    fallback_queries: list[str] = []
    first_rule_counter: Counter = Counter()

    for item in corpus:
        query = item["query"]
        matched_all: list[str] = []
        for rule in rules:
            if rule.pattern.search(query):
                matched_all.append(rule.rule_id)
                hits_per_rule[rule.rule_id].append(item["id"])
                hits_per_rule_task[rule.rule_id].add(rule.task)
        if matched_all:
            first = matched_all[0]
            first_rule_counter[first] += 1
            for other in matched_all[1:]:
                co_occurrence[first][other] += 1
        else:
            fallback_queries.append(f"{item['id']}:{query}")

    with args.out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rule_index", "rule_id", "task", "corpus_hits",
            "first_hit_wins", "example_hits", "conflicting_rules",
        ])
        for idx, rule in enumerate(rules):
            examples = hits_per_rule.get(rule.rule_id, [])[:5]
            conflicts = ", ".join(
                f"{other}({cnt})" for other, cnt
                in co_occurrence.get(rule.rule_id, Counter()).most_common(6))
            writer.writerow([
                idx, rule.rule_id, rule.task, len(hits_per_rule.get(rule.rule_id, [])),
                first_rule_counter.get(rule.rule_id, 0),
                " | ".join(examples), conflicts,
            ])
        writer.writerow([])
        writer.writerow(["# fallback queries (no rule matched)",
                         len(fallback_queries)])
        for fq in fallback_queries:
            writer.writerow(["fallback", fq])

    print(f"rules={len(rules)} corpus={len(corpus)} "
          f"fallback={len(fallback_queries)} -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
