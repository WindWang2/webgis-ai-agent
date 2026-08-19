"""本地统计数据契约：年鉴解析/adcode 连接 + 高德 POI GCJ-02→WGS84 入库与查询。

fixture 全部在 tmp_path 内合成（微型 shapefile / 年鉴 zip / POI zip），
不依赖外置盘真实数据；settings.LOCAL_GEODATA_DIR 指向 tmp。
"""
import zipfile

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from app.core.config import settings
from app.utils.coord_transform import wgs84_to_gcj02_array

# ── fixture 数据合成 ──────────────────────────────────────────────────────


def _write_district_shp(root):
    rows = [
        # 同名「市中区」两省：验证 province hint 消歧
        {"dt_name": "市中区", "ct_name": "乐山市", "pr_name": "四川省", "dt_adcode": "511102", "geometry": box(103.6, 29.4, 104.0, 29.8)},
        {"dt_name": "市中区", "ct_name": "枣庄市", "pr_name": "山东省", "dt_adcode": "370402", "geometry": box(117.5, 34.7, 117.9, 35.1)},
        # 县↔区 改名别名：年鉴 2014 写「双流县」，SHP 为「双流区」
        {"dt_name": "双流区", "ct_name": "成都市", "pr_name": "四川省", "dt_adcode": "510116", "geometry": box(103.8, 30.4, 104.4, 30.7)},
        {"dt_name": "金堂县", "ct_name": "成都市", "pr_name": "四川省", "dt_adcode": "510121", "geometry": box(104.4, 30.6, 105.0, 30.9)},
        # POI fixture 的锦江区点归属（district 过滤测试用）
        {"dt_name": "锦江区", "ct_name": "成都市", "pr_name": "四川省", "dt_adcode": "510104", "geometry": box(104.0, 30.6, 104.2, 30.8)},
    ]
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs="EPSG:4326")
    target = root / "ChinaAdminDivisonSHP" / "4. District"
    target.mkdir(parents=True, exist_ok=True)
    gdf.to_file(target / "district.shp", driver="ESRI Shapefile", encoding="utf-8")
    # city.shp：district 名 → 市级 adcode 前缀解析用
    city = gpd.GeoDataFrame(
        pd.DataFrame([
            {"ct_name": "成都市", "pr_name": "四川省", "ct_adcode": "510100",
             "geometry": box(103.0, 30.3, 105.0, 31.4)},
        ]),
        geometry="geometry", crs="EPSG:4326",
    )
    city_target = root / "ChinaAdminDivisonSHP" / "3. City"
    city_target.mkdir(parents=True, exist_ok=True)
    city.to_file(city_target / "city.shp", driver="ESRI Shapefile", encoding="utf-8")


def _yearbook_sheet_rows(pub_year):
    """模拟真实布局：续表行/省名行/单位行 + 双行表头 + 数据行（含空行与整行 NaN）。"""
    return [
        [f"续表{pub_year}", None, None, None, None, None, None, None],
        ["四川省", None, None, None, None, None, None, None],
        ["单位：公顷、个、人", None, None, None, None, None, None, None],
        ["名称", "行政区域\n面积", "居民委员会\n（社区）个数", "村民委员会\n个数", "户籍人口", "工业企业\n个数", None, "营业面积50\n平方米以上的商店或超市个数"],
        [None, None, None, None, None, None, "#规模以上", None],
        ["双流县永安镇", 5660, 3, 4, 36085, 40, 10, 58],  # 旧名：走县→区别名
        ["市中区全福镇", 3200, 2, 6, 21000, 5, 1, 12],     # 同名区：靠省提示连四川
        ["金堂县云合镇", 4427, 1, 7, 30205, 2, None, 45],
        ["金堂县又新镇", 5051, 3, 6, 33643, 7, None, 45],
        [None, None, None, None, None, None, None, None],   # 表尾空行
    ]


def _write_yearbook_zip(root, pub_year):
    src = root / "EXCEL-中国县域统计年鉴（乡镇卷）"
    src.mkdir(parents=True, exist_ok=True)
    out = src / f"{pub_year}-EXCEL-中国县域统计年鉴（乡镇卷）.zip"
    with zipfile.ZipFile(out, "w") as zf:
        frame = pd.DataFrame(_yearbook_sheet_rows(pub_year))
        with zf.open("各地区乡镇基本情况/【出版年份%d】四川省.xlsx" % pub_year, "w") as fh:
            frame.to_excel(fh, header=False, index=False, engine="openpyxl")
    return out


