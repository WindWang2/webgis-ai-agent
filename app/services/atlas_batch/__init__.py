"""Atlas batch publication —— 批量专题图册/系列制图/可复现发布流水线。

聚合编排层（不重写既有生产链）：PublicationIR 规划、publication_export
编译/组装接缝、product_manifest v1 单页语义、durable jobs 框架、artifact
registry —— 见 ``planning/atlas-batch-publication/``。
"""
from app.services.atlas_batch.executor import AtlasJobError, execute_atlas_job
from app.services.atlas_batch.planner import AtlasPlan, AtlasPageBinding, plan_atlas_binding
from app.services.atlas_batch.qa import AtlasQaReport, verify_atlas_pages, verify_pdf_page_count
from app.services.atlas_batch.submit import (
    ATLAS_TASK_TYPE,
    build_atlas_job_spec,
    dry_run_plan,
    submit_atlas_job,
)

__all__ = [
    "ATLAS_TASK_TYPE",
    "AtlasJobError",
    "AtlasPlan",
    "AtlasPageBinding",
    "AtlasQaReport",
    "build_atlas_job_spec",
    "dry_run_plan",
    "execute_atlas_job",
    "plan_atlas_binding",
    "submit_atlas_job",
    "verify_atlas_pages",
    "verify_pdf_page_count",
]
