"""语义槽位抽取与双语规则快路径（AC-01 / ADR-0150）。

本模块是 :mod:`app.services.gis_harness.intent` 的语义引擎：

- **IntentSlots**：统一的中英语义槽位契约（pydantic v2，``extra="forbid"``）；
- **双语规则快路径**：原 intent.py 的 23 条任务规则整体迁入并升级为
  「特异性分级 + 匹配跨度 + 规则序」三级裁决（见 :func:`decide_task`），
  并补充英文分支与中文改写分支 —— 中英共用同一套规则定义（P5），
  拉丁词词缘统一用 :func:`_latin` 构造（替代散落的 lookaround 补丁）；
- **实体解析**：城市/省/区县快路径词表降级为缓存（P2），未命中走
  ``local_first`` 行政区服务，服务不可用回退词表并标注 ``degraded_reason``；
- **双轨合并**：LLM 结构化槽位 ⊕ 规则槽位，冲突按证据优先级
  （结构化数据事实 > LLM 语义 > 正则规则，见 ac-01-decisions.md）；
- **证据置信度**：分证据加权 + 语料校准锚点（P3），替代常量加权；
- **observability**：bounded-label Prometheus 计数器（P6）。

确定性约束：本模块的规则路径是纯函数；LLM 只在显式注入/显式开启的
adaptive 入口被调用，永不影响 ``resolve_map_request_intent`` 的可复放性。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional, Pattern, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

logger = logging.getLogger(__name__)

# ─── 拉丁词词缘统一构造（P5：替代散落的 (?<![a-zA-Z]) 补丁） ─────────────


def _latin(token: str) -> str:
    """拉丁词在 CJK 邻接处的词缘（Python re 中汉字是 \\w，\\b 不可用）。"""
    return rf"(?<![a-zA-Z]){token}(?![a-zA-Z])"


# ─── 语义槽位契约 ─────────────────────────────────────────────────────────

# slot 来源词表："rule" | "llm" | "ontology" | "service" | "session"


class SlotValue(BaseModel):
    """单个槽位的取值 + 证据强度 + 来源（可审计）。"""

    model_config = ConfigDict(extra="forbid")

    value: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    source: str = "rule"


class SlotConstraint(BaseModel):
    """数量/否定/距离等约束（如「500米内」「除火锅店以外」「前10」）。"""

    model_config = ConfigDict(extra="forbid")

    kind: str                      # quantity | negation | distance | rank | other
    value: str = ""
    source: str = "rule"


class IntentSlots(BaseModel):
    """中英共用的语义槽位（P1）。

    LLM 结构化输出与规则快路径都归一到本模型；``extra="forbid"`` 保证
    LLM 幻觉字段在校验边界被拒而不是静默混入。
    """

    model_config = ConfigDict(extra="forbid")

    subject: SlotValue = Field(default_factory=SlotValue)   # 主体（含类型线索）
    area: SlotValue = Field(default_factory=SlotValue)      # 范围/锚点
    measure: SlotValue = Field(default_factory=SlotValue)   # count/density/…
    temporal: SlotValue = Field(default_factory=SlotValue)  # 时间限定
    audience: SlotValue = Field(default_factory=SlotValue)  # 受众（报告/公众…）
    output_form: SlotValue = Field(default_factory=SlotValue)  # map/chart/table…
    constraints: List[SlotConstraint] = []
    lang: str = "zh"
    task_candidate: str = ""        # LLM/本体的任务建议（仅 fallback 时采信）
    confidence: float = Field(0.0, ge=0.0, le=1.0)  # 槽位抽取自评
    degraded_reason: str = ""

    @field_validator("task_candidate", mode="before")
    @classmethod
    def _coerce_null_task_candidate(cls, v: Any) -> Any:
        """LLM prompt 允许 task_candidate=null（schema hint 与指令均明示），
        而公共类型是 str —— None 直通 model_validate 会整体拒绝合法载荷。
        在校验边界把 None 归一为 ""（未知=空串语义）。"""
        return "" if v is None else v


# ─── 主体词表（双语合一；legacy 四表 + 新增中文类目 + 英文表面词） ─────────
# 条目 = (表面词组, entity_type, 规范类目)。zh 表面词的规范类目=表面词本身
# （golden 兼容，见 decisions D9）；en 表面词映射到中文规范类目。

_SUBJECT_ENTRIES: Tuple[Tuple[Tuple[str, ...], str, str], ...] = (
    # ── point / poi（legacy _POINT_SUBJECTS 全量保留）──
    (("小学", "中学", "大学", "学校", "幼儿园", "科研机构"), "poi", None),
    (("医院", "诊所", "药店", "卫生服务中心"), "poi", None),
    (("银行", "超市", "便利店", "商场", "购物中心", "商铺", "门店", "商圈"),
     "poi", None),
    (("餐厅", "餐馆", "火锅店", "奶茶店", "咖啡店", "咖啡馆", "夜市", "农家乐"),
     "poi", None),
    (("加油站", "充电站", "充电桩", "消防站", "地铁站", "公交站", "雷达站"),
     "poi", None),
    (("公园", "景点", "文物古迹", "打卡地", "图书馆", "体育馆", "体育场馆",
      "体育场地", "体育中心", "健身房", "博物馆", "酒店", "星级酒店", "民宿",
      "楼宇"), "poi", None),
    (("设施", "站点", "网点", "场馆", "场地", "养老院", "养老机构", "高校"),
     "poi", None),
    (("primary\\s+schools?",), "poi", "小学"),
    (("hotpot\\s+restaurants?",), "poi", "火锅店"),
    (("hospitals?",), "poi", "医院"),
    (("clinics?",), "poi", "诊所"),
    (("pharmac(?:y|ies)", "drugstores?"), "poi", "药店"),
    (("schools?",), "poi", "学校"),
    (("kindergartens?",), "poi", "幼儿园"),
    (("universities?", "colleges?"), "poi", "大学"),
    (("museums?",), "poi", "博物馆"),
    (("libraries?",), "poi", "图书馆"),
    (("hotels?", "motels?", "homestays?", "guesthouses?"), "poi", "酒店"),
    (("caf(?:e|és?|eterias?)",), "poi", "咖啡馆"),
    (("restaurants?",), "poi", "餐厅"),
    (("bars?", "nightclubs?", "pubs?"), "poi", "夜市"),
    (("supermarkets?", "convenience\\s+stores?", "shopping\\s+malls?",
      "stores?", "shops?"), "poi", "商场"),
    (("parks?", "gardens?"), "poi", "公园"),
    (("tourist\\s+attractions?", "landmarks?", "scenic\\s+spots?"),
     "poi", "景点"),
    (("gas\\s+stations?",), "poi", "加油站"),
    (("charging\\s+stations?", "ev\\s+chargers?"), "poi", "充电站"),
    (("metro\\s+stations?", "subway\\s+stations?", "metro\\s+entrances?"),
     "poi", "地铁站"),
    (("bus\\s+(?:stops?|stations?)",), "poi", "公交站"),
    (("fire\\s+stations?",), "poi", "消防站"),
    (("police\\s+stations?",), "poi", "派出所"),
    (("gyms?",), "poi", "体育馆"),
    (("stadiums?", "sports\\s+(?:centers?|venues?|facilities?)"),
     "poi", "体育馆"),
    (("pois?\\b", "points\\s+of\\s+interest"), "poi", "poi"),
    # ── raster（legacy _RASTER_SUBJECTS + 领域扩展）──
    (("遥感", "影像", "dem", "高程", "地形", "植被指数", "ndvi", "气温",
      "降水", "栅格", "不透水面", "sar", "insar", "雷达", "合成孔径",
      "土壤", "地表温度", "夜间灯光", "夜光", "地物"), "raster", None),
    (("satellite", "imagery", "remote\\s+sensing", "elevation", "terrain",
      "precipitation", "rainfall", "land\\s+cover", "sentinel",
      "landsat", "insar", "deformation", "surface\\s+temperature",
      "nighttime\\s+lights?", "digital\\s+elevation"), "raster", "遥感"),
    # ── boundary / polygon（legacy _POLYGON_SUBJECTS）──
    (("边界", "行政区划", "行政区", "区划", "地块", "土地利用", "规划范围",
      "流域", "建成区", "建设用地", "绿地", "生态红线"), "boundary", None),
    (("land\\s+use", "administrative\\s+boundar(?:y|ies)", "parcels?",
      "built[-\\s]up\\s+areas?", "boundar(?:y|ies)\\b"), "boundary", "土地利用"),
    # ── network / line（legacy _LINE_SUBJECTS）──
    (("道路", "路网", "河流", "水系", "轨道", "管线", "航线", "地铁线路",
      "河网", "河道", "支流"), "network", None),
    (("roads?", "highways?", "rail\\s+transit", "railways?", "pipelines?",
      "rivers?", "streams?", "air\\s+routes?", "street\\s+network"),
     "network", "路网"),
    # 注意（AC-01 回归教训）：人口/企业等「统计字段」主体**不进**实体词表 ——
    # 把它们标成 raster 会改变 entity_type/geometry_expectation，下游
    # capability 解析（admin_aggregation）与 recipe 选择随之失配
    # （conformance 语料 CF-density-quantitative 1188 例回归实证）。
    # legacy 口径：此类查询 subject=unknown，几何由任务派生。
)

_SUBJECT_COMPILED: List[Tuple[Optional[Pattern[str]], str, str, str]] = []
for _surfaces, _etype, _canonical in _SUBJECT_ENTRIES:
    for _surface in _surfaces:
        _category = _canonical or _surface
        if re.escape(_surface) == _surface:
            # 纯字面（中文为主）：rfind 快路径
            _SUBJECT_COMPILED.append((None, _surface, _etype, _category))
        else:
            _SUBJECT_COMPILED.append(
                (re.compile(_surface, re.I), _surface, _etype, _category))


# 「最近的 X / nearest X」锚点：就近可达语义里被请求的主体是锚点**之后**
# 的第一个名词（AC-01：en-107「closest fire station to my hotel」主体是
# 消防站而非酒店）；无锚点时保持 #785 的「最后命中 = 中心语」语义。
_NEAREST_ANCHOR_RE = re.compile(
    r"(?:nearest|closest|最近的|离[^，。?？]{0,6}最近的)", re.I)


def match_subject(query: str) -> Tuple["SubjectLike", str]:
    """双语主体识别（#785 中心语语义 + 就近锚点特例）。

    无「最近/nearest」锚点时取**位置最靠后**的命中（中心语）；有锚点时
    取锚点之后的第一个主体。返回 ``(SubjectLike, matched_surface)``。
    zh 表面词 category=表面词（legacy golden 口径），en 表面词映射到中文
    规范类目（decisions D9）。
    """
    lowered = query.lower()
    candidates: List[Tuple[int, int, str, str, str]] = []
    for pattern, surface, entity_type, category in _SUBJECT_COMPILED:
        if pattern is None:
            pos = lowered.rfind(surface)
            if pos < 0:
                continue
            candidates.append((pos, len(surface), entity_type, category,
                               surface))
        else:
            last: Optional[re.Match] = None
            for m in pattern.finditer(query):
                last = m
            if last is None:
                continue
            candidates.append((last.start(), last.end() - last.start(),
                               entity_type, category, last.group(0)))
    if not candidates:
        return SubjectLike(), ""
    anchor = None
    for m in _NEAREST_ANCHOR_RE.finditer(query):
        anchor = m
    chosen: Optional[Tuple[int, int, str, str, str]] = None
    if anchor is not None:
        after = [c for c in candidates if c[0] >= anchor.end()]
        if after:
            chosen = min(after, key=lambda c: c[0])
    if chosen is None:
        chosen = max(candidates, key=lambda c: (c[0], c[1]))
    return SubjectLike(type=chosen[2], category=chosen[3]), chosen[4]


class SubjectLike(dict):
    """轻量 subject 载体（与 intent.SubjectIntent 字段对齐）。

    用 dict 子类避免 intent ↔ semantic 的模块级循环 import；
    intent 层组装为 :class:`SubjectIntent`。
    """

    def __init__(self, type: str = "unknown", category: str = "") -> None:
        super().__init__(type=type, category=category)

    @property
    def type(self) -> str:
        return self.get("type", "unknown")

    @property
    def category(self) -> str:
        return self.get("category", "")


# ─── 地理实体快路径词表（P2：legacy _KNOWN_CITIES 降级为缓存） ────────────

# legacy 38 城（原样保留，命中语义不变）+ 语料/高频城市扩展 + 直辖市口语
CITY_FAST_PATH: Tuple[str, ...] = (
    "成都", "北京", "上海", "广州", "深圳", "杭州", "武汉", "西安", "重庆",
    "天津", "南京", "苏州", "长沙", "郑州", "青岛", "大连", "厦门", "昆明",
    "拉萨", "乌鲁木齐", "哈尔滨", "沈阳", "长春", "兰州", "西宁", "银川",
    "南宁", "海口", "贵阳", "南昌", "合肥", "福州", "济南", "太原", "石家庄",
    "呼和浩特", "成都市", "北京市",
    # 扩展（非穷举；未命中仍可走行政区服务/服务不可用回退）
    "绵阳", "洛阳", "珠海", "温州", "徐州", "绍兴", "嘉兴", "金华", "南通",
    "台州", "淄博", "潍坊", "襄阳", "岳阳", "常州", "扬州", "泰州", "烟台",
    "宁波", "佛山", "东莞", "中山", "惠州", "唐山", "保定", "岳阳", "宜宾",
    "南充", "绵竹", "雅安", "贵阳",
    # -城 系真城市：「市」后缀的 城 守卫会拒绝其正则形态，快路径兜底
    "聊城", "邹城", "项城", "韩城", "宣城", "诸城",
)

PROVINCE_FAST_PATH: Tuple[str, ...] = (
    "四川", "云南", "贵州", "广东", "浙江", "江苏", "湖北", "湖南", "陕西",
    "甘肃", "青海", "山东", "河南", "河北", "山西", "安徽", "江西", "福建",
    "海南", "台湾", "广西", "内蒙古", "宁夏", "新疆", "西藏", "辽宁", "吉林",
    "黑龙江",
)

# 英文表面词 → (规范中文名, level)。level: city | province
# 规范中文名用于 ontology/服务解析；scope.name 保留查询原文表面词
# （语料 scope 断言为子串双向匹配，见 corpus_harness._scope_matches）。
_EN_CITY_FAST_PATH: Tuple[Tuple[str, str], ...] = tuple(
    (surface, surface)
    for surface in (
        "chengdu", "beijing", "shanghai", "guangzhou", "shenzhen", "hangzhou",
        "wuhan", "xi'an", "xian", "chongqing", "tianjin", "nanjing", "suzhou",
        "changsha", "zhengzhou", "qingdao", "dalian", "xiamen", "kunming",
        "lhasa", "urumqi", "harbin", "shenyang", "changchun", "lanzhou",
        "xining", "yinchuan", "nanning", "haikou", "guiyang", "nanchang",
        "hefei", "fuzhou", "jinan", "taiyuan", "shijiazhuang", "hohhot",
        "mianyang", "luoyang", "zhuhai", "wenzhou", "xuzhou", "shaoxing",
        "jiaxing", "jinhua", "nantong", "taizhou", "zibo", "weifang",
        "xiangyang", "yueyang", "changzhou", "yangzhou", "yantai", "ningbo",
        "foshan", "dongguan", "zhongshan", "huizhou", "tangshan", "baoding",
        "yibin", "nanchong", "ya'an", "sanya", "jiayuguan", "zhengzhou",
    )
)

_EN_PROVINCE_FAST_PATH: Tuple[Tuple[str, str], ...] = tuple(
    (surface, surface)
    for surface in (
        "sichuan", "yunnan", "guizhou", "guangdong", "zhejiang", "jiangsu",
        "hubei", "hunan", "shaanxi", "gansu", "qinghai", "shandong", "henan",
        "hebei", "shanxi", "anhui", "jiangxi", "fujian", "hainan", "taiwan",
        "guangxi", "inner mongolia", "ningxia", "xinjiang", "tibet",
        "liaoning", "jilin", "heilongjiang", "china",
    )
)

# 「市」后缀要求前面不是 超/城 —— 「连锁超市」「年城市扩张」不是城市名；
# -城 系真城市（聊城等）由快路径词表兜底（audit AC-01，语料 zh-034/069/100）。
_CITY_SUFFIX_RE = re.compile(
    r"(?P<name>[\u4e00-\u9fa5]{2,8}?(?:省|自治区))?"
    r"(?P<city>[\u4e00-\u9fa5]{2,6}?(?<![超城])市)"
)
_DISTRICT_RE = re.compile(r"([\u4e00-\u9fa5]{2,6}(?:区|县|旗))")
_PROVINCE_SUFFIX_RE = re.compile(r"([\u4e00-\u9fa5]{2,8}省)")
# 「各/每个 + 区县」等分组表述不是 scope，是 group_by 信号（legacy 语义）
_GROUPBY_RE = re.compile(r"(各|每个?|按)(?:个)?(?:区|县|市|街道|乡镇|镇|村)", re.I)
# audit #835: 疑问限定词不是地名（legacy 语义保留）
_INTERROGATIVE_RE = re.compile(r"(哪个|哪些|哪几|哪里|什么)")


def _zh_fast_city(query: str) -> str:
    hit = ""
    for city in CITY_FAST_PATH:
        if city in query and len(city) > len(hit):
            hit = city
    return hit


def _en_gazetteer_match(query: str, table: Tuple[Tuple[str, str], ...]) -> Optional[str]:
    lowered = query.lower()
    best: Optional[Tuple[int, int, str]] = None
    for surface, _canonical in table:
        pos = lowered.find(surface)
        if pos < 0:
            continue
        if best is None or (len(surface), -pos) > (best[1], -best[0]):
            best = (pos, len(surface), surface)
    if best is None:
        return None
    return query[best[0]:best[0] + best[1]]


class ScopeResult(dict):
    """scope 解析结果（name/level/source/degraded_reason 可审计）。"""

    @property
    def name(self) -> str:
        return self.get("name", "")

    @property
    def level(self) -> str:
        return self.get("level", "unknown")


def match_scope(
    query: str,
    *,
    entity_service=None,
) -> Tuple[ScopeResult, Dict[str, Any]]:
    """双语 scope 解析：快路径词表 → 正则 → 行政区服务校验（P2）。

    ``entity_service`` 注入 ``resolve_local_admin``-形态的可调用；缺省时
    惰性取 ``app.services.local_first.resolve_local_admin``。服务用于
    **校验词表外**的正则命中（回填 ``entity_resolved`` 证据）；服务不可用
    **不阻塞、不改判定**，记 ``degraded_reason``（零静默降级）。
    """
    trace: Dict[str, Any] = {"source": "none", "degraded_reason": ""}

    def _validate_with_service(name: str) -> None:
        nonlocal entity_service
        if entity_service is None:
            try:
                from app.core.config import settings

                if not getattr(settings, "INTENT_ENTITY_SERVICE", True):
                    trace["degraded_reason"] = "entity_service_disabled"
                    return
                from app.services.local_first import (
                    resolve_local_admin as entity_service,  # type: ignore[no-redef]
                )
            except Exception:  # noqa: BLE001 — 配置面缺失按不可用处理
                trace["degraded_reason"] = "entity_service_unavailable"
                return
        try:
            admin = entity_service(name)
        except Exception:  # noqa: BLE001 — 服务故障不阻塞解析
            trace["degraded_reason"] = "entity_service_error"
            return
        trace["entity_resolved"] = bool(admin)
        if not admin:
            trace["degraded_reason"] = "entity_unresolved"

    # 1) 中文城市快路径（legacy 最长命中语义）
    hit = _zh_fast_city(query)
    if hit:
        trace["source"] = "fast_path_city"
        return ScopeResult(name=hit, level="city"), trace
    # 2) 显式「市」后缀（legacy 首优先级；超/城 守卫防「超市/年城市」）
    m = _CITY_SUFFIX_RE.search(query)
    if m and m.group("city"):
        city = m.group("city")
        trace["source"] = "regex_city_suffix"
        if city not in CITY_FAST_PATH and city.rstrip("市") not in CITY_FAST_PATH:
            _validate_with_service(city)  # 词表外 → 服务校验（P2）
        return ScopeResult(name=city, level="city"), trace
    # 3) 省份（后缀 + 快路径）
    m = _PROVINCE_SUFFIX_RE.search(query)
    if m:
        trace["source"] = "regex_province_suffix"
        return ScopeResult(name=m.group(1), level="province"), trace
    for province in PROVINCE_FAST_PATH:
        if province in query:
            trace["source"] = "fast_path_province"
            return ScopeResult(name=province, level="province"), trace
    # 4) 区县（legacy 守卫保留：分组词/疑问词排除）
    m = _DISTRICT_RE.search(query)
    if m:
        captured = m.group(1)
        prefix = query[: m.start() + 2]
        if (
            not _GROUPBY_RE.search(prefix)
            and not _GROUPBY_RE.search(captured)
            and not _INTERROGATIVE_RE.search(captured)
        ):
            trace["source"] = "regex_district"
            return ScopeResult(name=captured, level="district"), trace
    # 5) 英文 gazetteer（city 优先 province）
    en_hit = _en_gazetteer_match(query, _EN_CITY_FAST_PATH)
    if en_hit:
        trace["source"] = "fast_path_en_city"
        return ScopeResult(name=en_hit, level="city"), trace
    en_hit = _en_gazetteer_match(query, _EN_PROVINCE_FAST_PATH)
    if en_hit:
        trace["source"] = "fast_path_en_province"
        return ScopeResult(name=en_hit, level="province"), trace
    # 6) 行政区服务（词表未命中的「X市/县/区」面）——不可用即回退
    candidate = None
    m = re.search(r"([\u4e00-\u9fa5]{2,6}(?:(?<![超城])市|县|区))", query)
    if m and not _GROUPBY_RE.search(m.group(1)) \
            and not _INTERROGATIVE_RE.search(m.group(1)):
        candidate = m.group(1)
    if candidate is not None:
        resolver = entity_service
        if resolver is None:
            try:
                from app.core.config import settings

                if getattr(settings, "INTENT_ENTITY_SERVICE", True):
                    from app.services.local_first import (
                        resolve_local_admin as resolver,  # type: ignore[no-redef]
                    )
                else:
                    trace["degraded_reason"] = "entity_service_disabled"
            except Exception:  # noqa: BLE001 — 配置面缺失按不可用处理
                trace["degraded_reason"] = "entity_service_unavailable"
        if resolver is not None:
            try:
                admin = resolver(candidate)
            except Exception:  # noqa: BLE001 — 服务故障不阻塞解析
                admin = None
                trace["degraded_reason"] = "entity_service_error"
            if admin:
                trace["source"] = "entity_service"
                trace["entity_resolved"] = True
                return ScopeResult(name=candidate, level="city"), trace
            if not trace["degraded_reason"]:
                trace["degraded_reason"] = "entity_unresolved"
    trace.setdefault("degraded_reason", "")
    return ScopeResult(), trace


# ─── 任务规则引擎（legacy 23 条迁移 + 双语补强；特异性分级裁决） ───────────

SPEC_TECHNICAL = 45    # SAR/地形/水文/自相关/趋势：专业计算语义（最强）
SPEC_DECISION = 40     # 公平/选址/适宜性/风险：评价决策语义
SPEC_CALC = 38         # 定量密度/光谱指数/网络路径/流动：可计算语义
SPEC_SPECIFIC = 30     # 行政统计/聚集/可达/邻近/变化/分类/栅格领域词
SPEC_INTERP = 25       # 插值面
SPEC_DISPLAY = 20      # 展示动词（最弱任务语义）
SPEC_RASTER = 15       # 栅格主体兜底
SPEC_DISTRIBUTION = 10  # 分布词兜底


@dataclass(frozen=True)
class TaskRule:
    rule_id: str
    pattern: Pattern[str]
    task: str
    specificity: int
    order: int


def _r(rule_id: str, task: str, spec: int, pattern: str) -> TaskRule:
    return TaskRule(rule_id, re.compile(pattern, re.I), task, spec, 0)


def _build_rules() -> List[TaskRule]:
    rules: List[TaskRule] = []
    order = 0

    def add(rule_id: str, task: str, spec: int, pattern: str) -> None:
        nonlocal order
        rules.append(TaskRule(rule_id, re.compile(pattern, re.I), task,
                              spec, order))
        order += 1

    # ══ legacy 23 条（正则原文迁移；决策序 = 特异性分级 + 原序）══
    add("analytical_density_per_area", "analytical_density", SPEC_CALC,
        r"每(?:平方|平方千米|平方公里|km|公里)[^，。?？]*密度|"
        r"密度[（(]?每|密度[^，。?？]{0,12}每(?:平方|km|公里)|"
        r"单位面积[^，。?？]*密度|"
        r"density\s*(?:map|surface)?\s*per|per\s+square\s+(?:km|kilometer)|"
        r"density\s*(?:\(|in\s)[^，。?？]{0,16}(?:km|square)")
    add("spatial_equity_request", "spatial_equity", SPEC_DECISION,
        r"(公平性|公平|均衡|是否合理|分布合理|教育资源不足|资源(不足|缺口)|"
        r"欠发达|不平等|差距[有大多小]?|人均|万人拥有|千人拥有|"
        r"equity|equitable|fairness|fair\s+access|"
        r"fairly\s+(?:distributed|allocated)|balanced\s+distribution|"
        r"underserved|under.?privileged)")
    add("site_selection_request", "site_selection", SPEC_DECISION,
        r"(选址|选址推荐|选址分析|最优位置|最佳位置|候选位置|候选址|"
        r"新校址|新院址|新站址|布点|选址建议|(?:哪里|何处|哪些地方?|哪儿)适合建|"
        r"(?:位置|地点)[^，。?？]{0,3}怎么选|怎么选(?:位置|地点)|"
        r"适合建(?:新|一)|"
        r"site\s+selection|choose\s+a\s+site|"
        r"best\s+location|candidate\s+site|"
        r"where\s+should\s+(?:we\s+)?(?:build|place|put)|"
        r"best\s+(?:site|spot|location)\s+for|"
        r"suitable\s+spots?\s+for\s+a?\s*new)")
    add("suitability_assessment_request", "suitability_assessment",
        SPEC_DECISION,
        r"(适宜性|适建区|适建性|适宜程度|适宜性评价|适宜性分析|"
        r"开发适宜|农业适宜|建设适宜|suitability|suitable\s+area)")
    add("risk_exposure_request", "risk_exposure", SPEC_DECISION,
        r"(风险|风险区|风险评估|风险分析|暴露|危险源|灾害易发|地质灾害|"
        r"安全隐患|卫生防护距离|安全距离|防护距离|"
        r"risk\s+(?:assessment|zone|area|map)|hazard|exposure)")
    add("sar_analysis_request", "sar_analysis", SPEC_TECHNICAL,
        rf"({_latin('sar')}|{_latin('insar')}|"
        r"合成孔径|雷达影像|雷达数据|干涉测量|差分干涉|"
        r"形变监测|地表形变|地面沉降|沉降监测|deformation\s+monitoring|"
        r"ground\s+settlement|interferometric|后向散射)")
    add("terrain_analysis_request", "terrain_analysis", SPEC_TECHNICAL,
        r"(坡度|坡向|山体阴影|地形因子|地形分析|地形起伏|地势|地形渲染|晕渲|"
        r"等高线|等值线|"
        r"视域|通视|可视域|hillshade|shaded\s+relief|slope\s+(?:analysis|map)|aspect\s+map|"
        r"viewshed|ruggedness|terrain\s+derivatives?|contour)")
    add("watershed_analysis_request", "watershed_analysis", SPEC_TECHNICAL,
        r"(流域|汇水|集水|水文分析|分水岭|河流提取|河网提取|河道提取|水系提取|汇流累积|"
        r"淹没范围|淹没初筛|水位推演|内涝淹没|"
        r"watershed|catchment|hydrology|drainage|flow\s+accumulation|"
        r"stream\s+extraction|pour\s+point|flood\s+extent|inundation)")
    add("spatial_autocorrelation_request", "spatial_autocorrelation",
        SPEC_TECHNICAL,
        r"(空间自相关|自相关|莫兰|moran|geary|lisa|局部聚集指数|"
        r"聚集显著性|spatial\s+autocorrelation|local\s+clusters?|"
        r"cluster\s+significance|cluster\s+map)")
    add("temporal_trend_request", "temporal_trend", SPEC_TECHNICAL,
        r"(变化趋势|趋势分析|动态趋势|逐年|年际|多(?:年|期)变化|时间序列|时序分析|"
        r"长系列|季节性趋势|突变点|拐点|转折点|变化节点|"
        r"temporal\s+trend|time\s+series|trend\s+analysis|"
        r"annual\s+(?:change|variation)|interannual)")
    add("network_route_request", "network_route", SPEC_CALC,
        r"(最短路径|最短路线|最短距离|最近设施|"
        r"(?:离|距)[^，。?？]{1,12}的?最近的?[\u4e00-\u9fa5]{2,8}"
        r"(?![^，。?？]{0,10}(?:分布|有哪些|清单|统计|构成))|"
        r"最近的(?:医院|消防站|站点|设施|派出所)"
        r"(?![^，。?？]{0,10}(?:分布|有哪些|清单|统计|构成))|"
        r"就近分配|"
        r"路径规划|路线规划|导航路线|配送路线|"
        r"shortest\s+path|shortest\s+route|closest\s+facility|nearest\s+facility|"
        r"route\s+planning|directions?\s+between)")
    add("administrative_statistic", "administrative_statistic", SPEC_SPECIFIC,
        r"(各|每个|按?分?)(?:个)?(?:区|县|市|街道|乡镇|镇|村|州|省)[^，。?？]*"
        r"(数量|多少|几|统计|计数|汇总|排名|最多|最少)|"
        r"(数量|统计|汇总)按?(?:行政)?(?:区|县|市|街道|划分)|"
        r"(?:number|count|total)\s+of\s+[^，。?？]{1,40}?\s+"
        r"(?:by|per|in)\s+(?:each\s+)?(?:district|county|borough|ward)|"
        r"how\s+many[^，。?？]{0,40}(?:each\s+)?(?:district|county|borough|ward)|"
        r"(?:count|counts|counting)\s+by\s+(?:district|county|borough)|"
        r"(?:by|per)\s+(?:each\s+)?(?:district|county|borough|ward)\b|"
        r"(?:district|county|borough)\s+(?:statistics|stats|ranking|breakdown)|"
        r"by\s+(?:district|county|borough)\b[^，。?？]{0,20}"
        r"(?:count|number|total|statistics|stats|ranking)")
    add("concentration_hotspot", "concentration_analysis", SPEC_SPECIFIC,
        r"(哪里|哪儿|何处|哪个|哪个地方|哪片|哪些)[^，。?？]{0,32}(最集中|最密|最热门|聚集|扎堆)|"
        r"(最集中|热点|高发区|聚集区|聚集效应|核心区在哪)|"
        r"(hottest|most\s+(?:concentrated|dense|crowded)|gathering\s+areas?)")
    add("accessibility_service_area", "accessibility_analysis", SPEC_SPECIFIC,
        r"(可达性|等时圈|服务区|服务域|泰森多边形|voronoi|覆盖范围|盲区|缺口|未覆盖|覆盖空白|空白区|欠覆盖|"
        r"通勤时间|车程[^，。?？]*内|步行[^，。?？]*分钟内|"
        r"\d+\s*分钟[^，。?？]{0,6}(?:步行|车程|公交|骑行|到达|可达|圈)|"
        r"(?:步行|骑行)(?:可)?到达|"
        r"accessibility|isochrone|service\s+area|walkable|walking\s+access)")
    add("proximity_buffer", "proximity_analysis", SPEC_SPECIFIC,
        r"(\d+\s*(?:m|米|km|公里|千米)[^，。?？]*(内|之内|范围内|周边|附近)|"
        r"(?:周边|附近)[^，。?？]{0,8}\d+\s*(?:m\b|米|km|公里|千米)|"
        r"within\s+\d+\s*(?:m\b|meters?|km|kilometers?)|"
        r"within\s+(?:walking|cycling|short)\s+distance|"
        r"(周边|附近|旁边|[^区县市旗]范围内)[^，。?？]{0,32}的)")
    add("change_detection", "change_detection", SPEC_SPECIFIC,
        r"(变化|变迁|前后对比|对比[^，。?？]*(年|期)|历年对比|两期|城市扩张|城镇扩展|扩张监测|扩展监测|"
        r"urban\s+expansion|"
        r"(?:城市|城镇|建成区)(?:扩张|扩展)(?:监测|分析)?|扩张监测|扩展监测|"
        r"changes?\s+(?:between|over|across)|change\s+detection|"
        r"compare[^，。?？]{0,30}(?:periods?|years?|images?))")
    add("categorical_breakdown", "categorical_distribution", SPEC_SPECIFIC,
        r"(各类|各类型|分类别|分类分布|业态分类|按(?:类型|类别|种类)|类别分布|(?<!土壤)类型分布|占比|构成|"
        r"category\s+breakdown|by\s+category|composition\s+of)")
    # legacy G5：显式光谱指数低于 change/admin 等宽规则 —— 「两期NDVI的变化」
    # 必须先命中变化检测（规则序语义由本 specificity 值保持）。
    add("vegetation_index_request", "vegetation_index", SPEC_SPECIFIC - 2,
        r"(ndvi|evi|ndwi|nbr|植被指数|植被覆盖度?|绿度)")
    add("mobility_flow_request", "mobility_flow", SPEC_SPECIFIC,
        r"(通勤流|出行流|客流|交通流|流向|流动|od\s*(?:矩阵|分析|联系|强度|流量|走廊)|"
        r"出行(od|分布)|commuting\s+(?:flows?|flow)|origin.destination|od\s+matrix)")
    add("simple_view", "simple_view", SPEC_DISPLAY,
        r"^(?:在地图上|地图上|在地图中|(?:帮我|请|把|将|咱|麻烦)[^，。?？]{0,10}|"
        r"(?:please\s+|can\s+you\s+|could\s+you\s+|i\s+want\s+to\s+|i'?d\s+like\s+to\s+))?"
        r"(给我看|看看|显示|展示|查看|瞄一眼|瞧瞧|放到|放上|标到|"
        r"show\s+me|show|display|map\s+the|map\b)"
        r"(?![^，。?？]{0,48}(?:分布|散布|态势|格局|统计|密度|热点|变化|服务区|可达|"
        r"占比|构成|聚类|均衡|选址|流(向|量)|通勤|插值|克里金|公平|风险|适宜|"
        r"distribution|statistics|density|hotspot|changes?|flow|equity|risk|"
        r"interpolat|kriging))")
    add("raster_subject_thematic", "raster_distribution", SPEC_RASTER,
        "(" + "|".join(_RASTER_SUBJECT_SURFACE(legacy=True)) + ")")
    add("interpolation_surface", "raster_distribution", SPEC_INTERP,
        r"(克里金|kriging|插值|interpolat)")
    add("distribution_generic", "distribution_overview", SPEC_DISTRIBUTION,
        r"(分布|散布|散落|态势|格局|疏密)")
    # 裸「密度/density」= 视觉密度概览（legacy G16/G17/C018 语义锁）：
    # 仅压过 fallback 兜底（特异性 11），保证此类查询有规则头标记、
    # 不落入「fallback+缺主体」的澄清触发面。
    add("bare_density_visual", "distribution_overview", SPEC_DISTRIBUTION + 1,
        r"(密度|density)")

    # ══ 双语补强规则（P1/P5：基线 miss 工程清单 → 规则）══
    # ── 中文改写/倒装/否定补强 ──
    add("zh_admin_ranking", "administrative_statistic", SPEC_SPECIFIC,
        r"(哪个|哪些)[^，。?？]{0,6}(区|县|街道)[^，。?？]{0,16}(最多|最少|排名|排行)|"
        r"排名[^，。?？]{0,10}(区|县)")
    add("zh_concentration_reversed", "concentration_analysis", SPEC_SPECIFIC,
        r"(主要)?(聚集|集中)(在|于)?(哪个|哪些|哪里|何处)|扎堆|最密集|密度最高|"
        r"(哪个|哪些|哪里)[^，。?？]{0,8}最热门|最密集的区域")
    add("zh_simple_mid_display", "simple_view", SPEC_DISPLAY,
        r"(帮我|请|把|将)[^，。?？]{0,6}(放到|放上|标到|标在|画到|显示在)[^，。?？]{0,4}(地图|图上)"
        r"(?![^，。?？]{0,32}(?:分布|统计|密度|热点|变化|占比|构成))"
        r"|(在?地图上|地图上)[^，。?？]{0,4}(标出来|画出来|展示出来)"
        r"(?![^，。?？]{0,32}(?:分布|统计|密度|热点|变化|占比|构成))")
    # 定量锚点补强（legacy G16/G17/C018 锁定：裸「密度」= 视觉分布，
    # 只有显式单位/每平方公里口径才是定量密度 —— 不收窄会翻转 golden）
    add("zh_density_map_word", "analytical_density", SPEC_CALC,
        r"每(平方|平方千米|平方公里|km|公里)[^。]{0,24}密度"
        r"|按每(平方|km|公里)"
        r"|单位[^，。?？]{0,4}按?每(平方|km|公里)"
        r"|[（(]人/(平方)?公里[）)]")
    # 27 = 三档约束（AC-01 golden 校准）：高于栅格主体兜底（15）/展示词（20），
    # 低于光谱指数（28，M083「遥感影像算NDWI」指数优先）、变化/分类（30，
    # G6「影像变化」变化优先；(?<!土壤) 让「土壤类型分布」不被分类抢走）。
    add("zh_raster_domain", "raster_distribution", SPEC_SPECIFIC - 3,
        r"(土壤类型|土地利用|不透水面|地表温度|夜间灯光|夜光|地物类型)(的)?"
        r"(分布|格局|图|数据|分类|反演|结果)|遥感影像|卫星影像|Landsat")
    add("zh_accessibility_notcovered", "accessibility_analysis", SPEC_SPECIFIC,
        r"未被[^，。?？]{0,12}覆盖|没有[^，。?？]{0,8}覆盖|覆盖不到")
    add("zh_equity_reasonable", "spatial_equity", SPEC_DECISION,
        r"布局合理|合理吗|均衡吗|资源差")
    add("zh_site_selection_supplement", "site_selection", SPEC_DECISION,
        r"选在什么位置|选在哪|候选地点|新址|怎么选|选个位置|选址位置")
    add("zh_suitability_supplement", "suitability_assessment", SPEC_DECISION,
        r"适宜开发|适合作为[^，。?？]{0,8}(建设|开发|耕作)|建设适宜|适建")
    add("zh_risk_supplement", "risk_exposure", SPEC_DECISION,
        r"洪灾|洪水|危化品?|化工厂|泥石流|滑坡")
    add("zh_temporal_supplement", "temporal_trend", SPEC_TECHNICAL,
        r"时序(变化|分析|数据)?|趋势显著|显著的?趋势|年际变化")
    add("zh_watershed_supplement", "watershed_analysis", SPEC_TECHNICAL,
        r"(提取|分析|生成)[^，。?？]{0,8}(河网|水系|河道)|河网水系")
    add("zh_network_supplement", "network_route", SPEC_CALC,
        r"怎么走|最近的[^，。?？]{0,8}(在哪|在哪里|怎么去)|离[^，。?？]{0,6}最近的")
    add("zh_categoric_supplement", "categorical_distribution", SPEC_SPECIFIC,
        r"按(业态|品类|行业|类型)的?(分布|构成|占比)|业态构成|类别构成")

    # ── English 分支补强（legacy 词表系统性缺英文）──
    add("en_admin_ranking", "administrative_statistic", SPEC_SPECIFIC,
        r"(?:which\s+(?:district|county)[^，。?？]{0,40}\b(?:has|have)\s+the\s+most\b)"
        r"|rank\w*\s+[^，。?？]{0,32}districts?"
        r"|district[-\s]?(?:wise|level)\b"
        r"|(?:total|number)\s+(?:of\s+)?[^，。?？]{0,28}\s+in\s+(?:each\s+)?(?:district|county)"
        r"|compare\s+the\s+number")
    add("en_density_supplement", "analytical_density", SPEC_CALC,
        r"(?:per[-\s]?square[-\s]?(?:km|kilometer)s?|(?:square|sq)\.?[-\s]?km(?:2|²)?"
        r"|per\s+km\s*2?|km2|km²"
        r"|density\s+in\s+(?:each\s+)?(?:district|county))")
    add("en_concentration_supplement", "concentration_analysis", SPEC_SPECIFIC,
        r"(?:most\s+clustered|cluster(?:ed)?\s+most|highest\s+density|hotspot(?:\s+analysis)?"
        r"|busiest|top\s+clustering|where\s+(?:do|are|is)[^，。?？]{0,28}\b(?:cluster|concentrat)"
        r"|most\s+concentrated)")
    # 29 = 低于 legacy 宽规则一档：「breakdown of hotels by district」
    # 的行政统计语义（by district）必须压过分类拆解（span 不再越过层级）。
    add("en_categorical_supplement", "categorical_distribution",
        SPEC_SPECIFIC - 1,
        r"(?:breakdown\s+of|share\s+of|proportion\s+of|classify\s+[^，。?？]{0,24}\s+by"
        r"|pie\s+chart|category\s+mix|types\s+of\s+[^，。?？]{0,24}\s+(?:in|across))")
    add("en_proximity_supplement", "proximity_analysis", SPEC_SPECIFIC,
        r"(?:in\s+the\s+vicinity\s+of|adjacent\s+to|surrounding\s+|near\b"
        r"|nearby|inside\s+a?\s*[\d.]+\s*(?:km|m)\s+buffer|within\s+a?\s*[\d.]+\s*(?:km|m)\b)")
    add("en_accessibility_supplement", "accessibility_analysis", SPEC_SPECIFIC,
        r"(?:\d+[\s-]*min(?:ute)?s?[\s-]*(?:walk|drive|bus|ride|transit)"
        r"|walk(?:ing)?\s+access\s+to|not\s+covered\s+by|coverage\s+gaps?"
        r"|within\s+a?\s*\d+[\s-]*min(?:ute)?s?\s+(?:walk|drive)"
        r"|walk\s+to\s+a?\s*[^，。?？]{0,24}\s+in\s+\d+\s*min"
        r"|without\s+(?:any|a)\s+[^，。?？]{0,20}\s+within)")
    add("en_change_supplement", "change_detection", SPEC_SPECIFIC,
        r"(?:changed?\s+(?:in|since|over)\b|detect\s+changes?|changes?\s+(?:in|from|since)\b"
        r"|expansion\s+detection|two\s+period\s+images)")
    add("en_vegetation_supplement", "vegetation_index", SPEC_SPECIFIC,
        r"(?:vegetation\s+(?:coverage|index)|greenness)")
    add("en_mobility_supplement", "mobility_flow", SPEC_SPECIFIC,
        r"(?:passenger\s+flow|bike\s+trips?|traffic\s+flow)")
    add("en_equity_supplement", "spatial_equity", SPEC_DECISION,
        r"(?:evenly\s+distributed|per\s+capita)")
    add("en_site_supplement", "site_selection", SPEC_DECISION,
        r"(?:optimal\s+placement|could\s+host|best\s+place|where\s+(?:should|to)\s+(?:build|put|place)"
        r"|placement\s+of|host\s+a\s+new)")
    add("en_suitability_supplement", "suitability_assessment", SPEC_DECISION,
        r"(?:suitable\s+for\s+(?:urban\s+)?(?:development|construction|agriculture)"
        r"|buildability|development\s+suitability)")
    add("en_risk_supplement", "risk_exposure", SPEC_DECISION,
        r"(?:flood(?:ing|s)?\b|prone\s+to|chemical\s+plants?|risk\s+buffer|flood\s+zones?)")
    # 31 = 高于邻近规则一档：否定覆盖（without any X within）是可达-缺口
    # 语义，压过「within walking distance」的邻近语义（D5 决策口径）。
    add("en_accessibility_negation", "accessibility_analysis",
        SPEC_SPECIFIC + 1,
        r"(?:without\s+(?:any|a)\s+[^，。?？]{0,20}\s+within"
        r"|no\s+[^，。?？]{0,16}\s+within\s+a?\s*[\d.]+\s*min)")
    add("en_network_supplement", "network_route", SPEC_CALC,
        r"(?:nearest\s+[^，。?？]{0,24}|closest\s+[^，。?？]{0,24}|how\s+to\s+get\s+to)")
    add("en_distribution_supplement", "distribution_overview", SPEC_SPECIFIC,
        r"(?:\b(?:distribution|spread|spatial\s+pattern|layout)\b\s+(?:of|across)?"
        r"|where\s+are\s+[^，。?？]{0,32}\s+located)")
    add("en_simple_supplement", "simple_view", SPEC_DISPLAY,
        r"(?:(?:put|plot|pin|draw|mark)\s+[^，。?？]{0,48}\s+on\s+(?:the\s+)?map"
        r"|on\s+(?:the\s+)?map\b"
        r"|(?:i'?d\s+like\s+to\s+see|let\s+me\s+see|can\s+you\s+show))"
        r"(?![^，。?？]{0,40}(?:distribution|density|hotspot|change|statistics|equity|risk))")
    # 注意：不含裸 dem/terrain（M074 golden 锁定「Show the DEM terrain」
    # = simple_view 展示语义；DEM 走 legacy 栅格主体面即可）
    add("en_raster_domain", "raster_distribution", SPEC_SPECIFIC,
        r"(?:(?:satellite|sentinel|landsat|radar|night\s*time|nighttime)\s+(?:imagery|images?|data)"
        r"|imagery\b|precipitation\s+data|land\s*cover\s+classification"
        r"|nighttime\s+lights?)")

    # 顺序 = 声明顺序（legacy 在前保持序；补强规则在后用特异性/跨度竞争）
    return [TaskRule(r.rule_id, r.pattern, r.task, r.specificity, i)
            for i, r in enumerate(rules)]


def _RASTER_SUBJECT_SURFACE(legacy: bool = False) -> List[str]:
    """legacy 栅格主体词表（原样迁移，含英文面）——raster_subject 规则用。"""
    return [
        "遥感", "影像", "dem", "高程", "地形", "植被指数", "ndvi", "气温",
        "降水", "栅格", "不透水面", "sar", "insar", "雷达", "合成孔径",
        "satellite", "imagery", "remote sensing", "elevation", "terrain",
        "precipitation", "rainfall", "land cover",
    ]


TASK_RULES: List[TaskRule] = _build_rules()


@dataclass
class TaskDecision:
    """任务裁决结果（全部候选可审计）。"""

    task: str = "distribution_overview"
    fallback: bool = False
    matched_rules: List[str] = dc_field(default_factory=list)
    candidates: List[Tuple[str, str, int, int]] = dc_field(default_factory=list)
    # (rule_id, task, specificity, span_len)


def decide_task(query: str) -> TaskDecision:
    """特异性分级裁决：全部规则参与，取 (specificity, span, -order) 最大。

    与 legacy first-match 的差异仅在「后续规则命中跨度显著更长且同级/高级」
    时（如「视域分析（雷达站选址点）」地形胜选址）；同级规则按声明序
    保持 legacy 行为。
    """
    decision = TaskDecision()
    best: Optional[Tuple[int, int, int, str, str]] = None
    for rule in TASK_RULES:
        m = rule.pattern.search(query)
        if not m:
            continue
        span = m.end() - m.start()
        decision.candidates.append((rule.rule_id, rule.task,
                                    rule.specificity, span))
        key = (rule.specificity, span, -rule.order)
        if best is None or key > (best[0], best[1], best[2]):
            best = (rule.specificity, span, -rule.order,
                    rule.rule_id, rule.task)
    if best is None:
        decision.fallback = True
        decision.matched_rules = ["fallback_distribution_default"]
        return decision
    decision.task = best[4]
    decision.matched_rules = [best[3]]
    return decision


# ─── 派生意图表（legacy _task_specific_intents 的数据化） ────────────────


@dataclass(frozen=True)
class DerivedIntents:
    analysis: Tuple[str, ...]
    cartography: Tuple[str, ...]
    output: Tuple[str, ...]
    measure: str
    group_by: str


_TASK_DERIVED: Dict[str, DerivedIntents] = {
    "distribution_overview": DerivedIntents(
        ("spatial_distribution", "administrative_summary", "profile"),
        ("density_overview", "point_overlay", "administrative_choropleth"),
        ("map", "statistics", "summary"), "count", "district"),
    "simple_view": DerivedIntents(
        ("profile",), ("simple_point_map",), ("map", "summary"), "count", ""),
    "administrative_statistic": DerivedIntents(
        ("administrative_aggregation", "administrative_summary", "profile"),
        ("administrative_choropleth", "point_overlay"),
        ("map", "statistics", "table", "summary"), "count", "district"),
    "analytical_density": DerivedIntents(
        ("analytical_density", "administrative_aggregation", "profile"),
        ("administrative_choropleth",),
        ("map", "statistics", "table", "summary"), "density", "district"),
    "concentration_analysis": DerivedIntents(
        ("kde_density", "hotspot", "administrative_summary"),
        ("density_overview", "hotspot_overlay", "point_overlay"),
        ("map", "statistics", "summary"), "density", ""),
    "categorical_distribution": DerivedIntents(
        ("category_breakdown", "profile"),
        ("categorical_thematic", "point_overlay"),
        ("map", "statistics", "chart", "summary"), "count", "category"),
    "proximity_analysis": DerivedIntents(
        ("proximity_buffer", "profile"),
        ("proximity_overlay", "point_overlay"),
        ("map", "statistics", "summary"), "count", ""),
    "accessibility_analysis": DerivedIntents(
        ("service_area", "profile"),
        ("proximity_overlay", "point_overlay"),
        ("map", "statistics", "summary"), "area", ""),
    "raster_distribution": DerivedIntents(
        ("profile",), ("raster_surface",),
        ("map", "statistics", "summary"), "area", ""),
    "change_detection": DerivedIntents(
        ("profile",), ("raster_surface",),
        ("map", "statistics", "summary"), "area", ""),
    "spatial_equity": DerivedIntents(
        ("administrative_aggregation", "administrative_summary",
         "equity_assessment", "profile"),
        ("administrative_choropleth", "point_overlay"),
        ("map", "statistics", "chart", "summary"), "ratio", "district"),
    "site_selection": DerivedIntents(
        ("proximity_buffer", "service_area", "mcda_evaluation", "profile"),
        ("proximity_overlay", "point_overlay"),
        ("map", "statistics", "table", "summary"), "score", ""),
    "suitability_assessment": DerivedIntents(
        ("proximity_buffer", "overlay_weighted", "mcda_evaluation", "profile"),
        ("raster_surface", "proximity_overlay"),
        ("map", "statistics", "summary"), "suitability", ""),
    "risk_exposure": DerivedIntents(
        ("proximity_buffer", "exposure_assessment", "administrative_summary",
         "profile"),
        ("proximity_overlay", "point_overlay", "administrative_choropleth"),
        ("map", "statistics", "summary"), "exposure", "district"),
    "terrain_analysis": DerivedIntents(
        ("terrain_derivatives", "profile"),
        ("raster_surface", "isoline_contour"),
        ("map", "statistics", "summary"), "area", ""),
    "watershed_analysis": DerivedIntents(
        ("hydrology_analysis", "terrain_derivatives", "profile"),
        ("raster_surface", "proximity_overlay"),
        ("map", "statistics", "summary"), "area", ""),
    "spatial_autocorrelation": DerivedIntents(
        ("autocorrelation_analysis", "administrative_aggregation",
         "administrative_summary"),
        ("administrative_choropleth", "hotspot_overlay"),
        ("map", "statistics", "chart", "summary"), "statistic", "district"),
    "temporal_trend": DerivedIntents(
        ("trend_analysis", "profile"),
        ("raster_surface", "point_overlay"),
        ("map", "chart", "statistics", "summary"), "trend", ""),
    "sar_analysis": DerivedIntents(
        ("sar_interpretation", "profile"), ("raster_surface",),
        ("map", "statistics", "summary"), "area", ""),
    "network_route": DerivedIntents(
        ("route_analysis", "profile"),
        ("proximity_overlay", "point_overlay"),
        ("map", "statistics", "summary"), "length", ""),
}

_DEFAULT_DERIVED = DerivedIntents(
    ("profile",), ("point_overlay",), ("map",), "count", "")


def derived_intents_for(task: str) -> DerivedIntents:
    return _TASK_DERIVED.get(task, _DEFAULT_DERIVED)


# ─── 形态信号与表单信号（legacy 迁移） ────────────────────────────────────

_GRID_AGG_RE = re.compile(r"(格网|网格|hexbin|六边形|蜂窝|h3)", re.I)
_BUBBLE_RE = re.compile(
    r"(气泡图|气泡|比例符号|按[^，。?？]{0,6}(大小|规模)(表示|展示)?|圆(的)?大小)", re.I)
_CHART_WORD_RE = re.compile(
    r"(柱状图|条形图|饼图|折线图|直方图|散点图|箱线图|图表|对比图|pie\s+chart|bar\s+chart|line\s+chart)",
    re.I)
_DENSITY_WORD_RE = re.compile(r"密度", re.I)
_MEASURE_COUNT_RE = re.compile(r"(数量|多少|几|个数|计数)", re.I)
_EXPORT_RE = re.compile(r"(导出|下载|出图|存成|保存为|export)", re.I)
_REPORT_RE = re.compile(
    r"(用于|做|做一份|生成|制作)[^，。?？]*(报告|汇报|论文|汇报材料|简报|插图|印刷|打印)|"
    r"(报告|论文|简报)[^，。?？]*(用|插图|配图)", re.I)


def apply_form_signals(
    query: str,
    analysis_intents: List[str],
    cartography_intents: List[str],
) -> Tuple[str, List[str], List[str]]:
    """显式制图形态信号（格网/气泡）：加法注入（legacy #780 语义）。"""
    if _GRID_AGG_RE.search(query):
        cartography_intents = list(dict.fromkeys(
            [*cartography_intents, "aggregate_grid"]))
        if "grid_binning" not in analysis_intents:
            analysis_intents.append("grid_binning")
        return "aggregate_grid", analysis_intents, cartography_intents
    if _BUBBLE_RE.search(query):
        cartography_intents = list(dict.fromkeys(
            [*cartography_intents, "proportional_symbol"]))
        return "proportional_symbol", analysis_intents, cartography_intents
    return "", analysis_intents, cartography_intents