def _write_panel_xlsx(root):
    df = pd.DataFrame([
        {"年份": 2022, "省份": "四川省", "城市": "成都市", "区县": "金堂县",
         "区县代码": 510121, "所属地域": "西部", "地区生产总值(万元)": 5200000.0, "户籍人口数(万人)": 88.5},
        {"年份": 2023, "省份": "四川省", "城市": "成都市", "区县": "金堂县",
         "区县代码": 510121, "所属地域": "西部", "地区生产总值(万元)": 5400000.0, "户籍人口数(万人)": 88.1},
    ])
    path = root / "EXCEL-中国县域统计年鉴（乡镇卷）" / "【数据年份2000-2024】中国县域面板数据.xlsx"
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(path, index=False, engine="openpyxl")
    return path


def _gcj_loc(wgs_x: float, wgs_y: float) -> str:
    """WGS 点 → GCJ-02 "lng,lat" 字符串（模拟高德导出格式）。"""
    lng, lat = wgs84_to_gcj02_array(np.array([wgs_x]), np.array([wgs_y]))
    return "%.6f,%.6f" % (float(lng[0]), float(lat[0]))


def _write_poi_zip(root, pcode="510000"):
    df = pd.DataFrame([
        # WGS(104.10, 30.66) 的 GCJ-02 偏移坐标（入库应转回 ≈WGS）
        {"id": "B001", "name": "唐昌镇", "type": "地名地址信息;普通地名;乡镇级地名",
         "address": "郫都区", "location": _gcj_loc(104.10, 30.66),
         "typecode": "190106", "pcode": pcode, "pname": "四川省", "citycode": "028",
         "cityname": "成都市", "adcode": "510124", "adname": "郫都区", "tel": ""},
        {"id": "B002", "name": "海底捞（春熙路店）", "type": "餐饮服务;中餐厅;火锅店",
         "address": "春熙路", "location": _gcj_loc(104.08, 30.66),
         "typecode": "050000", "pcode": pcode, "pname": "四川省", "citycode": "028",
         "cityname": "成都市", "adcode": "510104", "adname": "锦江区", "tel": "028-123"},
        {"id": "B003", "name": "华西医院", "type": "医疗保健服务;综合医院;三级甲等",
         "address": "国学巷", "location": _gcj_loc(104.05, 30.65),
         "typecode": "090100", "pcode": pcode, "pname": "四川省", "citycode": "028",
         "cityname": "成都市", "adcode": "510104", "adname": "锦江区", "tel": ""},
        {"id": "B004", "name": "坏坐标点", "type": "餐饮服务;中餐厅",
         "address": "", "location": "", "typecode": "050000", "pcode": pcode,
         "pname": "四川省", "citycode": "028", "cityname": "成都市",
         "adcode": "510104", "adname": "锦江区", "tel": ""},
    ])
    # 第二个 sheet：模拟大省超出 xlsx 单表上限时的续表（同表头）
    df2 = pd.DataFrame([
        {"id": "B005", "name": "宽窄巷子", "type": "风景名胜;风景名胜",
         "address": "长顺街", "location": _gcj_loc(104.05, 30.66),
         "typecode": "110200", "pcode": pcode, "pname": "四川省", "citycode": "028",
         "cityname": "成都市", "adcode": "510105", "adname": "青羊区", "tel": ""},
    ])
    poi_dir = root / "POI"
    poi_dir.mkdir(parents=True, exist_ok=True)
    out = poi_dir / f"gd_{pcode}_poi.zip"
    with zipfile.ZipFile(out, "w") as zf:
        with zf.open(f"gd_{pcode}_poi/gd_{pcode}_poi.xlsx", "w") as fh:
            with pd.ExcelWriter(fh, engine="openpyxl") as writer:
                df.to_excel(writer, sheet_name="查询编码", index=False)
                # 真实数据续 sheet 无表头、直接是数据行
                df2.to_excel(writer, sheet_name="查询编码(2)", index=False, header=False)
    return out


