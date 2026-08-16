"""Regression tests for #431: PostGIS _parse_safe_where must anchor operator
detection to the COLUMN region, not scan the whole expression.

Root cause: the operator was located via ``stripped.find(op)`` over the entire
expression, so a quoted *value* containing ``>``, ``<``, ``>=`` or ``<=``
(e.g. ``name = 'a>b'``) anchored the operator inside the value, the column
slice failed the identifier regex, and the parser raised ``ValueError:
Unsafe column name`` — valid filters were false-rejected. Values are bound
parameters, so operator characters inside values carry zero injection risk.

These tests are RED on the pre-fix parser and GREEN after the fix.
"""
import pytest

from app.services.data_fabric.adapters.postgis_adapter import _parse_safe_where


# ---------------------------------------------------------------------------
# Valid filters whose VALUES contain operator characters (the #431 bug)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "expr, expected_sql, expected_param",
    [
        ("name = 'a>b'", '"name" = %s', "a>b"),
        ("name = 'a<b'", '"name" = %s', "a<b"),
        ("name = 'a>=b'", '"name" = %s', "a>=b"),
        ("name = 'a<=b'", '"name" = %s', "a<=b"),
        ("name = 'a!=b'", '"name" = %s', "a!=b"),
        ("name = 'a=b'", '"name" = %s', "a=b"),
        ("status = 'active<2'", '"status" = %s', "active<2"),
        ("status = 'active>2'", '"status" = %s', "active>2"),
        ("code >= 'x<y'", '"code" >= %s', "x<y"),
        ("label != 'v>w'", '"label" != %s', "v>w"),
        ("label <= 'v>=w'", '"label" <= %s', "v>=w"),
        ("note LIKE '%a>b%'", '"note" LIKE %s', "%a>b%"),
        ("note LIKE '%a>=b%'", '"note" LIKE %s', "%a>=b%"),
        ("sym = '>'", '"sym" = %s', ">"),
        ("sym = '<='", '"sym" = %s', "<="),
        ('name = "a>b"', '"name" = %s', "a>b"),  # double-quoted value
        ("name = '->arrow'", '"name" = %s', "->arrow"),  # operator char at value start
    ],
)
def test_quoted_value_with_operator_chars_parses(expr, expected_sql, expected_param):
    """A quoted value may legitimately contain every operator character."""
    sql, params = _parse_safe_where(expr)
    assert sql == expected_sql
    assert params == [expected_param]


def test_operator_in_value_does_not_confuse_real_operator():
    """`pop > 100` and `name = 'a>b'` must both work — operator position is
    determined by the column region, never by value content."""
    assert _parse_safe_where("pop > 100") == ('"pop" > %s', [100])
    assert _parse_safe_where("name = 'a>b'") == ('"name" = %s', ["a>b"])


def test_no_space_forms_with_operator_chars_in_value():
    """Tight forms (no whitespace around the operator) with operator chars in
    the quoted value must still parse."""
    sql, params = _parse_safe_where("name='a>b'")
    assert sql == '"name" = %s'
    assert params == ["a>b"]
    sql2, params2 = _parse_safe_where("pop>100")
    assert sql2 == '"pop" > %s'
    assert params2 == [100]


def test_long_column_name_not_eaten_by_operator_scan():
    """A column like ``popularity`` must not be truncated by a ``<`` scan
    hitting the value first (regression guard for the new parser)."""
    sql, params = _parse_safe_where("popularity < 'x<y'")
    assert sql == '"popularity" < %s'
    assert params == ["x<y"]


# ---------------------------------------------------------------------------
# Genuinely unsafe inputs must stay rejected (no security regression)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "expr",
    [
        "1=1",                      # injection attempt
        "type = 'x' OR 1=1",        # conjunction inside value
        "name; DROP TABLE users",   # statement injection
        "type=(SELECT 1)",          # unquoted subquery
        "x = (SELECT 1)",
        "col == 5",                 # invalid operator
        "",
        "   ",
        "= 5",                      # missing column
        "col >",                    # missing value
        "co l > 5",                 # space in identifier
        "x = 1 UNION SELECT 2",     # unquoted union
        "x = DROP",                 # unquoted keyword
        "name = 'a' AND 1=1",       # quoted value + conjunction
        "x' OR '1'='1",             # quote-break attempt
    ],
)
def test_unsafe_expressions_still_rejected(expr):
    with pytest.raises(ValueError):
        _parse_safe_where(expr)


def test_operator_chars_do_not_bypass_value_literal_checks():
    """Operator chars in a value must not open a bypass for SQL structure:
    an unquoted value containing a conjunction is still rejected."""
    with pytest.raises(ValueError):
        _parse_safe_where("name = a>b OR 1=1")
