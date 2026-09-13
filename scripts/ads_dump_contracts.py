#!/usr/bin/env python
"""Dump ads-v1 contract JSON Schemas (DS0, ADR-0170).

Run after any additive contract change:

    ./.venv/Scripts/python scripts/ads_dump_contracts.py

Writes docs/dev/ads-v1-contracts/{d1,d2,d3,d4}*.schema.json. The contract
test (tests/unit/test_data_fabric_ads_contracts.py) fails if these dumps
drift from the models, so this script is the only sanctioned way to change
them.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app.services.data_fabric import contracts as C  # noqa: E402

OUT_DIR = REPO / "docs" / "dev" / "ads-v1-contracts"

DUMPS = [
    (C.D1DatasetDescriptor, "d1_dataset_descriptor.schema.json"),
    (C.AcquisitionPlan, "d2_acquisition_plan.schema.json"),
    (C.FallbackDecision, "d3_fallback_decision.schema.json"),
    (C.AcquisitionFact, "d4_acquisition_fact.schema.json"),
]


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for model, filename in DUMPS:
        schema = model.model_json_schema()
        schema["$id"] = f"https://webgis-ai-agent.local/schemas/ads-v1/{filename}"
        schema["x-ads-contract-version"] = C.CONTRACTS_VERSION
        path = OUT_DIR / filename
        path.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
