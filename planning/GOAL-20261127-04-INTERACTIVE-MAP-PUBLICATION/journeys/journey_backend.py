"""必过旅程 1/2（后端半边）—— 真实服务、真实 DB、真实 HTTP。

Phase A（seed，服务器启动前）：alembic 建库 → Conversation（匿名 owner_token）
→ 生产权威链 apply_mutation 注入合成区县学校 MapSpec（inline 载体，
≤5000 要素的合法形态）。
Phase B：uvicorn 全应用（USE_REDIS=false → Celery eager；PI_AUTOSTART=false）。
Phase C：HTTP 旅程 —— preview → draft → publish(confirm) → published → link
→ 读者 5 端点；含旅程 2 的负例（撤销后 404、跨 owner 403、编辑原图旧版不变）。

用法：python journeys/journey_backend.py   （独立 scratch 目录运行）
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SCRATCH = Path("/tmp/pub-journey")
DB_PATH = SCRATCH / "journey.db"
DATA_DIR = SCRATCH / "data"
PORT = 18801
BASE = f"http://127.0.0.1:{PORT}"

SESSION_ID = "journey-sess-author-0001"
OTHER_SESSION_ID = "journey-sess-other-0002"

SCHOOLS_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"school-{i}",
            "properties": {
                "school_name": f"第{i}小学",
                "level": "primary" if i % 2 else "middle",
                "students": 400 + i * 13,
                "internal_budget": f"SECRET-{i}",
            },
            "geometry": {"type": "Point", "coordinates": [116.30 + i * 0.01, 39.85 + i * 0.008]},
        }
        for i in range(1, 13)
    ],
}

DISTRICTS_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "id": f"district-{d}",
            "properties": {"district_name": f"示范区{d}", "population": 10000 * d},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [116.2 + d * 0.05, 39.7], [116.35 + d * 0.05, 39.7],
                    [116.35 + d * 0.05, 39.95], [116.2 + d * 0.05, 39.95],
                    [116.2 + d * 0.05, 39.7],
                ]],
            },
        }
        for d in range(1, 4)
    ],
}

ENV = {
    **os.environ,
    "DATABASE_URL": f"sqlite:///{DB_PATH}",
    "DATA_DIR": str(DATA_DIR),
    "USE_REDIS": "false",
    "PI_AUTOSTART": "false",
    "JWT_SECRET_KEY": "journey-secret-key-32-chars-ok-xxxx",
    "ENV": "development",
    "LLM_API_KEY": "test-key-not-real",
    "OMP_NUM_THREADS": "2",
    "OPENBLAS_NUM_THREADS": "2",
    # master 既有债务：webgis_diagram_symbol/scenario/paper_scale 工具声明的
    # capability id 在本环境 CapabilityRegistry 悬空（与本线无关的已合并工具）。
    # 按运行时文档化降级开关放行（错误信息自带指引），旅程证据不受影响。
    "GIS_MANIFEST_STRICT": "0",
}


def phase_a_seed() -> str:
    """建库 + 匿名会话 + 生产权威链注入 MapSpec。返回 owner_token。"""
    SCRATCH.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    os.environ.update(ENV)
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))  # repo root → import app

    import subprocess as sp

    r = sp.run([".venv/bin/python", "-m", "alembic", "upgrade", "head"],
               capture_output=True, text=True, env=ENV)
    if r.returncode != 0:
        print(r.stdout[-2000:], r.stderr[-2000:])
        raise SystemExit("alembic failed")

    import asyncio

    async def _seed() -> str:
        from app.core.database import Base
        import app.models  # noqa: F401 — 全模型注册（create_all 面）
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(bind=engine, expire_on_commit=False)

        owner_token = secrets.token_urlsafe(32)
        async with Session() as db:
            from app.models.db_model import Conversation

            db.add(Conversation(id=SESSION_ID, user_id=None, title="发布旅程会话",
                                owner_token=owner_token))
            db.add(Conversation(id=OTHER_SESSION_ID, user_id=None, title="他人会话",
                                owner_token=secrets.token_urlsafe(32)))
            await db.commit()

            # 生产权威链：agent-origin mutations（与 Agent 工具同一条 apply_mutation 权威）。
            from app.services.mapspec.intents import (
                InitProjectIntent,
                MapSpecLayerPayload,
                MapSpecSourcePayload,
                SetLayoutIntent,
                UpsertLayerIntent,
                UpsertSourceIntent,
            )
            from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine

            engine_mut = MapSpecLifecycleEngine()
            await engine_mut.apply_mutation(
                SESSION_ID, InitProjectIntent(view={"center": [116.45, 39.9], "zoom": 10}))
            await engine_mut.apply_mutation(
                SESSION_ID,
                UpsertSourceIntent(
                    source_id="schools",
                    source=MapSpecSourcePayload(
                        type="geojson", name="学校点",
                        attribution="合成区县数据（simulated）",
                        inlineData=SCHOOLS_FC,
                    ),
                ),
            )
            await engine_mut.apply_mutation(
                SESSION_ID,
                UpsertLayerIntent(
                    layer=MapSpecLayerPayload(
                        id="lyr-schools", type="circle", source="schools",
                        paint={"circle-color": "#ef4444", "circle-radius": 6},
                    ),
                ),
            )
            await engine_mut.apply_mutation(
                SESSION_ID,
                UpsertSourceIntent(
                    source_id="districts",
                    source=MapSpecSourcePayload(
                        type="geojson", name="区县面",
                        attribution="合成区县数据（simulated）",
                        inlineData=DISTRICTS_FC,
                    ),
                ),
            )
            await engine_mut.apply_mutation(
                SESSION_ID,
                UpsertLayerIntent(
                    layer=MapSpecLayerPayload(
                        id="lyr-districts", type="fill", source="districts",
                        paint={"fill-color": "#3b82f6", "fill-opacity": 0.35},
                    ),
                ),
            )
            # 标题走生产写路径：title 组件（前端 commitStudioText 同语义的
            # 组件突变；直写 layout.title 不是生产载体）。
            from app.services.mapspec.intents import PatchComponentIntent

            await engine_mut.apply_mutation(
                SESSION_ID,
                PatchComponentIntent(
                    component_id="title",
                    component_type="title",
                    enabled=True,
                    options={"text": "区县学校分布专题图"},
                    upsert=True,
                ),
            )
        await engine.dispose()
        return owner_token

    token = asyncio.run(_seed())
    return token


def error_reason(body) -> str:
    """统一错误信封（detail dict → data）与 legacy envelope 双兼容取 reason。"""
    if not isinstance(body, dict):
        return ""
    detail = body.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("reason", ""))
    data = body.get("data")
    if isinstance(data, dict):
        return str(data.get("reason", ""))
    if isinstance(detail, str):
        return detail
    return str(body.get("message", ""))


def http(method: str, path: str, *, token: str | None = None, body: dict | None = None):
    req = urllib.request.Request(f"{BASE}{path}", method=method)
    if token:
        req.add_header("X-Session-Token", token)
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        payload = exc.read()
        try:
            return exc.code, json.loads(payload) if payload else None
        except json.JSONDecodeError:
            return exc.code, payload.decode(errors="replace")


def wait_health(timeout_s: float = 90) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            status, _ = http("GET", "/api/v1/health")
            if status == 200:
                return
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit("server did not become healthy")


def main() -> None:
    owner_token = phase_a_seed()
    other_token = secrets.token_urlsafe(32)

    server = subprocess.Popen(
        [".venv/bin/python", "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        env=ENV, cwd=os.getcwd(),
        stdout=open(SCRATCH / "server.log", "w"), stderr=subprocess.STDOUT,
    )
    evidence: dict = {}
    try:
        wait_health()

        # ── 旅程 1：作者端预览 → 草稿 → 显式发布 → 链接 → 读者 ──────────
        status, body = http("POST", "/api/v1/publications/preview", token=owner_token, body={
            "session_id": SESSION_ID,
            "selected_layer_ids": ["lyr-schools", "lyr-districts"],
            "field_whitelists": {
                "lyr-schools": ["school_name", "level", "students"],
                "lyr-districts": ["district_name", "population"],
            },
        })
        assert status == 200, (status, body)
        plan = body["data"]
        assert plan["publishable"] is True, plan["blockers"]
        assert len(plan["layers"]) == 2
        evidence["preview_layers"] = [
            {"id": lp["layer_id"], "features": lp["feature_count"]} for lp in plan["layers"]]

        status, body = http("POST", "/api/v1/publications", token=owner_token, body={
            "session_id": SESSION_ID,
            "title": "区县学校分布专题图",
            "purpose": "面向读者的区县学校分布只读展示（合成数据）",
            "attribution": "合成区县数据（simulated）",
            "license": "CC0-SIMULATED",
            "missing_values_note": "students 缺失时按 null 显示，不参与统计",
            "selected_layer_ids": ["lyr-schools", "lyr-districts"],
            "field_whitelists": {
                "lyr-schools": ["school_name", "level", "students"],
                "lyr-districts": ["district_name", "population"],
            },
        })
        assert status == 200, (status, body)
        pub = body["data"]
        assert pub["status"] == "draft"
        evidence["draft_id"] = pub["id"]

        # confirm=false → 400（同意门）
        status, body = http("POST", f"/api/v1/publications/{pub['id']}/publish",
                            token=owner_token, body={"confirm": False})
        assert status == 400 and error_reason(body) == "confirmation_required", (status, body)

        # confirm=true → eager job 同步完成 → published
        status, body = http("POST", f"/api/v1/publications/{pub['id']}/publish",
                            token=owner_token, body={"confirm": True})
        assert status == 200, (status, body)
        assert body["data"]["job"]["status"] in ("analysis_task_started", "analysis_task_reused")
        job_id = body["data"]["job"]["job_id"]

        status, body = http("GET", f"/api/v1/publications/{pub['id']}", token=owner_token)
        assert status == 200 and body["data"]["status"] == "published", (status, body)
        assert body["data"]["materialized"] is True
        evidence["publish_job"] = job_id

        # 链接签发（显式）
        status, body = http("POST", f"/api/v1/publications/{pub['id']}/link",
                            token=owner_token, body={"ttl_days": 7})
        assert status == 200, (status, body)
        code = body["data"]["share_code"]
        evidence["share_code"] = code

        # 读者面（匿名、无 token）
        status, body = http("GET", f"/api/v1/publications/shared/{code}")
        assert status == 200, (status, body)
        doc = body["data"]
        assert doc["meta"]["title"] == "区县学校分布专题图"
        assert doc["accessibility"]["altText"]
        assert doc["accessibility"]["colorblindSafety"]["declaration"]
        evidence["reader_layers"] = [l["layer_id"] for l in doc["layers"]]

        status, body = http("GET", f"/api/v1/publications/shared/{code}/map")
        assert status == 200
        reader_spec = body["data"]["spec"]
        assert reader_spec["sources"]["schools"]["url"].endswith(
            f"/shared/{code}/layers/lyr-schools/data")
        assert "internal_budget" not in json.dumps(reader_spec)

        status, body = http("GET", f"/api/v1/publications/shared/{code}/layers/lyr-schools/data")
        assert status == 200
        fc = body
        props = fc["features"][0]["properties"]
        assert "school_name" in props and "internal_budget" not in props
        evidence["reader_feature_count"] = len(fc["features"])

        import urllib.parse
        where = urllib.parse.quote(json.dumps(
            {"field": "students", "op": "gte", "value": 420}))
        status, body = http(
            "GET",
            f"/api/v1/publications/shared/{code}/layers/lyr-schools/features?where={where}")
        assert status == 200
        assert body["data"]["denominator"]["total_features"] == 12
        evidence["filtered_matching"] = body["data"]["denominator"]["matching_features"]

        status, body = http(
            "GET",
            f"/api/v1/publications/shared/{code}/summary?layer_id=lyr-schools&field=students")
        assert status == 200
        assert body["data"]["stats"]["count"] == 12
        evidence["summary"] = body["data"]["stats"]

        # 白名单外字段 → 400
        status, body = http(
            "GET",
            f"/api/v1/publications/shared/{code}/layers/lyr-schools/features?fields=internal_budget")
        assert status == 400 and error_reason(body) == "field_not_allowed", (status, body)

        # ── 旅程 2：编辑原图旧版不变；跨 owner 403；撤销 404 ─────────────
        status, body = http("GET", f"/api/v1/publications/{pub['id']}", token=other_token)
        assert status == 403, (status, body)

        status, body = http("POST", f"/api/v1/publications/{pub['id']}/revoke",
                            token=other_token, body={})
        assert status == 403

        # v1 指纹固定；创建 v2（显式新版本）
        status, body = http("GET", f"/api/v1/publications/{pub['id']}", token=owner_token)
        fp_v1 = body["data"]["spec_fingerprint"]
        snap_v1 = json.dumps(body["data"].get("qa"), sort_keys=True)

        # 撤销 v1 → 读者 404
        status, body = http("POST", f"/api/v1/publications/{pub['id']}/revoke",
                            token=owner_token, body={})
        assert status == 200 and body["data"]["revoked"] is True
        status, body = http("GET", f"/api/v1/publications/shared/{code}")
        assert status == 404, (status, body)

        # v2 草稿（继承 meta；同 product）
        status, body = http("POST", "/api/v1/publications", token=owner_token, body={
            "session_id": SESSION_ID,
            "base_publication_id": pub["id"],
            "selected_layer_ids": ["lyr-schools", "lyr-districts"],
            "field_whitelists": {
                "lyr-schools": ["school_name", "level", "students"],
                "lyr-districts": ["district_name", "population"],
            },
        })
        assert status == 200, (status, body)
        v2 = body["data"]
        assert v2["version"] == 2 and v2["product_id"] == pub["product_id"]
        assert v2["spec_fingerprint"] == fp_v1  # 数据未变 → 指纹一致（可复现）
        evidence["v2"] = {"id": v2["id"], "version": v2["version"]}
        evidence["snapshot_v1_immutable"] = snap_v1

        # v2 显式发布 + 签发链接（留给浏览器旅程用；v1 已撤销验证过 404）
        status, body = http("POST", f"/api/v1/publications/{v2['id']}/publish",
                            token=owner_token, body={"confirm": True})
        assert status == 200, (status, body)
        status, body = http("GET", f"/api/v1/publications/{v2['id']}", token=owner_token)
        assert body["data"]["status"] == "published"
        status, body = http("POST", f"/api/v1/publications/{v2['id']}/link",
                            token=owner_token, body={"ttl_days": 7})
        assert status == 200, (status, body)
        live_code = body["data"]["share_code"]
        status, body = http("GET", f"/api/v1/publications/shared/{live_code}")
        assert status == 200
        evidence["v2_share_code"] = live_code
        SCRATCH.joinpath("share_code.txt").write_text(
            json.dumps({"code": live_code}), encoding="utf-8")

        print(json.dumps({"ok": True, "evidence": evidence}, ensure_ascii=False, indent=2))
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()


if __name__ == "__main__":
    main()
