# Chinese Map API Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Amap and Baidu Maps APIs as backend tools with automatic WGS84 ↔ GCJ-02 ↔ BD-09 coordinate transformation.

**Architecture:** New `app/utils/coord_transform.py` for pure-math coordinate conversion. New `app/tools/chinese_maps.py` with 5 tools registered via the existing `ToolRegistry` pattern. Each tool accepts a `provider` parameter and handles coordinate transformation transparently. All input/output is WGS84.

**Tech Stack:** Python 3.13, aiohttp (already in project), pure-math coordinate transforms (no new dependencies)

---

## File Structure

| File | Responsibility |
|------|---------------|
| `app/utils/coord_transform.py` (new) | WGS84 ↔ GCJ-02 ↔ BD-09 coordinate conversion functions |
| `app/tools/chinese_maps.py` (new) | 5 tools: `search_poi`, `geocode_cn`, `reverse_geocode_cn`, `plan_route`, `get_district` |
| `app/core/config.py` (modify) | Add `AMAP_API_KEY` and `BAIDU_MAP_AK` fields |
| `.env.example` (modify) | Add placeholder entries for both API keys |
| `app/api/routes/chat.py` (modify) | Import and call `register_chinese_map_tools(registry)` |
| `app/services/chat_engine.py` (modify) | Update SYSTEM_PROMPT to document new tools |
| `tests/test_coord_transform.py` (new) | Unit tests for coordinate conversion |

---

### Task 1: Coordinate Transform Module

**Files:**
- Create: `app/utils/coord_transform.py`
- Create: `tests/test_coord_transform.py`

- [ ] **Step 1: Write coordinate transform tests**

```python
# tests/test_coord_transform.py
import math
from app.utils.coord_transform import (
    wgs84_to_gcj02, gcj02_to_wgs84,
    wgs84_to_bd09, bd09_to_wgs84,
    gcj02_to_bd09, bd09_to_gcj02,
)

def test_wgs84_gcj02_roundtrip():
    """WGS84 → GCJ-02 → WGS84 should be within 2cm of original."""
    lng, lat = 116.4074, 39.9042
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    back_lng, back_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
    assert abs(back_lng - lng) < 0.000002, f"lng drift: {back_lng - lng}"
    assert abs(back_lat - lat) < 0.000002, f"lat drift: {back_lat - lat}"

def test_wgs84_bd09_roundtrip():
    """WGS84 → BD-09 → WGS84 should be within 2cm of original."""
    lng, lat = 116.4074, 39.9042
    bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
    back_lng, back_lat = bd09_to_wgs84(bd_lng, bd_lat)
    assert abs(back_lng - lng) < 0.000002, f"lng drift: {back_lng - lng}"
    assert abs(back_lat - lat) < 0.000002, f"lat drift: {back_lat - lat}"

def test_gcj02_bd09_roundtrip():
    """GCJ-02 → BD-09 → GCJ-02 should roundtrip exactly."""
    lng, lat = 116.4074, 39.9042
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    bd_lng, bd_lat = gcj02_to_bd09(gcj_lng, gcj_lat)
    back_lng, back_lat = bd09_to_gcj02(bd_lng, bd_lat)
    assert abs(back_lng - gcj_lng) < 1e-10
    assert abs(back_lat - gcj_lat) < 1e-10

def test_shanghai_coordinates():
    """Known offset for Shanghai area."""
    lng, lat = 121.4737, 31.2304
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    # GCJ-02 should be offset by roughly 400-600m from WGS84
    diff_lng = abs(gcj_lng - lng) * 111000 * math.cos(math.radians(lat))
    diff_lat = abs(gcj_lat - lat) * 111000
    assert 200 < diff_lng < 800, f"Expected ~500m offset, got {diff_lng:.0f}m lng"
    assert 200 < diff_lat < 800, f"Expected ~500m offset, got {diff_lat:.0f}m lat"

def test_out_of_china_no_transform():
    """Coordinates outside China should not be transformed."""
    lng, lat = -73.9857, 40.7484  # New York
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    assert gcj_lng == lng
    assert gcj_lat == lat
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -m pytest tests/test_coord_transform.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.utils.coord_transform'`

- [ ] **Step 3: Implement coordinate transform module**