def entity_geometry(subject_type: str, task: str) -> str:
    if subject_type == "poi":
        return "point"
    if subject_type == "network":
        return "line"
    if subject_type == "boundary":
        return "polygon"
    if subject_type == "raster":
        return "raster"
    if task == "administrative_statistic":
        return "polygon"
    return "unknown"


def detect_language(query: str) -> str:
    cjk = sum(1 for ch in query if "\u4e00" <= ch <= "\u9fff")
    latin = sum(1 for ch in query if ch.isascii() and ch.isalpha())
    return "zh" if cjk >= latin else "en"


def extract_slots(query: str) -> IntentSlots:
    """规则快路径槽位抽取（纯函数、零 I/O）。"""
    lang = detect_language(query)
    scope, _trace = match_scope(query)
    subject, subject_surface = match_subject(query)
    decision = decide_task(query)
    slots = IntentSlots(lang=lang)
    if subject.type != "unknown":
        slots.subject = SlotValue(value=subject.category, confidence=0.8,
                                  source="rule")
        slots.constraints.append(SlotConstraint(kind="subject_type",
                                                value=subject.type))
    if scope.name:
        slots.area = SlotValue(value=scope.name, confidence=0.8,
                               source="rule")
    if decision.fallback:
        slots.degraded_reason = "task_rule_miss"
        slots.confidence = 0.3
    else:
        slots.confidence = 0.7
    m = re.search(r"(\d+)\s*(?:m|米|km|公里|千米)", query, re.I)
    if m:
        slots.constraints.append(SlotConstraint(kind="distance", value=m.group(0)))
    if re.search(r"(除|不含|没有|排除|非|not|without|excluding)", query, re.I):
        slots.constraints.append(SlotConstraint(kind="negation", value=""))
    return slots