def _write_poi_csv_zip(root, pcode="320000"):
    """江苏包是 csv 成员（全引号、无表头、比 xlsx 多 parent 列），覆盖双格式支持。"""
    poi_dir = root / "POI"
    poi_dir.mkdir(parents=True, exist_ok=True)

    def line(pid, parent, name, typ, addr, loc, tc, adcode, adname):
        cells = [pid, parent, name, typ, addr, loc, tc, pcode, "江苏省",
                 "025", "南京市", adcode, adname, ""]
        return ",".join(f'"{c}"' for c in cells)

    rows = [
        line("B101", "[]", "新街口地铁站", "交通设施服务;地铁站", "中山路",
             _gcj_loc(118.78, 32.04), "150500", "320102", "玄武区"),
        line("B102", "[]", "夫子庙", "风景名胜;风景名胜", "贡院街",
             _gcj_loc(118.79, 32.02), "110200", "320104", "秦淮区"),
        line("B103", "[]", "坏坐标", "餐饮服务;中餐厅", "", "", "050000",
             "320104", "秦淮区"),
    ]
    out = poi_dir / f"gd_{pcode}_poi.zip"
    with zipfile.ZipFile(out, "w") as zf:
        zf.writestr(f"gd_{pcode}_poi/gd_{pcode}_poi.csv", "\n".join(rows))
    return out


@pytest.fixture
def stats_env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_GEODATA_DIR", str(tmp_path), raising=False)
    _write_district_shp(tmp_path)
    _write_yearbook_zip(tmp_path, 2024)
    _write_panel_xlsx(tmp_path)
    _write_poi_zip(tmp_path)
    from app.tools import local_admin
    local_admin._reset_cache_for_tests()
    yield tmp_path
    local_admin._reset_cache_for_tests()


def _ingest_all():
    from app.services import local_poi, local_yearbook
    yb = local_yearbook.ingest_yearbook()
    panel = local_yearbook.ingest_county_panel()
    poi = local_poi.ingest_gd_poi()
    return yb, panel, poi


# ── 年鉴入库与 adcode 连接 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_yearbook_ingest_links_adcode(stats_env):
    yb, panel, _ = _ingest_all()
    assert yb["rows"] == 4
    assert yb["linked"] == 4  # 全部连上（含县→区别名与跨省同名消歧）

    from app.services.local_yearbook import query_township
    hit = query_township("永安镇", pub_year=2024)
    assert hit["count"] == 1
    row = hit["rows"][0]
    assert row["district_adcode"] == "510116"      # 双流县(旧名) → 双流区
    assert row["district"] == "双流县"              # 年鉴原文保留
    assert row["township"] == "永安镇"
    ind = row["indicators"]
    assert ind["行政区域面积(公顷)"] == 5660        # 跨年归一后的规范指标名
    assert ind["规模以上工业企业个数(个)"] == 10     # #子表头并入
    assert ind["户籍人口(人)"] == 36085

    # 同名「市中区」两省：省提示 → 四川乐山 511102（而非山东 370402）
    zf = query_township("全福镇")
    assert zf["rows"][0]["district_adcode"] == "511102"

    # adcode 下钻：金堂县全部乡镇
    jt = query_township(district_adcode="510121")
    assert {r["township"] for r in jt["rows"]} == {"云合镇", "又新镇"}


@pytest.mark.asyncio
async def test_county_panel_roundtrip(stats_env):
    _, panel, _ = _ingest_all()
    assert panel["rows"] == 2

    from app.services.local_yearbook import query_county_panel
    ts = query_county_panel("金堂县", indicators=["地区生产总值(万元)"])
    assert [r["year"] for r in ts["rows"]] == [2022, 2023]
    assert ts["rows"][1]["indicators"]["地区生产总值(万元)"] == 5400000
    assert "户籍人口数(万人)" not in ts["rows"][0]["indicators"]  # indicators 裁剪


@pytest.mark.asyncio
async def test_yearbook_catalog(stats_env):
    _ingest_all()
    from app.services.local_yearbook import yearbook_catalog
    cat = yearbook_catalog()
    assert cat["available"] is True
    assert cat["township_years"] == [2024]
    assert cat["township_link_rate"] == "4/4"
    assert cat["panel_years"] == [2022, 2023]
    assert "地区生产总值(万元)" in cat["panel_indicators"]


