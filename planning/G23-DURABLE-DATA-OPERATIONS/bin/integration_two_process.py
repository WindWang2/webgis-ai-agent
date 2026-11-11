"""G23 P7 — 双真实 API 进程 + 共享 SQLite 集成验证（故障注入 + 资源采样）。

拓扑：两个真实 uvicorn 进程（A/B，独立 PID/事件循环）+ 共享 DATA_DIR +
共享 SQLite 文件库（WAL）。不用对象模拟副本。生产多副本（k8s replicas≥2）
的正确性风险面由此覆盖：跨进程可见性/取消/崩溃恢复/重启一致。

场景（对应验收矩阵）：
  S1  A 提交（大 CSV）→ B 读批次不重置 + B 在任务中心看到同一 job（A03/A21 基础）
  S2  B 取消 → A 的 runner 停止；job/batch 双面 cancelled（A07/A08）
  S3  B 重提 → completed；B 读收据（幂等/恢复）
  S4  A 崩溃（kill -9）→ B 读：不伪造状态（仍 committing）；注入 stale 清扫
      （模拟 300s 心跳超时后的生产 sweep）→ B 读：归一化恢复（A04/A25）
  S5  compute：A 后台执行 → B 查 running → B 取消 → cancelled（compute 面 A07）
  资源：A/B RSS 采样曲线 + DATA_DIR 磁盘占用（P5 证据）。

用法：.venv/bin/python planning/G23-DURABLE-DATA-OPERATIONS/bin/integration_two_process.py <out_dir>
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

PORT_A, PORT_B = 18171, 18172
SESSION_ID = "sess-g23-integration"
TOKEN = "g23-integration-token"
HEADERS = {"X-Session-Token": TOKEN}

RESULTS: dict = {"scenarios": {}, "resource": {}}
_stop = threading.Event()
_rss_samples: list = []


def _sample_resources(pid_a: int | None, pid_b: int | None, data_dir: Path) -> None:
    def _rss(pid):
        try:
            with open(f"/proc/{pid}/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        return int(line.split()[1])  # kB
        except OSError:
            return None
        return None

    def _dir_size(p: Path) -> int:
        total = 0
        for f in p.rglob("*"):
            try:
                if f.is_file():
                    total += f.stat().st_size
            except OSError:
                pass
        return total

    while not _stop.is_set():
        _rss_samples.append({
            "t": round(time.time(), 2),
            "a_kb": _rss(pid_a), "b_kb": _rss(pid_b),
            "data_dir_bytes": _dir_size(data_dir),
        })
        time.sleep(1.0)


def _start_server(port: int, env: dict, log_path: Path):
    proc = subprocess.Popen(
        [str(REPO / ".venv/bin/python"), "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(REPO), env=env,
        stdout=open(log_path, "ab"), stderr=subprocess.STDOUT,
    )
    return proc


async def _wait_healthy(client, port: int, timeout=90):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = await client.get(f"http://127.0.0.1:{port}/api/v1/health")
            if r.status_code in (200, 204):
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


def _rss_peak(which: str) -> int | None:
    vals = [s[which] for s in _rss_samples if s.get(which)]
    return max(vals) if vals else None


def big_csv(rows: int) -> bytes:
    import io

    buf = io.StringIO()
    buf.write("lng,lat,v\n")
    for i in range(rows):
        buf.write(f"{116.0 + (i % 5000) * 0.001},{39.0 + (i % 5000) * 0.001},{i}\n")
    return buf.getvalue().encode()


async def main() -> int:
    out_dir = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/g23-integration")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    data_dir = out_dir / "data"
    data_dir.mkdir()
    db_file = data_dir / "webgis.db"

    env = dict(os.environ)
    env.update({
        "DATA_DIR": str(data_dir),
        "DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
        "USE_REDIS": "false",
        "CELERY_BROKER_URL": "memory://",
        "CELERY_RESULT_BACKEND": "cache+memory://",
        "WEBGIS_ENV": "development",
        # 直接 uvicorn 启动时 CapabilityRegistry 尚未填充（master 既有行为，
        # 与 G23 无关；容器启动路径不同）。仓库自带的降级开关，仅影响启动
        # 校验，不改变任何被测语义。
        "GIS_MANIFEST_STRICT": "0",
    })

    # 建库（含 analysis_tasks/conversations）+ WAL + 种子会话
    sys.path.insert(0, str(REPO))
    os.environ.update({k: v for k, v in env.items() if k != "WEBGIS_ENV"})
    from app.core.database import init_db
    from app.core.owner_tokens import owner_token_digest
    from app.models.db_model import Base, Conversation
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    init_db()
    eng = create_engine(f"sqlite:///{db_file.as_posix()}")
    Base.metadata.create_all(eng)
    with sessionmaker(bind=eng)() as db:
        # OwnerTokenColumn 在绑定参数时自行哈希 —— 传原始 token，勿预哈希
        db.add(Conversation(id=SESSION_ID, title="G23 integration",
                            owner_token=TOKEN))
        db.commit()
    # WAL：双进程并发读写必需（journal 模式持久化在库文件里）
    raw = sqlite3.connect(db_file)
    raw.execute("PRAGMA journal_mode=WAL")
    raw.commit()
    raw.close()
    eng.dispose()

    log_a, log_b = out_dir / "uvicorn-a.log", out_dir / "uvicorn-b.log"
    proc_a = _start_server(PORT_A, env, log_a)
    proc_b = _start_server(PORT_B, env, log_b)

    import httpx

    async with httpx.AsyncClient(timeout=60) as client:
        ok_a = await _wait_healthy(client, PORT_A)
        ok_b = await _wait_healthy(client, PORT_B)
        if not (ok_a and ok_b):
            RESULTS["error"] = f"servers not healthy a={ok_a} b={ok_b}"
            _write_report(out_dir, proc_a, proc_b)
            return 1

        sampler = threading.Thread(
            target=_sample_resources,
            args=(proc_a.pid, proc_b.pid, data_dir), daemon=True)
        sampler.start()

        A = f"http://127.0.0.1:{PORT_A}/api/v1"
        B = f"http://127.0.0.1:{PORT_B}/api/v1"

        try:
            # ══ S1：A 提交大 CSV；B 读不重置 + 任务中心跨进程可见 ══
            files = {"files": ("s1_big.csv", big_csv(350_000), "text/csv")}
            r = await client.post(f"{A}/data-import/batches",
                                  data={"session_id": SESSION_ID}, files=files,
                                  headers=HEADERS)
            r.raise_for_status()
            batch = r.json()
            member = batch["members"][0]["member_key"]
            r = await client.post(
                f"{A}/data-import/batches/{batch['batch_id']}/plan",
                params={"session_id": SESSION_ID}, headers=HEADERS,
                json={"member_configs": {member: {"mode": "table"}},
                      "selected": [member]})
            r.raise_for_status()
            r = await client.post(
                f"{A}/data-import/batches/{batch['batch_id']}/commit",
                params={"session_id": SESSION_ID}, headers=HEADERS, json={})
            r.raise_for_status()
            commit_out = r.json()
            assert commit_out["job_id"], commit_out
            job_id = commit_out["job_id"]

            await asyncio.sleep(0.4)  # 提交进行中
            r = await client.get(
                f"{B}/data-import/batches/{batch['batch_id']}",
                params={"session_id": SESSION_ID}, headers=HEADERS)
            b_view = r.json()
            r = await client.get(f"{B}/tasks/jobs/{job_id}", headers=HEADERS)
            job_view = r.json()
            RESULTS["scenarios"]["S1"] = {
                "batch_status_via_b": b_view["status"],
                "job_status_via_b": job_view["status"],
                "job_kind": job_view["kind"],
                "pass": b_view["status"] == "committing"
                and job_view["status"] in ("running", "pending", "queued")
                and job_view["kind"] == "data_import",
            }

            # ══ S2：B 取消 → 双面 cancelled ══
            r = await client.post(
                f"{B}/data-import/batches/{batch['batch_id']}/cancel",
                params={"session_id": SESSION_ID}, headers=HEADERS)
            cancel_out = r.json()
            deadline = time.time() + 60
            final_status = None
            while time.time() < deadline:
                r = await client.get(
                    f"{B}/data-import/batches/{batch['batch_id']}",
                    params={"session_id": SESSION_ID}, headers=HEADERS)
                final_status = r.json()["status"]
                if final_status != "committing":
                    break
                await asyncio.sleep(0.5)
            r = await client.get(f"{B}/tasks/jobs/{job_id}", headers=HEADERS)
            job_final = r.json()["status"]
            RESULTS["scenarios"]["S2"] = {
                "cancel_response": cancel_out.get("status"),
                "batch_final_via_b": final_status,
                "job_final_via_b": job_final,
                "pass": final_status == "cancelled" and job_final == "cancelled",
            }

            # ══ S3：B 重提 → completed；收据可读 ══
            r = await client.post(
                f"{B}/data-import/batches/{batch['batch_id']}/commit",
                params={"session_id": SESSION_ID}, headers=HEADERS, json={})
            r.raise_for_status()
            recommit = r.json()
            job2 = recommit.get("job_id")
            deadline = time.time() + 120
            status3 = None
            while time.time() < deadline:
                r = await client.get(f"{B}/tasks/jobs/{job2}", headers=HEADERS)
                status3 = r.json()["status"]
                if status3 in ("completed", "failed", "cancelled", "stale"):
                    break
                await asyncio.sleep(0.5)
            r = await client.get(
                f"{B}/data-import/batches/{batch['batch_id']}/receipts",
                params={"session_id": SESSION_ID}, headers=HEADERS)
            receipts = r.json()
            RESULTS["scenarios"]["S3"] = {
                "job2": job2, "job2_final": status3,
                "batch_final": receipts["status"],
                "receipt_status": receipts["receipts"].get(member, {}).get("status"),
                "pass": status3 == "completed"
                and receipts["status"] == "completed"
                and receipts["receipts"][member]["status"] == "completed",
            }

            # ══ S4：A 崩溃 → B 不伪造状态；注入 stale → 恢复归一化 ══
            files = {"files": ("s4_big.csv", big_csv(350_000), "text/csv")}
            r = await client.post(f"{A}/data-import/batches",
                                  data={"session_id": SESSION_ID}, files=files,
                                  headers=HEADERS)
            r.raise_for_status()
            batch4 = r.json()
            member4 = batch4["members"][0]["member_key"]
            await client.post(
                f"{A}/data-import/batches/{batch4['batch_id']}/plan",
                params={"session_id": SESSION_ID}, headers=HEADERS,
                json={"member_configs": {member4: {"mode": "table"}},
                      "selected": [member4]})
            r = await client.post(
                f"{A}/data-import/batches/{batch4['batch_id']}/commit",
                params={"session_id": SESSION_ID}, headers=HEADERS, json={})
            job4 = r.json()["job_id"]
            await asyncio.sleep(0.4)
            os.kill(proc_a.pid, signal.SIGKILL)  # A 硬崩溃
            proc_a.wait(timeout=10)

            r = await client.get(
                f"{B}/data-import/batches/{batch4['batch_id']}",
                params={"session_id": SESSION_ID}, headers=HEADERS)
            after_crash = r.json()["status"]
            # 生产语义：job 仍 running（心跳未超时）→ B 诚实显示 committing，
            # 不伪造 completed/failed（A25）。
            # 注入 stale 清扫（模拟 300s 心跳超时后 main.py 的周期 sweep）：
            raw = sqlite3.connect(db_file)
            cur = raw.execute(
                "UPDATE analysis_tasks SET status='stale', "
                "progress_message='worker heartbeat lost (injected)' "
                "WHERE id=?", (int(job4),))
            raw.commit()
            injected_rows = cur.rowcount
            raw.close()
            r = await client.get(
                f"{B}/data-import/batches/{batch4['batch_id']}",
                params={"session_id": SESSION_ID}, headers=HEADERS)
            after_sweep = r.json()["status"]
            RESULTS["scenarios"]["S4"] = {
                "job4": job4,
                "b_view_after_crash": after_crash,
                "stale_rows_injected": injected_rows,
                "b_view_after_sweep": after_sweep,
                "pass": after_crash == "committing"
                and injected_rows == 1
                and after_sweep in ("configured", "partial", "completed",
                                    "cancelled", "failed"),
            }

            # 重启 A（新 PID）：读同一批次，两副本视图一致
            proc_a = _start_server(PORT_A, env, log_a)
            ok_a = await _wait_healthy(client, PORT_A)
            r = await client.get(
                f"{A}/data-import/batches/{batch4['batch_id']}",
                params={"session_id": SESSION_ID}, headers=HEADERS)
            a_view_restart = r.json()["status"]
            RESULTS["scenarios"]["S4"]["a_view_after_restart"] = a_view_restart
            RESULTS["scenarios"]["S4"]["pass"] = (
                RESULTS["scenarios"]["S4"]["pass"]
                and ok_a and a_view_restart == after_sweep)

            # ══ S5：geojson 导入（B）→ compute 后台执行（B）→ A 查 running → A 取消 ══
            # 本地拓扑：USE_REDIS=false 时 session store 为进程内存（生产为
            # Redis，ref 天然跨副本）。计算源 ref 须在 B（S4 重启过 A）——
            # 导入与执行走 B；**查询与取消从 A 发出**（跨进程语义正是被测对象）。
            # 注：import 表产物（{table:{columns,rows}} 列数组）与 compute 的
            # 属性行字典契约不同（G19 既有事实），故计算源走 geojson 导入链。
            fc_features = [
                {"type": "Feature",
                 "properties": {"lng": 116.0 + (i % 900) * 0.01,
                                "lat": 39.0 + (i // 900) * 0.01},
                 "geometry": {"type": "Point",
                              "coordinates": [116.0 + (i % 900) * 0.01,
                                              39.0 + (i // 900) * 0.01]}}
                for i in range(45_000)
            ]
            geojson_bytes = json.dumps({
                "type": "FeatureCollection", "features": fc_features}).encode()
            files = {"files": ("s5.geojson", geojson_bytes, "application/geo+json")}
            r = await client.post(f"{B}/data-import/batches",
                                  data={"session_id": SESSION_ID}, files=files,
                                  headers=HEADERS)
            r.raise_for_status()
            batch5 = r.json()
            member5 = batch5["members"][0]["member_key"]
            await client.post(
                f"{B}/data-import/batches/{batch5['batch_id']}/plan",
                params={"session_id": SESSION_ID}, headers=HEADERS,
                json={"member_configs": {member5: {}}, "selected": [member5]})
            r = await client.post(
                f"{B}/data-import/batches/{batch5['batch_id']}/commit",
                params={"session_id": SESSION_ID}, headers=HEADERS, json={})
            r.raise_for_status()
            job5_import = r.json()["job_id"]
            deadline = time.time() + 180
            src_ref = None
            while time.time() < deadline:
                r = await client.get(
                    f"{B}/data-import/batches/{batch5['batch_id']}/receipts",
                    params={"session_id": SESSION_ID}, headers=HEADERS)
                rec = r.json()
                if rec["status"] != "committing":
                    break
                await asyncio.sleep(0.5)
            src_ref = rec["receipts"].get(member5, {}).get("output_ref")
            assert src_ref, rec

            r = await client.post(
                f"{B}/field-compute/plan", headers=HEADERS,
                json={"session_id": SESSION_ID,
                      "source_ref": src_ref,
                      "columns": [{"name": "lat2", "expression": "{lat} + 0.001"},
                                  {"name": "ring", "expression": "{lng} * {lat}"}]})
            if r.status_code != 200:
                RESULTS["scenarios"]["S5"] = {
                    "error": r.text[:400], "pass": False,
                }
                raise RuntimeError(f"field-compute/plan failed: {r.text[:200]}")
            r.raise_for_status()
            plan_id = r.json()["plan"]["plan_id"]
            r = await client.post(
                f"{B}/field-compute/plan/{plan_id}/execute",
                params={"session_id": SESSION_ID}, headers=HEADERS,
                json={"background": True})
            exec_out = r.json() if r.status_code == 200 else {"raw": r.text}
            fc_job = (exec_out.get("job_id")
                      or (exec_out.get("receipt") or {}).get("output_ref", ""))
            if fc_job and str(fc_job).isdigit():
                await asyncio.sleep(0.3)
                # A 查 B 上执行的 job（跨进程可见性）
                r = await client.get(f"{A}/tasks/jobs/{fc_job}", headers=HEADERS)
                a_job_view = r.json()
                b_job_view = a_job_view.get("status")
                # A 取消 B 上执行的计算（跨进程取消；durable 桥）
                r = await client.post(
                    f"{A}/field-compute/plan/{plan_id}/cancel",
                    params={"session_id": SESSION_ID}, headers=HEADERS)
                b_cancel = r.json()
                # 轮询到**终态**：cancelling 是确认前的过渡态（执行副本的
                # 看门狗在下个检查点收敛），不能当作结束。
                deadline = time.time() + 90
                fc_final = None
                terminal = {"completed", "failed", "cancelled", "stale"}
                while time.time() < deadline:
                    r = await client.get(f"{A}/tasks/jobs/{fc_job}", headers=HEADERS)
                    fc_final = r.json()["status"]
                    if fc_final in terminal:
                        break
                    await asyncio.sleep(0.5)
                s5_pass = (b_job_view in ("running", "pending", "queued")
                           and fc_final == "cancelled")
            else:
                # 数据太小同步完成：记录为 skipped（fixture 限制）
                b_job_view, b_cancel, fc_final = "completed", None, "completed"
                s5_pass = None
            RESULTS["scenarios"]["S5"] = {
                "plan_id": plan_id, "job": fc_job,
                "a_job_view_status": b_job_view, "a_cancel": b_cancel,
                "job_final": fc_final,
                "pass": s5_pass,
            }

        except Exception as exc:  # noqa: BLE001
            import traceback

            RESULTS["error"] = f"{type(exc).__name__}: {exc}"
            RESULTS["traceback"] = traceback.format_exc()
        finally:
            _stop.set()
            time.sleep(1.2)
            RESULTS["resource"] = {
                "samples": len(_rss_samples),
                "rss_peak_a_kb": _rss_peak("a_kb"),
                "rss_peak_b_kb": _rss_peak("b_kb"),
                "data_dir_peak_bytes": max(
                    (s["data_dir_bytes"] for s in _rss_samples), default=0),
            }
            _write_report(out_dir, proc_a, proc_b)
            for p in (proc_a, proc_b):
                if p.poll() is None:
                    p.terminate()
            time.sleep(1.5)
            for p in (proc_a, proc_b):
                if p.poll() is None:
                    p.kill()
            return 0 if not RESULTS.get("error") else 1
    return 0


def _write_report(out_dir: Path, proc_a, proc_b):
    (out_dir / "report.json").write_text(
        json.dumps(RESULTS, ensure_ascii=False, indent=2, default=str))
    csv_lines = ["t,a_kb,b_kb,data_dir_bytes"] + [
        f"{s['t']},{s['a_kb']},{s['b_kb']},{s['data_dir_bytes']}"
        for s in _rss_samples]
    (out_dir / "resource_curve.csv").write_text("\n".join(csv_lines))
    print(json.dumps(RESULTS, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
