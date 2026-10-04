#!/usr/bin/env python3
"""Oracle corpus 文件级漂移 pin manifest 再生成器（#1552）。

期望值的"一次真相"是 scripts/gen_science_oracles.py，
``tests/science_oracles/data/*.json`` 是冻结快照。本脚本把快照逐文件
重新哈希（sha256 + case 数 + domain）写入
``tests/science_oracles/data/corpus_manifest.json``，供漂移 pin 测试
（tests/science_oracles/test_corpus_drift_pin.py）比对 —— 任何 data
文件的字节级改动都会让该测试失败。

用法（工作树根目录）::

    python scripts/regen_science_oracle_manifest.py

输出为确定性字节序（sorted keys、indent=1、末尾换行，与 data 文件的
生成格式一致）——数据未变时重复运行字节相同（幂等）。manifest 不对
自身取哈希。成功静默（退出码 0，结果看 git diff）；错误写 stderr 并
以非零码退出。
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "tests" / "science_oracles" / "data"
MANIFEST_NAME = "corpus_manifest.json"
GENERATED_BY = "scripts/gen_science_oracles.py"
CORPUS_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_manifest() -> dict:
    files: dict[str, dict] = {}
    for path in sorted(DATA_DIR.glob("*.json")):
        if path.name == MANIFEST_NAME:
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        files[path.name] = {
            "case_count": len(raw["cases"]),
            "domain": raw["domain"],
            "sha256": _sha256(path),
        }
    return {
        "corpus_schema_version": CORPUS_SCHEMA_VERSION,
        "generated_by": GENERATED_BY,
        "files": files,
    }


def main() -> int:
    try:
        payload = build_manifest()
    except (OSError, ValueError, KeyError) as exc:
        sys.stderr.write(f"regen_science_oracle_manifest: {exc}\n")
        return 1
    (DATA_DIR / MANIFEST_NAME).write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
