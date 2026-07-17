"""Pure direction-classification logic.

The ``DirectionLabel`` enum and ``classify_direction`` / azimuth math are ported
**verbatim** from the gader-data-onboarding direction-mapper
(``direction_mapper/core/mapper.py`` and ``models.py``). The only addition is a
Hamburg-specific parser that turns German traversal phrases ("Nord nach Süd")
into a travel azimuth, replacing gader's single Dutch cardinal codes ("Z").
"""

from __future__ import annotations

from enum import Enum
from math import atan2, cos, degrees, radians, sin
from typing import Optional

from shapely import wkt
from shapely.geometry import Point


class DirectionLabel(str, Enum):
    """Valid direction labels for measurement locations.

    Ported verbatim from gader. ``bothDirections`` denotes a bidirectional
    measurement (both travel directions counted together); it has no single
    bearing, so it is only ever produced from a "no direction" input, never
    derived from a bearing comparison.
    """

    IN_DIRECTION = "inDirection"
    IN_OPPOSITE_DIRECTION = "inOppositeDirection"
    BOTH_DIRECTIONS = "bothDirections"


# --- German cardinal words -> azimuth (degrees) ---------------------------
# Hamburg encodes direction as a traversal phrase "X nach Y" (from X to Y). The
# *travel* bearing is the compass direction of the "to" word (Y). E.g.
# "Nord nach Süd" = travelling toward the south = 180°.
_GERMAN_CARDINALS: dict[str, float] = {
    "nord": 0.0,
    "nordost": 45.0,
    "ost": 90.0,
    "südost": 135.0,
    "suedost": 135.0,
    "süd": 180.0,
    "sued": 180.0,
    "südwest": 225.0,
    "suedwest": 225.0,
    "west": 270.0,
    "nordwest": 315.0,
}

# Phrases that mean "no single direction" -> bothDirections.
_NO_DIRECTION = {"keine richtung", "keine", "beide", "beide richtungen"}


class DirectionParseError(ValueError):
    """Raised when a direction phrase cannot be interpreted."""


def parse_german_direction(phrase: str) -> Optional[float]:
    """Parse a Hamburg German direction phrase into a travel azimuth (degrees).

    Returns ``None`` for explicitly non-directional phrases ("Keine Richtung"),
    signalling ``bothDirections`` upstream. Raises ``DirectionParseError`` if
    the phrase is directional but unrecognised.

    Examples:
        "Nord nach Süd"          -> 180.0   (travelling south)
        "Nordost nach Südwest"   -> 225.0   (travelling south-west)
        "Keine Richtung"         -> None    (bidirectional)
    """
    norm = " ".join(phrase.strip().lower().split())
    if norm in _NO_DIRECTION:
        return None

    # "X nach Y" -> the travel bearing is Y (the destination cardinal).
    if " nach " in norm:
        _, _, dest = norm.partition(" nach ")
        dest = dest.strip()
        if dest in _GERMAN_CARDINALS:
            return _GERMAN_CARDINALS[dest]
        raise DirectionParseError(f"Unknown destination cardinal: {dest!r}")

    # Bare cardinal ("Süd") -> that bearing.
    if norm in _GERMAN_CARDINALS:
        return _GERMAN_CARDINALS[norm]

    raise DirectionParseError(f"Unrecognised direction phrase: {phrase!r}")


# --- Azimuth math (ported verbatim from gader mapper.py) -------------------
def calculate_azimuth(start: Point, end: Point) -> float:
    """Azimuth (bearing) in degrees from *start* to *end* on WGS-84 lon/lat.

    Returns a value in [0, 360). Verbatim from gader."""
    lon1, lat1 = radians(start.x), radians(start.y)
    lon2, lat2 = radians(end.x), radians(end.y)

    d_lon = lon2 - lon1
    x = sin(d_lon) * cos(lat2)
    y = cos(lat1) * sin(lat2) - sin(lat1) * cos(lat2) * cos(d_lon)
    return (degrees(atan2(x, y)) + 360) % 360


def angle_diff(a: float, b: float) -> float:
    """Minimum angular difference between two bearings. Verbatim from gader."""
    return min((a - b) % 360, (b - a) % 360)


def classify_direction(
    measure_azimuth: float,
    start_geometry_wkt: str,
    end_geometry_wkt: str,
    threshold: float = 90.0,
) -> DirectionLabel:
    """Classify a measured travel azimuth as in-direction or opposite-direction
    relative to the road segment's natural direction (start → end).

    This is gader's ``DirectionMapper.classify_direction`` — the segment
    endpoints define the reference bearing, and the measured bearing is
    compared against it. Verbatim logic; the only shape change is taking a
    pre-parsed azimuth (Hamburg's phrase is parsed separately) instead of a
    cardinal/numeric ``measure_direction``.
    """
    start = wkt.loads(start_geometry_wkt)
    end = wkt.loads(end_geometry_wkt)
    azimuth = calculate_azimuth(start, end)
    diff = angle_diff(measure_azimuth, azimuth)
    if diff <= threshold:
        return DirectionLabel.IN_DIRECTION
    return DirectionLabel.IN_OPPOSITE_DIRECTION
