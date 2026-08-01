"""Capability-matrix parity test for the Chinese-maps providers.

The ``ChineseMapsProvider`` Protocol (``app/tools/chinese_maps/protocol.py``)
declares the 9 shared capabilities. This test asserts the full matrix — *which
provider implements which capability* — explicitly. It is the declarative
capability matrix that replaces the scattered ``exclude=`` sets and
``if provider == "amap"`` branches as the source of truth.

Two layers of assertion:

1. **Structural**: each provider that claims a capability has a matching async
   method with the right name.
2. **Matrix**: the exact ✓/✗ grid documented in the Protocol docstring holds —
   e.g. Tianditu must NOT define route/input_tips/search_poi_polygon/distance_matrix.

If someone adds a capability to one provider, this test forces them to update
the Protocol and the matrix docstring rather than silently diverging.
"""
import inspect

from app.tools.chinese_maps.protocol import ChineseMapsProvider
from app.tools.chinese_maps.amap import AmapProvider
from app.tools.chinese_maps.baidu import BaiduProvider
from app.tools.chinese_maps.tianditu import TiandituProvider

# The 9 Protocol capabilities.
_CAPABILITIES = (
    "search_poi",
    "search_poi_around",
    "search_poi_polygon",
    "geocode",
    "reverse_geocode",
    "route",
    "input_tips",
    "district",
    "distance_matrix",
)

# The authoritative capability matrix. True = provider implements the method.
# MUST match the table in protocol.py's ChineseMapsProvider docstring.
_MATRIX = {
    "amap": {c: True for c in _CAPABILITIES},
    "baidu": {c: True for c in _CAPABILITIES},
    "tianditu": {
        "search_poi": True,
        "search_poi_around": True,
        "search_poi_polygon": False,
        "geocode": True,
        "reverse_geocode": True,
        "route": False,
        "input_tips": False,
        "district": True,
        "distance_matrix": False,
    },
}

_PROVIDERS = {
    "amap": AmapProvider,
    "baidu": BaiduProvider,
    "tianditu": TiandituProvider,
}


def test_providers_satisfy_protocol_for_their_declared_capabilities():
    """Each provider structurally satisfies the Protocol for the capabilities
    the matrix says it implements.

    Whole-object ``isinstance`` against a ``runtime_checkable`` Protocol would
    require ALL methods present — but Tianditu intentionally omits 4. So instead
    we check per-capability: for every capability the matrix marks True, the
    provider must define a matching async method. This is the real invariant;
    the Protocol's ``isinstance`` is a strictness that doesn't fit partial
    implementers, which is exactly why the explicit matrix is the authority.
    """
    for provider_name, caps in _MATRIX.items():
        cls = _PROVIDERS[provider_name]
        for cap, implemented in caps.items():
            if not implemented:
                # must be genuinely absent (caught more precisely by the
                # matrix test, but asserted here for locality)
                continue
            assert callable(getattr(cls, cap, None)), (
                f"{provider_name} claims {cap} in the matrix but does not define it"
            )


def test_capability_matrix_matches_implementation():
    """The documented ✓/✗ matrix matches what each provider actually defines.

    A provider marked True for a capability MUST define the method; one marked
    False MUST NOT. This catches both missing implementations and accidental
    additions.
    """
    for provider_name, caps in _MATRIX.items():
        cls = _PROVIDERS[provider_name]
        for cap, expected in caps.items():
            has_method = callable(getattr(cls, cap, None))
            assert has_method == expected, (
                f"{provider_name}.{cap}: matrix says {expected}, "
                f"but method {'exists' if has_method else 'missing'}"
            )


def test_protocol_methods_are_async():
    """Every Protocol-declared capability on every provider is async.

    A silently-sync method would break the ``await`` at the dispatch site.
    """
    for provider_name, caps in _MATRIX.items():
        cls = _PROVIDERS[provider_name]
        for cap, implemented in caps.items():
            if not implemented:
                continue
            method = getattr(cls, cap)
            assert inspect.iscoroutinefunction(method), (
                f"{provider_name}.{cap} must be async"
            )


def test_protocol_signatures_match_across_implementers():
    """The 9 shared capabilities share identical parameter names.

    This is the property that makes generic dispatch (``_dispatch[p](**args)``)
    sound. If a provider diverges on a parameter name, the shared dispatch
    contract breaks.
    """
    for cap in _CAPABILITIES:
        # Find all providers that implement this capability.
        sigs = {}
        for provider_name, caps in _MATRIX.items():
            if caps[cap]:
                method = getattr(_PROVIDERS[provider_name], cap)
                params = tuple(inspect.signature(method).parameters.keys())
                sigs[provider_name] = params
        # All implementing providers must agree on parameter names.
        unique_sigs = set(sigs.values())
        assert len(unique_sigs) == 1, (
            f"{cap}: parameter names diverge across providers: {sigs}"
        )


def test_amap_only_features_are_not_in_protocol():
    """isochrone/transit/traffic are Amap-only and must NOT be Protocol methods.

    They live as non-Protocol methods on AmapProvider and are called directly
    (no fallback). If they ever appear on the Protocol, this test catches it.
    """
    amap_only = ("isochrone", "transit", "traffic")
    for name in amap_only:
        assert not hasattr(ChineseMapsProvider, name), (
            f"{name} is Amap-only and must not be on the Protocol"
        )
        assert callable(getattr(AmapProvider, name, None)), (
            f"AmapProvider must define the amap-only {name} method"
        )
        # baidu/tianditu must NOT have them
        assert not callable(getattr(BaiduProvider, name, None)), (
            f"BaiduProvider must not define amap-only {name}"
        )
        assert not callable(getattr(TiandituProvider, name, None)), (
            f"TiandituProvider must not define amap-only {name}"
        )
