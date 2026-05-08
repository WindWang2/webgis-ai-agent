"""What-if scenario rule dictionaries."""

WHAT_IF_RULES = {
    "subway": {
        "name": "新建地铁站",
        "direct_radius_m": 500,
        "indirect_radius_m": 1500,
        "impact": {
            "housing_price": {"direct": (0.15, 0.25), "indirect": (0.05, 0.10)},
            "rent": {"direct": (0.10, 0.18), "indirect": (0.03, 0.06)},
            "commute_time": {"direct": (-0.15, -0.05), "indirect": (-0.05, 0.0)},
            "commercial_vitality": {"direct": (0.20, 0.40), "indirect": (0.05, 0.15)},
        },
    },
    "school": {
        "name": "新建学校",
        "direct_radius_m": 500,
        "indirect_radius_m": 1000,
        "impact": {
            "housing_price": {"direct": (0.08, 0.15), "indirect": (0.03, 0.06)},
            "education_access": {"direct": (0.30, 0.50), "indirect": (0.10, 0.20)},
            "rent": {"direct": (0.05, 0.12), "indirect": (0.02, 0.05)},
        },
    },
    "hospital": {
        "name": "新建医院",
        "direct_radius_m": 1500,
        "indirect_radius_m": 3000,
        "impact": {
            "housing_price": {"direct": (0.05, 0.10), "indirect": (0.02, 0.05)},
            "medical_access": {"direct": (0.40, 0.60), "indirect": (0.15, 0.25)},
        },
    },
    "population_growth": {
        "name": "人口增长",
        "direct_radius_m": None,
        "indirect_radius_m": None,
        "impact_per_10pct": {
            "housing_demand": (0.08, 0.12),
            "traffic_load": (0.10, 0.15),
            "school_demand": (0.10, 0.15),
            "hospital_demand": (0.05, 0.10),
            "commercial_demand": (0.08, 0.12),
        },
    },
    "traffic_restriction": {
        "name": "交通限行",
        "direct_radius_m": None,
        "indirect_radius_m": None,
        "impact": {
            "road_saturation": (-0.20, -0.10),
            "public_transit_usage": (0.15, 0.30),
            "commute_time": (0.05, 0.15),
            "air_quality": (0.05, 0.15),
        },
    },
    "park": {
        "name": "新建公园",
        "direct_radius_m": 300,
        "indirect_radius_m": 800,
        "impact": {
            "housing_price": {"direct": (0.05, 0.10), "indirect": (0.02, 0.05)},
            "living_quality": {"direct": (0.15, 0.25), "indirect": (0.05, 0.10)},
        },
    },
}


def get_rule(scenario_type: str) -> dict:
    """Return the rule dict for a given scenario type key."""
    return WHAT_IF_RULES.get(scenario_type, {})


def list_scenarios() -> list[dict]:
    """Return a list of scenario info dicts with type, name, and radii."""
    return [
        {
            "type": key,
            "name": value["name"],
            "direct_radius_m": value.get("direct_radius_m"),
            "indirect_radius_m": value.get("indirect_radius_m"),
        }
        for key, value in WHAT_IF_RULES.items()
    ]