# ── POI：GCJ-02 → WGS84 与查询 ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_poi_ingest_converts_gcj02_to_wgs84(stats_env):
    *_, poi = _ingest_all()
    assert poi["provinces"]["510000"] == 4  # 5 行 - 1 坏坐标；含第二 sheet 续表

    from app.services.local_poi import query_gd_poi
    fc = query_gd_poi([104.0, 30.6, 104.2, 30.7])
    assert fc["count"] == 4
    assert "WGS84" in fc["crs_note"]
    by_id = {f["properties"]["poi_id"]: f for f in fc["features"]}
    coord = by_id["B001"]["geometry"]["coordinates"]
    # 迭代逆变换 ~1m 精度：与原 WGS 点差 < 1e-5°
    assert abs(coord[0] - 104.10) < 1e-5 and abs(coord[1] - 30.66) < 1e-5
    assert by_id["B001"]["properties"]["category"] == "地名地址信息"
    assert by_id["B002"]["properties"]["subtype"] == "中餐厅;火锅店"
    assert "宽窄巷子" in {p["name"] for p in
                         (f["properties"] for f in fc["features"])}


@pytest.mark.asyncio
async def test_poi_filters_and_centers(stats_env):
    _ingest_all()
    from app.services.local_poi import query_gd_poi
    from app.services.local_yearbook import lookup_township_center

    fc = query_gd_poi(name_like="海底捞")
    assert fc["count"] == 1 and fc["features"][0]["properties"]["name"].startswith("海底捞")

    fc = query_gd_poi([104.0, 30.6, 104.2, 30.7], category="医疗保健服务")
    assert fc["count"] == 1 and fc["features"][0]["properties"]["name"] == "华西医院"

    fc = query_gd_poi(adcode="510104")
    assert fc["count"] == 2  # 锦江区两行（坏坐标已剔除）

    # 4 位市级编码前缀展开（不得被 zfill 成 510100 精确匹配）
    fc = query_gd_poi(adcode="5101", category="餐饮服务")
    names = {f["properties"]["name"] for f in fc["features"]}
    assert "海底捞（春熙路店）" in names  # 锦江区餐饮命中

    # 乡镇级地名 → 年鉴乡镇中心点回填
    center = lookup_township_center("唐昌镇")
    assert center is not None
    assert abs(center["lng"] - 104.10) < 1e-5 and abs(center["lat"] - 30.66) < 1e-5
    assert center["adcode"] == "510124"


@pytest.mark.asyncio
async def test_poi_query_requires_filter(stats_env):
    _ingest_all()
    from app.services.local_poi import query_gd_poi
    out = query_gd_poi()
    assert "error" in out  # 无 bbox/名称/adcode 的全表扫描被拒绝


def test_parse_location_tolerates_all_invalid_chunk():
    """整块全空 location（sheet 尾部对照表段）不得让 .str 访问器崩溃。"""
    from app.services.local_poi import _parse_location

    lng, lat = _parse_location(pd.Series(["", "", None, "垃圾数据"]))
    assert (lng[~np.isnan(lng)]).size == 0
    assert (lat[~np.isnan(lat)]).size == 0
    lng, lat = _parse_location(pd.Series([" 104.08 , 30.66 "]))
    assert abs(lng[0] - 104.08) < 1e-9 and abs(lat[0] - 30.66) < 1e-9


def test_dedupe_header():
    from app.services.local_poi import _dedupe_header

    assert _dedupe_header(["id", "name", "type", "name"]) == [
        "id", "name", "type", "name_1",
    ]
    assert _dedupe_header(["a", "b"]) == ["a", "b"]


def _write_osm_pois_fixture(root):
    """微型 osm pois.gpkg（与 osm-ingest 产物同 schema），用于 gd→OSM 主次验证。"""
    import pyogrio

    out = root / "osm_gpkg"
    out.mkdir(exist_ok=True)
    gdf = gpd.GeoDataFrame(
        {
            "osm_id": ["1", "2"],
            "name": ["春熙路餐厅", "四川大学"],
            "category": ["restaurant", "university"],
            "tags": ['{"amenity": "restaurant"}', '{"amenity": "university"}'],
        },
        geometry=[Point(104.081, 30.659), Point(104.071, 30.663)],
        crs="EPSG:4326",
    )
    pyogrio.write_dataframe(gdf, out / "pois.gpkg", layer="pois", driver="GPKG")


