"""分层工具目录 (Tool Catalog) — 用户消息驱动的动态工具子集选择。

设计动机：当 ToolRegistry 累积到 80+ 工具时，每轮把完整 schema 推给 LLM 会
(a) 浪费 token、(b) 降低工具选择准确率（同义/相邻工具互相干扰）。
本目录把工具按"频率/相关性"分三层：

    Tier 1 — always-on：基础空间分析、图层管理、地理编码兜底等
             高频工具，每轮都进 catalog。
    Tier 2 — domain-scoped：按主题（raster/osm/chinese/network/...）分组，
             仅当用户当前消息或最近 N 轮命中相应关键词时才纳入。
    Tier 3 — rare/heavy：罕见或破坏性工具（如 what_if_simulate、skill_creator），
             仅 LLM 显式调用 list_available_tools(domain=...) 后才看到。

未在 ToolRegistry 中标注 tier/domains 的工具默认 tier=1，保证向后兼容。
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


# ─── 主题关键词库（双语，大小写不敏感） ────────────────────────────────────
# 关键：宁严勿宽。误激活只是多发了几个工具 schema (轻量 token 损失)，
# 漏激活会导致 LLM 在该领域无可用工具直接放弃。
DOMAIN_KEYWORDS: dict[str, list[str]] = {
    # 中国本地 GIS 数据源（高德/百度/天地图，15 工具：含 11 chinese_maps + 本地行政/POI）
    "chinese": [
        "高德", "百度", "天地图", "腾讯地图", "amap", "baidu", "tianditu",
        "省", "市", "区县", "县城", "街道", "行政区", "全国",
        "中文地址", "POI 中文", "中国",
        # 典型中文城市/地名：不含“市”也能激活 chinese 域（如“成都的小学”）
        "北京", "上海", "广州", "深圳", "成都", "重庆", "杭州", "南京", "武汉", "西安",
        "苏州", "天津", "长沙", "郑州", "青岛", "宁波", "无锡", "合肥", "昆明", "济南",
        "福州", "厦门", "大连", "沈阳", "哈尔滨", "长春", "太原", "石家庄", "南昌", "南宁",
        "贵阳", "兰州", "海口", "呼和浩特", "银川", "西宁", "乌鲁木齐", "拉萨",
        "河北", "山西", "辽宁", "吉林", "黑龙江", "江苏", "浙江", "安徽", "福建", "江西",
        "山东", "河南", "湖北", "湖南", "广东", "海南", "四川", "贵州", "云南", "陕西",
        "甘肃", "青海", "台湾", "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
    ],
    # OSM/Overpass 数据查询
    "osm": [
        "OSM", "OpenStreetMap", "Overpass", "开源地图", "全球",
        "POI", "小学", "中学", "大学", "学校", "医院", "餐厅", "设施",
    ],
    # 遥感 / 栅格 / 地形
    "raster": [
        "遥感", "卫星", "影像", "栅格", "TIFF", "tif",
        "高程", "海拔", "DEM", "坡度", "坡向", "山体阴影", "地形",
        "NDVI", "NDWI", "EVI", "NBR", "植被指数", "植被", "植被覆盖",
        "Sentinel", "Landsat", "湿地", "燃烧比", "火灾", "干旱",
        "云覆盖", "波段",
    ],
    # 路网 / 路径 / 可达性 / 选址优化 / 巡航
    "network": [
        "路径", "路线", "导航", "可达", "通勤", "时圈", "等时圈", "等时线",
        "服务区", "驾驶", "步行", "骑行", "公交", "地铁", "沿路网",
        "OD", "起终点", "距离矩阵", "路况", "拥堵", "最近设施", "选址优化",
        "巡航", "VRP", "route", "isochrone", "accessibility", "shortest_path",
    ],
    # 时间维度 / 时空分析 / 动态演变
    "temporal": [
        "时间", "时空", "时刻", "时间轴", "演变", "趋势", "变化", "对比",
        "动态", "播放", "时间序列", "重采样", "时空热点", "temporal", "time",
        "spatiotemporal", "timeline",
    ],
    # 空间统计 / 聚类 / 密度
    "statistics": [
        "热点", "热力", "聚类", "聚集", "分布", "密度", "插值",
        "莫兰", "Moran", "LISA", "热点分析", "Getis",
        "Voronoi", "泰森", "凸包", "标准差椭圆", "中心要素",
        "kde", "核密度", "IDW", "反距离",
        # #715: the administrative-count family (『各区…数量』) is the
        # canonical webgis_map_intent/webgis_map_product use case — without
        # these keywords the product layer was invisible exactly there.
        "统计", "数量", "多少", "排名", "各区", "个数", "计数",
    ],
    # MapSpec 意图域（#713）：图层增删改/版面调整的 desired-state 工具。
    # 之前挂在 report 域，『把这个图层删掉』这类纯编辑追问一个关键词都
    # 命不中，模型只能用 runtime-only 的 remove_layer，desired MapSpec 与
    # runtime 永久分叉。
    "mapspec": [
        "图层", "删掉", "删除", "移除", "去掉", "清掉", "隐藏", "显示",
        "版面", "布局", "制图组件", "图例位置",
    ],
    # 报告 / 导出
    "report": [
        "报告", "导出", "PDF", "下载", "图件", "制图",
    ],
    # 数据接入 / 编目 / 上传（#678：dataset 域工具从 tier-1 降级后必须有激活词，
    # 否则永远不可见。裸"数据"过宽不加——几乎所有 GIS 查询都含它）
    "dataset": [
        "数据集", "数据源", "上传", "编目", "物化",
        "dataset", "data source", "materialize",
    ],
    # What-if 情景模拟与空间决策
    "what_if": [
        "假设", "情景", "模拟", "推演", "what if", "what-if",
        "如果", "假如", "决策", "方案", "选址", "对比", "评估",
        "Pareto", "帕累托", "空间决策",
    ],
    # 技能元工具
    "meta": [
        "创建技能", "新建工具", "自定义脚本", "create skill", "new tool",
    ],
}

# 命中后保持载入的轮次（衰减式 sticky，避免每轮重复探测）
_DEFAULT_STICKY_TTL = 3

# 本地 OSM GPKG 可用时，不要再把高德/百度 POI 工具塞给模型。
_SUPPRESS_WHEN_LOCAL_OSM = frozenset({
    "search_poi",
    "search_poi_polygon",
    "search_poi_around",
    "search_and_extract_poi",
})


class ToolCatalog:
    """分层 + 关键词 + 会话粘性的工具目录。

    无状态查询用法：
        catalog = ToolCatalog(registry)
        schemas = catalog.select_schemas("成都的医院 NDVI 分布", session_id="abc")

    会话粘性：命中的 domain 在 sticky_ttl 个**用户轮**内保持载入（#684：按用户轮衰减，
    同一用户轮内的多次 LLM 轮不衰减），避免用户多轮追问时上一轮意图丢失
    （例：第 1 轮"获取 NDVI"，第 2 轮"再算下均值"虽不再含 NDVI 关键词，但 raster
    域仍然 active）。内部通过 ``turn_id`` 区分用户轮边界，未传 ``turn_id`` 时
    回退为按调用衰减以保持向后兼容。
    """

    def __init__(self, registry: ToolRegistry, sticky_ttl: int = _DEFAULT_STICKY_TTL):
        self.registry = registry
        self.sticky_ttl = max(0, sticky_ttl)
        # session_id -> {domain -> 剩余轮次}
        self._sticky: dict[str, dict[str, int]] = {}
        # #684：按用户轮衰减 — 记录 session 上次看到的 turn_id，用于判断是否跨用户轮
        self._sticky_turn: dict[str, Optional[str]] = {}
        self._MAX_STICKY_SESSIONS = 500

    # ─── 公共接口 ──────────────────────────────────────────────

    def select_schemas(
        self,
        user_message: str,
        session_id: Optional[str] = None,
        declared_domains: Optional[set[str]] = None,
        turn_id: Optional[str] = None,
    ) -> list[dict]:
        """根据用户消息 + 会话粘性 + 计划声明的 domain，返回本轮 schema 子集。

        declared_domains 来自规划阶段产出的 Plan.domains；与关键词检测、
        sticky 取并集——关键词检测保留作安全网，不被替换。

        turn_id：#684 按用户轮衰减的边界信号。同一用户轮内的多次 LLM 轮应传
        相同 turn_id，此时不重复衰减；跨用户轮传不同 turn_id 时衰减 1。未传
        时回退为按调用衰减（向后兼容）。
        """
        active_domains = self._activate_domains(user_message, session_id, turn_id=turn_id)
        if declared_domains:
            active_domains = active_domains | set(declared_domains)
        names: set[str] = set()
        for name, meta in self.registry.all_metadata().items():
            tier = int(meta.get("tier", 1))
            if tier == 1:
                names.add(name)
                continue
            if tier == 2:
                tool_domains = set(meta.get("domains", []))
                if tool_domains & active_domains:
                    names.add(name)
                continue
            # tier 3 永远不自动纳入；由 list_available_tools 显式查询
        if _local_osm_available():
            names.difference_update(_SUPPRESS_WHEN_LOCAL_OSM)
        schemas = self.registry.get_schemas_subset(names)
        logger.debug(
            "[ToolCatalog] session=%s domains=%s selected=%d/%d",
            session_id, sorted(active_domains), len(schemas), len(self.registry.get_schemas()),
        )
        return schemas

    def active_domains(self, session_id: Optional[str]) -> set[str]:
        """诊断用：返回会话当前的 sticky domain 集（不触发新激活）。"""
        if not session_id:
            return set()
        return {d for d, ttl in self._sticky.get(session_id, {}).items() if ttl > 0}

    def reset_session(self, session_id: str) -> None:
        """清掉会话粘性（清理会话时调用）。"""
        self._sticky.pop(session_id, None)
        self._sticky_turn.pop(session_id, None)

    def reset_sticky(self, session_id: str) -> None:
        """design-v3 §5：明确换目标（followup 分类 new_goal）时清空会话粘性。

        用户换了一个全新目标时，旧目标领域的 sticky domain 不应继续污染
        本轮工具 schema 选择。由 execution_engine 在 new_goal 时调用。
        """
        self._sticky.pop(session_id, None)
        self._sticky_turn.pop(session_id, None)

    def decay_sticky_domain(self, session_id: str) -> None:
        """手动衰减一轮会话 sticky domain TTL。

        保留（public API 兼容）。design-v3 §5 / R8：plan_orchestrator 的调用点
        已移除——该分支在生产路径从不触发（引擎从未传过 tool_catalog），TTL
        衰减由 ``_activate_domains`` 按用户轮进行（#684：同一用户轮内多次
        select_schemas 不重复衰减；跨用户轮衰减 1，未传 turn_id 时回退为按调用衰减）。
        """
        sticky = self._sticky.get(session_id)
        if not sticky:
            return
        self._sticky[session_id] = {d: t - 1 for d, t in sticky.items() if t - 1 > 0}

    # ─── 内部 ──────────────────────────────────────────────────

    @staticmethod
    def detect_domains(text: str) -> set[str]:
        """纯函数：在一段文本中关键词命中哪些 domain。可在测试中独立验证。"""
        if not text:
            return set()
        # 简单 lowercase + 子串匹配。中文关键词不会被 lower 影响。
        low = text.lower()
        triggered: set[str] = set()
        for domain, kws in DOMAIN_KEYWORDS.items():
            for kw in kws:
                kw_low = kw.lower()
                # 英文关键词加单词边界检查防误伤；中文直接子串。
                if re.match(r"^[\x00-\x7f]+$", kw_low):
                    if re.search(r"\b" + re.escape(kw_low) + r"\b", low):
                        triggered.add(domain)
                        break
                else:
                    if kw in text:
                        triggered.add(domain)
                        break
        return triggered

    def _activate_domains(
        self, user_message: str, session_id: Optional[str], turn_id: Optional[str] = None
    ) -> set[str]:
        fresh = self.detect_domains(user_message or "")
        if not session_id or self.sticky_ttl == 0:
            return fresh

        # F-08: evict oldest sessions when sticky cache exceeds cap
        if len(self._sticky) > self._MAX_STICKY_SESSIONS:
            evict_count = self._MAX_STICKY_SESSIONS // 4
            for sid in list(self._sticky.keys())[:evict_count]:
                del self._sticky[sid]
                self._sticky_turn.pop(sid, None)

        sticky = self._sticky.get(session_id, {})
        # #684：按用户轮衰减。turn_id 相同 → 同一用户轮内多次调用不衰减；
        # 不同 turn_id → 跨用户轮衰减 1；未传 turn_id → 按调用衰减（向后兼容）。
        if turn_id is not None:
            last_turn = self._sticky_turn.get(session_id)
            if last_turn == turn_id:
                decayed = dict(sticky)
            else:
                decayed = {d: t - 1 for d, t in sticky.items() if t - 1 > 0}
                self._sticky_turn[session_id] = turn_id
        else:
            decayed = {d: t - 1 for d, t in sticky.items() if t - 1 > 0}
        # 新命中的 domain 满 TTL 重置
        for d in fresh:
            decayed[d] = self.sticky_ttl
        self._sticky[session_id] = decayed
        return set(decayed.keys())


def _local_osm_available() -> bool:
    try:
        from app.services.local_osm import theme_gpkg_path

        return theme_gpkg_path("pois").exists()
    except Exception:  # noqa: BLE001
        return False
