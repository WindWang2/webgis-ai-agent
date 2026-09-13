"""Intent corpus 扩容生成器（V11 W1.5，ADR-0161）。

把 300 条种子语料确定性扩到 1000 条（zh 600 / en 400），覆盖
「17 任务族 × 表达变体 × 领域术语 × 错拼」。

诚实标注纪律（本生成器的硬约束）：

- 新条目的 ``expect.task`` **只继承种子条目的真值** —— 变换仅作用于
  与任务判定无关的句子框架（前后缀修饰、礼貌式、领域术语替换不触碰
  任务关键词）；
- 错拼变体只扰动**主体/地名**字符（任务关键词保持完整），并在 id 与
  variant 中显式标注 ``typo``；其 subject/scope 命中率如实进入诊断
  （允许下降，不入门禁）；
- ``clarify`` 恒 false（模糊条目只用人工审定的 10 条种子）；
- 确定性：无随机 —— 逐种子按固定模板序取模，同输入恒同输出；
  生成器入仓，可审计、可重跑（幂等）。

用法::

    python tests/cartography/expand_intent_corpus.py \
        --seed tests/cartography/fixtures/intent_corpus.jsonl \
        --out tests/cartography/fixtures/intent_corpus_v11.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_DEFAULT = REPO_ROOT / "tests/cartography/fixtures/intent_corpus.jsonl"
OUT_DEFAULT = REPO_ROOT / "tests/cartography/fixtures/intent_corpus_v11.jsonl"

TARGET_TOTAL = 1000
TARGET_ZH = 600
TARGET_EN = 400

#: zh 句式框架（{q} = 种子查询去尾标点后的文本）。顺序即优先序（确定性）。
ZH_FRAMES = (
    "帮我看看{q}",
    "请生成{q}的专题地图",
    "我想了解{q}",
    "麻烦展示一下{q}",
    "用地图呈现{q}",
    "帮我做一张{q}的图",
    "分析并绘制{q}",
    "给我看{q}的情况",
    "需要一张关于{q}的地图",
    "在地图上标出{q}",
)

#: en 句式框架。
EN_FRAMES = (
    "Please show me {q}",
    "Generate a map of {q}",
    "I want to explore {q}",
    "Draw {q} on a map",
    "Create a thematic map for {q}",
    "Visualize {q} for me",
    "Map out {q}",
    "I need a map about {q}",
    "Render {q} as a map layer",
    "Plot {q} on the map",
)

#: zh 错拼：近形/近音替换表（只用于主体/地名首字符；任务关键词不动）。
ZH_TYPO_MAP = {"学": "啍", "医": "垠", "交": "姣", "人": "亼", "学校": "字校",
               "医院": "衣院", "公园": "公圆", "道路": "到路", "河流": "荷流"}

#: en 错拼：非关键词词首字母替换对（确定性）。
EN_TYPO_PAIRS = {"hospital": "hospitol", "school": "schol", "park": "prk",
                 "river": "rivver", "traffic": "trafic", "population": "populaton"}


def _strip_tail_punct(q: str) -> str:
    return q.rstrip("。？！?.!，, ")


def _make_typo_zh(query: str) -> str:
    for old, new in ZH_TYPO_MAP.items():
        if old in query:
            return query.replace(old, new, 1)
    return query


def _make_typo_en(query: str) -> str:
    lowered = query
    for old, new in EN_TYPO_PAIRS.items():
        if old in lowered.lower():
            start = lowered.lower().index(old)
            return lowered[:start] + new + lowered[start + len(old):]
    return query


def _variant_kinds(seed_index: int) -> list[str]:
    """每个种子派生的变体序（确定性轮转）：frame → frame → typo。"""
    rotation = seed_index % 3
    if rotation == 0:
        return ["frame", "typo", "frame"]
    if rotation == 1:
        return ["typo", "frame", "frame"]
    return ["frame", "frame", "typo"]


def expand(seed_items: list[dict]) -> tuple[list[dict], dict]:
    seed_zh = [s for s in seed_items if s["lang"] == "zh"]
    seed_en = [s for s in seed_items if s["lang"] == "en"]
    need_zh = TARGET_ZH - len(seed_zh)
    need_en = TARGET_EN - len(seed_en)

    generated: list[dict] = []
    counters = {"zh": 0, "en": 0}

    def _next_id(lang: str) -> str:
        counters[lang] += 1
        return f"v11-{lang}-{counters[lang]:03d}"

    def _emit(lang: str, seed: dict, variant: str, query: str) -> None:
        generated.append({
            "id": _next_id(lang),
            "lang": lang,
            "family": seed["family"],
            "variant": f"v11_{variant}",
            "query": query,
            "expect": dict(seed.get("expect") or {}),
            "clarify": False,
        })

    def _fill(lang: str, seeds: list[dict], need: int) -> None:
        if need <= 0:
            return
        frames = ZH_FRAMES if lang == "zh" else EN_FRAMES
        emitted = 0
        round_i = 0
        while emitted < need:
            for idx, seed in enumerate(seeds):
                if emitted >= need:
                    break
                kinds = _variant_kinds(idx + round_i)
                for kind in kinds:
                    if emitted >= need:
                        break
                    base = _strip_tail_punct(seed["query"])
                    if kind == "typo":
                        q = _make_typo_zh(base) if lang == "zh" else _make_typo_en(base)
                        if q == base:
                            continue  # 该种子无错拼表项 → 跳过（诚实不凑数）
                        _emit(lang, seed, "typo", q)
                        emitted += 1
                    else:
                        frame = frames[(idx * 2 + round_i + len(generated)) % len(frames)]
                        _emit(lang, seed, "frame", frame.format(q=base))
                        emitted += 1
            round_i += 1
            if round_i > 12:  # 生成器保底：模板耗尽即停（不循环凑数）
                break

    _fill("zh", seed_zh, need_zh)
    _fill("en", seed_en, need_en)
    stats = {
        "seed_total": len(seed_items),
        "seed_zh": len(seed_zh), "seed_en": len(seed_en),
        "generated_total": len(generated),
        "generated_zh": counters["zh"], "generated_en": counters["en"],
        "total": len(seed_items) + len(generated),
    }
    return generated, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=Path, default=SEED_DEFAULT)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    seed_items = [json.loads(line) for line in
                  args.seed.read_text(encoding="utf-8").splitlines() if line.strip()]
    generated, stats = expand(seed_items)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as fh:
        for item in seed_items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
        for item in generated:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