@pytest.mark.asyncio
async def test_query_local_osm_pois_prefers_gd(stats_env):
    """query_local_osm 的 pois 主题（无标签）先查高德库，OSM 为补充。"""
    _ingest_all()  # 写入 gd_pois.gpkg
    _write_osm_pois_fixture(stats_env)

    from app.tools.local_osm import register_local_osm_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_local_osm_tools(reg)

    bbox = [104.05, 30.64, 104.10, 30.68]
    # ① 无标签：gd 命中（海底捞）→ source=local_gd_poi
    res = await reg.dispatch("query_local_osm", {"theme": "pois", "bbox": bbox, "name_like": "海底捞"})
    assert res.get("source") == "local_gd_poi"
    names = {f["properties"]["name"] for f in res["features"]}
    assert "海底捞（春熙路店）" in names

    # ② gd 查不到（四川大学是 OSM 独有）→ 自动降级 OSM 补充
    res = await reg.dispatch("query_local_osm", {"theme": "pois", "bbox": bbox, "name_like": "四川大学"})
    assert res.get("count") == 1
    assert res["features"][0]["properties"]["name"] == "四川大学"

    # ③ 指定可翻译的 OSM 标签：amenity=school → 高德「学校」子类
    res = await reg.dispatch("query_local_osm", {"theme": "pois", "bbox": bbox, "tag": "amenity=school"})
    # gd fixture 无「学校」类目 → 回落 OSM（原标签 amenity=school，fixture 也没有）→ 0
    assert "error" not in res
    assert res.get("count") == 0

    # ③' 直接给 gd 库补一个学校类 POI，同一查询应命中 gd（主力优先于 OSM）
    import pyogrio
    school = gpd.GeoDataFrame(
        {
            "poi_id": ["G9"], "name": ["锦江区实验学校"],
            "category": ["科教文化服务"], "subtype": ["学校;小学"],
            "typecode": [""], "adcode": ["510104"], "adname": ["锦江区"],
            "cityname": ["成都市"], "pname": ["四川省"], "address": [""], "tel": [""],
        },
        geometry=[Point(104.09, 30.65)], crs="EPSG:4326",
    )
    pyogrio.write_dataframe(
        school, stats_env / "gd_pois" / "gd_pois.gpkg",
        layer="pois", driver="GPKG", append=True,
    )
    res = await reg.dispatch("query_local_osm", {"theme": "pois", "bbox": bbox, "tag": "amenity=school"})
    assert res.get("source") == "local_gd_poi"
    names = {f["properties"]["name"] for f in res["features"]}
    assert "锦江区实验学校" in names

    # ③'' OSM 特有语义标签（映射表未收录，如饮水点）：保持 OSM 语义
    res = await reg.dispatch("query_local_osm", {"theme": "pois", "bbox": bbox, "tag": "amenity=drinking_water"})
    assert "error" not in res
    assert res.get("count") == 0  # 走了 OSM（fixture 无饮水点），未报错

    # ④ 非 pois 主题不受影响（roads 等仍走 OSM）
    res = await reg.dispatch("query_local_osm", {"theme": "roads", "bbox": bbox})
    assert "error" in res  # 测试环境未导入 roads.gpkg，诚实报错


@pytest.mark.asyncio
async def test_poi_csv_member_and_incremental_ingest(stats_env):
    """江苏 csv 成员可入库；provinces 增量模式只跑指定省，其余省跳过。"""
    from app.services.local_poi import ingest_gd_poi, query_gd_poi

    _ingest_all()  # 先入 510000（xlsx）
    _write_poi_csv_zip(stats_env)
    stats = ingest_gd_poi(provinces=["320000"])
    assert stats["provinces"]["320000"] == 2  # 坏坐标行剔除
    assert stats["skipped"] == []  # provinces 过滤后仅含目标省
    # 无过滤重跑：两省均已入 meta，全部幂等跳过
    stats = ingest_gd_poi()
    assert sorted(stats["skipped"]) == ["320000", "510000"]

    fc = query_gd_poi(adcode="320104")
    assert fc["count"] == 1 and fc["features"][0]["properties"]["name"] == "夫子庙"
    coord = fc["features"][0]["geometry"]["coordinates"]
    assert abs(coord[0] - 118.79) < 1e-5 and abs(coord[1] - 32.02) < 1e-5
    # 原有省未被增量模式破坏
    fc = query_gd_poi(name_like="华西医院")
    assert fc["count"] == 1


