"""Unit-bearing records produced by the terrain measurement kernels.

Array samples stay internal; public profiles and evidence are JSON records.
Nullable elevations distinguish unsampled/nodata positions from zero height.
"""
from __future__ import annotations

import numpy as np
from numpy.typing import NDArray
from typing_extensions import NotRequired, TypedDict


class VerticalReference(TypedDict):
    vertical_datum: str
    z_units: str
    z_to_m: float
    flags: list[str]
    transformations: list[str]


class PolylineSamples(TypedDict):
    xs: NDArray[np.float64]
    ys: NDArray[np.float64]
    d: NDArray[np.float64]
    z: NDArray[np.float64]
    n_samples: int
    horizontal_m: float
    n_invalid: int


class IntervalStatistics(TypedDict):
    surface_distance_m: float
    ascent_m: float
    descent_m: float
    covered_horizontal_m: float
    n_valid_pairs: int
    n_dropped_pairs: int


class ProfilePoint(TypedDict):
    x: float
    y: float
    z_m: float | None
    distance_m: float
    slope: NotRequired[float | None]


class DistanceStatistics(TypedDict):
    horizontal_m: float
    surface_distance_m: float
    ascent_m: float
    descent_m: float
    covered_horizontal_m: float
    z_start_m: float | None
    z_end_m: float | None
    z_min_m: NotRequired[float | None]
    z_max_m: NotRequired[float | None]
    z_diff_m: NotRequired[float | None]
    slope_distance_m: NotRequired[float | None]


class TerrainProfile(TypedDict):
    statistics: DistanceStatistics
    points: list[ProfilePoint]


class CurvatureEvidence(TypedDict):
    model: str
    refraction_coefficient: float | None
    effective_earth_radius_m: float | None
    bulge_formula: NotRequired[str]


class SightObstruction(TypedDict):
    x: float
    y: float
    distance_from_observer_m: float
    terrain_z_m: float
    terrain_z_effective_m: float
    sight_z_m: float
    clearance_m: float


class SightStatistics(TypedDict):
    visible: bool
    obstruction: SightObstruction | None
    min_clearance_m: float | None
    min_clearance_distance_m: float | None
    horizontal_m: float
    slope_distance_m: float
    eye_z_observer_m: float
    eye_z_target_m: float
    terrain_z_observer_m: float
    terrain_z_target_m: float


class LineOfSight(TypedDict):
    statistics: SightStatistics
    terrain_profile: list[ProfilePoint]
    sight_profile: list[ProfilePoint]


class DistanceConvergence(TypedDict):
    half_step_m: float
    surface_distance_half_step_m: float
    delta_rel: float


class AreaStatistics(TypedDict):
    planar_area_m2: float
    surface_area_m2: float
    roughness_ratio: float
    usable_quads: int
    candidate_quads: int
    boundary_centers_without_facet: int


class MeasurementMetadata(TypedDict):
    vertical_datum: str
    z_units: str
    z_unit_transformations: list[str]
    quality_flags: list[str]
    method: str
    sampling_step_requested_m: NotRequired[float]
    sampling_step_effective_m: NotRequired[float]
    sample_count: NotRequired[int]
    sample_points: NotRequired[list[ProfilePoint]]
    sample_point_stride: NotRequired[int]
    output_point_count: NotRequired[int]
    invalid_sample_count: NotRequired[int]
    nodata_policy: NotRequired[str]
    max_invalid_fraction: NotRequired[float]
    curvature: NotRequired[CurvatureEvidence]
    grazing_tolerance_m: NotRequired[float]
    visibility_symmetry: NotRequired[str]
    convergence: NotRequired[DistanceConvergence | None]