# ─── 置信度：证据加权 + 校准锚点（P3） ───────────────────────────────────


def _conf_weights() -> Dict[str, float]:
    try:
        from app.core.config import settings

        return {
            "task_evidence": settings.INTENT_CONF_W_TASK,
            "slot_completeness": settings.INTENT_CONF_W_SLOTS,
            "entity_quality": settings.INTENT_CONF_W_ENTITY,
            "session_consistency": settings.INTENT_CONF_W_SESSION,
        }
    except Exception:  # noqa: BLE001 — 配置缺失用默认权重
        return {
            "task_evidence": 0.40,
            "slot_completeness": 0.25,
            "entity_quality": 0.20,
            "session_consistency": 0.15,
        }


WEAK_TASKS = {"simple_view", "distribution_overview", "raster_distribution"}


def confidence_components(
    decision: TaskDecision,
    slots: IntentSlots,
    scope_known: bool,
    session_consistency: float = 0.5,
) -> Dict[str, float]:
    """证据分量（全部 [0,1]）：任务证据 / 槽位完整度 / 实体质量 / 会话一致。"""
    if decision.fallback:
        task_evidence = 0.0
    elif decision.task in WEAK_TASKS:
        task_evidence = 0.6
    else:
        task_evidence = 1.0
    completeness = sum([
        1.0 if slots.subject.value else 0.0,
        1.0 if scope_known else 0.0,
        1.0 if (slots.measure.value or slots.constraints) else 0.0,
    ]) / 3.0
    entity_quality = 0.0
    if slots.subject.value:
        entity_quality += 0.5
    if scope_known:
        entity_quality += 0.5
    return {
        "task_evidence": task_evidence,
        "slot_completeness": round(completeness, 3),
        "entity_quality": entity_quality,
        "session_consistency": session_consistency,
    }


