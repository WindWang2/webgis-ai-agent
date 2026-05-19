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
    # 中国本地 GIS 数据源（高德/百度/天地图，30+ 工具）
    "chinese": [
        "高德", "百度", "天地图", "腾讯地图", "amap", "baidu", "tianditu",
        "省", "市", "区县", "县城", "街道", "行政区", "全国",
        "中文地址", "POI 中文", "中国",
    ],
    # OSM/Overpass 数据查询
    "osm": [
        "OSM", "OpenStreetMap", "Overpass", "开源地图", "全球",
    ],
    # 遥感 / 栅格 / 地形
    "raster": [
        "遥感", "卫星", "影像", "栅格", "TIFF", "tif",
        "高程", "海拔", "DEM", "坡度", "坡向", "山体阴影", "地形",
        "NDVI", "NDWI", "EVI", "NBR", "植被指数", "植被", "植被覆盖",
        "Sentinel", "Landsat", "湿地", "燃烧比", "火灾", "干旱",
        "云覆盖", "波段",
    ],
    # 路网 / 路径 / 可达性
    "network": [
        "路径", "路线", "导航", "可达", "通勤", "时圈", "等时圈", "等时线",
        "服务区", "驾驶", "步行", "骑行", "公交", "地铁",
        "OD", "起终点", "距离矩阵", "路况", "拥堵",
        "route", "isochrone", "accessibility",
    ],
    # 空间统计 / 聚类 / 密度
    "statistics": [
        "热点", "热力", "聚类", "聚集", "分布", "密度", "插值",
        "莫兰", "Moran", "LISA", "热点分析", "Getis",
        "Voronoi", "泰森", "凸包", "标准差椭圆", "中心要素",
        "kde", "核密度", "IDW", "反距离",
    ],
    # 报告 / 导出
    "report": [
        "报告", "导出", "PDF", "下载", "图件", "制图",
    ],
    # What-if 情景模拟
    "what_if": [
        "假设", "情景", "模拟", "推演", "what if", "what-if",
        "如果", "假如",
    ],
    # 技能元工具
    "meta": [
        "创建技能", "新建工具", "自定义脚本", "create skill", "new tool",
    ],
}

# 命中后保持载入的轮次（衰减式 sticky，避免每轮重复探测）
_DEFAULT_STICKY_TTL = 3


class ToolCatalog:
    """分层 + 关键词 + 会话粘性的工具目录。

    无状态查询用法：
        catalog = ToolCatalog(registry)
        schemas = catalog.select_schemas("成都的医院 NDVI 分布", session_id="abc")

    会话粘性：命中的 domain 在 sticky_ttl 轮内保持载入，避免用户多轮追问时
    上一轮意图丢失（例：第 1 轮"获取 NDVI"，第 2 轮"再算下均值"虽不再含
    NDVI 关键词，但 raster 域仍然 active）。
    """

    def __init__(self, registry: ToolRegistry, sticky_ttl: int = _DEFAULT_STICKY_TTL):
        self.registry = registry
        self.sticky_ttl = max(0, sticky_ttl)
        # session_id -> {domain -> 剩余轮次}
        self._sticky: dict[str, dict[str, int]] = {}

    # ─── 公共接口 ──────────────────────────────────────────────

    def select_schemas(self, user_message: str, session_id: Optional[str] = None) -> list[dict]:
        """根据用户消息 + 会话粘性，返回当前轮应推给 LLM 的 schema 子集。"""
        active_domains = self._activate_domains(user_message, session_id)
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

    def _activate_domains(self, user_message: str, session_id: Optional[str]) -> set[str]:
        fresh = self.detect_domains(user_message or "")
        if not session_id or self.sticky_ttl == 0:
            return fresh

        sticky = self._sticky.get(session_id, {})
        # 先衰减一轮
        decayed = {d: t - 1 for d, t in sticky.items() if t - 1 > 0}
        # 新命中的 domain 满 TTL 重置
        for d in fresh:
            decayed[d] = self.sticky_ttl
        self._sticky[session_id] = decayed
        return set(decayed.keys())
