"""#312 live verification: OSM seam through ProviderHealthTracker.

External endpoints are blocked from this environment (Nominatim unreachable,
Overpass public mirror 504 upstream) — so the SUCCESS path is exercised against
a local mock server speaking the real protocol, through the REAL seam code
(aiohttp via get_shared_client, JSON parsing, business checkers, tracking).
The DEGRADATION path uses a real circuit-breaker state (no network needed).
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiohttp import web

from app.core.config import settings
from app.services.provider_health import (
    ProviderHealthTracker,
    health_tracker,
    tracked_provider_get,
)


async def handle_overpass(request: web.Request) -> web.Response:
    body = await request.post()
    assert "data" in body, "Overpass 查询体必须经 data= 提交"
    return web.json_response({"elements": [{"type": "node", "lat": 39.9, "lon": 116.4, "tags": {"name": "TestPOI"}}]})


async def handle_nominatim_search(request: web.Request) -> web.Response:
    q = request.query.get("q", "")
    assert request.query.get("format") == "json"
    return web.json_response([
        {"display_name": f"{q}, China", "lat": "39.9", "lon": "116.4",
         "importance": "0.8", "type": "city",
         "boundingbox": ["39.7", "40.1", "116.2", "116.6"]},
    ])


async def handle_nominatim_reverse(request: web.Request) -> web.Response:
    return web.json_response({"display_name": "Beijing, China", "lat": "39.9", "lon": "116.4", "address": {"city": "Beijing"}})


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_post("/overpass", handle_overpass)
    app.router.add_get("/nominatim/search", handle_nominatim_search)
    app.router.add_get("/nominatim/reverse", handle_nominatim_reverse)
    return app


async def main() -> None:
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    print(f"[mock] OSM mock server on {base}")

    overpass_url = f"{base}/overpass"
    nominatim_url = f"{base}/nominatim/search"
    reverse_url = f"{base}/nominatim/reverse"
    settings.OVERPASS_API_URL = overpass_url
    settings.NOMINATIM_URL = nominatim_url

    results = {"pass": 0, "fail": 0}

    def check(name: str, cond: bool, detail: str = "") -> None:
        results["pass" if cond else "fail"] += 1
        print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")

    # 1) seam 成功路径:真实 aiohttp GET(POST 分支在单测已覆盖,此处也跑一次真实 POST)
    data = await tracked_provider_get("overpass", overpass_url, {}, method="POST", data={"data": "[out:json];node(1);out;"}, timeout=10)
    check("seam POST 成功路径(本地 mock)", "elements" in data and data["elements"][0]["tags"]["name"] == "TestPOI", f"-> {list(data.keys())}")

    data = await tracked_provider_get("nominatim", nominatim_url, {"q": "Beijing", "format": "json", "limit": 5, "accept-language": "zh"}, timeout=10)
    check("seam GET 成功路径(本地 mock)", isinstance(data, list) and data[0]["display_name"] == "Beijing, China", f"-> {len(data)} results")

    # 2) 业务校验器真实生效
    bad = await tracked_provider_get("nominatim", nominatim_url, {"q": "x", "format": "json"}, timeout=10, business_checker=lambda d: (False, "模拟业务拒绝"))
    check("业务校验器拒绝→error dict + 计入错误", "error" in bad and "模拟业务拒绝" in bad["error"])

    # 3) 熔断降级:真实 tracker 打开熔断后,调用立即降级、不发起网络
    tracker = ProviderHealthTracker(error_threshold=2, recovery_seconds=300)
    await tracker.record_error("overpass", Exception("boom"))
    await tracker.record_error("overpass", Exception("boom"))
    snap = await tracker.snapshot()
    check("熔断已打开", snap["overpass"]["circuit_open"] is True)
    degraded = await tracked_provider_get("overpass", overpass_url, {}, method="POST", data={"data": "x"}, timeout=10, tracker=tracker)
    check("熔断打开→立即降级 error dict", "error" in degraded and "暂时不可用" in degraded["error"], degraded["error"][:30])

    # 4) health snapshot 覆盖 OSM 两家
    snap = await health_tracker.snapshot()
    check("health snapshot 含 overpass", "overpass" in snap)
    check("health snapshot 含 nominatim", "nominatim" in snap)

    await runner.cleanup()
    from app.core.network import close_shared_client
    await close_shared_client()
    print(f"\nRESULT: {results['pass']} passed / {results['fail']} failed")
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
