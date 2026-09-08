#!/usr/bin/env python
"""生成物 staleness 前置检查（Quality V2 W12）。

把『注册表变更后忘记再生成』从合并后的字节闸红，提前到合并前的显式
清单：重算每个生成物的输入指纹（source→generated 依赖图，
app/lib/quality/artifact_graph.py）并与账本比对。

用法：
    python scripts/check_generated_staleness.py           # 检查（stale → exit 1）
    python scripts/check_generated_staleness.py --update  # 各 gen 再生成后刷新账本
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[1]
GRAPH_PATH = REPO / "docs/quality/generated-artifacts.json"


def main() -> int:
    from app.lib.quality.artifact_graph import (
        ARTIFACT_GRAPH_PATH,
        build_graph_state,
        find_stale,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true",
                        help="先在仓库各 gen 脚本再生成之后，刷新账本指纹")
    args = parser.parse_args()

    if args.update:
        payload = {
            "note": "生成物输入指纹账本（app/lib/quality/artifact_graph.py 派生）。"
                    "流程：跑各 gen_* 再生成 → 本脚本 --update 刷新指纹 → 提交。"
                    "check_generated_staleness.py 在合并前比对，stale 即红。"
                    "（R2 review：不写时间戳 —— 与 ADR-0118 确定性声明一致）",
            "artifacts": build_graph_state(),
        }
        GRAPH_PATH.parent.mkdir(parents=True, exist_ok=True)
        GRAPH_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=1)
            + "\n", encoding="utf-8")
        print(f"wrote {ARTIFACT_GRAPH_PATH}")
        return 0

    from app.lib.quality.artifact_graph import load_recorded

    stale = find_stale(load_recorded())
    if stale:
        print("stale generated artifacts (inputs changed, regenerate then "
              "re-run with --update):")
        for item in stale:
            print(f"  - {item}")
        print("regenerate with the owning gen_* script, then: "
              "python scripts/check_generated_staleness.py --update")
        return 1
    print("generated artifacts up to date (input fingerprints match)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
