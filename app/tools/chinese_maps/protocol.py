"""The Chinese-maps provider capability contract.

``ChineseMapsProvider`` is the declarative capability matrix for the three
Chinese map providers (Amap / Baidu / Tianditu). It lists the **9 shared
capabilities** whose call signatures are identical across providers; which
provider implements which capability is otherwise implicit in the dispatch
layer's ``exclude=`` sets.

The Protocol is the single source of truth for "what a Chinese-maps provider
must offer." Adding a new capability means adding a method here and implementing
it on each provider; adding a new provider means implementing every method here.

Not all providers implement every capability — Tianditu omits route /
input_tips / search_poi_polygon. A provider that does not implement a method
the dispatch layer would route to it is simply not in that capability's
``exclude=`` allow-set. ``isinstance`` against this Protocol therefore checks
*structural* compatibility of the methods a provider *does* define; the parity
test in ``tests/unit/test_protocol_parity.py`` asserts the full matrix
explicitly.

This is intentionally a separate module from the provider implementations so
they carry no cross-provider import coupling — the Protocol is referenced only
by the parity test and (optionally) by type annotations at the dispatch site.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class ChineseMapsProvider(Protocol):
    """The 9 shared Chinese-maps capabilities.

    Capability matrix (which provider implements which method):

    | capability         | amap | baidu | tianditu |
    |--------------------|:----:|:-----:|:--------:|
    | search_poi         |  ✓   |   ✓   |    ✓     |
    | search_poi_around  |  ✓   |   ✓   |    ✓     |
    | search_poi_polygon |  ✓   |   ✓   |    ✗     |
    | geocode            |  ✓   |   ✓   |    ✓     |
    | reverse_geocode    |  ✓   |   ✓   |    ✓     |
    | route              |  ✓   |   ✓   |    ✗     |
    | input_tips         |  ✓   |   ✓   |    ✗     |
    | district           |  ✓   |   ✓   |    ✓     |
    | distance_matrix    |  ✓   |   ✓   |    ✗     |

    The three Amap-only features (isochrone / transit / traffic) are NOT in
    this Protocol — they live as non-Protocol methods on ``AmapProvider`` and
    are called directly (no fallback). See CONTEXT.md "Chinese Maps Provider".
    """

    async def search_poi(self, keyword: str, city: str, limit: int) -> dict: ...

    async def search_poi_around(
        self, center: list, radius_m: int, keyword: str, types: str, limit: int,
    ) -> dict: ...

    async def search_poi_polygon(
        self, polygon: list, keyword: str, types: str, limit: int,
    ) -> dict: ...

    async def geocode(self, address: str, city: str) -> dict: ...

    async def reverse_geocode(self, lng: float, lat: float) -> dict: ...

    async def route(self, origin: list, dest: list, mode: str, city: str) -> dict: ...

    async def input_tips(
        self, keyword: str, city: str, location: Optional[list],
    ) -> dict: ...

    async def district(
        self, keywords: str, level: str, return_geometry: str = "point",
    ) -> dict: ...

    async def distance_matrix(
        self, origins: list[list], destinations: list[list], mode: str,
    ) -> dict: ...
