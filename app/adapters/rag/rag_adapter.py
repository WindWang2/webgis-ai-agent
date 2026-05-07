"""RAG 知识库适配器 —— 预留，未来接入时零改动"""
from app.adapters.base import BaseDataAdapter, DataSource
from app.services.explorer.models import (
    RawContent, StructuredData, SearchContext, DataSourceQualityScore,
)


class RAGAdapter(BaseDataAdapter):
    """RAG 知识库适配器 —— 预留，未来接入时零改动"""

    name = "rag"
    supported_query_types = ["policy_analysis", "planning_review"]

    async def discover(self, query: str, context: SearchContext) -> list[DataSource]:
        # 预留：vector_db.similarity_search(query, top_k=5)
        return []

    async def quick_assess(self, query: str, source: DataSource) -> DataSourceQualityScore:
        # 预留
        from app.services.explorer.models import DataSourceQualityScore
        return DataSourceQualityScore(
            temporal_score=1.0,
            thematic_score=0.5,
            spatial_score=0.0,
            field_score=0.5,
            precision_score=0.0,
            overall=0.5,
        )

    async def fetch(self, source: DataSource) -> RawContent:
        # 预留：获取 Chunk 文本
        raise NotImplementedError("RAG adapter not yet implemented")

    async def parse(self, raw: RawContent) -> StructuredData:
        # 预留：文本 → 结构化（LLM 提取地理信息）
        raise NotImplementedError("RAG adapter not yet implemented")
