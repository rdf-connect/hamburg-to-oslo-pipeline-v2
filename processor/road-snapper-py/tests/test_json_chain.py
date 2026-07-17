"""End-to-end JSON enrichment test: snap then classify, against the real pbf.

Simulates the pre-RML chain fetcher -> road-snapper -> direction-mapper on a
realistic SensorThings record, using the pure logic modules directly (no
rdfc_runner / gRPC needed)."""

import importlib.util
import os
import sys

HERE = os.path.dirname(__file__)


def _load(mod_name, rel_path):
    path = os.path.join(HERE, rel_path)
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod

snap = _load("_snap", "../src/rdfc_road_snapper/snap.py")
classify = _load(
    "_classify",
    "../../direction-mapper-py/src/rdfc_direction_mapper/classify.py",
)

PBF = os.path.join(HERE, "..", "resources", "hamburg-260714.osm.pbf")

# A realistic fetcher record (datastream 26598 = "Nord nach Süd", meetpunt 12317).
RECORD = {
    "thing": {"@iot.id": 12317, "properties": {"richtung": "Nord nach Süd"}},
    "datastream": {"@iot.id": 26598},
    "observation": {"@iot.id": 1095675070, "result": 2781},
    "locations": [
        {"location": {"geometry": {"coordinates": [9.917789, 53.447992]}}}
    ],
}


def point_wkt(record):
    c = record["locations"][0]["location"]["geometry"]["coordinates"]
    return f"POINT ({c[0]} {c[1]})"


def main() -> int:
    if not os.path.exists(PBF):
        print(f"SKIP: pbf not found at {PBF}")
        return 0

    snapper = snap.RoadSnapper(PBF)  # defaults: pbf, lines, drivable, UTM32N

    # --- road-snapper step: inject _wegsegment ---
    result = snapper.snap(point_wkt(RECORD))
    assert result is not None, "expected a snap"
    RECORD["_wegsegment"] = {
        "centerlineWkt": result.road_segment_wkt,
        "beginWkt": result.road_segment_start_wkt,
        "eindWkt": result.road_segment_end_wkt,
        "offsetM": result.offset_m,
        "distanceM": result.distance_m,
    }
    print("after snapper: _wegsegment injected")
    print("  begin:", RECORD["_wegsegment"]["beginWkt"])
    print("  eind :", RECORD["_wegsegment"]["eindWkt"])

    # --- direction-mapper step: classify + inject _rijrichting ---
    phrase = RECORD["thing"]["properties"]["richtung"]
    azimuth = classify.parse_german_direction(phrase)
    assert azimuth == 180.0
    label = classify.classify_direction(
        azimuth, RECORD["_wegsegment"]["beginWkt"], RECORD["_wegsegment"]["eindWkt"]
    )
    RECORD["_rijrichting"] = {
        "locationDirection": label.value,
        "measurementDirection": label.value,
    }
    print(f"\nafter direction-mapper: phrase={phrase!r} azimuth={azimuth} "
          f"-> {label.value}")

    assert label.value in ("inDirection", "inOppositeDirection")
    # Original fields preserved, enrichment additive.
    assert RECORD["observation"]["result"] == 2781
    assert "_wegsegment" in RECORD and "_rijrichting" in RECORD
    print("\nPASS: JSON enrichment chain (snap -> classify) works on the real pbf.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
