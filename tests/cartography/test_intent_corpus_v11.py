"""W1.5 语料扩容门禁（V11，ADR-0161，缺口 G11）。

1000 条双语意图语料（zh 600 / en 400；300 种子 + 700 确定性生成）的
回归门禁：

- **overall ≥ V10 基线 + 5pt**（0.6933 + 0.05 = 0.7433，任务书 W1 验收）；
- en 命中 ≥ zh 的 90%（双语均衡不劣化）；
- fallback_rate < 0.25（兜底纪律随扩容保持）；
- 17 任务族全覆盖（扩容不得缩族）；
- 错拼变体显式标注（``v11_typo``），其 subject/scope 命中率如实进诊断、
  不掩盖（诚实标注纪律，见 expand_intent_corpus.py）。

既有 300 条语料的 +8pt 门禁（``test_intent_adaptive.py``）与 204 条闭环
语料矩阵（``test_closed_loop_corpus_v6.py``）不改动 —— 防劣化由它们继续
锁定。生成器：``tests/cartography/expand_intent_corpus.py``（幂等可重跑）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from corpus_harness import evaluate_corpus, load_corpus  # noqa: E402

pytestmark = pytest.mark.cartography

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_V11 = REPO_ROOT / "tests/cartography/fixtures/intent_corpus_v11.jsonl"
CORPUS_SEED = REPO_ROOT / "tests/cartography/fixtures/intent_corpus.jsonl"

#: V10 基线（300 条，ac-01-intent-recon 锚点）+ 任务书要求的 5pt。
V10_BASELINE_OVERALL = 0.6933
GAIN_REQUIRED = 0.05
TARGET_TOTAL, TARGET_ZH, TARGET_EN = 1000, 600, 400


@pytest.fixture(scope="module")
def corpus_v11() -> list:
    return load_corpus(CORPUS_V11)


@pytest.fixture(scope="module")
def report_v11(corpus_v11: list):
    def _resolve_rule_only(query: str):
        from app.services.gis_harness.intent import resolve_intent_adaptive
        intent, _ = resolve_intent_adaptive(query, use_llm=False)
        return intent

    def _clarified(_query: str, dumped: dict) -> bool:
        return bool(dumped.get("clarification"))

    return evaluate_corpus(corpus_v11, resolver=_resolve_rule_only,
                           clarifier=_clarified)


def test_corpus_shape_1000_zh600_en400(corpus_v11: list) -> None:
    assert len(corpus_v11) == TARGET_TOTAL
    zh = sum(1 for i in corpus_v11 if i["lang"] == "zh")
    en = sum(1 for i in corpus_v11 if i["lang"] == "en")
    assert (zh, en) == (TARGET_ZH, TARGET_EN)
    ids = [i["id"] for i in corpus_v11]
    assert len(set(ids)) == TARGET_TOTAL  # id 唯一（可回放定位）


def test_family_coverage_not_shrunk(corpus_v11: list) -> None:
    seed_families = {i["family"] for i in load_corpus(CORPUS_SEED)}
    v11_families = {i["family"] for i in corpus_v11}
    assert seed_families <= v11_families, (
        f"扩容缩族: {sorted(seed_families - v11_families)}"
    )


def test_typo_variants_explicitly_labeled(corpus_v11: list) -> None:
    typos = [i for i in corpus_v11 if i["variant"] == "v11_typo"]
    assert len(typos) >= 60, "错拼覆盖不足（任务书 W1.5 要求错拼变体）"
    assert all(i["clarify"] is False for i in typos)


def test_overall_gain_over_v10_baseline(report_v11) -> None:
    assert report_v11.overall_score >= V10_BASELINE_OVERALL + GAIN_REQUIRED, (
        f"overall={report_v11.overall_score:.4f} 门禁="
        f"{V10_BASELINE_OVERALL + GAIN_REQUIRED:.4f}"
    )


def test_english_not_below_90pct_of_chinese(report_v11) -> None:
    zh = report_v11.per_lang["zh"]["task_hit_rate"]
    en = report_v11.per_lang["en"]["task_hit_rate"]
    assert en >= 0.9 * zh, f"en={en:.4f} zh={zh:.4f}"


def test_fallback_rate_stays_below_quarter(report_v11) -> None:
    assert report_v11.fallback_rate < 0.25, (
        f"fallback_rate={report_v11.fallback_rate:.4f}"
    )


def test_ambiguous_seeds_still_all_clarify(report_v11) -> None:
    """10 条人工模糊种子在扩容语料中保持 100% 澄清触发（误触发面不扩大）。"""
    assert report_v11.ambiguous == 10
    assert report_v11.clarify_hit_rate == 1.0


def test_corpus_file_is_deterministic_replay(tmp_path) -> None:
    """生成器幂等：重跑逐字节一致（byte 级，防漂移；产物落 pytest tmp）。"""
    import os
    import subprocess

    out = tmp_path / "corpus_replay.jsonl"
    env = {
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(REPO_ROOT),
    }
    subprocess.run(
        [sys.executable, str(REPO_ROOT / "tests/cartography/expand_intent_corpus.py"),
         "--out", str(out)],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
        env=env, check=True,
    )
    assert out.read_text(encoding="utf-8") == \
        CORPUS_V11.read_text(encoding="utf-8")
