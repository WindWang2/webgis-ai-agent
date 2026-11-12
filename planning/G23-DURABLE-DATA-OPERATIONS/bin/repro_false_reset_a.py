"""G23 P0 复现 — 副本 A：创建批次并启动后台提交。

两个真实进程验证（任务书 P0）：副本 A 的 `_committing_ids` 只在 A 进程内存；
副本 B 读批次时会误判"无在飞任务"。本脚本是 A 侧。

用法（由 run_repro.sh 驱动，不单独执行）：
    G23_ROOT=<tmpdir> python repro_false_reset_a.py <batch_id>
"""
from __future__ import annotations

import asyncio
import io
import json
import os
import sys
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
    (root / "data").mkdir(parents=True, exist_ok=True)

    from app.core.database import init_db

    init_db()  # 生成 analysis_tasks 等表（G23 durable job 事实源）

    from app.services.data_import.service import get_data_import_service

    session_id = "sess-g23-repro"
    batch_file = root / "batch_id.txt"

    # 350k 行 CSV（< COMMIT_ROW_LIMIT 500k）：纯 Python 解释耗时足以撑开复现窗口
    n = 350_000
    buf = io.StringIO()
    buf.write("id,name,value\n")
    for i in range(n):
        buf.write(f"{i},row{i},{i * 0.5:.2f}\n")
    csv_bytes = buf.getvalue().encode()

    svc = get_data_import_service()
    state = await svc.create_batch(
        session_id, "g23-repro-user", [("g23_repro.csv", csv_bytes)]
    )
    batch_id = state.batch_id
    batch_file.write_text(batch_id)

    member_key = state.members[0].member_key
    await svc.save_plan(
        batch_id, session_id, {member_key: {"mode": "table"}},
        selected=[member_key],
    )

    out = await svc.start_commit(batch_id, session_id, background=True)
    # 通知 B：批次已进入 COMMITTING
    (root / "ready.flag").write_text(batch_id)

    # 等待提交完成，输出终态与收据
    task = svc._commit_tasks.get(batch_id)
    if task is not None:
        summary = await asyncio.wait_for(asyncio.shield(task), timeout=300)
    else:  # background=False 回落（不应发生）
        summary = out
    final = await svc.get_batch(batch_id, session_id)
    print(
        json.dumps(
            {
                "role": "A",
                "batch_id": batch_id,
                "submit_out": out,
                "final_status": final.status.value,
                "member_statuses": [m.status.value for m in final.members],
                "receipt_statuses": {
                    k: r.status for k, r in final.receipts.items()
                },
                "summary_status": summary.get("status"),
            },
            ensure_ascii=False,
            default=str,
        ),
        flush=True,
    )
    (root / "a_done.flag").write_text("done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