# 校准锚点：(raw 证据分 → 语料实测准确率)。由 300 条语料分箱拟合（P3，
# 拟合过程与分箱表见 ac-01-intent-recon.md §7）：明确条目正确性=任务命中，
# 模糊条目正确性=0（未经澄清的自信解读即错）。单调保序；实测全部分箱
# |校准值−准确率| ≤ 0.15（门禁），低证据带压在 0.55 以下保证澄清可触发。
CALIBRATION_ANCHORS: Tuple[Tuple[float, float], ...] = (
    (0.00, 0.15),
    (0.43, 0.67),
    (0.60, 0.95),
    (0.70, 0.97),
    (0.90, 1.00),
    (1.00, 1.00),
)


def calibrate(raw: float) -> float:
    """分段线性校准（简化 Platt / 分箱锚点插值）。"""
    anchors = CALIBRATION_ANCHORS
    if raw <= anchors[0][0]:
        return anchors[0][1]
    if raw >= anchors[-1][0]:
        return anchors[-1][1]
    for (x0, y0), (x1, y1) in zip(anchors[:-1], anchors[1:]):
        if x0 <= raw <= x1:
            ratio = (raw - x0) / (x1 - x0) if x1 > x0 else 0.0
            return y0 + ratio * (y1 - y0)
    return raw


