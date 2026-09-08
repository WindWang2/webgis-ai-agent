#!/usr/bin/env python
"""从 registry + 测试引用索引生成 Quality Manifest（docs/quality/）。

manifest 是派生物：唯一事实源是各 registry 与 tests/ 源码本身。
语义变化后重新运行本脚本即可（CI 由
tests/quality/test_quality_manifest_gate.py 校验字节一致性）。

用法：
    python scripts/gen_quality_manifest.py            # 写入 MD + JSON
    python scripts/gen_quality_manifest.py --check    # 仅校验（过期则退出 1）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_MD = Path("docs/quality/QUALITY_MANIFEST.md")
DEFAULT_JSON = Path("docs/quality/quality-manifest.json")


def generate() -> tuple[str, str]:
    from app.lib.quality.manifest import compile_quality_manifest, render_json, render_markdown

    manifest = compile_quality_manifest()
    return render_markdown(manifest), render_json(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    md, js = generate()
    if args.check:
        stale = []
        for path, content in ((DEFAULT_MD, md), (DEFAULT_JSON, js)):
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        if stale:
            print("stale: " + ", ".join(stale))
            print("run: python scripts/gen_quality_manifest.py")
            return 1
        print("quality manifest up to date")
        return 0

    DEFAULT_MD.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_MD.write_text(md, encoding="utf-8")
    DEFAULT_JSON.write_text(js, encoding="utf-8")
    print(f"wrote {DEFAULT_MD} and {DEFAULT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
