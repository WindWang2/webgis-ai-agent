#!/usr/bin/env python
"""生成 docs/quality/certifications/DETERMINISM.md（ADR-0104 Wave 12）。

用法：
    python scripts/gen_determinism_certification.py            # 写入
    python scripts/gen_determinism_certification.py --check    # 过期则退出 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_OUT = Path("docs/quality/certifications/DETERMINISM.md")


def generate() -> str:
    from app.lib.quality.determinism import render_determinism_md

    return render_determinism_md()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    content = generate()
    if args.check:
        if not DEFAULT_OUT.exists() or DEFAULT_OUT.read_text(encoding="utf-8") != content:
            print(f"stale: {DEFAULT_OUT}")
            print("run: python scripts/gen_determinism_certification.py")
            return 1
        print("determinism certification up to date")
        return 0

    DEFAULT_OUT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUT.write_text(content, encoding="utf-8")
    print(f"wrote {DEFAULT_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
