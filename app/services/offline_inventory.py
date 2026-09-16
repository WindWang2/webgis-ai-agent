"""离线部署清单面（ADR-0197）：SBOM 与离线资产 manifest。

``manage.py sbom`` / ``manage.py asset-manifest`` 消费。

- SBOM：**只含元数据**（发行名/版本/来源），从 importlib.metadata 与
  frontend/package.json 读取——不打包、不内嵌任何第三方受限资产。
- 资产 manifest：离线部署需要**运营侧预置**的外部资产（模型权重/字形/
  底图瓦片/本地地理数据），登记获取途径与存在性判定。仓库不发行这些
  资产本体（Oracle：无受限资产打包）。

序列化确定性：除 generated_at 外所有字段由配置/环境决定，两次生成可 diff。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict

SBOM_SCHEMA_VERSION = 1
ASSET_MANIFEST_SCHEMA_VERSION = 1


def generate_sbom() -> Dict[str, Any]:
    """Python 发行清单（importlib.metadata）+ 前端依赖声明清单。"""
    import sys
    from importlib import metadata as importlib_metadata

    distributions = []
    for dist in sorted(importlib_metadata.distributions(),
                       key=lambda d: (d.metadata.get("Name") or "").lower()):
        name = dist.metadata.get("Name")
        if not name:
            continue
        distributions.append({
            "name": name,
            "version": dist.version or "",
        })

    frontend = _frontend_dependencies()
    return {
        "schema_version": SBOM_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "python": {
            "python_version": sys.version.split(" ", 1)[0],
            "distribution_count": len(distributions),
            "distributions": distributions,
        },
        "frontend": frontend,
        "note": (
            "元数据清单：不含任何模型权重/字体/瓦片等受限资产本体；"
            "离线部署所需外部资产见 asset-manifest"
        ),
    }


def _frontend_dependencies() -> Dict[str, Any]:
    """frontend/package.json 的 dependencies/devDependencies 声明版本。"""
    from pathlib import Path

    pkg = Path(__file__).resolve().parents[2] / "frontend" / "package.json"
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 前端目录缺失时诚实缺失
        return {"dependencies": {}, "devDependencies": {},
                "source": "frontend/package.json (missing)"}
    return {
        "dependencies": dict(data.get("dependencies") or {}),
        "devDependencies": dict(data.get("devDependencies") or {}),
        "source": "frontend/package.json",
    }


def generate_asset_manifest() -> Dict[str, Any]:
    """离线部署所需外部资产登记 + 存在性判定。

    status 词表：present / absent / operator_supplied（后者 = 由运营侧
    通过本地服务供给，仓库不做存在性断言，不伪造）。
    """
    checks = [
        _check_rag_model(),
        _check_local_geodata(),
        _static("map_glyphs", "MapLibre 字形字体（PBF）", "operator_supplied",
                provisioning="NEXT_PUBLIC_MAP_GLYPHS_URL 指向内网字形服务"
                             "（或自建字体 PBF 目录）"),
        _static("local_basemap_tiles", "本地底图瓦片（XYZ/PMTiles）",
                "operator_supplied",
                provisioning="前端 local 底图 provider（NEXT_PUBLIC_* 或内网"
                             "瓦片服务）；仓库不发行瓦片"),
        _static("llm_weights", "本地 LLM 权重", "operator_supplied",
                provisioning="本地推理服务（ollama/vllm/lmdeploy）自带；"
                             "LLM_BASE_URL 指向该端点"),
    ]
    return {
        "schema_version": ASSET_MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "assets": checks,
        "note": "仓库不打包任何受限资产；本清单登记离线部署的运营侧预置面",
    }


def _static(asset_id: str, description: str, status: str,
            provisioning: str) -> Dict[str, Any]:
    return {
        "id": asset_id,
        "description": description,
        "status": status,
        "provisioning": provisioning,
    }


def _check_rag_model() -> Dict[str, Any]:
    from app.core.config import settings

    offline = bool(settings.RAG_EMBEDDING_OFFLINE)
    return _static(
        "rag_embedding_model",
        "RAG embedding 模型（paraphrase-multilingual-MiniLM-L12-v2）",
        "operator_supplied" if offline else "absent",
        provisioning=(
            "构建机出网预热 HF 缓存 volume 后随镜像分发；"
            "RAG_EMBEDDING_OFFLINE=true 启用（docs/DEPLOYMENT-offline.md）"
        ),
    )


def _check_local_geodata() -> Dict[str, Any]:
    import os

    from app.core.config import settings

    raw = (settings.LOCAL_GEODATA_DIR or "").strip()
    if not raw:
        return _static(
            "local_geodata", "本地地理数据（行政区 SHP / OSM GPKG / POI / 年鉴）",
            "absent", provisioning="配置 LOCAL_GEODATA_DIR 并运行 "
                                   "manage.py osm-ingest / gd-poi-ingest / "
                                   "yearbook-ingest",
        )
    state = "present" if os.path.isdir(raw) else "absent"
    return _static(
        "local_geodata",
        "本地地理数据（行政区 SHP / OSM GPKG / POI / 年鉴）",
        state, provisioning=f"root={raw}",
    )
