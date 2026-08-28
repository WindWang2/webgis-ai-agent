"""#556 contract: the domain vocabulary advertised by ``list_available_tools``
and the Pi extension's example tool names must match the LIVE registry.

Previously the ``ListAvailableToolsArgs.domain`` description was a hand-written
list claiming ``core`` / ``report`` domains that have ZERO tools in the
registry (dead end for the LLM) while omitting four real ones
(``temporal`` / ``data_fabric`` / ``spatial_catalog`` / ``dataset``), and the
Pi extension's ``webgis_execute`` parameter description cited example tool
names (``spatial_analyze`` / ``raster_ndvi``) that do not exist — following
them literally caused UNKNOWN_TOOL round trips.

The contract pinned here: every domain the tool advertises must return ≥1
tool, and every example name published by the Pi extension must resolve in
the live registry (mirror of the #438 name-integrity gate).
"""
import re

import pytest

from app.services.chat.pi_native_surface import NATIVE_TOOL_NAMES, native_tools_for_pi
from app.services.tool_dispatch_service import LEGACY_TOOL_NAME_MAP


@pytest.fixture(scope="module")
def registry():
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry

    r = ToolRegistry()
    init_tools(r)
    return r


def _list_available_tools_schema(registry) -> dict:
    """Return the JSON schema of the list_available_tools tool."""
    for schema in registry.get_schemas():
        fn = schema.get("function", {})
        if fn.get("name") == "list_available_tools":
            return schema
    raise AssertionError("list_available_tools is not registered")


def _advertised_domains(registry) -> list[str]:
    schema = _list_available_tools_schema(registry)
    desc = (
        (schema.get("function", {}).get("parameters", {})
         .get("properties", {}).get("domain", {}).get("description", ""))
    )
    assert "取值之一：" in desc, f"domain description lost its vocab marker: {desc!r}"
    vocab = desc.split("取值之一：", 1)[1]
    return [d.strip() for d in vocab.split("/") if d.strip()]


def _registry_domains(registry) -> dict[str, int]:
    """domain -> number of tools carrying it (from the registry's own metadata)."""
    counts: dict[str, int] = {}
    for meta in registry.all_metadata().values():
        for d in meta.get("domains", []):
            counts[d] = counts.get(d, 0) + 1
    return counts


def test_list_available_tools_advertised_domains_all_have_tools(registry):
    counts = _registry_domains(registry)
    for domain in _advertised_domains(registry):
        assert counts.get(domain, 0) >= 1, (
            f"list_available_tools advertises domain {domain!r} which has no "
            "tools in the live registry — an LLM following the description "
            "hits a zero-result dead end"
        )


def test_list_available_tools_vocab_covers_real_domains(registry):
    """The live vocabulary must include the real high-value domains and must
    NOT claim ghost domains. #678 consolidated data_fabric/spatial_catalog
    into `dataset` and gave report/export tools the `report` domain — the
    pinned vocab follows the live registry (#556 contract stays drift-proof:
    advertised == real)."""
    advertised = set(_advertised_domains(registry))
    for real in ("temporal", "dataset", "raster", "network", "statistics",
                 "report", "chinese"):
        assert real in advertised, f"real domain {real!r} missing from the advertised vocab"
    assert "core" not in advertised
    # 已收敛的死域不得复活（#678：统一进 dataset）
    assert "data_fabric" not in advertised
    assert "spatial_catalog" not in advertised


@pytest.mark.asyncio
async def test_report_domain_query_truthful(registry):
    """list_available_tools('report') must return the true count — the
    #556 honesty contract holds in both regimes (0 tools or N tools)."""
    counts = _registry_domains(registry)
    result = await registry.dispatch(
        "list_available_tools", {"domain": "report"}, session_id="contract"
    )
    assert result["count"] == counts.get("report", 0), (
        f"list_available_tools('report') returned {result['count']} but the live "
        f"registry carries {counts.get('report', 0)} report-domain tools"
    )