```python
# app/utils/coord_transform.py
"""WGS84 ↔ GCJ-02 ↔ BD-09 coordinate transformation.

Pure math implementation based on Krasovsky 1940 ellipsoid.
No third-party dependencies required.
"""
import math

_A = 6378245.0
_EE = 0.00669342162296594323


def _out_of_china(lng: float, lat: float) -> bool:
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(lng: float, lat: float) -> float:
    ret = (-100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat +
           0.1 * lng * lat + 0.2 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) +
            20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) +
            40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) +
            320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(lng: float, lat: float) -> float:
    ret = (300.0 + lng + 2.0 * lat + 0.1 * lng * lng +
           0.1 * lng * lat + 0.1 * math.sqrt(abs(lng)))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) +
            20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) +
            40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) +
            300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng + dlng, lat + dlat


def gcj02_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    if _out_of_china(lng, lat):
        return lng, lat
    dlat = _transform_lat(lng - 105.0, lat - 35.0)
    dlng = _transform_lng(lng - 105.0, lat - 35.0)
    radlat = lat / 180.0 * math.pi
    magic = math.sin(radlat)
    magic = 1 - _EE * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrtmagic) * math.pi)
    dlng = (dlng * 180.0) / (_A / sqrtmagic * math.cos(radlat) * math.pi)
    return lng - dlng, lat - dlat


def gcj02_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    z = math.sqrt(lng * lng + lat * lat) + 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) + 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta) + 0.0065, z * math.sin(theta) + 0.006


def bd09_to_gcj02(lng: float, lat: float) -> tuple[float, float]:
    lng -= 0.0065
    lat -= 0.006
    z = math.sqrt(lng * lng + lat * lat) - 0.00002 * math.sin(lat * math.pi * 3000.0 / 180.0)
    theta = math.atan2(lat, lng) - 0.000003 * math.cos(lng * math.pi * 3000.0 / 180.0)
    return z * math.cos(theta), z * math.sin(theta)


def wgs84_to_bd09(lng: float, lat: float) -> tuple[float, float]:
    gcj = wgs84_to_gcj02(lng, lat)
    return gcj02_to_bd09(gcj[0], gcj[1])


def bd09_to_wgs84(lng: float, lat: float) -> tuple[float, float]:
    gcj = bd09_to_gcj02(lng, lat)
    return gcj02_to_wgs84(gcj[0], gcj[1])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -m pytest tests/test_coord_transform.py -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add app/utils/coord_transform.py tests/test_coord_transform.py
git commit -m "feat: add WGS84/GCJ-02/BD-09 coordinate transformation module"
```

---

### Task 2: Configuration — Add API Key Fields

**Files:**
- Modify: `app/core/config.py:39-51` (after Tianditu, before Sentinel Hub)
- Modify: `.env.example:25-26` (after Tianditu line)

- [ ] **Step 1: Add config fields**

In `app/core/config.py`, after the `TIANDITU_TOKEN` line (line 40), add:

```python
    # 高德地图 (Amap)
    AMAP_API_KEY: str = Field(default="", env="AMAP_API_KEY")

    # 百度地图 (Baidu Maps)
    BAIDU_MAP_AK: str = Field(default="", env="BAIDU_MAP_AK")
```

In `.env.example`, after the `TIANDITU_TOKEN=` line (line 25), add:

```env
# === Chinese Map APIs (高德/百度) ===
AMAP_API_KEY=
BAIDU_MAP_AK=
```

- [ ] **Step 2: Verify config loads**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.core.config import settings; print('AMAP:', bool(settings.AMAP_API_KEY)); print('BAIDU:', bool(settings.BAIDU_MAP_AK))"`
Expected: `AMAP: False` / `BAIDU: False` (keys not yet set, but no import errors)

- [ ] **Step 3: Commit**

```bash
git add app/core/config.py .env.example
git commit -m "feat: add AMAP_API_KEY and BAIDU_MAP_AK config fields"
```

---

### Task 3: Chinese Map Tools — Core Implementation

**Files:**
- Create: `app/tools/chinese_maps.py`

This is the largest task. The file contains 5 tools plus shared helper functions.

- [ ] **Step 1: Create the tool file with shared helpers**