# ── 工具注册与调度 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_local_stats_tools_registered(stats_env):
    _ingest_all()
    from app.tools.local_stats import register_local_stats_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_local_stats_tools(reg)
    for name in ("query_local_yearbook", "query_local_poi", "get_local_stats_catalog", "get_township_center"):
        assert name in reg.list_tools()

    res = await reg.dispatch(
        "query_local_yearbook",
        {"dataset": "township", "name": "云合镇", "year": 2024},
    )
    assert res["count"] == 1
    res = await reg.dispatch("get_township_center", {"name": "唐昌镇"})
    assert res["adcode"] == "510124"


@pytest.mark.asyncio
async def test_tool_params_accept_numeric_adcode_and_year(stats_env):
    """LLM 常把 adcode/year/limit 传成数字（复现线上 'adcode 校验失败'）。"""
    _ingest_all()
    from app.tools.local_stats import register_local_stats_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_local_stats_tools(reg)

    # 数字 adcode + 数字 limit（正是报错的那组参数形态）
    res = await reg.dispatch(
        "query_local_poi",
        {"adcode": 510104, "category": "餐饮服务", "subtype": "中餐厅", "limit": 200},
    )
    assert "error" not in res
    assert res["count"] >= 1
    assert "海底捞" in res["features"][0]["properties"]["name"]

    # 字符串 year / 数字 adcode 混用
    res = await reg.dispatch(
        "query_local_yearbook",
        {"dataset": "township", "adcode": 510121, "year": "2024"},
    )
    assert res["count"] == 2

    # 行政区工具同样接受数字编码
    from app.tools.local_admin import register_local_admin_tools
    register_local_admin_tools(reg)
    res = await reg.dispatch("get_local_admin_boundary", {"level": "district", "adcode": 510121})
    assert res["count"] == 1
    assert res["features"][0]["properties"]["dt_name"] == "金堂县"


def test_poi_category_alone_is_a_valid_filter(stats_env):
    """category 有索引，可独立作过滤条件；subtype 单独不行。"""
    _ingest_all()
    from app.services.local_poi import query_gd_poi

    # 仅 category：合法且命中（fixture 有餐饮服务）
    fc = query_gd_poi(category="餐饮服务")
    assert "error" not in fc
    assert fc["count"] >= 1

    # 仅 subtype：仍被拒绝（LIKE 无法独立缩小扫描面）
    out = query_gd_poi(subtype="中餐厅")
    assert "至少提供一个" in out["error"]


@pytest.mark.asyncio
async def test_query_local_poi_accepts_bare_comma_bbox(stats_env):
    """agent 常传裸逗号 bbox "w,s,e,n"（无方括号）——必须生效而非静默丢弃。

    回归：此前工具层只认 [..]JSON 字符串，裸串被丢成 None + category 独立
    过滤放开后退化为全国查询（用户实测「搜成都小学返回北京 POI」）。
    """
    _ingest_all()
    from app.tools.local_stats import register_local_stats_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_local_stats_tools(reg)

    # 海底捞(餐饮)在 (104.08, 30.66)。不含它的 bbox + 餐饮大类 → 0 条；
    # 若 bbox 被丢弃则全国餐饮会命中 1 条。
    res = await reg.dispatch("query_local_poi", {
        "bbox": "104.0,30.60,104.05,30.65", "category": "餐饮服务",
    })
    assert "error" not in res
    assert res["count"] == 0

    # 含它的裸逗号 bbox → 1 条
    res = await reg.dispatch("query_local_poi", {
        "bbox": "104.0,30.60,104.10,30.70", "category": "餐饮服务",
    })
    assert res["count"] == 1
    assert res["features"][0]["properties"]["name"].startswith("海底捞")