def test_extension_example_names_exist(registry):
    """The Pi extension's webgis_execute toolName description examples must be
    real registry tool names (or legacy aliases the dispatch normalizes). Both
    the source (index.ts) and the compiled runtime artifact (index.mjs) are
    checked — the extension ships from the .mjs."""
    import pathlib

    ext_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "extensions" / "webgis-tools"
    registered = set(registry.all_metadata().keys()) | set(
        LEGACY_TOOL_NAME_MAP.keys()
    )
    for artifact in ("index.ts", "index.mjs"):
        text = (ext_dir / artifact).read_text(encoding="utf-8")
        m = re.search(r"e\.g\.,\s*([^)\"]+)", text)
        assert m, f"could not find the toolName example list in {artifact}"
        examples = [x.strip() for x in m.group(1).split(",") if x.strip()]
        assert examples, f"no example tool names found in {artifact}"

        missing = [e for e in examples if e not in registered]
        assert not missing, (
            f"{artifact} advertises example tool names that do not resolve in "
            f"the live registry (UNKNOWN_TOOL round trips): {missing}"
        )


def test_native_name_lists_pinned_equal_across_sources(registry):
    """#1044 contract: the native tool name list lives in three places —
    Python ``NATIVE_TOOL_NAMES`` (dispatch surface), the extension's
    ``FALLBACK_NATIVE`` (prompt vocabulary), and the spawn-time schema dump
    (what Pi actually registers). Drift between them means either the prompt
    advertises natives Pi cannot call or the dispatch surface rejects names
    the prompt steers toward. Pin all three equal; the .mjs is the shipped
    artifact (index.ts is the documented dead copy)."""
    import pathlib

    ext = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "extensions" / "webgis-tools" / "index.mjs"
    ).read_text(encoding="utf-8")
    m = re.search(r"const FALLBACK_NATIVE\s*=\s*\[([^\]]*)\]", ext)
    assert m, "could not locate FALLBACK_NATIVE array in index.mjs"
    fallback = re.findall(r'"([^"]+)"', m.group(1))
    assert fallback, "FALLBACK_NATIVE parsed to an empty list"

    assert set(fallback) == set(NATIVE_TOOL_NAMES), (
        f"extension FALLBACK_NATIVE drifted from NATIVE_TOOL_NAMES: "
        f"only-in-extension={sorted(set(fallback) - set(NATIVE_TOOL_NAMES))} "
        f"only-in-python={sorted(set(NATIVE_TOOL_NAMES) - set(fallback))}"
    )

    dumped = {item["name"] for item in native_tools_for_pi(registry)}
    assert dumped == set(NATIVE_TOOL_NAMES), (
        f"spawn-time dump drifted from NATIVE_TOOL_NAMES: "
        f"only-in-dump={sorted(dumped - set(NATIVE_TOOL_NAMES))} "
        f"only-in-python={sorted(set(NATIVE_TOOL_NAMES) - dumped)}"
    )


def test_extension_routes_distribution_before_status_pull():
    """Native GIS tools + execute tail. The snippet used to advertise
    webgis_cartography_status as the GIS example, so live turns stuffed
    city/topic/scope into that zero-arg verdict pull. The prompt must name
    the real first-turn tools and pin status to {}."""
    import pathlib

    text = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "extensions" / "webgis-tools" / "index.mjs"
    ).read_text(encoding="utf-8")
    assert "webgis_map_intent" in text
    assert "query_local_poi" in text
    assert "get_local_admin_boundary" in text
    assert "webgis_cartography_status {}" in text or "webgis_cartography_status', {})" in text
    assert "Never pass city" in text or "does not accept city" in text
    assert "--no-builtin-tools" not in text  # spawn flag lives in the RPC client
    assert "before_agent_start" in text
    assert "GeoAgent" in text
    assert "WEBGIS_NATIVE_TOOLS_PATH" in text