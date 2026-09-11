"""Build production source census for the full master audit (Phase A, read-only)."""

import csv
import os
from collections import Counter

ROOTS = {
    "backend": ("app", {".py"}),
    "frontend": ("frontend", {".ts", ".tsx"}),
}
SKIP_DIRS = {
    "__pycache__", "node_modules", "dist", ".next", "build", ".git",
    "venv", ".venv", "coverage", "__snapshots__",
}


def subsystem_of(path: str, area: str) -> str:
    parts = path.split("/")
    if area == "backend":
        # app/services/chat/x.py -> services/chat
        if len(parts) >= 4:
            return "/".join(parts[1:3])
        return parts[1] if len(parts) >= 3 else parts[-1]
    # frontend/lib/store/x.ts -> lib/store
    if len(parts) >= 4:
        return "/".join(parts[1:3])
    return parts[1] if len(parts) >= 3 else parts[-1]


def main() -> None:
    rows = []
    for area, (root, exts) in ROOTS.items():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in sorted(filenames):
                ext = os.path.splitext(fn)[1]
                if ext not in exts:
                    continue
                path = os.path.join(dirpath, fn).replace(os.sep, "/")
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        loc = sum(1 for _ in f)
                except OSError:
                    continue
                rows.append(
                    {
                        "path": path,
                        "area": area,
                        "language": ext.lstrip("."),
                        "loc": loc,
                        "subsystem": subsystem_of(path, area),
                        "review_owner": "",
                        "review_status": "pending",
                        "risk": "",
                        "findings": "",
                    }
                )
    rows.sort(key=lambda r: r["path"])

    out_dir = os.path.join(".agent-work", "full-master-audit-2026-09", "census")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "production-source-census.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "path", "area", "language", "loc", "subsystem",
                "review_owner", "review_status", "risk", "findings",
            ],
        )
        w.writeheader()
        w.writerows(rows)

    print("total files:", len(rows))
    print("total LOC:", sum(r["loc"] for r in rows))
    c = Counter(r["subsystem"] for r in rows)
    for k, v in c.most_common(60):
        print(f"{v:5d}  {k}")


if __name__ == "__main__":
    main()
