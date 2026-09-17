"""
数据模型模块
导出系统包含的所有 SQLAlchemy ORM 数据模型
"""
from app.models.db_model import (
    Organization,
    User,
    Layer,
    AnalysisTask,
    LayerPermission,
    Conversation,
    Message,
    CartographyTemplate,
)
from app.models.upload import UploadRecord
from app.models.report import Report
from app.models.knowledge_base import Document, Chunk
from app.models.project import (
    Project,
    ProjectDataset,
    Workflow,
    WorkflowRevision,
    WorkflowRun,
    Artifact,
    ArtifactLineage,
    CartoProjectFact,
)
from app.models.lakehouse_catalog import (  # noqa: F401 — 模型注册 + 再导出
    LakehouseCatalogItem,
)
from app.models.lakehouse_datasets import (  # noqa: F401 — 模型注册 + 再导出
    LakehouseDataset,
    LakehouseDatasetRef,
    LakehouseDatasetVersion,
)
from app.models.data_fabric import (
    DataSource,
    DataFabricDataset,
    DataMaterializationRecord,
    DataSourceModel,
    CatalogItemModel,
    MaterializationModel,
    DataFabricAuditLog,
)
from app.models.intent_learning import (  # noqa: F401 — 模型注册 + 再导出（V11 W1，ADR-0161）
    CartoIntentEvidence,
    CartoFeedbackSignal,
    CartoRecipeAffinity,
)
from app.models.spatial_memory import (  # noqa: F401 — 模型注册 + 再导出（方向 9，ADR-0183）
    GISSpatialMemory,
)
from app.models.mission import (  # noqa: F401 — Direction 01 / ADR-0197
    GISMissionCheckpointRow,
    GISMissionRow,
    GISMissionSwarmRunRow,
)
from app.models.project_knowledge import (  # noqa: F401 — 模型注册 + 再导出
    ProjectKnowledgeEntry,
)

__all__ = [
    "ProjectKnowledgeEntry",
    "GISSpatialMemory",
    "GISMissionRow",
    "GISMissionCheckpointRow",
    "GISMissionSwarmRunRow",
    "Organization",
    "User",
    "Layer",
    "AnalysisTask",
    "LayerPermission",
    "Conversation",
    "Message",
    "CartographyTemplate",
    "UploadRecord",
    "Report",
    "Document",
    "Chunk",
    "Project",
    "ProjectDataset",
    "Workflow",
    "WorkflowRevision",
    "WorkflowRun",
    "Artifact",
    "ArtifactLineage",
    "CartoProjectFact",
    "DataSource",
    "DataFabricDataset",
    "DataMaterializationRecord",
    "DataSourceModel",
    "CatalogItemModel",
    "MaterializationModel",
    "DataFabricAuditLog",
    "CartoIntentEvidence",
    "CartoFeedbackSignal",
    "CartoRecipeAffinity",
]
