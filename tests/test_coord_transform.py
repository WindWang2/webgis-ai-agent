import math
from app.utils.coord_transform import (
    wgs84_to_gcj02, gcj02_to_wgs84,
    wgs84_to_bd09, bd09_to_wgs84,
    gcj02_to_bd09, bd09_to_gcj02,
)

def test_wgs84_gcj02_roundtrip():
    lng, lat = 116.4074, 39.9042
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    back_lng, back_lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
    assert abs(back_lng - lng) < 0.00001, f"lng drift: {back_lng - lng}"
    assert abs(back_lat - lat) < 0.00001, f"lat drift: {back_lat - lat}"

def test_wgs84_bd09_roundtrip():
    lng, lat = 116.4074, 39.9042
    bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
    back_lng, back_lat = bd09_to_wgs84(bd_lng, bd_lat)
    assert abs(back_lng - lng) < 0.00001, f"lng drift: {back_lng - lng}"
    assert abs(back_lat - lat) < 0.00001, f"lat drift: {back_lat - lat}"

def test_gcj02_bd09_roundtrip():
    lng, lat = 116.4074, 39.9042
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    bd_lng, bd_lat = gcj02_to_bd09(gcj_lng, gcj_lat)
    back_lng, back_lat = bd09_to_gcj02(bd_lng, bd_lat)
    assert abs(back_lng - gcj_lng) < 1e-5
    assert abs(back_lat - gcj_lat) < 1e-5

def test_shanghai_coordinates():
    lng, lat = 121.4737, 31.2304
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    diff_lng = abs(gcj_lng - lng) * 111000 * math.cos(math.radians(lat))
    diff_lat = abs(gcj_lat - lat) * 111000
    assert 200 < diff_lng < 800, f"Expected ~500m offset, got {diff_lng:.0f}m lng"
    assert 200 < diff_lat < 800, f"Expected ~500m offset, got {diff_lat:.0f}m lat"

def test_out_of_china_no_transform():
    lng, lat = -73.9857, 40.7484  # New York
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    assert gcj_lng == lng
    assert gcj_lat == lat