```python
# app/tools/chinese_maps.py
"""高德地图/百度地图 API 工具 — POI搜索、地理编码、路径规划、行政区划查询"""
import logging
import aiohttp
from typing import Optional
from app.core.config import settings
from app.core.network import get_ssl_context, get_base_headers
from app.tools.registry import ToolRegistry, tool
from app.utils.coord_transform import (
    wgs84_to_gcj02, gcj02_to_wgs84,
    wgs84_to_bd09, bd09_to_wgs84,
)

logger = logging.getLogger(__name__)

_VALID_PROVIDERS = ("amap", "baidu")
_AMAP_BASE = "https://restapi.amap.com/v3"
_BAIDU_BASE = "https://api.map.baidu.com"


def _has_provider(provider: str) -> bool:
    if provider == "amap":
        return bool(settings.AMAP_API_KEY)
    if provider == "baidu":
        return bool(settings.BAIDU_MAP_AK)
    return False


def _provider_key(provider: str) -> str:
    if provider == "amap":
        return settings.AMAP_API_KEY
    return settings.BAIDU_MAP_AK


async def _amap_get(endpoint: str, params: dict) -> dict:
    params["key"] = settings.AMAP_API_KEY
    params["output"] = "json"
    url = f"{_AMAP_BASE}{endpoint}"
    async with aiohttp.ClientSession(headers=get_base_headers()) as session:
        async with session.get(
            url, params=params, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {"error": f"Amap API HTTP {resp.status}"}
            data = await resp.json()
            if data.get("status") != "1" and data.get("infocode") != "10000":
                return {"error": f"Amap: {data.get('info', 'unknown error')}"}
            return data


async def _baidu_get(endpoint: str, params: dict) -> dict:
    params["ak"] = settings.BAIDU_MAP_AK
    params["output"] = "json"
    url = f"{_BAIDU_BASE}{endpoint}"
    async with aiohttp.ClientSession(headers=get_base_headers()) as session:
        async with session.get(
            url, params=params, ssl=get_ssl_context(),
            proxy=settings.HTTPS_PROXY or settings.HTTP_PROXY,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {"error": f"Baidu API HTTP {resp.status}"}
            data = await resp.json()
            if data.get("status") != 0:
                return {"error": f"Baidu: {data.get('message', 'unknown error')}"}
            return data
```

- [ ] **Step 2: Add search_poi tool**

Append to the same file inside `register_chinese_map_tools`:

```python
def register_chinese_map_tools(registry: ToolRegistry):

    @tool(registry, name="search_poi",
           description="使用高德或百度地图搜索 POI（餐厅、学校、医院等），支持中文关键词和城市限定",
           param_descriptions={
               "keyword": "搜索关键词，如'火锅店'、'三甲医院'",
               "city": "城市名称，如'北京'、'上海'",
               "provider": "服务商: 'amap'(高德, 默认) 或 'baidu'(百度)",
               "limit": "返回结果数量，默认20",
           })
    async def search_poi(keyword: str, city: str = "", provider: str = "amap", limit: int = 20) -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'，收到: {provider}"}

        if _has_provider(provider):
            try:
                if provider == "amap":
                    return await _search_poi_amap(keyword, city, limit)
                else:
                    return await _search_poi_baidu(keyword, city, limit)
            except Exception as e:
                logger.warning(f"search_poi {provider} failed: {e}, trying fallback")

        # Try other provider as fallback
        other = "baidu" if provider == "amap" else "amap"
        if _has_provider(other):
            try:
                if other == "amap":
                    return await _search_poi_amap(keyword, city, limit)
                else:
                    return await _search_poi_baidu(keyword, city, limit)
            except Exception as e:
                return {"error": f"两个服务商均失败: {e}"}

        return {"error": "未配置高德或百度 API Key，请在 .env 中设置 AMAP_API_KEY 或 BAIDU_MAP_AK"}
```

Then add the provider-specific POI functions outside the register function:

```python
async def _search_poi_amap(keyword: str, city: str, limit: int) -> dict:
    params = {"keywords": keyword, "city": city, "citylimit": "true" if city else "false", "offset": str(limit)}
    data = await _amap_get("/place/text", params)
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            continue
        gcj_lng, gcj_lat = float(loc[0]), float(loc[1])
        lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "tel": p.get("tel", ""),
                "city": p.get("cityname", ""),
                "district": p.get("adname", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "amap",
    }


async def _search_poi_baidu(keyword: str, city: str, limit: int) -> dict:
    params = {"query": keyword, "region": city or "全国", "page_size": str(min(limit, 20))}
    data = await _baidu_get("/place/v2/search", params)
    if "error" in data:
        return data
    pois = data.get("results", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", {})
        bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "tel": p.get("telephone", ""),
                "city": p.get("city", ""),
                "district": p.get("area", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "baidu",
    }
```

- [ ] **Step 3: Add geocode_cn, reverse_geocode_cn, plan_route, get_district tools**

