"""Standalone verification of the snap logic against a synthetic Hamburg road
network. No external OSM data needed — we write a tiny real shapefile and snap
the actual meetpunt coordinates from the pipeline against it."""

import importlib.util
import os
import sys
import tempfile

import pyogrio
import shapely
from shapely.geometry import LineString, Point

# Load the pure snap module directly by path, bypassing the package __init__
# (which imports rdflib/rdfc_runner — not needed for the pure logic test).
_snap_path = os.path.join(os.path.dirname(__file__), "..", "src", "rdfc_road_snapper", "snap.py")
_spec = importlib.util.spec_from_file_location("_snap", _snap_path)
_snap = importlib.util.module_from_spec(_spec)
sys.modules["_snap"] = _snap
_spec.loader.exec_module(_snap)
RoadSnapper = _snap.RoadSnapper

# One real meetpunt from the live KG (Verkehrszählstelle 0370921).
MEETPUNT_WKT = "POINT (9.917789 53.447992)"


def _write_synthetic_shapefile(path: str) -> None:
    """A short N–S road passing very close to the meetpunt, plus a decoy far
    road. Snapper should pick the near one."""
    near_road = LineString([(9.9178, 53.4475), (9.9178, 53.4485)])  # ~N-S
    far_road = LineString([(10.20, 53.60), (10.21, 53.61)])  # kilometers away
    gdf = shapely  # alias to satisfy linters; not used
    pyogrio.write_dataframe(
        _frame([near_road, far_road]),
        path,
        driver="ESRI Shapefile",
    )


def _frame(geoms):
    import geopandas as gpd  # only needed to author the test fixture

    return gpd.GeoDataFrame({"id": range(len(geoms))}, geometry=geoms, crs="EPSG:4326")


def main() -> int:
    with tempfile.TemporaryDirectory() as d:
        shp = os.path.join(d, "roads.shp")
        try:
            _write_synthetic_shapefile(shp)
        except ImportError:
            print("SKIP: geopandas not available to author the fixture shapefile")
            return 0

        snapper = RoadSnapper(shp, projected_crs="EPSG:25832", max_distance_m=500.0)
        result = snapper.snap(MEETPUNT_WKT)

        assert result is not None, "expected a snap within 500 m"
        print("road_segment_wkt      :", result.road_segment_wkt)
        print("road_segment_start_wkt:", result.road_segment_start_wkt)
        print("road_segment_end_wkt  :", result.road_segment_end_wkt)
        print(f"offset_m              : {result.offset_m:.2f}")
        print(f"distance_m            : {result.distance_m:.2f}")

        # The near road runs N->S; endpoints must differ and the point must be close.
        start = shapely.from_wkt(result.road_segment_start_wkt)
        end = shapely.from_wkt(result.road_segment_end_wkt)
        assert isinstance(start, Point) and isinstance(end, Point)
        assert (start.x, start.y) != (end.x, end.y), "segment endpoints identical"
        assert result.distance_m < 100, f"snapped too far: {result.distance_m} m"
        assert result.offset_m >= 0
        print("\nPASS: point snapped to the near N-S segment with distinct endpoints.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
