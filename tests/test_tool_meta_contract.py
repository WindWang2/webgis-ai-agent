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
    real registry tool names (or legacy aliases the dispatch normalizes). The
    compiled runtime artifact (index.mjs) is checked — the extension ships from
    the .mjs (the index.ts dead copy was removed, AH-P3-1)."""
    import pathlib

    ext_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "extensions" / "webgis-tools"
    registered = set(registry.all_metadata().keys()) | set(
        LEGACY_TOOL_NAME_MAP.keys()
    )
    for artifact in ("index.mjs",):
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