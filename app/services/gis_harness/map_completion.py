"""Compat shim — completion runtime moved to app/services/gis_harness/completion (Runtime V4 §33, ADR-0091)."""
from __future__ import annotations

import asyncio  # noqa: F401
import logging  # noqa: F401
from dataclasses import dataclass, field  # noqa: F401
from typing import Any, Dict, List, Optional  # noqa: F401

from app.services.gis_harness.completion import *  # noqa: F401,F403
from app.services.gis_harness.completion.contracts import *  # noqa: F401,F403

# 显式再导出旧模块的全部私有顶层符号（star import 不带下划线名）——
# 旧调用方（render_observation / runtime_repair / tests 等）从
# ``app.services.gis_harness.map_completion`` 引用它们，路径必须继续成立。
from app.services.gis_harness.completion.contracts import (  # noqa: F401
    _spec_layers,
)
from app.services.gis_harness.completion.validators.artifacts import (  # noqa: F401
    _NON_SPATIAL_ARTIFACT_TYPES,
    _OPTIONAL_FC_REF_TYPES,
    _capability_fc_ref_policy,
)
from app.services.gis_harness.completion.validators.layers import (  # noqa: F401
    _layer_declared_visible,
)
from app.services.gis_harness.completion.validators.components import (  # noqa: F401
    _family_renderable,
)
from app.services.gis_harness.completion.validators.semantics import (  # noqa: F401
    _LEGEND_KIND_TO_COMPONENT,
    _normalize_crs_for_wgs84,
)
from app.services.gis_harness.completion.pipeline import (  # noqa: F401
    _current_mapspec_revision,
    _rows_fingerprint,
    _stored_checked_revision,
    _stored_render_seq,
    _validate_all,
)

logger = logging.getLogger(__name__)
