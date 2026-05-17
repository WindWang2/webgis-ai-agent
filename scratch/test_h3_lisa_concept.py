import h3
import geopandas as gpd
from shapely.geometry import Polygon

# Generate a set of hexes around a center point
center = h3.geo_to_h3(39.9, 116.39, 8)
hexes = h3.k_ring(center, 2)
print("Num hexes:", len(hexes))