def compute_confidence(
    decision: TaskDecision,
    slots: IntentSlots,
    scope_known: bool,
    session_consistency: float = 0.5,
) -> Tuple[float, Dict[str, float]]:
    components = confidence_components(decision, slots, scope_known,
                                       session_consistency)
    weights = _conf_weights()
    raw = sum(weights[name] * value
              for name, value in components.items())
    return round(min(max(calibrate(raw), 0.0), 1.0), 2), components


# ─── 本体对齐（ontology_link） ───────────────────────────────────────────


def ontology_link_for(task: str) -> Optional[Dict[str, Any]]:
    """task → gis_ontology TaskDescriptor 的对齐信息（懒加载、可失败）。"""
    try:
        from app.services.gis_harness import gis_ontology as onto

        for descriptor in onto.ONTOLOGY_TASKS:
            triggers = getattr(descriptor, "family_triggers", None) or ()
            if task in triggers:
                return {
                    "task_id": getattr(descriptor, "task_id", ""),
                    "domain": getattr(descriptor, "domain", ""),
                    "label_zh": getattr(descriptor, "label_zh", ""),
                    "label_en": getattr(descriptor, "label_en", ""),
                    "ontology_version": getattr(onto, "ONTOLOGY_VERSION", None),
                }
    except Exception:  # noqa: BLE001 — 本体不可用时 link 置空，不阻塞
        logger.debug("ontology_link lookup failed", exc_info=True)
    return None


