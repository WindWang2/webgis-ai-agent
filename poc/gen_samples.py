# PROTOTYPE (throwaway) — wayfinder ticket-007 样本数据生成。
# 产物: sample-dem.tif (300x300 UTM 50N 高斯山丘 DEM)、sample-polys.geojson (400 重叠圆多边形)、
#       sample-meta.json (DEM 4326 边界 + 中心 UTM 坐标, 供 viewshed 观测站与画布叠加)
import json
import math

import numpy as np
import pyproj
from rasterio.transform import from_origin

W, H = 300, 300
RES = 10.0  # m/px
CENTER_LON, CENTER_LAT = 116.40, 39.90

tr = pyproj.Transformer.from_crs(4326, 32650, always_xy=True)
e0, n0 = tr.transform(CENTER_LON, CENTER_LAT)
west, north = e0 - W * RES / 2, n0 + H * RES / 2

# 高斯山丘 + 缓坡 + 噪声
xs = (np.arange(W) + 0.5) * RES
ys = (H - np.arange(H) - 0.5) * RES  # 北→南 递减
xg, yg = np.meshgrid(xs, ys)
dem = 380 * np.exp(-(((xg - W * RES / 2) ** 2 + (yg - H * RES / 2) ** 2) / (2 * 700.0**2)))
dem += 0.02 * (xg - W * RES / 2) + 0.015 * (yg - H * RES / 2) + 50
rng = np.random.default_rng(42)
dem += rng.normal(0, 1.2, dem.shape)

import rasterio

with rasterio.open(
    "sample-dem.tif", "w", driver="GTiff", width=W, height=H, count=1,
    dtype="float32", crs="EPSG:32650", transform=from_origin(west, north, RES, RES),
    nodata=None,
) as dst:
    dst.write(dem.astype("float32"), 1)

# 400 个重叠圆多边形 (4326), 半径 30-80m
inv = pyproj.Transformer.from_crs(32650, 4326, always_xy=True)
features = []
for i in range(400):
    re = rng.uniform(W * RES / 2 - 600, W * RES / 2 + 600)
    rn = rng.uniform(H * RES / 2 - 600, H * RES / 2 + 600)
    rad = rng.uniform(30, 80)
    ring = []
    for k in range(16):
        a = 2 * math.pi * k / 16
        lon, lat = inv.transform(e0 - W * RES / 2 + (W * RES / 2 + re + rad * math.cos(a)) - (W * RES / 2 - W * RES / 2),
                                 n0 - H * RES / 2 + (H * RES / 2 + rn + rad * math.sin(a)))
        ring.append([round(lon, 7), round(lat, 7)])
    ring.append(ring[0])
    features.append({"type": "Feature", "properties": {"id": i}, "geometry": {"type": "Polygon", "coordinates": [ring]}})

with open("sample-polys.geojson", "w") as f:
    json.dump({"type": "FeatureCollection", "features": features}, f)

sw = inv.transform(west, north - H * RES)
ne = inv.transform(west + W * RES, north)
with open("sample-meta.json", "w") as f:
    json.dump({
        "bounds4326": {"west": sw[0], "south": sw[1], "east": ne[0], "north": ne[1]},
        "centerUTM": {"e": e0, "n": n0},
        "crs": "EPSG:32650", "res": RES, "size": [W, H],
    }, f, indent=1)

print("dem range:", float(dem.min()), float(dem.max()))
print("center z:", float(dem[H // 2, W // 2]))
print("ok")
