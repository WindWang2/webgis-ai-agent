"""Standards catalog generator — registry truth → docs projection (ADR-0200).

Same doctrine as catalog_docs: the registry (standards/packs) is the single
truth; docs/cartography/standards-catalog.{md,json} are generated artifacts.

    python -m app.lib.cartography.standards.catalog          # 生成
    python -m app.lib.cartography.standards.catalog --check  # 漂移检查（CI/测试用）

禁止手改生成文件 —— 修改请进入 standards 规则/包定义，再重新生成。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DOCS_DIR = REPO_ROOT / "docs" / "cartography"
MD_PATH = DOCS_DIR / "standards-catalog.md"
JSON_PATH = DOCS_DIR / "standards-catalog.json"


def generate_catalog_json() -> dict:
    from app.lib.cartography.standards.packs import get_standards_registry

    registry = get_standards_registry()
    return {
        "version": 1,
        "packs": [pack.to_dict() for pack in registry.packs()],
    }


def generate_catalog_markdown() -> str:
    from app.lib.cartography.standards.rule import (
        MAP_AUDIENCES,
        MAP_MEDIUMS,
        MAP_PURPOSES,
    )

    data = generate_catalog_json()
    lines = [
        "# Cartographic Standards Catalog",
        "",
        "> 由 `app.lib.cartography.standards.catalog` 从 StandardsRegistry 生成；",
        "> 手改无效。真值：`app/lib/cartography/standards/**`（ADR-0200）。",
        "",
        f"义务轴：purpose {'/'.join(MAP_PURPOSES)}；"
        f"audience {'/'.join(MAP_AUDIENCES)}；medium {'/'.join(MAP_MEDIUMS)}。",
        "",
    ]
    for pack in data["packs"]:
        lines += [
            f"## Pack `{pack['pack_id']}` v{pack['version']}",
            "",
            f"- fingerprint: `{pack['fingerprint']}`",
            f"- 规则数：{pack['rule_count']}",
            "",
            "| rule_id | kind | severity | 义务 | fix 路由 | 依赖 | 参考 |",
            "|---|---|---|---|---|---|---|",
        ]
        for rule in pack["rules"]:
            hint = rule["fix_hint"]
            route = (
                f"{hint['route']}:{hint.get('operation') or hint.get('component_type') or '—'}"
                if hint else "—"
            )
            lines.append(
                f"| `{rule['rule_id']}` | {rule['kind']} | {rule['severity']} "
                f"| {rule['message']} | {route} "
                f"| {', '.join(rule['requires']) or '—'} "
                f"| {', '.join(rule['references']) or '—'} |"
            )
        lines += ["", "### 适用轴", ""]
        for rule in pack["rules"]:
            a = rule["applies_when"]
            lines.append(
                f"- `{rule['rule_id']}`：purpose={a['purposes']}；"
                f"audience={a['audiences']}；medium={a['mediums']}；"
                f"data={a['data_semantics']}"
            )
        lines.append("")
    return "\n".join(lines)


def _write() -> None:
    MD_PATH.write_text(generate_catalog_markdown(), encoding="utf-8")
    JSON_PATH.write_text(
        json.dumps(generate_catalog_json(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")


def _check() -> bool:
    ok = True
    if MD_PATH.read_text(encoding="utf-8") != generate_catalog_markdown():
        print(f"DRIFT: {MD_PATH} 与 registry 不一致（请重新生成）")
        ok = False
    committed = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    if committed != generate_catalog_json():
        print(f"DRIFT: {JSON_PATH} 与 registry 不一致（请重新生成）")
        ok = False
    return ok


if __name__ == "__main__":
    if "--check" in sys.argv:
        sys.exit(0 if _check() else 1)
    _write()
    print(f"written: {MD_PATH}")
    print(f"written: {JSON_PATH}")
