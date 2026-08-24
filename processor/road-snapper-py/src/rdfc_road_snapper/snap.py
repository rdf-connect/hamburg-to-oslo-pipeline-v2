"""Pure road-snapping logic.

Distilled from the gader-data-onboarding road-snapper's domain layer
(``domain/core.clj`` + ``repo/shapefile.clj``): load road LineStrings into an
R-tree, find the nearest segment to an input point, and expose that segment's
geometry plus its start/end points. No Kafka, no caching, no HTTP — just the
snap.

Distances are computed in a projected CRS (meters), not in WGS84 degrees, so
"nearest" and ``max_distance`` are meter-accurate. For Germany the sensible
projected CRS is UTM 32N (EPSG:25832); the gader original hardcoded Belgian
Lambert72 (EPSG:31370), which is wrong outside Belgium.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from pyproj import Transformer
from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

WGS84 = "EPSG:4326"


@dataclass(frozen=True)
class SnapResult:
    """The outcome of snapping one point to the road network.

    All WKT strings are in WGS84 (lon lat), matching the geometry the rest of
    the pipeline produces. ``offset_m`` is the distance along the segment from
    its start to the nearest point on the segment; ``distance_m`` is how far
    the input point was from the segment (both in meters)."""

    road_segment_wkt: str
    road_segment_start_wkt: str
    road_segment_end_wkt: str
    offset_m: float
    distance_m: float


# Drivable OSM highway classes a motor-vehicle (Kfz) counter can sit on.
# Excludes footway/path/cycleway/steps/etc., which otherwise win on raw
# nearest-distance (a footpath often runs a couple of meters closer to the
# sensor than the road it's counting). See README "Road class filter".
DRIVABLE_HIGHWAY = (
    "motorway", "motorway_link",
    "trunk", "trunk_link",
    "primary", "primary_link",
    "secondary", "secondary_link",
    "tertiary", "tertiary_link",
    "unclassified", "residential", "living_street", "service",
)


def _drivable_where(classes: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{c}'" for c in classes)
    return f"highway IN ({quoted})"


def _is_pbf(path: str) -> bool:
    return path.lower().endswith((".pbf", ".osm.pbf", ".osm"))


class RoadSnapper:
    """Snaps WGS84 points to the nearest road segment from an OSM source.

    Reads either an OSM roads shapefile (``.shp``) or an OSM extract
    (``.osm.pbf``) directly via GDAL/pyogrio. For a ``.pbf`` the roads live in
    the ``lines`` layer mixed with waterways/railways/footpaths, so a highway
    filter is applied; by default only *drivable* classes are kept.

    Args:
        source_path: Path to an OSM roads ``.shp`` or an ``.osm.pbf``.
        projected_crs: Metric CRS used for distance math. Default EPSG:25832
            (UTM 32N), correct for Hamburg/Germany.
        max_distance_m: Reject a snap if the nearest segment is further than
            this (meters). Mirrors ``ROAD_SNAPPER_MAX_DISTANCE_M`` (default 500).
        layer: OGR layer to read. Default ``"lines"`` (the OSM roads layer in a
            ``.pbf``). Ignored/irrelevant for single-layer shapefiles.
        where: OGR SQL filter pushed down to GDAL. Default keeps only drivable
            ``highway`` classes. Pass ``None`` to read every feature (e.g. for a
            pre-filtered roads shapefile that has no ``highway`` column).
    """

    def __init__(
        self,
        source_path: str,
        projected_crs: str = "EPSG:25832",
        max_distance_m: float = 500.0,
        layer: str | None = "lines",
        where: str | None = _drivable_where(DRIVABLE_HIGHWAY),
    ) -> None:
        self.max_distance_m = max_distance_m
        self._to_proj = Transformer.from_crs(
            WGS84, projected_crs, always_xy=True
        ).transform
        self._to_wgs84 = Transformer.from_crs(
            projected_crs, WGS84, always_xy=True
        ).transform

        self._segments_proj = self._load_segments(source_path, layer, where)
        self._tree = STRtree(self._segments_proj)

    def _load_segments(
        self, source_path: str, layer: str | None, where: str | None
    ) -> list[LineString]:
        """Read road LineStrings from the OSM source, reprojected to metric CRS.

        pyogrio (GDAL) reads both shapefiles and ``.osm.pbf`` via geopandas. The
        ``where`` filter is pushed down to GDAL so non-road lines (waterways,
        railways, footpaths) never reach Python. MultiLineStrings are exploded
        into their component LineStrings so the R-tree and linear-referencing
        operate on simple segments.
        """
        import pyogrio

        read_kwargs = {"columns": [], "read_geometry": True}
        if layer is not None and _is_pbf(source_path):
            read_kwargs["layer"] = layer
        if where is not None:
            read_kwargs["where"] = where

        try:
            frame = pyogrio.read_dataframe(source_path, **read_kwargs)
        except Exception:
            # A pre-filtered roads shapefile may have no `highway` column, so a
            # highway WHERE clause would error — retry without the filter.
            if "where" in read_kwargs:
                read_kwargs.pop("where")
                frame = pyogrio.read_dataframe(source_path, **read_kwargs)
            else:
                raise

        segments: list[LineString] = []
        for geom in frame.geometry:
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "LineString":
                parts = [geom]
            elif geom.geom_type == "MultiLineString":
                parts = list(geom.geoms)
            else:
                continue
            for part in parts:
                # Reproject each vertex WGS84 -> metric CRS once, up front.
                proj_coords = [self._to_proj(x, y) for x, y in part.coords]
                segments.append(LineString(proj_coords))
        if not segments:
            raise ValueError(
                f"No road LineStrings loaded from source: {source_path} "
                f"(layer={layer!r}, where={where!r})"
            )
        return segments

    def snap(self, point_wkt: str) -> Optional[SnapResult]:
        """Snap a WGS84 WKT POINT to the nearest road segment.

        Returns ``None`` if the nearest segment is beyond ``max_distance_m``.
        Raises ``ValueError`` if ``point_wkt`` is not a valid POINT.
        """
        geom = shapely_wkt.loads(point_wkt)
        if not isinstance(geom, Point):
            raise ValueError(f"Expected a WKT POINT, got {geom.geom_type}")

        px, py = self._to_proj(geom.x, geom.y)
        pt_proj = Point(px, py)

        idx = self._tree.nearest(pt_proj)
        segment = self._segments_proj[idx]

        distance_m = segment.distance(pt_proj)
        if distance_m > self.max_distance_m:
            return None

        offset_m = segment.project(pt_proj)

        start_proj = Point(segment.coords[0])
        end_proj = Point(segment.coords[-1])

        return SnapResult(
            road_segment_wkt=self._line_to_wgs84_wkt(segment),
            road_segment_start_wkt=self._point_to_wgs84_wkt(start_proj),
            road_segment_end_wkt=self._point_to_wgs84_wkt(end_proj),
            offset_m=offset_m,
            distance_m=distance_m,
        )

    def _point_to_wgs84_wkt(self, pt_proj: Point) -> str:
        lon, lat = self._to_wgs84(pt_proj.x, pt_proj.y)
        return Point(lon, lat).wkt

    def _line_to_wgs84_wkt(self, line_proj: LineString) -> str:
        coords = [self._to_wgs84(x, y) for x, y in line_proj.coords]
        return LineString(coords).wkt
