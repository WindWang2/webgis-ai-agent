"""ADR 编号契约（issue #1679）。

`docs/adr/` 曾有 19 个编号被 2–6 篇决策共用（共 62 篇卷入，重编号前 257 篇只占
214 个唯一编号），散文里的 `ADR-NNNN` 因此不可解析。本契约钉住两件事：

1. **编号唯一**：docs/adr 下每个编号恰好一篇；
2. **交叉引用可解析**：ADR 正文引用的每个 `ADR-NNNN` 都有唯一对应文件。

外加一条增强：`RENUMBERING_MAP.md` 与现态一致（改号行的新文件在场、旧文件退场、
0231–0273 号段与映射表逐一对应）。
"""
from __future__ import annotations

import re
import collections
from pathlib import Path

ADR_DIR = Path("docs/adr")
NUM_PREFIX = re.compile(r"^(\d{4})-")


def _numbered_files() -> list[Path]:
    return [p for p in ADR_DIR.glob("*.md") if NUM_PREFIX.match(p.name)]


def _existing_numbers() -> set[str]:
    return {NUM_PREFIX.match(p.name).group(1) for p in _numbered_files()}


def test_adr_numbers_are_unique():
    counts = collections.Counter()
    for p in _numbered_files():
        counts[NUM_PREFIX.match(p.name).group(1)] += 1
    dupes = {n: c for n, c in counts.items() if c > 1}
    assert not dupes, f"ADR 编号撞号：{dupes}"


def test_adr_cross_references_resolve():
    """正文引用的 ADR-NNNN 必须在 docs/adr/ 中有唯一对应文件。"""
    existing = _existing_numbers()
    for p in ADR_DIR.glob("*.md"):
        for ref in set(re.findall(r"ADR-(\d{4})", p.read_text(encoding="utf-8"))):
            assert ref in existing, f"{p.name} 引用 ADR-{ref} 不存在"


def test_renumbering_map_matches_reality():
    """RENUMBERING_MAP.md 的每一行都与现态一致（增强断言，非 issue 核心）。"""
    map_path = ADR_DIR / "RENUMBERING_MAP.md"
    assert map_path.exists(), "缺少 docs/adr/RENUMBERING_MAP.md"
    rows = re.findall(
        r"^\|\s*(\d{4})\s*\|\s*`([^`]+)`\s*\|[^|]*\|\s*(保留原号|改号)\s*\|\s*"
        r"(\d{4})\s*\|\s*`([^`]+)`\s*\|",
        map_path.read_text(encoding="utf-8"), re.MULTILINE)
    assert rows, "RENUMBERING_MAP.md 未解析到映射行"
    renamed_new_names: set[str] = set()
    for old_num, old_name, action, new_num, new_name in rows:
        if action == "保留原号":
            assert new_num == old_num and new_name == old_name, f"保留行自相矛盾：{old_name}"
            assert (ADR_DIR / new_name).exists(), f"保留行文件缺席：{new_name}"
        else:
            assert (ADR_DIR / new_name).exists(), f"改号行新文件缺席：{new_name}"
            assert not (ADR_DIR / old_name).exists(), f"改号行旧文件仍在：{old_name}"
            assert new_name.startswith(new_num + "-"), f"新编号与文件名不符：{new_name}"
            assert old_name.startswith(old_num + "-"), f"旧编号与文件名不符：{old_name}"
            renamed_new_names.add(new_name)
    # 0231–0273 号段（43 篇）必须与映射表改号行一一对应
    band = {p.name for p in _numbered_files()
            if p.name[:4].isdigit() and 231 <= int(p.name[:4]) <= 273}
    assert band == renamed_new_names, (
        f"0231–0273 号段与映射表不一致：缺 {sorted(renamed_new_names - band)}，"
        f"多 {sorted(band - renamed_new_names)}")