@pytest.mark.asyncio
async def test_poi_district_and_polygon_filters(stats_env):
    """district（行政区精确归属）与 polygon（矢量精确包含）两种空间过滤。"""
    _ingest_all()
    from app.tools.local_stats import register_local_stats_tools
    from app.tools.registry import ToolRegistry

    reg = ToolRegistry()
    register_local_stats_tools(reg)

    # ① district=成都市 → 市 adcode 前缀 5101 → 命中锦江区两行（郫都区唐昌镇
    #    的 adcode 是 510124，也属 5101 前缀；成都外无 fixture 点）
    res = await reg.dispatch("query_local_poi", {"district": "成都市", "category": "餐饮服务"})
    assert res["count"] == 1  # 海底捞（锦江区）
    assert res["features"][0]["properties"]["adname"] == "锦江区"

    # ② district=锦江区（区县级精确 adcode）→ 只命中锦江区的点
    res = await reg.dispatch("query_local_poi", {"district": "锦江区"})
    assert res["count"] == 2  # 海底捞 + 华西医院

    # ③ polygon：矩形套住全部点，但多边形只取左半 [104.00,104.06]×[30.60,30.70]
    #    → 只含华西医院(≈104.050,30.650)；海底捞(≈104.082)/唐昌镇(≈104.099) 在外
    poly = {
        "type": "Polygon",
        "coordinates": [[[104.0, 30.6], [104.06, 30.6], [104.06, 30.7], [104.0, 30.7], [104.0, 30.6]]],
    }
    res = await reg.dispatch("query_local_poi", {"polygon": poly})
    names = {f["properties"]["name"] for f in res["features"]}
    assert "华西医院" in names
    assert "海底捞（春熙路店）" not in names
    assert "唐昌镇" not in names

    # ④ 非法 polygon → 明确报错
    res = await reg.dispatch("query_local_poi", {"polygon": {"type": "Point", "coordinates": [104, 30]}})
    assert "polygon" in res.get("error", "")

    # ⑤ 未知行政区 → 明确报错
    res = await reg.dispatch("query_local_poi", {"district": "不存在的城市"})
    assert "未找到行政区" in res.get("error", "")


# ── subtype 别名映射 / 截断均匀采样 / 零命中 hint（「成都高校全是锦江区培训机构」修复）──


def _append_edu_pois(root, pcode="511101"):
    """追加教育类 POI（独立 pcode 的第二个 zip，按省刷新不会删基础行）。

    type 为「大类;中类;小类」——入库拆分后 category=科教文化服务、
    subtype=「学校;高等院校」（与真实高德数据同构）。
    """
    def row(pid, name, typ, lon, lat, adcode, adname):
        return {"id": pid, "name": name, "type": typ, "address": "",
                "location": _gcj_loc(lon, lat), "typecode": "141201",
                "pcode": pcode, "pname": "四川省", "citycode": "028",
                "cityname": "成都市", "adcode": adcode, "adname": adname,
                "tel": ""}

    rows = [
        row("E001", "四川大学江安校区", "科教文化服务;学校;高等院校", 103.98, 30.51, "510116", "双流区"),
        row("E002", "成都信息工程大学", "科教文化服务;学校;高等院校", 104.20, 30.82, "510121", "金堂县"),
    ]
    # 双流/金堂各 15 行同子类：采样测试用（总量 30 > limit，旧行为全取
    # fid 最小的双流段——对应真库「成都科教文化 limit2000 全是锦江区」）
    for i in range(15):
        rows.append(row(f"S1{i:02d}", f"双流培训点{i}", "科教文化服务;培训机构;培训机构",
                        103.9 + i * 0.01, 30.45, "510116", "双流区"))
        rows.append(row(f"S2{i:02d}", f"金堂培训点{i}", "科教文化服务;培训机构;培训机构",
                        104.5 + i * 0.01, 30.75, "510121", "金堂县"))
    poi_dir = root / "POI"
    out = poi_dir / f"gd_{pcode}_poi.zip"
    with zipfile.ZipFile(out, "w") as zf:
        with zf.open(f"gd_{pcode}_poi/gd_{pcode}_poi.xlsx", "w") as fh:
            pd.DataFrame(rows).to_excel(fh, index=False)
    return out


def test_poi_subtype_alias_and_zero_hit_hint(stats_env):
    """口语「大学」别名映射为「高等院校」；真零命中时返回实际子类分布。"""
    _append_edu_pois(stats_env)
    _ingest_all()
    from app.services.local_poi import query_gd_poi

    r = query_gd_poi(None, district="成都市", category="科教文化服务", subtype="大学")
    names = {f["properties"]["name"] for f in r["features"]}
    assert "四川大学江安校区" in names and "成都信息工程大学" in names
    assert "别名映射" in r.get("note", "")

    # 真·零命中：note 带真实分布、correction_hint 指路
    r0 = query_gd_poi(None, district="成都市", category="科教文化服务", subtype="魔法学校")
    assert r0["count"] == 0
    assert "高等院校" in r0.get("note", "")
    assert r0.get("correction_hint")