Inside the same `register_chinese_map_tools` function, add these four tools. Each follows the same pattern: validate provider → call provider-specific function → fallback to other provider → fallback to existing tools.

```python
    @tool(registry, name="geocode_cn",
           description="中文地址转坐标（高德/百度），比 Nominatim 中文地址准确率更高",
           param_descriptions={
               "address": "中文地址，如'北京市海淀区中关村'",
               "city": "限定城市，如'北京'",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def geocode_cn(address: str, city: str = "", provider: str = "amap") -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _geocode_amap(address, city)
                else:
                    return await _geocode_baidu(address, city)
            except Exception as e:
                logger.warning(f"geocode_cn {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}

    @tool(registry, name="reverse_geocode_cn",
           description="坐标转中文地址（高德/百度），返回详细地址和附近 POI",
           param_descriptions={
               "location": "WGS84 坐标 [经度, 纬度]",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def reverse_geocode_cn(location: list, provider: str = "amap") -> dict:
        if len(location) != 2:
            return {"error": "location 必须是 [经度, 纬度]"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _reverse_geocode_amap(location[0], location[1])
                else:
                    return await _reverse_geocode_baidu(location[0], location[1])
            except Exception as e:
                logger.warning(f"reverse_geocode_cn {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}

    @tool(registry, name="plan_route",
           description="路径规划（驾车/步行/骑行/公交），返回距离、时间和路线坐标",
           param_descriptions={
               "origin": "起点 WGS84 坐标 [经度, 纬度]",
               "destination": "终点 WGS84 坐标 [经度, 纬度]",
               "mode": "出行方式: 'driving'(默认), 'walking', 'cycling', 'transit'",
               "city": "城市名（公交模式必填）",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def plan_route(origin: list, destination: list, mode: str = "driving", city: str = "", provider: str = "amap") -> dict:
        if len(origin) != 2 or len(destination) != 2:
            return {"error": "origin/destination 必须是 [经度, 纬度]"}
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _route_amap(origin, destination, mode, city)
                else:
                    return await _route_baidu(origin, destination, mode, city)
            except Exception as e:
                logger.warning(f"plan_route {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key，路径规划需要 API Key"}

    @tool(registry, name="get_district",
           description="查询行政区划边界（高德/百度），返回 GeoJSON 格式",
           param_descriptions={
               "keywords": "行政区划名称，如'海淀区'、'成都市'",
               "level": "级别: 'province', 'city', 'district'",
               "provider": "服务商: 'amap'(默认) 或 'baidu'",
           })
    async def get_district(keywords: str, level: str = "district", provider: str = "amap") -> dict:
        if provider not in _VALID_PROVIDERS:
            return {"error": f"provider 必须是 'amap' 或 'baidu'"}

        for p in [provider, "baidu" if provider == "amap" else "amap"]:
            if not _has_provider(p):
                continue
            try:
                if p == "amap":
                    return await _district_amap(keywords, level)
                else:
                    return await _district_baidu(keywords, level)
            except Exception as e:
                logger.warning(f"get_district {p} failed: {e}")
        return {"error": "未配置高德或百度 API Key"}
```

- [ ] **Step 4: Add provider-specific helper functions**

These go at module level (outside `register_chinese_map_tools`), following the same pattern as `_search_poi_amap` / `_search_poi_baidu`:

