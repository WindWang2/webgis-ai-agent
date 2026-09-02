"""高级空间分析工具 (FC) — body assembled from parts (MCP commit size).

Implementation is split across `_advanced_spatial_body_*.py` and
concatenated at import. Source is master + #1110 CRS tool-boundary fix
(attribute_filter / central_feature forward full FeatureCollection).
"""
from pathlib import Path as _Path

_dir = _Path(__file__).resolve().parent
_body = "".join(
    (_dir / f"_advanced_spatial_body_{i}.py").read_text(encoding="utf-8")
    for i in range(3)
)
exec(compile(_body, str(_Path(__file__).resolve()), "exec"), globals())
del _Path, _dir, _body
