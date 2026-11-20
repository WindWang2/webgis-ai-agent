"""G23 P0 复现 — 副本 B：在 A 提交进行中读取批次。

BASE 行为预期（bug）：B 的 `_load_owned` 看到 COMMITTING 且本进程
`_committing_ids` 为空 → 复位成员为 PLANNED、批次改 CONFIGURED 并覆写
batch.json —— A 的活任务被误判为残留。
修复后预期：B 观察到活跃 durable job → 不复位，批次保持 COMMITTING。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path


def _setup_env(root: Path) -> None:
    os.environ["DATA_DIR"] = str(root / "data")
    os.environ["DATABASE_URL"] = f"sqlite:///{root / 'data' / 'webgis.db'}"
    os.environ["USE_REDIS"] = "false"
    os.environ["CELERY_BROKER_URL"] = "memory://"
    # 仓库根 = scripts/g23 的上两级（脚本直接运行时 cwd 不在 sys.path）
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


async def main() -> int:
    root = Path(os.environ["G23_ROOT"])
    _setup_env(root)
    batch_id = (root / "batch_id.txt").read_text().strip()

    from app.core.database import init_db

    init_db()  # 生成 analysis_tasks 等表（G23 durable job 事实源）

    from app.services.data_import import staging
    from app.services.data_import.service import get_data_import_service

    session_id = "sess-g23-repro"
    state_path = staging.batch_root(batch_id, data_dir=str(root / "data")) / "batch.json"

    # 等 A 把批次推进到 COMMITTING（读原始文件，不经过服务层）
    deadline = time.time() + 60
    observed = None
    while time.time() < deadline:
        if state_path.exists():
            raw = json.loads(state_path.read_text())
            if raw.get("status") == "committing":
                observed = {
                    "raw_status": raw["status"],
                    "raw_members": [m["status"] for m in raw.get("members", [])],
                }
                break
        await asyncio.sleep(0.05)
    if observed is None:
        print(json.dumps({"role": "B", "error": "never saw committing"}))
        return 1

    # B 读批次（不同进程 → 进程内 `_committing_ids` 为空）
    svc = get_data_import_service()
    b_view = await svc.get_batch(batch_id, session_id)

    # 再读一次原始文件，看 B 的读是否覆写了 A 正在提交的状态
    after = json.loads(state_path.read_text())
    result = {
        "role": "B",
        "before_b_read": observed,
        "b_view_status": b_view.status.value,
        "b_view_members": [m.status.value for m in b_view.members],
        "file_after_b_read": {
            "status": after.get("status"),
            "members": [m["status"] for m in after.get("members", [])],
            "rev": after.get("rev"),
        },
        # BASE 判定：b_view_status != committing ⇒ 误复位复现
        "false_reset_reproduced": b_view.status.value != "committing",
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    (root / "b_done.flag").write_text(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
