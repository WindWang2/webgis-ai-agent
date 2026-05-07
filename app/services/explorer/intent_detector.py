"""意图识别器：判断是否需要深度搜索"""
import logging
from pydantic import BaseModel, Field
from app.services.explorer.models import SearchContext

logger = logging.getLogger(__name__)


class ExploreDecision(BaseModel):
    """探索决策结果"""
    decision: str = Field(..., pattern="^(auto_execute|ask_user|skip)$")
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""
    recommended_sources: list[str] = Field(default_factory=list)
    expected_data_type: str = "poi_list"


class IntentDetector:
    """意图识别器：判断是否需要深度搜索"""

    # 触发深度搜索的关键词
    EXPLORATION_TRIGGERS = {
        "poi_list": ["分布", "POI", "位置", "在哪里", "有哪些", "名录", "列表"],
        "boundary": ["边界", "区划", "范围", "行政区划"],
        "heatmap": ["密度", "热力", "分布密度", "聚集"],
        "statistics": ["统计", "分析", "数量", "比例"],
    }

    # 数据源偏好映射
    SOURCE_HINTS = {
        "学校": ["gov", "osm"],
        "医院": ["gov", "osm"],
        "餐厅": ["osm", "amap"],
        "人口": ["gov"],
        "房价": ["gov", "web"],
        "交通": ["osm", "amap"],
    }

    def detect(
        self,
        user_query: str,
        current_layers: list[dict],
        session_history: list[dict],
    ) -> ExploreDecision:
        """
        判断是否需要深度搜索。
        基于规则+启发式，非 LLM，保证 <100ms 响应。
        """
        query = user_query.lower()

        # 规则1：用户明确指令"深度搜索"
        if any(kw in query for kw in ["深度搜索", "全网搜", "查 deeper", "深入查找"]):
            return ExploreDecision(
                decision="auto_execute",
                confidence=1.0,
                reason="用户明确请求深度搜索",
                recommended_sources=self._infer_sources(query),
            )

        # 规则2：地图已有匹配主题的图层
        for layer in current_layers:
            layer_name = layer.get("name", "").lower()
            if any(word in query for word in layer_name.split()):
                return ExploreDecision(
                    decision="skip",
                    confidence=0.9,
                    reason="地图已有匹配主题的图层",
                )

        # 规则3：意图缺口感知
        data_type, confidence = self._infer_data_type(query)

        # 规则4：历史搜索记忆
        recent_exploration = self._check_recent_history(session_history, query)
        if recent_exploration:
            confidence *= 0.7  # 降低置信度，可能数据已过时

        # 决策
        if confidence >= 0.8:
            decision = "auto_execute"
        elif confidence >= 0.5:
            decision = "ask_user"
        else:
            decision = "skip"

        return ExploreDecision(
            decision=decision,
            confidence=round(confidence, 4),
            reason=f"检测到'{data_type}'类型意图，置信度{confidence}",
            recommended_sources=self._infer_sources(query),
            expected_data_type=data_type,
        )

    def _infer_data_type(self, query: str) -> tuple[str, float]:
        """推断数据类型和置信度"""
        best_type = "poi_list"
        best_score = 0.0

        for data_type, triggers in self.EXPLORATION_TRIGGERS.items():
            score = sum(1 for t in triggers if t in query) / len(triggers)
            if score > best_score:
                best_score = score
                best_type = data_type

        # 基础置信度：至少有一个触发词 = 0.6
        if best_score > 0:
            confidence = 0.6 + min(0.3, best_score * 0.5)
        else:
            confidence = 0.3

        return best_type, round(confidence, 4)

    def _infer_sources(self, query: str) -> list[str]:
        """推断推荐数据源"""
        for keyword, sources in self.SOURCE_HINTS.items():
            if keyword in query:
                return sources
        return ["osm", "gov"]

    def _check_recent_history(self, session_history: list[dict], query: str) -> bool:
        """检查近期是否已有类似搜索"""
        if not session_history:
            return False
        # 简化：检查最近 3 条消息中是否有探索相关
        recent = session_history[-3:]
        for msg in recent:
            content = msg.get("content", "")
            if "ref:explorer_" in content or "深度搜索" in content:
                return True
        return False