# ─── LLM 双轨（显式入口；规则路径永不调用） ───────────────────────────────

_LLM_PLACEHOLDER_KEYS = {"", "your-api-key-here", None}

_SLOTS_JSON_SCHEMA_HINT = """{
  "subject": {"value": "医院", "confidence": 0.9},
  "area": {"value": "成都市", "confidence": 0.9},
  "measure": {"value": "count", "confidence": 0.8},
  "temporal": {"value": "", "confidence": 0.0},
  "audience": {"value": "", "confidence": 0.0},
  "output_form": {"value": "map", "confidence": 0.8},
  "constraints": [{"kind": "quantity", "value": "前10"}],
  "task_candidate": "administrative_statistic | null",
  "lang": "zh",
  "confidence": 0.85
}"""


def llm_available() -> bool:
    """LLM 是否配置可用（key 缺省/占位符 → 不可用）。"""
    try:
        from app.core.config import settings

        key = getattr(settings, "LLM_API_KEY", None)
        return key not in _LLM_PLACEHOLDER_KEYS
    except Exception:  # noqa: BLE001
        return False


def _parse_llm_json(content: str) -> Optional[Dict[str, Any]]:
    """剥 markdown fence → json.loads（spatial_reasoning 同款最小修复）。"""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        try:
            payload = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


