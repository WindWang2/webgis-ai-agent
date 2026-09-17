"""NetworkDependencyCatalog（ADR-0202）—— 机器可读网络依赖清单。

覆盖：登记完整性（id 唯一/词表封闭/env key 真实存在）、当前 profile 下的
离线判定、JSON 序列化稳定性、与工具面 network=True 的交叉矩阵。
"""
import json

import pytest

from app.core import network_dependency as nd
from app.core.config import settings


def _dependency_ids():
    return [d.id for d in nd.network_dependencies()]


# ── 登记完整性 ──────────────────────────────────────────────────────


def test_ids_unique_and_sorted():
    ids = _dependency_ids()
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)
    assert len(ids) >= 12  # Phase 0 勘察的最小覆盖面


def test_categories_closed_vocabulary():
    for dep in nd.network_dependencies():
        assert dep.category in nd.DEPENDENCY_CATEGORIES, dep.id


def test_setting_keys_exist_in_settings():
    for dep in nd.network_dependencies():
        if dep.setting_key is None:
            continue
        assert hasattr(settings, dep.setting_key), (
            f"{dep.id}: setting_key {dep.setting_key} 不存在于 Settings"
        )
        # 诚实登记：Settings 键与裸 env 键不混用
        assert dep.env_key is None, dep.id


def test_every_dependency_has_offline_alternative():
    for dep in nd.network_dependencies():
        assert dep.offline_alternative.strip(), dep.id
        assert dep.call_sites, dep.id


def test_guard_coverage_declared():
    """pystac-client / rasterio /vsicurl / DDGS / HF hub 在第三方库内部建连，
    必须显式声明不受运行时守卫覆盖（不得谎称全防）。"""
    uncovered = {d.id for d in nd.network_dependencies()
                 if not d.enforced_by_egress_guard}
    assert {"stac_catalog", "remote_raster_vsicurl", "web_search_ddgs",
            "rag_embedding_download", "frontend_basemap_tiles"} <= uncovered


# ── 当前 profile 下的离线判定 ────────────────────────────────────────


def test_offline_status_cloud_vs_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "unrestricted")
    nd.reset_catalog_settings_cache()
    cloud = nd.offline_capability_summary()
    assert cloud["profile"] == "cloud"

    monkeypatch.setattr(settings, "DEPLOYMENT_PROFILE", "air_gapped")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_MODE", "allowlist")
    monkeypatch.setattr(settings, "NETWORK_EGRESS_ALLOW_PRIVATE", "true")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "http://127.0.0.1:11434/v1")
    nd.reset_catalog_settings_cache()
    offline = nd.offline_capability_summary()
    assert offline["profile"] == "air_gapped"
    by_id = {d["id"]: d for d in offline["dependencies"]}
    # 本地 LLM → available；公网 geocoder → unavailable（typed 降级）
    assert by_id["llm_chat"]["available_offline"] is True
    assert by_id["geocoder_amap"]["available_offline"] is False


def test_summary_counts_are_consistent():
    summary = nd.offline_capability_summary()
    deps = summary["dependencies"]
    assert summary["counts"]["total"] == len(deps)
    assert summary["counts"]["available_offline"] == sum(
        1 for d in deps if d["available_offline"])
    assert summary["counts"][
        "covered_by_egress_guard"] == sum(
        1 for d in deps if d["enforced_by_egress_guard"])


# ── 序列化 ──────────────────────────────────────────────────────────


def test_catalog_to_dict_json_stable():
    payload = nd.catalog_to_dict()
    assert payload["schema_version"] == nd.CATALOG_SCHEMA_VERSION
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    again = json.dumps(nd.catalog_to_dict(), ensure_ascii=False, sort_keys=True)
    assert text == again  # 同配置下序列化确定（重复生成 manifest 可 diff）


def test_endpoint_resolution_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "OVERPASS_API_URL",
                        "https://overpass.example.internal/api")
    nd.reset_catalog_settings_cache()
    by_id = {d.id: d for d in nd.network_dependencies()}
    assert by_id["osm_overpass"].resolved_endpoint() == \
        "https://overpass.example.internal/api"


# ── 工具面交叉矩阵（registry 未注入时诚实降级）────────────────────────


def test_tool_matrix_degrades_honestly_without_registry():
    from app.agent_pi_bridge import get_tool_registry

    with pytest.raises(Exception):
        get_tool_registry()
    matrix = nd.tool_network_matrix()
    assert matrix["status"] == "registry_unavailable"
    assert matrix["network_tools"] == []


def test_tool_matrix_lists_network_tools():
    """registry 已注入（conftest 环境）时能列出 network=True 工具。"""
    try:
        from app.agent_pi_bridge import get_tool_registry
        registry = get_tool_registry()
    except Exception:
        pytest.skip("tool registry not initialized in this test env")
    matrix = nd.tool_network_matrix(registry=registry)
    assert matrix["status"] == "ok"
    assert matrix["counts"]["network_true"] >= 30  # Phase 0 勘察 ~45
    entry = matrix["network_tools"][0]
    assert {"tool", "offline_fallback", "fallback_description"} <= set(entry)
