"""GIS Methodology Intelligence —— 方法知识层（Epic 11）。

canonical registries 之上的只读知识投影层：

- taxonomy：20 类任务分类学（对齐 ontology/families，不复制）
- graph：方法知识图（typed edges / integrity / fingerprint / diff）
- descriptors：方法级增强（problem class / assumptions / alternatives）
- provenance：知识出处登记（LLM 生成内容无合法来源词）
- qualification：数据事实 → 方法资格统一报告（独立模块，Wave 8+）
- ranking：混合排序 + abstention（独立模块，Wave 13+）

红线：本包不做第二 registry；全部引用 canonical id；悬空 fatal；
零 LLM、零 I/O；lib 层不顶层 import services（延迟注入先例）。
"""
from app.lib.gis.methodology.descriptors import (  # noqa: F401
    DESCRIPTOR_SCHEMA_VERSION,
    MethodDescriptorRegistry,
    MethodologyDescriptorV2,
    QUALIFICATION_DIMENSIONS,
    get_method_descriptor_registry,
    reset_method_descriptor_registry,
)
from app.lib.gis.methodology.graph import (  # noqa: F401
    EDGE_RELATIONS,
    GraphBuildError,
    MethodologyGraph,
    get_knowledge_graph,
    reset_knowledge_graph,
)
from app.lib.gis.methodology.provenance import (  # noqa: F401
    KnowledgeProvenance,
    PROVENANCE_LEDGER,
    content_fingerprint as provenance_fingerprint,
    get_provenance,
    provenance_exists,
    validate_ledger,
)
from app.lib.gis.methodology.taxonomy import (  # noqa: F401
    GIS_TASK_CATEGORIES,
    TAXONOMY_SCHEMA_VERSION,
    TaskCategoryDescriptor,
    TaskTaxonomy,
    get_task_taxonomy,
    reset_task_taxonomy,
)

__all__ = [
    "GIS_TASK_CATEGORIES",
    "TAXONOMY_SCHEMA_VERSION",
    "TaskCategoryDescriptor",
    "TaskTaxonomy",
    "get_task_taxonomy",
    "reset_task_taxonomy",
    "DESCRIPTOR_SCHEMA_VERSION",
    "QUALIFICATION_DIMENSIONS",
    "MethodologyDescriptorV2",
    "MethodDescriptorRegistry",
    "get_method_descriptor_registry",
    "reset_method_descriptor_registry",
    "EDGE_RELATIONS",
    "GraphBuildError",
    "MethodologyGraph",
    "get_knowledge_graph",
    "reset_knowledge_graph",
    "KnowledgeProvenance",
    "PROVENANCE_LEDGER",
    "provenance_fingerprint",
    "provenance_exists",
    "get_provenance",
    "validate_ledger",
]