def extract_slots_with_llm(query: str) -> IntentSlots:
    """LLM 结构化槽位抽取。不可用/失败 → 空槽位 + degraded_reason，不抛错。"""
    if not llm_available():
        return IntentSlots(degraded_reason="llm_unavailable")
    try:
        from app.services.chat.llm_client import call_llm
        from app.services.chat.model_config import ModelRole, resolve_llm_config

        cfg = resolve_llm_config(ModelRole.SPATIAL)
        system = (
            "你是 GIS 制图意图的语义槽位抽取器。只输出 JSON，不要输出任何其他文字。"
            "字段结构如下（所有字段必须存在，未知填空串/0 置信度）：\n"
            + _SLOTS_JSON_SCHEMA_HINT
            + "\ntask_candidate 只能取这些值或 null："
            "distribution_overview, simple_view, administrative_statistic, "
            "analytical_density, concentration_analysis, categorical_distribution, "
            "proximity_analysis, accessibility_analysis, raster_distribution, "
            "change_detection, vegetation_index, mobility_flow, spatial_equity, "
            "site_selection, suitability_assessment, risk_exposure, "
            "terrain_analysis, watershed_analysis, spatial_autocorrelation, "
            "temporal_trend, sar_analysis, network_route"
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": query},
        ]
        response = call_llm(cfg, messages)
        content = ((response or {}).get("choices") or [{}])[0].get(
            "message", {}).get("content", "")
        payload = _parse_llm_json(content)
        if payload is None:
            return IntentSlots(degraded_reason="llm_output_invalid")
        payload.pop("task_candidate_list", None)
        slots = IntentSlots.model_validate(payload)
        for value in (slots.subject, slots.area, slots.measure,
                      slots.temporal, slots.audience, slots.output_form):
            value.source = "llm"
        slots.degraded_reason = ""
        return slots
    except ValidationError:
        return IntentSlots(degraded_reason="llm_output_invalid")
    except Exception as exc:  # noqa: BLE001 — LLM 链路任何故障都降级不阻塞
        logger.info("intent LLM slot extraction degraded: %s", type(exc).__name__)
        return IntentSlots(degraded_reason="llm_error")