```python
async def _geocode_amap(address: str, city: str) -> dict:
    params = {"address": address}
    if city:
        params["city"] = city
    data = await _amap_get("/geocode/geo", params)
    if "error" in data:
        return data
    geocodes = data.get("geocodes", [])
    if not geocodes:
        return {"results": [], "count": 0}
    results = []
    for g in geocodes:
        loc = g.get("location", "").split(",")
        if len(loc) != 2:
            continue
        gcj_lng, gcj_lat = float(loc[0]), float(loc[1])
        lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
        results.append({
            "location": [lng, lat],
            "formatted_address": g.get("formatted_address", ""),
            "province": g.get("province", ""),
            "city": g.get("city", ""),
            "district": g.get("district", ""),
            "adcode": g.get("adcode", ""),
        })
    return {"results": results, "count": len(results), "provider": "amap"}


async def _geocode_baidu(address: str, city: str) -> dict:
    params = {"address": address}
    if city:
        params["city"] = city
    data = await _baidu_get("/geocoding/v3/", params)
    if "error" in data:
        return data
    loc = data.get("result", {}).get("location", {})
    bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
    lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
    return {
        "results": [{
            "location": [lng, lat],
            "formatted_address": data.get("result", {}).get("level", ""),
            "province": "",
            "city": city,
            "district": "",
            "adcode": str(data.get("result", {}).get("cityCode", "")),
        }],
        "count": 1,
        "provider": "baidu",
    }


async def _reverse_geocode_amap(lng: float, lat: float) -> dict:
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    params = {"location": f"{gcj_lng},{gcj_lat}", "extensions": "all"}
    data = await _amap_get("/geocode/regeo", params)
    if "error" in data:
        return data
    r = data.get("regeocode", {})
    addr = r.get("addressComponent", {})
    pois = r.get("pois", [])[:5]
    return {
        "formatted_address": r.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("district", ""),
        "street": addr.get("streetNumber", {}).get("street", ""),
        "street_number": addr.get("streetNumber", {}).get("number", ""),
        "nearby_pois": [{"name": p.get("name"), "distance": p.get("distance")} for p in pois],
        "provider": "amap",
    }


async def _reverse_geocode_baidu(lng: float, lat: float) -> dict:
    bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
    params = {"location": f"{bd_lat},{bd_lng}", "extensions_poi": 1}
    data = await _baidu_get("/reverse_geocoding/v3/", params)
    if "error" in data:
        return data
    r = data.get("result", {})
    addr = r.get("addressComponent", {})
    pois = r.get("pois", [])[:5]
    return {
        "formatted_address": r.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("district", ""),
        "street": addr.get("street", ""),
        "street_number": addr.get("street_number", ""),
        "nearby_pois": [{"name": p.get("name"), "distance": p.get("distance")} for p in pois],
        "provider": "baidu",
    }


async def _route_amap(origin: list, dest: list, mode: str, city: str) -> dict:
    mode_map = {"driving": "driving", "walking": "walking", "cycling": "bicycling", "transit": "transit/integrated"}
    endpoint = mode_map.get(mode, "driving")
    o_gcj = wgs84_to_gcj02(origin[0], origin[1])
    d_gcj = wgs84_to_gcj02(dest[0], dest[1])
    params = {"origin": f"{o_gcj[0]},{o_gcj[1]}", "destination": f"{d_gcj[0]},{d_gcj[1]}"}
    if mode == "transit" and city:
        params["city"] = city
    data = await _amap_get(f"/direction/{endpoint}", params)
    if "error" in data:
        return data
    route = data.get("route", {})
    paths = route.get("paths", [])
    if not paths:
        return {"error": "未找到路线"}
    path = paths[0]
    steps_out = []
    polyline = []
    for step in path.get("steps", []):
        steps_out.append({
            "instruction": step.get("instruction", ""),
            "distance": step.get("distance", "0"),
            "duration": step.get("duration", "0"),
        })
        for loc in step.get("polyline", "").split(";"):
            parts = loc.split(",")
            if len(parts) == 2:
                gcj_lng, gcj_lat = float(parts[0]), float(parts[1])
                lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
                polyline.append([lng, lat])
    return {
        "distance_m": int(path.get("distance", 0)),
        "duration_s": int(path.get("duration", 0)),
        "polyline": polyline,
        "steps": steps_out,
        "provider": "amap",
    }


async def _route_baidu(origin: list, dest: list, mode: str, city: str) -> dict:
    mode_map = {"driving": "driving", "walking": "walking", "cycling": "riding", "transit": "transit"}
    endpoint = mode_map.get(mode, "driving")
    o_bd = wgs84_to_bd09(origin[0], origin[1])
    d_bd = wgs84_to_bd09(dest[0], dest[1])
    params = {"origin": f"{o_bd[0]},{o_bd[1]}", "destination": f"{d_bd[0]},{d_bd[1]}"}
    if mode == "transit" and city:
        params["city"] = city
    data = await _baidu_get(f"/directionlite/v1/{endpoint}", params)
    if "error" in data:
        return data
    route = data.get("result", {}).get("routes", [])
    if not route:
        return {"error": "未找到路线"}
    r = route[0]
    steps_out = []
    polyline = []
    for step in r.get("steps", []):
        steps_out.append({
            "instruction": step.get("instruction", ""),
            "distance": step.get("distance", "0"),
            "duration": step.get("duration", "0"),
        })
        for loc in step.get("path", "").split(";"):
            parts = loc.split(",")
            if len(parts) == 2:
                bd_lng, bd_lat = float(parts[0]), float(parts[1])
                lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
                polyline.append([lng, lat])
    return {
        "distance_m": int(r.get("distance", 0)),
        "duration_s": int(r.get("duration", 0)),
        "polyline": polyline,
        "steps": steps_out,
        "provider": "baidu",
    }


async def _district_amap(keywords: str, level: str) -> dict:
    params = {"keywords": keywords, "subdistrict": "1", "extensions": "base"}
    level_map = {"country": "0", "province": "1", "city": "2", "district": "3"}
    if level in level_map:
        params["subdistrict"] = level_map[level]
    data = await _amap_get("/config/district", params)
    if "error" in data:
        return data
    districts = data.get("districts", [])
    features = []
    for d in districts:
        center = d.get("center", "").split(",")
        lng, lat = (gcj02_to_wgs84(float(center[0]), float(center[1])) if len(center) == 2 else (0, 0))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("level", ""),
                "adcode": d.get("adcode", ""),
                "citycode": d.get("citycode", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "amap"}


async def _district_baidu(keywords: str, level: str) -> dict:
    params = {"q": keywords}
    data = await _baidu_get("/api/v2/administrative", params)
    if "error" in data:
        return data
    districts = data.get("results", [])
    features = []
    for d in districts:
        loc = d.get("location", {})
        bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("level", ""),
                "code": str(d.get("code", "")),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "baidu"}
```

