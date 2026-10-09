"""Oracle corpus 文件级漂移 pin（#1552）。

回放测试用目标函数的输出比对 ``data/*.json`` 里的冻结期望值；若代码
（目标函数）与数据（JSON 期望值）**同步**修改，回放将全绿且无察觉
（期望值与实现互相"印证"）。本测试用 ``data/corpus_manifest.json``
（逐数据文件的 sha256 + case 数 + domain）做文件级漂移钉扎：任何
data 文件的字节级改动、新增、删除都会让本测试红。

漂移**有意**不被静默放行：禁止手改 manifest 去"对齐"数据。若数据
变更是有意的（期望值/用例集更新、域增删），必须先用
``python scripts/regen_science_oracle_manifest.py`` 重新生成 manifest，
在 PR 描述中说明理由，并将数据与 manifest **同一 PR** 随代码变更提交。

零 LLM/DB/网络，纯 stdlib，确定性。
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

DATA_DIR = Path(__file__).resolve().parent / "data"
MANIFEST_PATH = DATA_DIR / "corpus_manifest.json"
REGEN_CMD = "python scripts/regen_science_oracle_manifest.py"

# 显式契约：当前 corpus 的 14 个数据文件（增减域必须有意的同 PR 变更）。
EXPECTED_FILES: frozenset[str] = frozenset({
    "crs_units.json",
    "edge_cases.json",
    "geodetector.json",
    "geostat.json",
    "network.json",
    "point_pattern.json",
    "regression.json",
    "sar.json",
    "science_v4.json",
    "science_v5.json",
    "spectral.json",
    "statistics_global.json",
    "statistics_local.json",
    "terrain.json",
})

if not MANIFEST_PATH.exists():
    raise RuntimeError(
        f"missing {MANIFEST_PATH.name}: run `{REGEN_CMD}` from the repo root "
        "to generate the oracle corpus file-level drift pin manifest")

MANIFEST = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))

_DRIFT_HINT = (
    "\n这是 oracle corpus 文件级漂移钉扎（有意设计：data 的任何字节级变更"
    "都应显式可见）。若数据变更是**有意**的，必须先运行 "
    f"`{REGEN_CMD}` 重新生成 manifest，在 PR 描述中说明理由，并与代码变更"
    "**同一 PR** 提交数据与 manifest；禁止手改 manifest 绕过本检查。"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_corpus_manifest_shape() -> None:
    assert MANIFEST["corpus_schema_version"] == 1
    assert MANIFEST["generated_by"] == "scripts/gen_science_oracles.py"
    files = MANIFEST["files"]
    assert set(files) == EXPECTED_FILES, (
        f"manifest 数据文件集合偏离显式契约: "
        f"missing={sorted(EXPECTED_FILES - set(files))}, "
        f"extra={sorted(set(files) - EXPECTED_FILES)}"
        + _DRIFT_HINT)
    for name, entry in files.items():
        assert set(entry) == {"sha256", "case_count", "domain"}, name
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), name
        assert isinstance(entry["case_count"], int) and entry["case_count"] > 0, name
        assert isinstance(entry["domain"], str) and entry["domain"], name
    # files 按字典序确定排列（审计 diff 友好）
    assert list(files) == sorted(files)


def test_corpus_file_set_pinned() -> None:
    on_disk = {p.name for p in DATA_DIR.glob("*.json")} - {MANIFEST_PATH.name}
    pinned = set(MANIFEST["files"])
    missing = pinned - on_disk
    untracked = on_disk - pinned
    assert not missing, (
        f"manifest 钉扎但磁盘缺失的数据文件（删除了 data 文件？）: "
        f"{sorted(missing)}" + _DRIFT_HINT)
    assert not untracked, (
        f"manifest 未收录的新增数据文件: {sorted(untracked)}" + _DRIFT_HINT)


@pytest.mark.parametrize("filename", sorted(MANIFEST["files"]))
def test_corpus_file_pinned(filename: str) -> None:
    entry = MANIFEST["files"][filename]
    path = DATA_DIR / filename
    assert path.exists(), f"钉扎的数据文件缺失: {filename}" + _DRIFT_HINT
    actual_sha = _sha256(path)
    assert actual_sha == entry["sha256"], (
        f"sha256 漂移: {filename} manifest={entry['sha256']} "
        f"actual={actual_sha}" + _DRIFT_HINT)
    raw = json.loads(path.read_text(encoding="utf-8"))
    actual_count = len(raw["cases"])
    assert actual_count == entry["case_count"], (
        f"case 数漂移: {filename} manifest={entry['case_count']} "
        f"actual={actual_count}" + _DRIFT_HINT)
    assert raw["domain"] == entry["domain"], (
        f"domain 漂移: {filename} manifest={entry['domain']!r} "
        f"actual={raw['domain']!r}" + _DRIFT_HINT)
    # 回放运行器按 domain（stem）定位数据文件，两者必须一致。
    assert entry["domain"] == Path(filename).stem, (
        f"{filename} 的 domain 必须与文件 stem 一致（回放运行器按 domain "
        f"定位文件）")
