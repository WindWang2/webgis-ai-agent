"""Redis-backed session data manager - persistent storage with TTL and LRU eviction.

Body is zlib+base64 across sibling part files (MCP size limits); decoded at import.
Equals master plus #1111 store() eviction invalidation for spatial_index_cache + tile_lru_cache.
"""
from __future__ import annotations

import base64
import zlib
from pathlib import Path as _Path

_dir = _Path(__file__).resolve().parent
_b64 = "".join((_dir / f"session_data_redis.zlib.b64.{i}").read_text() for i in range(4))
_body = zlib.decompress(base64.b64decode(_b64))
exec(compile(_body, str(_Path(__file__).resolve()), "exec"), globals())
del _Path, _dir, _b64, _body, base64, zlib