def merge_slots(
    rule_slots: IntentSlots,
    llm_slots: Optional[IntentSlots],
) -> Tuple[IntentSlots, List[str]]:
    """双轨合并（证据优先级：结构化事实 > LLM > 规则的槽位面）。

    task_candidate 仅作建议返回给调用方（仅在规则 fallback 时采信）。
    """
    merged = rule_slots.model_copy(deep=True)
    conflicts: List[str] = []
    if llm_slots is None or llm_slots.degraded_reason == "llm_unavailable":
        return merged, conflicts
    for name in ("subject", "area", "measure", "temporal", "audience",
                 "output_form"):
        rule_value: SlotValue = getattr(rule_slots, name)
        llm_value: SlotValue = getattr(llm_slots, name)
        if not llm_value.value:
            continue
        if not rule_value.value:
            setattr(merged, name, llm_value)
        elif llm_value.value.lower() != rule_value.value.lower() \
                and llm_value.confidence > rule_value.confidence + 0.2:
            setattr(merged, name, llm_value)
            conflicts.append(name)
    if not merged.constraints and llm_slots.constraints:
        merged.constraints = llm_slots.constraints
    merged.lang = rule_slots.lang or llm_slots.lang
    merged.task_candidate = llm_slots.task_candidate or ""
    merged.confidence = max(rule_slots.confidence, llm_slots.confidence)
    if llm_slots.degraded_reason:
        merged.degraded_reason = merged.degraded_reason or llm_slots.degraded_reason
    return merged, conflicts


# ─── observability（bounded labels，fire-and-forget） ────────────────────

_OUTCOMES = ("rule", "rule_fallback", "semantic", "semantic_fallback",
             "clarify")
_LANGS = ("zh", "en", "other")
_DEGRADED_REASONS = (
    "llm_unavailable", "llm_error", "llm_output_invalid", "llm_not_requested",
    "entity_service_unavailable", "entity_service_disabled",
    "entity_service_error", "entity_unresolved", "task_rule_miss",
)


def _counter(name: str, doc: str, labels: Tuple[str, ...]):
    try:
        from prometheus_client import Counter

        return Counter(name, doc, list(labels))
    except Exception:  # noqa: BLE001 — 观测面缺失不阻塞业务
        return None


_RESOLVE_TOTAL = _counter(
    "gis_intent_resolve_total", "intent resolutions by lang/outcome",
    ("lang", "outcome"))
_CLARIFY_TOTAL = _counter(
    "gis_intent_clarification_total", "clarification questions by slot",
    ("slot",))
_DEGRADED_TOTAL = _counter(
    "gis_intent_degraded_total", "degraded resolutions by reason",
    ("reason",))


def record_resolve(lang: str, outcome: str) -> None:
    if _RESOLVE_TOTAL is None:
        return
    try:
        _RESOLVE_TOTAL.labels(
            lang=lang if lang in _LANGS else "other",
            outcome=outcome if outcome in _OUTCOMES else "rule",
        ).inc()
    except Exception:  # noqa: BLE001
        pass


def record_clarification(slot: str) -> None:
    if _CLARIFY_TOTAL is None:
        return
    try:
        _CLARIFY_TOTAL.labels(slot=slot).inc()
    except Exception:  # noqa: BLE001
        pass


def record_degraded(reason: str) -> None:
    if _DEGRADED_TOTAL is None:
        return
    try:
        _DEGRADED_TOTAL.labels(
            reason=reason if reason in _DEGRADED_REASONS else "llm_error",
        ).inc()
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "IntentSlots",
    "SlotValue",
    "SlotConstraint",
    "TaskRule",
    "TaskDecision",
    "decide_task",
    "TASK_RULES",
    "match_scope",
    "match_subject",
    "extract_slots",
    "extract_slots_with_llm",
    "merge_slots",
    "llm_available",
    "confidence_components",
    "compute_confidence",
    "calibrate",
    "CALIBRATION_ANCHORS",
    "ontology_link_for",
    "derived_intents_for",
    "apply_form_signals",
    "entity_geometry",
    "detect_language",
    "record_resolve",
    "record_clarification",
    "record_degraded",
]
