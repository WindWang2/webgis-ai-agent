"""One synchronous validation snapshot, executed outside the event loop."""


def validate_snapshot(mapspec: dict[str, object]) -> dict[str, object]:
    from app.services.mapspec.coordinator import validate
    from app.lib.cartography.quality_loop import review_cartography
    from app.lib.cartography.grammar_propagation import grammar_auditor_for_mapspec

    result = validate(mapspec)
    review = review_cartography(
        mapspec, grammar_decision=grammar_auditor_for_mapspec(mapspec),
    ).to_dict()
    result["mapspec_fingerprint"] = review["final_fingerprint"]
    result["cartographic_review"] = review
    return result
