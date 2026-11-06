"""Review-R2 S14 regressions: declarative templates, bounds and honest exports."""
import math
import numpy as np
import pytest
from app.lib.cartography.component_abi import validate_props, validate_component_abi
from app.lib.cartography.component_registry import get_component_registry
from app.lib.cartography.component_templates import SEED_COMPONENT_TEMPLATES
from app.lib.cartography.component_composer import plan_layout_repairs
from app.lib.cartography.category_collapse import apply_collapse, OTHER_KEY
from app.lib.cartography.classify import classify_values


def test_every_native_seed_options_pass_abi():
    for template in SEED_COMPONENT_TEMPLATES:
        if template.runtime_status == 'native':
            assert not validate_props(template.component_type, template.default_options), template.id
    assert not validate_component_abi(get_component_registry())


def test_extra_keys_cannot_hide_invalid_declared_property():
    props = {f'a{index:02}': 'junk' for index in range(40)}
    props['variant'] = 'evilevilevil'
    errors = validate_props('north_arrow', props)
    assert 'props_invalid:__:too_many_fields' in errors
    assert 'props_invalid:variant:not_in_enum' in errors


@pytest.mark.parametrize('value', [math.nan, math.inf, -math.inf])
def test_abi_rejects_nonfinite_numeric_options(value):
    assert validate_props('graticule', {'opacity': value}) == ['props_invalid:opacity:not_finite']


def test_no_nonfinite_values_enter_classification():
    assert classify_values([math.nan, 1, math.inf, 2], 'quantiles', 2) == [1, 1.5, 2]
    assert classify_values([math.nan, math.inf], 'natural_breaks', 2) == []


def test_sampled_jenks_retains_full_data_extrema():
    values = np.random.default_rng(15).lognormal(0, 2, 1100).tolist()
    breaks = classify_values(values, 'natural_breaks', 4)
    assert breaks[0] == min(values)
    assert breaks[-1] == max(values)


def test_collapse_preserves_category_type_and_matches_string_legend_keys():
    outcome = apply_collapse([1, '2', True], colors=['#000'], keep_classes=2)
    assert outcome.collapse_key_for(True) == OTHER_KEY
    assert outcome.collapse_key_for(1.0) == OTHER_KEY
    assert outcome.collapse_key_for(1) == outcome.entries[0]['key'] == '1'


def test_overflow_never_displaces_title_from_exclusive_center():
    components = [{'id': 'title', 'type': 'title', 'position': 'top-center', 'priority': 10}]
    components += [{'id': str(index), 'type': 'annotation', 'position': 'top-left', 'priority': index} for index in range(4)]
    actions = plan_layout_repairs(components)
    assert not any(action.get('component_id') == 'title' and action.get('type') == 'change_anchor' for action in actions)
    # Invalid metadata is not a reason to crash an entire repair plan.
    plan_layout_repairs([{'id': 'a', 'type': 'annotation', 'position': 'top-left', 'priority': 'bad'}])


def test_profile_uses_profile_ref_not_layer_binding():
    assert not get_component_registry().get_by_type('elevation_profile').requires_layer_binding


def test_single_character_keyword_not_matched_inside_arbitrary_query():
    hits = get_component_registry().search('北京人口密度')
    north = next((hit for hit in hits if hit.descriptor.type == 'north_arrow'), None)
    assert north is None or 'keyword' not in north.matched_fields
