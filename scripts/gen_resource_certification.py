#!/usr/bin/env python
"""生成 docs/quality/certifications/ 的 CANCELLATION_COVERAGE 与
RESOURCE_SAFETY 认证表（ADR-0104 Wave 9/10）。

用法：
    python scripts/gen_resource_certification.py            # 写入
    python scripts/gen_resource_certification.py --check    # 过期则退出 1
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_CANCELLATION = Path("docs/quality/certifications/CANCELLATION_COVERAGE.md")
DEFAULT_RESOURCE = Path("docs/quality/certifications/RESOURCE_SAFETY.md")


def generate() -> tuple[str, str]:
    from app.lib.quality.certification import render_cancellation_md, render_resource_md

    return render_cancellation_md(), render_resource_md()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="只校验，不写入")
    args = parser.parse_args()

    cancellation, resource = generate()
    if args.check:
        stale = []
        for path, content in ((DEFAULT_CANCELLATION, cancellation), (DEFAULT_RESOURCE, resource)):
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path))
        if stale:
            print("stale: " + ", ".join(stale))
            print("run: python scripts/gen_resource_certification.py")
            return 1
        print("certification tables up to date")
        return 0

    DEFAULT_CANCELLATION.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_CANCELLATION.write_text(cancellation, encoding="utf-8")
    DEFAULT_RESOURCE.write_text(resource, encoding="utf-8")
    print(f"wrote {DEFAULT_CANCELLATION} and {DEFAULT_RESOURCE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
