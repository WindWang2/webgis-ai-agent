"""本地统计/POI 查询工具：年鉴（乡镇卷+县域面板）与高德 POI（WGS84）。

参数契约：adcode/year/limit 等 LLM 常以数字或字符串混用传入
（如 adcode=510104 与 "510104"），注解一律 Union 两栖 + 边界处归一。
"""
import logging
from typing import List, Optional, Union

from app.services.local_poi import gd_poi_catalog, query_gd_poi
from app.services.local_yearbook import (
    lookup_township_center,
    query_county_panel,
    query_township,
    yearbook_catalog,
)
from app.tools.registry import ToolExecutionPolicy, ToolRegistry, tool

logger = logging.getLogger(__name__)


def _to_int(value: Union[int, str, None], default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_str(value: Union[str, int, None]) -> str:
    return "" if value is None else str(value).strip()


def register_local_stats_tools(registry: ToolRegistry):
    @tool(
        registry,
        tier=2, domains=["statistics"], name="query_local_yearbook",
        description=(
            "本地统计年鉴查询：中国县域统计年鉴（乡镇卷 2014-2025，乡镇级指标）"
            "与县域面板（2000-2024，75+ 县级指标，含 GDP/人口/财政/教育/医疗）。"
            "✅ 用于：『郫都区唐昌镇户籍人口』『金堂县各乡镇 2023 年工业企业』"
            "『雷波县历年 GDP』等中国县域/乡镇社会经济数据——离线秒级，"
            "优先于任何在线检索。"
            "\n❌ 不要用于：境外数据、非统计属性（那用 get_local_admin_boundary）。"
            "返回行自带 district_adcode（已与行政区连接）与 indicators 指标字典。"
        ),
        param_descriptions={
            "dataset": "'township'（乡镇卷，乡镇粒度）或 'county_panel'（县域面板，县级时间序列）",
            "name": "乡镇名/全名（如 '唐昌镇'、'郫都区唐昌镇'）或区县名（如 '金堂县'），包含匹配",
            "adcode": "六位区县行政编码精确下钻（如 '510124'=郫都区），与 name 二选一或并用",
            "year": "township: 出版年份（数据年≈前一年）；county_panel: 起始年",
            "year_to": "county_panel 截止年（默认同 year，单年）；township 忽略",
            "province": "省份名过滤（同名区县多省时消歧），如 '四川省'",
            "indicators": "county_panel 可选：只返回这些指标（逗号分隔，如 '地区生产总值(万元),户籍人口数(万人)'）",
            "limit": "返回上限（默认 200，最大 2000）",
        },
        execution_policy=ToolExecutionPolicy.INLINE,
        timeout=60.0,
    )
    def query_local_yearbook(
        dataset: str = "township",
        name: str = "",
        adcode: Union[str, int] = "",
        year: Union[int, str] = 0,
        year_to: Union[int, str] = 0,
        province: str = "",
        indicators: str = "",
        limit: Union[int, str] = 200,
    ) -> dict:
        ind_list: Optional[List[str]] = (
            [s.strip() for s in indicators.split(",") if s.strip()] or None
        ) if indicators else None
        adcode_s = _to_str(adcode)
        year_i, year_to_i = _to_int(year), _to_int(year_to)
        limit_i = _to_int(limit, 200)
        if dataset == "county_panel":
            return query_county_panel(
                name=name or None,
                adcode=adcode_s or None,
                year_from=year_i or None,
                year_to=(year_to_i or year_i) or None,
                indicators=ind_list,
                limit=limit_i,
            )
        return query_township(
            name or None,
            pub_year=year_i or None,
            province=province,
            district_adcode=adcode_s,
            limit=limit_i,
        )

    @tool(
        registry,
        name="query_local_poi",
        description=(
            "本地高德 POI 查询【POI 检索主力工具】：全国高德 POI 库"
            "（5174 万点，已 GCJ-02→WGS84），bbox/名称/大类/adcode 过滤，"
            "返回 WGS84 FeatureCollection。"
            "✅ 用于：一切中国境内兴趣点检索的首选——中文商户名远比 OSM 全，"
            "如『锦江区所有三甲医院坐标』『春熙路周边海底捞』『成都的小学』。"
            "OSM POI 仅在查不到时作补充（query_local_osm）。"
            "\n❌ 不要用于：境外 POI；全库无过滤扫描（必须给 bbox/name/adcode 之一）；"
            "道路/铁路/水系（用 query_local_osm）。"
            "category 为高德一级大类（餐饮服务/购物服务/医疗保健服务/科教文化服务…）。"
        ),
        param_descriptions={
            "bbox": "WGS84 矩形边界框 [minx,miny,maxx,maxy]——注意矩形会把邻区边角的点带进来；行政区查询建议用 district",
            "district": "行政区名精确过滤（推荐）：如 '成都市'（市内全部区县）、'锦江区'——按 POI 的 adcode 归属，无矩形外溢",
            "polygon": "任意矢量区域：WGS84 GeoJSON Polygon/MultiPolygon（可用行政区要素 geometry），bbox 预过滤 + 精确包含判断",
            "name_like": "名称包含匹配（中文名最有效），如 '海底捞'",
            "category": "高德一级大类精确匹配，如 '医疗保健服务'、'餐饮服务'；可单独作过滤条件（有索引）",
            "subtype": (
                "子类包含匹配，实际值是「大类;小类」格式的 小类 段：高校→'高等院校'（别名'大学/高校'自动映射）、"
                "'小学'、'中学'、'幼儿园'、'职业技术学校'、'科研机构'、'培训机构'、'三级甲等'。"
                "零命中时返回该范围真实子类分布提示。不能单独使用（必须配合空间或 category 过滤）"
            ),
            "adcode": "行政编码过滤（'510104' 区县精确 / '5101' 市级前缀）",
            "limit": (
                "返回上限（默认 2000=服务端最大值）。超出时服务端按 fid 均匀采样返回"
                "（空间分布覆盖整个查询范围），total_matched 字段给出真实命中数"
            ),
        },
        tier=1,
        domains=["poi", "dataset"],
        execution_policy=ToolExecutionPolicy.THREAD,
        timeout=120.0,
    )
    def query_local_poi(
        bbox="",
        name_like: Optional[str] = None,
        category: Optional[str] = None,
        subtype: Optional[str] = None,
        adcode: Union[str, int, None] = None,
        district: Optional[str] = None,
        polygon=None,
        limit: Union[int, str] = 2000,
    ) -> dict:
        # limit 默认 2000（服务端上限）：市级范围查询（如成都全市 ~千所小学）
        # 不应被默认 200 静默截断——agent 常省略 limit，默认值即结果完整性。
        # bbox 直通服务层解析（支持 [..]JSON、裸逗号串 "w,s,e,n"、数组）。
        # 空间过滤优先级：polygon（任意矢量区域，精确包含）> bbox（矩形）
        # > district/adcode（行政区精确归属——行政区查询首选，无矩形外溢）。
        raw_bbox = list(bbox) if isinstance(bbox, (list, tuple)) else (bbox or None)
        return query_gd_poi(
            raw_bbox,
            name_like=name_like,
            category=category,
            subtype=subtype,
            adcode=_to_str(adcode) or None,
            district=district,
            polygon=polygon,
            limit=_to_int(limit, 2000),
        )

    @tool(
        registry,
        tier=2, domains=["statistics"], name="get_local_stats_catalog",
        description=(
            "本地统计数据目录：年鉴库（年份/行数/行政区连接率/指标词表）与"
            "高德 POI 库（省份/行数）的可用性总览。"
            "✅ 用于：query_local_yearbook / query_local_poi 前确认数据覆盖。"
        ),
        execution_policy=ToolExecutionPolicy.INLINE,
    )
    def get_local_stats_catalog() -> dict:
        return {"yearbook": yearbook_catalog(), "gd_poi": gd_poi_catalog()}

    @tool(
        registry,
        tier=2, domains=["chinese"], name="get_township_center",
        description=(
            "乡镇中心点查询：返回乡镇/街道的 WGS84 坐标点（来自高德乡镇级地名），"
            "年鉴乡镇行空间定位用。"
            "✅ 用于：『唐昌镇在哪里』、给 query_local_yearbook 结果挂点坐标。"
        ),
        param_descriptions={
            "name": "乡镇/街道名，如 '唐昌镇'、'春熙街道'",
            "adcode": "可选：所属区县编码消歧（跨省同名乡镇）",
        },
        execution_policy=ToolExecutionPolicy.INLINE,
    )
    def get_township_center(name: str, adcode: Union[str, int] = "") -> dict:
        hit = lookup_township_center(name, _to_str(adcode))
        if hit is None:
            return {
                "error": f"未找到乡镇中心点 '{name}'",
                "correction_hint": "中心点来自高德乡镇级地名，仅覆盖大陆；可用 get_local_admin_boundary 查区县替代。",
            }
        return hit
