"""Typed terrain evidence and numerical products.

Metadata fields are optional because algorithms disclose different facts. Each
field retains its concrete unit-bearing value type; array products are separate
from JSON-serializable evidence. This module does not validate or coerce data.
"""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from typing_extensions import NotRequired, TypedDict


class LevelSegments(TypedDict):
    level: int
    segments: int


class TerrainFacts(TypedDict, total=False):
    altitude_degrees: float
    approximation: str
    azimuth_count: int
    azimuth_policy: str
    azimuths: list[float]
    boundary: str
    breached_cells: int
    carved_volume: float
    cell_size: float
    cell_size_x: float
    clamping: str
    class_codes: dict[str, str]
    class_distribution: dict[str, int]
    code_distribution: dict[str, int]
    combine: str
    converged: bool
    day_of_year: int
    decision_table: str
    degenerate: bool
    dem_note: str
    desmet_n: float
    distance_convention: str
    distinct_codes: int
    earth_curvature_refraction: str
    edge_policy: str
    elevation_range: list[float]
    elevation_tolerance: float
    encoding: str
    epsilon: float
    extraterrestrial_ra: float
    fallback_filled_cells: int
    far_cells: float
    fill_meta_volume: float | None
    filled_cell_count: int
    filled_volume: float
    filled_volume_units: str
    flat_epsilon: float
    flat_reference: str
    flat_routing: str
    flats: str
    flatten_degrees: float
    flow_split: str
    formula: str
    grid_cells: int
    hand_range: list[float | None]
    hierarchy_note: str
    hypsometric_integral: float
    insolation_range: list[float]
    is_consistent: bool
    latitude_deg: float
    length_convention: str
    level_segments: list[LevelSegments]
    levels: int
    limitations_extra: str
    lookup_radius_cells: int
    m_table: str
    magnitude_distribution_top: dict[str, int]
    mainstem_cells: int
    max_breach_depth_applied: float
    max_distance_m: float
    max_fill_depth: float
    max_length_m: float
    max_magnitude: int
    max_order: int
    max_sweeps: int
    max_tributaries: int
    mean_insolation: float
    mean_length_m: float
    method: str
    method_references_note: str
    mode: str
    model: str
    n_azimuths: int
    n_bands: int
    n_depressions: int
    n_levels: int
    no_flow_sentinel: str
    numerical_tolerance: str
    observer_height: float
    observer_row_col: tuple[float, float]
    order_distribution: dict[str, int]
    outlet: list[int]
    processing_order: str
    radius_cells: int
    sca_convention: str
    sd_large: float
    sd_small: float
    sign_convention: str
    skipped_segments: int
    slope_units: str
    stream_cells: int
    stream_threshold: float
    sweeps_executed: int
    tan_beta_floor: float
    target_height: float
    threshold: float
    tie_break: str
    tpi_window_includes_center: bool
    tpi_window_large: int
    tpi_window_small: int
    transmissivity: float
    tributary_codes: dict[str, int]
    truth_source: str
    units: str
    unresolvable_cells: int
    unsampled_leg_policy: str
    visible_fraction_basis: str
    window: int
    window_includes_center: bool


class TerrainMetadata(TerrainFacts):
    algorithm: str
    cells_total: NotRequired[int]
    cells_valid: NotRequired[int]
    counting_convention: NotRequired[str]
    pour_cells: NotRequired[list[tuple[int, int]]]
    cells_in_watershed: NotRequired[int]
    includes_pour_point: NotRequired[bool]
    levels_policy: NotRequired[str]
    levels_requested: NotRequired[list[float]]
    levels_drawn: NotRequired[list[float]]
    feature_count: NotRequired[int]
    transform: NotRequired[list[float]]
    nodata_breaks_lines: NotRequired[bool]
    d8: NotRequired[D8Evidence]
    cells_valid_accum: NotRequired[int]
    pour_point: NotRequired[list[tuple[int, int]]]
    basin_length_convention: NotRequired[str]
    drainage_density: NotRequired[str]


class D8Evidence(TypedDict):
    encoding: str
    tie_break: str
    flats: str
    boundary: str


class ViewshedResult(TypedDict):
    visible: NDArray[np.bool_]
    visible_fraction: float
    visible_area_m2: float


class FlowTopologyReport(TypedDict):
    n_cells: int
    n_valid: int
    outlets: int
    outlet_samples: list[list[int]]
    cycles: int
    dangling_receivers: int
    dangling_samples: list[list[int]]
    out_of_bounds_receivers: int
    accumulation_violations: int
    violation_samples: list[list[int]]
    equal_accumulation_plateaus: int
    is_consistent: bool


class WatershedMetrics(TypedDict):
    pour_point_row_col: tuple[int, int]
    cell_count: int
    area_m2: float
    area_km2: float
    perimeter_m: float
    basin_length_m: float
    form_factor: float
    elongation_ratio: float
    relief_m: float
    relief_ratio: float
    stream_cells: NotRequired[int]
    max_stream_order: NotRequired[int]
    stream_length_m: NotRequired[float]
    drainage_density_km_per_km2: NotRequired[float | None]


class HorizonResult(TypedDict):
    azimuths: list[float]
    horizon: dict[str, NDArray[np.float64]]
    max: NDArray[np.float64]


class SkyViewResult(TypedDict):
    svf: NDArray[np.float64]
    azimuths: list[float]
    horizon: dict[str, NDArray[np.float64]]


class GeomorphonResult(TypedDict):
    classes: NDArray[np.integer]
    class_names: tuple[str, ...]
    ternary_legs: NDArray[np.integer]
    dn_run: NDArray[np.integer]
    up_run: NDArray[np.integer]


class LandformResult(TypedDict):
    classes: NDArray[np.integer]
    class_names: tuple[str, ...]
    tpi_standardized_small: NDArray[np.float64]
    tpi_standardized_large: NDArray[np.float64]
    elevation_percentile: NDArray[np.float64]


class D8RoutingFacts(TypedDict, total=False):
    flat_routing: str
    flat_epsilon: float
    filled_cell_count: int
    dem_note: str


class GeometryOutputBounds(TypedDict):
    max_polygons: int
    max_total_vertices: int


class RasterMaskMetadata(TypedDict):
    kind: str
    mask_cells: int
    polygonization: str
    output_bounds: GeometryOutputBounds
    polygon_count: NotRequired[int]
    attributed_cells: NotRequired[int]
    unattributed_cells: NotRequired[int]
    vertex_budget_exceeded: NotRequired[bool]
    dropped_polygon_count: NotRequired[int]
    area_threshold_cells: NotRequired[int]
    vertex_count: NotRequired[int]
    polygon_area_crs_units2: NotRequired[float]


class VisibilityGeometryMetadata(TypedDict):
    source: str
    visible_cells: int
    valid_cells: int
    total_cells: int
    valid_coverage_fraction: float
    attributed_visible_area_m2: float
    unattributed_visible_area_m2: float
    geometry_attribution_fraction: float
    effective_resolution_m: list[float]
    geometry_meta: RasterMaskMetadata
    method: str
