"""Moving-object trajectory domain (GOAL-20261127-02).

Entity-observed, time-stamped trajectory construction and spatiotemporal
event analysis. This package owns mobile-object semantics only; generic
temporal profiling/filtering/aggregation stays in ``app.services.temporal``.

Modules are pure domain (no I/O, no stores) unless stated otherwise:
- ``contracts``  — pydantic contracts, hard caps, typed rejections
- ``ingest``     — point table → normalized per-entity observation streams
- ``builder``    — trajectory segmentation (gaps/jumps are explicit breaks)
- ``motion``     — interval motion quantities (dt, geodesic distance, speed,
                   bearing, cumulative path length) with no fake zeros/inf
- ``dwell``      — radius+duration dwell detection with evidence
- ``quality``    — diagnostics separated from cleaning suggestions
- ``events``     — region enter/exit/cross/dwell/unknown events with
                   sampling-interval honesty (inferred intervals are labeled)
- ``aggregate``  — entity/region level event summaries with honest denominators
- ``service``    — orchestration seam shared by the tool face and REST API
- ``oracles``    — deterministic synthetic trajectory generators (tests)
"""