- [ ] **Step 5: Verify import works**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.tools.chinese_maps import register_chinese_map_tools; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/tools/chinese_maps.py
git commit -m "feat: add Chinese map API tools (Amap + Baidu) with POI, geocoding, routing"
```

---

### Task 4: Wire Up Registration + Update System Prompt

**Files:**
- Modify: `app/api/routes/chat.py:44-46` (add import and registration call)
- Modify: `app/services/chat_engine.py` (SYSTEM_PROMPT — add tool documentation)

- [ ] **Step 1: Register tools in chat.py**

In `app/api/routes/chat.py`, add the import at the top with other tool imports:

```python
from app.tools.chinese_maps import register_chinese_map_tools
```

Add the registration call after `register_crawler_tools(registry)` (line 44):

```python
register_chinese_map_tools(registry)
```

- [ ] **Step 2: Update SYSTEM_PROMPT tool documentation**

In `app/services/chat_engine.py`, inside the `## 工具使用规则` section of `SYSTEM_PROMPT`, add a new subsection after the `### 数据管理` block:

```
### 高德/百度地图服务
- `search_poi(keyword, city, provider, limit)` — POI 搜索（中文关键词，支持高德/百度双服务商）
- `geocode_cn(address, city, provider)` — 中文地址转坐标（比 Nominatim 中文准确率更高）
- `reverse_geocode_cn(location, provider)` — 坐标转中文地址（含附近 POI）
- `plan_route(origin, destination, mode, city, provider)` — 路径规划（驾车/步行/骑行/公交）
- `get_district(keywords, level, provider)` — 行政区划查询

所有 `provider` 参数支持 `"amap"`（高德，默认）和 `"baidu"`（百度），自动 fallback。
坐标输入输出均为 WGS84，内部自动处理 GCJ-02/BD-09 转换。
需在 `.env` 配置 `AMAP_API_KEY` 或 `BAIDU_MAP_AK`，未配置时自动回退到 OSM/Nominatim。
```

- [ ] **Step 3: Verify everything imports**

Run: `cd /home/kevin/projects/webgis-ai-agent && python -c "from app.api.routes.chat import registry; tools = registry.list_tools(); cn_tools = [t for t in tools if t in ('search_poi','geocode_cn','reverse_geocode_cn','plan_route','get_district')]; print(f'Chinese map tools: {cn_tools}'); print(f'Total tools: {len(tools)}')"`
Expected: Lists 5 Chinese map tools among the total

- [ ] **Step 4: Commit**

```bash
git add app/api/routes/chat.py app/services/chat_engine.py
git commit -m "feat: register Chinese map tools and update system prompt"
```

---

## Verification

1. Run coord transform tests: `python -m pytest tests/test_coord_transform.py -v` — all pass
2. Verify tool registration: tools appear in `registry.list_tools()`
3. Verify config fields: `python -c "from app.core.config import settings; print(settings.AMAP_API_KEY, settings.BAIDU_MAP_AK)"` — prints empty strings without error
4. Verify system prompt contains new tool documentation
5. End-to-end test (requires API keys): configure keys in `.env`, start backend, ask agent "找一下北京海淀区的火锅店" — should call `search_poi` and return results on the map
