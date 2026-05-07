"""数据源质量评估引擎"""
import math
from datetime import datetime
from typing import Optional
from app.services.explorer.models import DataSourceQualityScore, FieldInfo


class QualityEngine:
    """数据源质量评估引擎"""

    # 数据类型 -> 月度衰减系数 lambda
    TEMPORAL_LAMBDA = {
        "education": 0.03,
        "medical": 0.05,
        "transport": 0.10,
        "poi": 0.30,
        "population": 0.02,
        "housing_price": 0.50,
        "event": 2.00,
        "default": 0.10,
    }

    # 关键字段需求映射
    KEY_FIELDS = {
        "poi_list": ["name", "address", "lat", "lon"],
        "boundary": ["name", "boundary"],
        "heatmap": ["lat", "lon", "weight"],
        "route": ["origin", "destination", "path"],
    }

    def calc_temporal_score(self, data_type: str, published_at: datetime) -> float:
        """计算时效性分数"""
        lambda_val = self.TEMPORAL_LAMBDA.get(data_type, self.TEMPORAL_LAMBDA["default"])
        delta_months = (datetime.now() - published_at).days / 30.0
        score = math.exp(-lambda_val * delta_months)
        return round(score, 4)

    def calc_thematic_score(
        self,
        user_intent: str,
        dataset_title: str,
        dataset_description: str = "",
        dataset_tags: Optional[list[str]] = None,
        dataset_fields: Optional[list[str]] = None,
    ) -> float:
        """计算主题匹配度（简化版：关键词覆盖）"""
        dataset_tags = dataset_tags or []
        dataset_fields = dataset_fields or []

        # 层1：标题+描述中的关键词匹配（简化）
        intent_keywords = set(user_intent.lower().split())
        title_words = set(dataset_title.lower().split())
        desc_words = set(dataset_description.lower().split())

        title_match = len(intent_keywords & title_words) / max(len(intent_keywords), 1)
        desc_match = len(intent_keywords & desc_words) / max(len(intent_keywords), 1)
        semantic_score = min(1.0, title_match * 0.7 + desc_match * 0.3)

        # 层2：标签和字段覆盖
        tag_match = len(intent_keywords & set(dataset_tags)) / max(len(intent_keywords), 1)
        field_match = len(intent_keywords & set(dataset_fields)) / max(len(intent_keywords), 1)
        keyword_score = min(1.0, tag_match * 0.6 + field_match * 0.4)

        combined = semantic_score * 0.6 + keyword_score * 0.4
        return round(min(1.0, combined), 4)

    def calc_spatial_score(self, data_bbox: str, target_bbox: str) -> float:
        """计算空间覆盖度（简化：bbox 重叠比例）"""
        try:
            ds = [float(x) for x in data_bbox.split(",")]
            ts = [float(x) for x in target_bbox.split(",")]
            if len(ds) != 4 or len(ts) != 4:
                return 0.0

            # 计算交集面积
            inter_s = max(ds[0], ts[0])
            inter_w = max(ds[1], ts[1])
            inter_n = min(ds[2], ts[2])
            inter_e = min(ds[3], ts[3])

            if inter_s >= inter_n or inter_w >= inter_e:
                return 0.0

            inter_area = (inter_n - inter_s) * (inter_e - inter_w)
            target_area = (ts[2] - ts[0]) * (ts[3] - ts[1])
            score = inter_area / target_area if target_area > 0 else 0.0
            return round(min(1.0, score), 4)
        except (ValueError, IndexError):
            return 0.0

    def calc_field_score(
        self,
        expected_fields: list[str],
        actual_fields: list[str],
    ) -> float:
        """计算字段完整度"""
        if not expected_fields:
            return 1.0
        matched = sum(1 for f in expected_fields if f in actual_fields)
        return round(matched / len(expected_fields), 4)

    def calc_precision_score(self, geocoded_results: list[dict]) -> float:
        """计算地理编码精度分数"""
        if not geocoded_results:
            return 0.0
        # 有精确坐标的比例
        has_precise = sum(
            1 for r in geocoded_results
            if r.get("lat") and r.get("lon") and r.get("precision") != "district"
        )
        return round(has_precise / len(geocoded_results), 4)

    def assess_overall(
        self,
        temporal: float,
        thematic: float,
        spatial: float,
        field: float,
        precision: float,
    ) -> DataSourceQualityScore:
        """计算综合质量评分"""
        # 权重配置
        weights = {
            "temporal": 0.20,
            "thematic": 0.30,
            "spatial": 0.20,
            "field": 0.15,
            "precision": 0.15,
        }
        overall = (
            temporal * weights["temporal"] +
            thematic * weights["thematic"] +
            spatial * weights["spatial"] +
            field * weights["field"] +
            precision * weights["precision"]
        )
        return DataSourceQualityScore(
            temporal_score=round(temporal, 4),
            thematic_score=round(thematic, 4),
            spatial_score=round(spatial, 4),
            field_score=round(field, 4),
            precision_score=round(precision, 4),
            overall=round(overall, 4),
            details=weights,
        )