def test_poi_truncation_uniform_sampling(stats_env):
    """命中数超出 limit 时按 fid 哈希均匀采样——不得只取索引头部单一区县。"""
    _append_edu_pois(stats_env)
    _ingest_all()
    from app.services.local_poi import query_gd_poi

    # 32 条命中（2 高校 + 30 培训）、limit=12：旧 max_features 头部截断全落
    # 双流段；采样后必须覆盖 ≥2 个区县，且如实上报 total_matched。
    r = query_gd_poi(None, district="成都市", category="科教文化服务", limit=12)
    assert r["count"] == 12
    assert r.get("truncated") is True
    assert r.get("total_matched") == 32
    adcodes = {f["properties"]["adcode"] for f in r["features"]}
    assert len(adcodes) >= 2
    assert "均匀采样" in r.get("note", "")


def _write_mini_poi_gpkg(root):
    """Write a tiny pois GPKG so query_gd_poi can be tested without xlsx ingest."""
    import sqlite3

    gpkg = root / "gd_pois" / "gd_pois.gpkg"
    gpkg.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "poi_id": "A1", "name": "海底捞春熙", "category": "餐饮服务",
            "subtype": "中餐厅;火锅店", "typecode": "", "adcode": "510104",
            "adname": "锦江区", "cityname": "成都市", "pname": "四川省",
            "address": "", "tel": "", "geometry": Point(104.08, 30.66),
        },
        {
            "poi_id": "A2", "name": "华西医院", "category": "医疗保健服务",
            "subtype": "综合医院", "typecode": "", "adcode": "510104",
            "adname": "锦江区", "cityname": "成都市", "pname": "四川省",
            "address": "", "tel": "", "geometry": Point(104.09, 30.65),
        },
        {
            "poi_id": "A3", "name": "四川大学", "category": "科教文化服务",
            "subtype": "学校;高等院校", "typecode": "", "adcode": "510107",
            "adname": "武侯区", "cityname": "成都市", "pname": "四川省",
            "address": "", "tel": "", "geometry": Point(104.08, 30.63),
        },
        {
            "poi_id": "A4", "name": "O'Reilly 100% 书店", "category": "购物服务",
            "subtype": "书店", "typecode": "", "adcode": "510105",
            "adname": "青羊区", "cityname": "成都市", "pname": "四川省",
            "address": "", "tel": "", "geometry": Point(104.06, 30.67),
        },
    ]
    gdf = gpd.GeoDataFrame(pd.DataFrame(rows), geometry="geometry", crs="EPSG:4326")
    gdf.to_file(gpkg, layer="pois", driver="GPKG")
    conn = sqlite3.connect(gpkg)
    try:
        with conn:
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pois_adcode ON "pois"(adcode)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_pois_category ON "pois"(category)')
    finally:
        conn.close()
    return gpkg


def test_query_gd_poi_bound_params_without_ingest(tmp_path, monkeypatch):
    """Parameterized sqlite WHERE (bandit B608) still filters and hints correctly."""
    monkeypatch.setattr(settings, "LOCAL_GEODATA_DIR", str(tmp_path), raising=False)
    _write_mini_poi_gpkg(tmp_path)
    from app.services.local_poi import query_gd_poi

    fc = query_gd_poi(name_like="海底捞")
    assert fc["count"] == 1
    assert fc["features"][0]["properties"]["name"] == "海底捞春熙"
    assert fc.get("total_matched") == 1

    fc = query_gd_poi(adcode="5101", category="医疗保健服务")
    assert fc["count"] == 1
    assert fc["features"][0]["properties"]["name"] == "华西医院"

    # LIKE metacharacters in the name must not be treated as wildcards.
    fc = query_gd_poi(name_like="O'Reilly 100%")
    assert fc["count"] == 1
    assert "书店" in fc["features"][0]["properties"]["name"]

    miss = query_gd_poi(category="科教文化服务", subtype="魔法学校")
    assert miss["count"] == 0
    assert "高等院校" in miss.get("note", "")
    assert miss.get("correction_hint")

    sampled = query_gd_poi(adcode="51", limit=2)
    assert sampled["count"] == 2
    assert sampled.get("truncated") is True
    assert sampled.get("total_matched") == 4
